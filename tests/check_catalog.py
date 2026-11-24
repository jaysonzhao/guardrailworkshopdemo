"""Verify selection, paired examples, rendered prompts and publication/rollback without a cluster."""
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import catalog_cli
import lab
import policy
import rulebook
import rule_sample


class Revision:
    PROFILE = lab.PROFILE
    resource = staticmethod(lab.resource)

    def __init__(self):
        self.proxy, _ = lab.manifests("guardrails.yaml")
        self.proxy["metadata"]["annotations"] = {self.PROFILE:"rules"}
        self.old = "grlab-rules-000000000000"
        pod = self.proxy["spec"]["template"]["spec"]
        pod["volumes"][0]["configMap"]["name"] = self.old
        pod["containers"][0]["volumeMounts"] = []
        self.provider = {"model":"workshop","url":"https://main.example.test/v1","subscription":""}
        data = policy.render("rules", self.provider["model"], self.provider["url"])
        self.configs = {self.old:{"data":data}}
        self.fail = False
        self.patches = 0

    def current(self):
        return self.proxy["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"]

    def get(self, kind, name):
        if kind=="deployment":
            return deepcopy(self.proxy)
        if name=="grlab-provider":
            return {"data":self.provider}
        return deepcopy(self.configs.get(name))

    def apply(self, doc):
        self.configs[doc["metadata"]["name"]] = deepcopy(doc)

    def oc(self, *args, data):
        assert args[:2] == ("patch","deploy/grlab-rails")
        test_name, test_mounts, new_name, new_mounts = json.loads(data)
        pod = self.proxy["spec"]["template"]["spec"]
        assert self.current() == test_name["value"] and pod["containers"][0]["volumeMounts"] == test_mounts["value"]
        pod["volumes"][0]["configMap"]["name"] = new_name["value"]
        pod["containers"][0]["volumeMounts"] = new_mounts["value"]
        self.patches += 1

    def rollout(self, name):
        assert name=="grlab-rails"

    def verify(self, stage):
        assert stage=="rules"
        if self.fail:
            raise RuntimeError("simulated failure")


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.categories,cls.rules=rulebook.catalog()

    def test_source_and_all_local_pairs(self):
        rows=self.rules
        self.assertEqual((len(self.categories), len(rows)),(8,53))
        self.assertEqual({r["taxonomy"] for r in rows.values() if "taxonomy" in r},{f"S{i}" for i in range(1,24)})
        patterns=yaml.safe_load((rulebook.POLICIES/"rules.yaml").read_text())["patterns"]
        with redirect_stdout(StringIO()), self.assertLogs("banking_rule_sample",level="WARNING"):
            for r in rows.values():
                if r["engine"]=="local":
                    for choice in ({r["id"]},set(rows)):
                        rule_sample.demonstrate(r,rulebook.POLICIES,rulebook.runtime(rows,choice,patterns,"rules"))

    def test_invalid_verdicts_cannot_pass_a_category_test(self):
        banking = rule_sample.load_banking(rulebook.POLICIES)
        for raw in ('unsafe', 'null', '{"User Safety":"safe","User Safety":"safe"}',
                    '{"User Safety":"unsafe"}','{"User Safety":"safe","Safety Categories":"S16"}'):
            self.assertEqual(banking.read_safety_verdict(raw,"User Safety")[0],"invalid")
        self.assertEqual(banking.read_safety_verdict('{"User Safety":"unsafe","Safety Categories":"S16"}',"User Safety"),("unsafe",["S16"]))

    def test_staging_preserves_other_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"selection.yaml"
            for key in ("marketing-consent","s16","marketing-consent"):
                rulebook.change(key,True,self.rules,file)
            self.assertEqual(rulebook.selection(self.rules,file)["enable"],["marketing-consent","s16"])
            rulebook.change("marketing-consent",False,self.rules,file)
            self.assertEqual(rulebook.selection(self.rules,file),{"enable":["s16"],"disable":["marketing-consent"]})

    def test_one_rule_from_empty_and_restore_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "selection.yaml"
            rulebook.baseline("empty", self.rules, file)
            rulebook.change("credential", True, self.rules, file)
            chosen = rulebook.resolve(self.rules, rulebook.selection(self.rules, file), set(self.rules))
            self.assertEqual(chosen, {"credential"})
            rulebook.baseline("defaults", self.rules, file)
            self.assertEqual(rulebook.resolve(self.rules, rulebook.selection(self.rules, file), chosen),
                             rulebook.defaults(self.rules))

    def test_directions_and_taxonomy(self):
        base=rulebook.defaults(self.rules)
        for chosen in (base, (base | {"s16"}) - {"s10","model-output","model-banking-policy"}):
            direct=policy.render("rules","workshop","http://model/v1",active_ids=chosen)
            self.assertNotIn("prompts.yml",direct)
            self.assertEqual(len(yaml.safe_load(direct["config.yaml"])["models"]),1)
            content=policy.render("content","workshop","http://model/v1",active_ids=chosen)
            cfg=yaml.safe_load(content["config.yaml"])
            for direction in ("input","output"):
                self.assertEqual("banking content safety "+direction in cfg["rails"][direction]["flows"], "model-"+direction in chosen)
            for task in yaml.safe_load(content["prompts.yml"])["prompts"]:
                body=task["content"]
                for n in range(1,24):
                    self.assertEqual(f"\nS{n}:" in body, "s"+str(n) in chosen)
                self.assertEqual("mandatory banking-demo policy" in body.lower(), "model-banking-policy" in chosen)
        with self.assertRaisesRegex(ValueError,"Select a Nemotron category"):
            policy.render("content","workshop","http://model/v1",active_ids={"model-input"})

    def test_publish_and_restore_previous_revision_on_error(self):
        live=Revision()
        edits={"enable":["marketing-consent","s16"],"disable":["training-patterns"]}
        with redirect_stdout(StringIO()):
            catalog_cli.revise(live,True,self.rules,edits)
            self.assertEqual((live.current(),live.patches),(live.old,0))
            catalog_cli.revise(live,False,self.rules,edits)
            mounted=live.current()
            active=catalog_cli.selected(live.configs[mounted]["data"])
            self.assertIn("marketing-consent",active)
            self.assertIn("s16",active)
            self.assertNotIn("training-patterns",active)
            catalog_cli.revise(live,False,self.rules,edits)
            self.assertEqual(live.patches,1)
            live.fail=True
            with self.assertRaisesRegex(RuntimeError,"simulated"):
                catalog_cli.revise(live,False,self.rules,{"enable":["collections-tone"],"disable":[]})
            self.assertEqual(live.current(),mounted)

if __name__=="__main__":
    unittest.main()
