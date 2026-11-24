"""Exercise the direct cutover, recovery and precise deletion plan without a cluster."""
from contextlib import redirect_stdout
from copy import deepcopy
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cleanup
import lab
import verify


class Project:
    NS = "fixture-lab"
    PART_OF = lab.PART_OF
    manifests = staticmethod(lab.manifests)

    def __init__(self):
        self.events = []
        self.fail_verify = self.fail_delete = False
        self.secret = "present"
        self.db = {}
        for filename in ("app.yaml", "guardrails.yaml", "network.yaml", "content-model.yaml", "showroom.yaml"):
            for obj in lab.manifests(filename):
                obj["metadata"].setdefault("labels", {}).update(lab.PART_OF)
                kind = {"PersistentVolumeClaim":"pvc"}.get(obj["kind"], obj["kind"].lower())
                self.db[kind, obj["metadata"]["name"]] = obj
        for kind,name in [("buildconfig","grlab-rails"), ("imagestream","grlab-rails"),
                          ("configmap","grlab-provider"),("configmap","grlab-rules-"+"a"*12),
                          ("configmap","grlab-site-settings"),("pvc","user-data")]:
            self.db[kind,name] = {"metadata":{"name":name,"labels":dict(lab.PART_OF)}}
        self.db["configmap", "grlab-provider"]["data"] = {"model":"sample","url":"https://maas.example.test/v1","subscription":"demo"}
        self.env[:] = [{"name":"CUSTOM_ENV","value":"keep"},{"name":"BASE_URL","value":"http://grlab-rails/v1"},
                       {"name":"GRLAB_BACKEND_MODE","value":"proxy"},{"name":"MODEL_ID","value":"sample"},
                       {"name":"API_KEY","value":"workshop-local"}]

    @property
    def env(self):
        return self.db["deployment","grlab-agent"]["spec"]["template"]["spec"]["containers"][0]["env"]

    def get(self, kind, name):
        return deepcopy(self.db.get((kind, name)))

    def oc(self, *args, data=None, **kwargs):
        if args[:2] == ("get","configmaps"):
            return json.dumps({"items":[o for (k,_),o in self.db.items() if k=="configmap"]})
        if args[:2] == ("get","secret/grlab-main"):
            return self.secret
        if args[0] == "patch":
            test, replace = json.loads(data)
            assert test["path"] == replace["path"] == "/spec/template/spec/containers/0/env"
            assert test["value"] == self.env
            self.env[:] = replace["value"]
            self.events.append("switch")
        elif args[0] == "delete":
            assert "-l" in args and args[args.index("-l")+1] == "app.kubernetes.io/part-of=guardrail-agent-showroom"
            name = next(x.split("metadata.name=")[1] for x in args if x.startswith("--field-selector=metadata.name="))
            self.events.append(("delete",args[1],name))
            if self.fail_delete and args[1] == "service":
                raise RuntimeError("simulated deletion interruption")
            self.db.pop((args[1],name),None)
        else:
            raise AssertionError(args)

    def rollout(self, name):
        assert name == "grlab-agent"
        self.events.append("rollout")

    def verify(self, stage):
        assert stage == "direct"
        env = {v["name"]:v for v in self.env}
        assert env["CUSTOM_ENV"]["value"] == "keep" and env["GRLAB_BACKEND_MODE"]["value"]=="direct"
        assert env["BASE_URL"]["valueFrom"]["configMapKeyRef"] == {"name":"grlab-provider","key":"url"}
        assert env["API_KEY"]["valueFrom"]["secretKeyRef"] == {"name":"grlab-main","key":"api-key"}
        assert env["MAAS_SUBSCRIPTION"]["valueFrom"]["configMapKeyRef"]["key"] == "subscription"
        self.events.append("test direct")
        if self.fail_verify:
            raise RuntimeError("simulated direct model failure")


class CleanupTests(unittest.TestCase):
    def invoke(self, lab, **flags):
        with redirect_stdout(StringIO()):
            cleanup.run(lab, **flags)

    def test_preview_and_default_cleanup(self):
        proj = Project()
        before = deepcopy(proj.db)
        self.invoke(proj,dry=True)
        self.assertEqual((proj.db,proj.events), (before, []))
        self.invoke(proj)
        self.assertEqual(proj.events[:3], ["switch","rollout","test direct"])
        self.assertEqual(proj.events[-1],"test direct")
        removed = set(before)-set(proj.db)
        self.assertEqual(removed,{("deployment","grlab-rails"),("service","grlab-rails"),("buildconfig","grlab-rails"),
                                 ("imagestream","grlab-rails"),("networkpolicy","grlab-rails-ingress"),
                                 ("configmap","grlab-rules-"+"a"*12)})
        proj.events.clear()
        self.invoke(proj)
        self.assertEqual(proj.events,["rollout","test direct","test direct"])

    def test_optional_gpu_and_cache(self):
        proj = Project()
        with self.assertRaises(ValueError):
            self.invoke(proj,delete_cache=True)
        self.assertFalse(proj.events)
        self.invoke(proj,remove_model=True)
        for kind in ("deployment","service"):
            self.assertNotIn((kind,"nemotron-safety-guard-vllm"),proj.db)
        self.assertNotIn(("networkpolicy","grlab-content-ingress"),proj.db)
        self.assertIn(("pvc","nemotron-safety-guard-model-cache"),proj.db)
        self.invoke(proj,remove_model=True,delete_cache=True)
        self.assertNotIn(("pvc","nemotron-safety-guard-model-cache"),proj.db)
        self.assertIn(("pvc","user-data"),proj.db)

    def test_direct_gate_requires_tool_and_exact_amount(self):
        health = {"status":"healthy","guardrails_reachable":False,"agent_initialized":True}
        tool = {"role":"tool","name":"check_balance","content":"Account ACCT-12345: Checking balance $2,450.00"}
        for claimed, include_tool, passed in [("$2,450.00",True,True), ("$2,450.00",False,False),
                                               ("$12,450.99",True,False), ("$2,450.77",True,False)]:
            with self.subTest(claimed=claimed, include_tool=include_tool):
                deltas = ([tool] if include_tool else []) + [{"content":"Your checking balance is "+claimed}]
                raw = "".join("data: "+json.dumps({"choices":[{"delta":delta}]})+"\n\n" for delta in deltas) + "data: [DONE]\n\n"
                with patch.object(verify,"request",return_value=health), patch.object(verify,"chat",return_value="Open the official app to reset your password."), \
                     patch.object(verify,"urlopen",return_value=BytesIO(raw.encode())), redirect_stdout(StringIO()):
                    if passed:
                        verify.run("direct","http://example.test")
                    else:
                        with self.assertRaises(RuntimeError):
                            verify.run("direct","http://example.test")

    def test_failed_direct_gate_restores_proxy(self):
        proj = Project()
        before = deepcopy(proj.db)
        proj.fail_verify=True
        with self.assertRaisesRegex(RuntimeError,"model failure"):
            self.invoke(proj)
        self.assertEqual(proj.db,before)
        self.assertEqual(proj.events,["switch","rollout","test direct","switch","rollout"])

    def test_partial_deletion_is_retriable(self):
        proj = Project()
        proj.fail_delete=True
        with self.assertRaisesRegex(RuntimeError,"interruption"):
            self.invoke(proj)
        self.assertNotIn(("deployment","grlab-rails"),proj.db)
        self.assertIn(("service","grlab-rails"),proj.db)
        proj.fail_delete=False
        self.invoke(proj)
        self.assertNotIn(("service","grlab-rails"),proj.db)

    def test_ownership_and_credential_checks_precede_writes(self):
        proj=Project()
        proj.db["deployment","grlab-rails"]["metadata"]["labels"].clear()
        with self.assertRaisesRegex(RuntimeError,"Ownership conflict"):
            self.invoke(proj)
        self.assertFalse(proj.events)
        proj=Project()
        proj.secret=""
        with self.assertRaisesRegex(RuntimeError,"API key"):
            self.invoke(proj)
        self.assertFalse(proj.events)

if __name__=="__main__":
    unittest.main()
