"""Verify the app's direct model connection before removing workshop-owned NeMo resources."""
import json
import re
import subprocess
from urllib.parse import urlsplit


PROXY = [
    ("deployment", "grlab-rails"),
    ("service", "grlab-rails"),
    ("networkpolicy", "grlab-rails-ingress"),
    ("buildconfig", "grlab-rails"),
]
CLASSIFIER = [
    ("deployment", "nemotron-safety-guard-vllm"),
    ("service", "nemotron-safety-guard-vllm"),
    ("networkpolicy", "grlab-content-ingress"),
]
CACHE = ("pvc", "nemotron-safety-guard-model-cache")
OWNER = "app.kubernetes.io/part-of"
SELECTOR = OWNER + "=guardrail-agent-showroom"


def owned(lab, obj):
    return obj["metadata"].get("labels", {}).get(OWNER) == lab.PART_OF[OWNER]


def targets(lab, remove_model, delete_cache):
    resources = list(PROXY)
    configmaps = json.loads(lab.oc("get", "configmaps", "-l", SELECTOR, "-o", "json"))["items"]
    for configmap in configmaps:
        name = configmap["metadata"]["name"]
        if re.fullmatch(r"grlab-(?:bootstrap|rules|content)-[0-9a-f]{12}", name):
            resources.append(("configmap", name))
    if remove_model:
        resources.extend(CLASSIFIER)
    if delete_cache:
        resources.append(CACHE)

    plan = []
    for kind, name in resources:
        obj = lab.get(kind, name)
        if obj is None:
            continue
        if not owned(lab, obj):
            raise RuntimeError(f"Ownership conflict at {kind}/{name}. No resources have been removed.")
        plan.append((kind, name))
    return plan


def swap_env(lab, index, previous, replacement):
    path = f"/spec/template/spec/containers/{index}/env"
    patch = [{"op": "test", "path": path, "value": previous}, {"op": "replace", "path": path, "value": replacement}]
    lab.oc("patch", "deployment/grlab-agent", "--type=json", "--patch-file=/dev/stdin", data=json.dumps(patch))


def agent_container(obj):
    containers = (obj or {}).get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    for index, container in enumerate(containers):
        if container["name"] == "agent":
            return index, container
    raise RuntimeError("The banking agent container is missing. Inspect the grlab-agent Deployment.")


def check_provider(lab):
    provider = lab.get("configmap", "grlab-provider")
    settings = (provider or {}).get("data", {})
    url = urlsplit(settings.get("url", ""))
    if (not settings.get("model") or url.scheme not in ("http", "https") or not url.hostname
            or url.username is not None or url.password is not None or url.query or url.fragment
            or "subscription" not in settings or not url.path.rstrip("/").endswith("/v1")
            or url.hostname.split(".")[0] == "grlab-rails"):
        raise RuntimeError("grlab-provider must identify the original main model and its direct /v1 endpoint.")
    present = lab.oc("get", "secret/grlab-main", "-o", 'go-template={{if index .data "api-key"}}present{{end}}')
    if present != "present":
        raise RuntimeError("The project Secret grlab-main needs its model API key.")


def run(lab, dry=False, remove_model=False, delete_cache=False):
    if delete_cache and not remove_model:
        raise ValueError("--delete-model-cache requires --remove-model.")
    app = lab.get("deployment", "grlab-agent")
    if not app or not owned(lab, app):
        raise RuntimeError("Cleanup requires the workshop-owned banking application in this project.")
    check_provider(lab)
    plan = targets(lab, remove_model, delete_cache)
    index, container = agent_container(app)
    print(f"Project: {lab.NS} | Switch grlab-agent to grlab-provider and grlab-main. Verify direct chat and the balance tool.")
    for kind, name in plan:
        print("  remove", f"{kind}/{name}")
    if not remove_model and lab.get("deployment", "nemotron-safety-guard-vllm"):
        print("The optional classifier stays deployed and can continue to reserve its GPU. Use --remove-model to release it.")
    if dry:
        return

    old = container["env"]
    _, template = agent_container(lab.manifests("app.yaml")[0])
    managed = {item["name"]: item for item in template["env"]}
    direct = [managed.get(item["name"], item) for item in old]
    existing = {item["name"] for item in old}
    direct.extend(item for name, item in managed.items() if name not in existing)
    if direct != old:
        swap_env(lab, index, old, direct)
    try:
        lab.rollout("grlab-agent")
        lab.verify("direct")
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        if direct != old:
            print("Direct verification failed. Restoring the previous app connection.")
            swap_env(lab, index, direct, old)
            lab.rollout("grlab-agent")
        raise

    # A failed deletion leaves an already-verified direct app. A retry will plan only the remaining objects.
    for kind, name in plan:
        _, running = agent_container(lab.get("deployment", "grlab-agent"))
        if running.get("env") != direct:
            raise RuntimeError("The app connection changed after verification. Cleanup stopped.")
        lab.oc("delete", kind, "-l", SELECTOR, "--field-selector=metadata.name=" + name,
               "--ignore-not-found", "--wait=true", "--timeout=3m", capture=False)
        if lab.get(kind, name):
            raise RuntimeError(f"{kind}/{name} remains. Inspect the object before retrying cleanup.")
    lab.verify("direct")
    print("PASS direct model chat and synthetic balance after cleanup.")
