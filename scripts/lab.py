#!/usr/bin/env python3
"""Deploy one workshop stage into the explicitly selected OpenShift project."""
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile
from urllib.parse import urlsplit

import yaml
from policy import render, STAGES

ROOT = Path(__file__).resolve().parents[1]
PART_OF = {"app.kubernetes.io/part-of": "guardrail-agent-showroom"}
PROFILE = "showroom.rhpds.io/profile"
NS = os.environ.get("LAB_NAMESPACE", "")


def oc(*args, data=None, capture=True):
    result = subprocess.run(["oc", "-n", NS, *args], input=data, text=True,
                            stdout=subprocess.PIPE if capture else None, check=True)
    return (result.stdout or "").strip()


def get(kind, name):
    raw = oc("get", kind, name, "--ignore-not-found", "-o", "json")
    return json.loads(raw) if raw else None


def resource(api, kind, name, **fields):
    return {"apiVersion": api, "kind": kind, "metadata": {"name": name}, **fields}


def apply(doc):
    doc["metadata"].setdefault("labels", {}).update(PART_OF)
    oc("apply", "-f", "-", data=json.dumps(doc), capture=False)


def manifests(filename):
    return list(yaml.safe_load_all((ROOT / "deploy" / filename).read_text()))


def rollout(name, timeout="10m"):
    oc("rollout", "status", "deployment/" + name, "--timeout=" + timeout, capture=False)


def build(name, directory):
    apply(resource("image.openshift.io/v1", "ImageStream", name))
    apply(resource("build.openshift.io/v1", "BuildConfig", name, spec={
        "runPolicy": "Serial", "source": {"type": "Binary", "binary": {}},
        "strategy": {"type": "Docker", "dockerStrategy": {}},
        "output": {"to": {"kind": "ImageStreamTag", "name": name + ":latest"}},
        "triggers": []}))
    oc("start-build", name, "--from-dir=" + str(ROOT / directory), "--follow", "--wait", capture=False)
    image = oc("get", "istag", name + ":latest", "-o", "jsonpath={.image.dockerImageReference}")
    if "@sha256:" not in image:
        raise RuntimeError(f"Missing published image digest for {name}")
    return image


def site():
    if not (ROOT / "showroom/www/index.html").is_file():
        raise RuntimeError("Build the guide first with scripts/build-site.sh.")
    dep, svc, route, policy = manifests("showroom.yaml")
    dep["spec"]["template"]["spec"]["containers"][0]["image"] = build("grlab-showroom", "showroom")
    for doc in (policy, dep, svc, route):
        apply(doc)
    rollout("grlab-showroom")
    base = "https://" + oc("get", "route/grlab-showroom", "-o", "jsonpath={.spec.host}")
    print("Guide: " + base, flush=True)
    last_error = None
    for attempt in range(20):
        try:
            with urlopen(base + "/modules/en/index.html", timeout=8) as page:
                if b"Guardrails for a banking agent" not in page.read():
                    raise RuntimeError("unexpected guide page")
            with urlopen(base + "/modules/en/_attachments/lab-files.zip", timeout=12) as bundle:
                with ZipFile(BytesIO(bundle.read())) as archive:
                    if archive.testzip() or "guardrail-agent-lab/scripts/lab.py" not in archive.namelist():
                        raise RuntimeError("invalid participant download")
            print("PASS HTTPS guide and participant ZIP")
            return
        except (URLError, OSError, BadZipFile, RuntimeError) as exc:
            last_error = exc
            if attempt < 19:
                time.sleep(3)
    raise RuntimeError(f"Guide deployed but HTTPS delivery was not verified: {last_error}")


def configure_provider():
    current = get("configmap", "grlab-provider")
    saved = current["data"] if current else {}
    model = os.getenv("MAIN_MODEL_ID", saved.get("model", ""))
    url = os.getenv("MAIN_BASE_URL", saved.get("url", "")).rstrip("/")
    subscription = os.getenv("MAAS_SUBSCRIPTION", saved.get("subscription", ""))
    parts = urlsplit(url)
    if (not model or not parts.hostname or parts.scheme not in {"http", "https"} or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or not parts.path.endswith("/v1")):
        raise RuntimeError("Set MAIN_MODEL_ID and a credential-free MAIN_BASE_URL ending in /v1.")
    # A Secret is created only once. A retry preserves the cluster's existing key.
    if not oc("get", "secret/grlab-main", "--ignore-not-found", "-o", "name"):
        keyfile = os.environ.get("MAIN_API_KEY_FILE")
        if not keyfile:
            raise RuntimeError("Set MAIN_API_KEY_FILE to a private, one-line file or provision grlab-main/api-key.")
        key = Path(keyfile).read_text().rstrip("\r\n")
        if not key or "\n" in key or "\r" in key:
            raise RuntimeError("The key file must contain one nonempty line.")
        oc("create", "secret", "generic", "grlab-main", "--from-file=api-key=/dev/stdin", data=key)
    present = oc("get", "secret/grlab-main", "-o", 'go-template={{if index .data "api-key"}}present{{end}}')
    if present != "present":
        raise RuntimeError("grlab-main is missing a nonempty api-key entry.")
    apply(resource("v1", "ConfigMap", "grlab-provider", data={"url": url, "model": model, "subscription": subscription}))


def agent(mode, image):
    dep, svc = manifests("app.yaml")
    container = dep["spec"]["template"]["spec"]["containers"][0]
    container["image"] = image
    provider_revision = oc("get", "configmap/grlab-provider", "-o", "jsonpath={.metadata.resourceVersion}")
    dep["spec"]["template"]["metadata"]["annotations"] = {"showroom.rhpds.io/provider-revision": provider_revision}
    if mode == "proxy":
        by_name = {e["name"]: e for e in container["env"]}
        by_name["BASE_URL"] = {"name": "BASE_URL", "value": "http://grlab-rails/v1"}
        by_name["API_KEY"] = {"name": "API_KEY", "value": "workshop-local"}
        by_name["GRLAB_BACKEND_MODE"]["value"] = "proxy"
        del by_name["MAAS_SUBSCRIPTION"]
        container["env"] = list(by_name.values())
    apply(dep)
    apply(svc)
    rollout("grlab-agent")


def pre():
    existing = get("deployment", "grlab-agent")
    if existing:
        old = existing["spec"]["template"]["spec"]["containers"][0]
        if any(e.get("name") == "GRLAB_BACKEND_MODE" and e.get("value") == "proxy" for e in old["env"]):
            raise RuntimeError("The agent already uses NeMo. Continue with rules, content or test in this project.")
    configure_provider()
    for obj in manifests("network.yaml"):
        apply(obj)
    agent("direct", build("grlab-agent", "app"))
    verify("direct")


def active():
    dep = get("deployment", "grlab-rails")
    return dep["metadata"].get("annotations", {}).get(PROFILE) if dep else None


def verify(stage):
    script = (ROOT / "scripts/verify.py").read_text()
    oc("exec", "-i", "deployment/grlab-agent", "--", "python", "-", stage, data=script, capture=False)


def mount_profile(stage, image, settings):
    data = render(stage, settings["model"], settings["url"], settings["subscription"])
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]
    cm_name = f"grlab-{stage}-{digest}"
    apply(resource("v1", "ConfigMap", cm_name, data=data, immutable=True))
    dep, svc = manifests("guardrails.yaml")
    dep["metadata"]["annotations"] = {PROFILE: stage}
    pod = dep["spec"]["template"]["spec"]
    container = pod["containers"][0]
    container["image"] = image
    container["args"][container["args"].index("PROFILE")] = stage
    # NeMo follows symlinks recursively. Mount individual keys so Kubernetes'
    # atomic ..data projection cannot cause duplicate flow/config loading.
    container["volumeMounts"] = [
        {"name": "policy", "subPath": name, "mountPath": f"/opt/rail-configs/{stage}/{name}", "readOnly": True}
        for name in sorted(data)
    ]
    pod["volumes"][0]["configMap"]["name"] = cm_name
    if stage == "content":
        container["env"].append({"name": "NEMOTRON_API_KEY", "value": "workshop-local"})
    apply(svc)
    apply(dep)
    rollout("grlab-rails")


def model():
    pvc, deployment, svc, policy = manifests("content-model.yaml")
    storage = os.getenv("MODEL_STORAGE_CLASS")
    current = get("pvc", pvc["metadata"]["name"])
    if current:
        present = current["spec"].get("storageClassName")
        if storage and storage != present:
            raise RuntimeError("The existing model PVC uses a different storage class.")
        if present:
            pvc["spec"]["storageClassName"] = present
    elif storage:
        pvc["spec"]["storageClassName"] = storage
    for doc in (policy, pvc, svc, deployment):
        apply(doc)
    name = deployment["metadata"]["name"]
    rollout(name, "60m")
    script = (ROOT / "scripts/probe_model.py").read_text()
    prompt = yaml.safe_load((ROOT / "policies/prompts.yml").read_text())["prompts"][0]["content"]
    # Supply the same rendered input prompt used by NeMo, over stdin together with the probe.
    code = "PROMPT = " + repr(prompt.replace("{{ user_input }}", "How do I reset my bank password?")) + "\n" + script
    oc("exec", "-i", "deployment/" + name, "-c", "vllm", "--", "python", "-", data=code, capture=False)


def activate(stage):
    app = get("deployment", "grlab-agent")
    if not app:
        raise RuntimeError("Deploy Pre-1 first.")
    proxy = get("deployment", "grlab-rails")
    old = active() if proxy else None
    if not old and stage != "bootstrap":
        raise RuntimeError("Deploy Module 1 first.")
    if stage == "content" and old not in ("rules", "content"):
        raise RuntimeError("Activate and check Module 2 before adding a content model.")
    if stage == "bootstrap" and old in ("rules", "content"):
        print("The proxy already has an active policy. Verifying " + old)
        verify(old)
        return
    if stage == "content":
        model()  # Keep the current policy serving during download, load and inference checks.
    cfg = get("configmap", "grlab-provider")["data"]
    image = proxy["spec"]["template"]["spec"]["containers"][0]["image"] if proxy else build("grlab-rails", "proxy")
    previous = None
    if proxy:
        previous = {k: proxy[k] for k in ("apiVersion", "kind", "spec")}
        previous["metadata"] = {"name": "grlab-rails", "annotations": {PROFILE: old}}
    try:
        mount_profile(stage, image, cfg)
        agent("proxy", app["spec"]["template"]["spec"]["containers"][0]["image"])
        verify(stage)
    except (subprocess.CalledProcessError, RuntimeError):
        if previous:
            print("Activation failed. Restoring the preceding proxy revision.", file=sys.stderr)
            apply(previous)
            rollout("grlab-rails")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=("showroom", "pre-1", "nemo", "rules", "content", "test"))
    parser.add_argument("--expect", choices=STAGES, help="require this active profile when running test")
    args = parser.parse_args()
    if args.expect and args.step != "test":
        parser.error("--expect applies only to the test step")
    if not NS or NS in {"default", "kube-system", "openshift"} or NS.startswith(("openshift-", "kube-")):
        parser.error("Set LAB_NAMESPACE to a dedicated application project.")
    oc("whoami")
    oc("get", "project", NS, "-o", "name")
    if args.step == "showroom":
        site()
    elif args.step == "pre-1":
        pre()
    elif args.step == "test":
        stage = active()
        if stage not in STAGES:
            raise RuntimeError("No active workshop NeMo profile.")
        if args.expect and stage != args.expect:
            raise RuntimeError(f"Expected {args.expect}, found {stage}")
        verify(stage)
    else:
        activate("bootstrap" if args.step == "nemo" else args.step)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print("Workshop stopped:", exc, file=sys.stderr)
        sys.exit(1)
