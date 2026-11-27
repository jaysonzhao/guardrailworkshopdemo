"""Optional OpenTelemetry tracing for the workshop's existing NeMo Deployment."""
import argparse
import copy
import hashlib
import json
import re
import subprocess
from urllib.parse import urlsplit

import yaml

from catalog_cli import current, selected
from cleanup import owned
from policy import STAGES

SETTINGS = "grlab-nemo-otel-settings"
IMAGE = "grlab-rails-otel"
BASE = "showroom.rhpds.io/nemo-otel-base-image"
CAPTURE = "showroom.rhpds.io/nemo-otel-capture"
CA_VOLUME = "grlab-nemo-otel-ca"
SERVICE = "nemo-guardrails"
DEMO_UI = "/nemo-traces"
DEMO_QUERY = "http://grlab-jaeger:16686" + DEMO_UI
DEMO_ENDPOINT = "http://grlab-jaeger:4317"
MANAGED = {
    "OTEL_SERVICE_NAME", "OTEL_RESOURCE_ATTRIBUTES", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_METRICS_EXPORTER", "OTEL_LOGS_EXPORTER", "OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_CERTIFICATE",
    "GRLAB_OTEL_CAPTURE_CONTENT", "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
}


def url(value, *, receiver=False):
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or any(c.isspace() for c in value)
            or "\\" in value or (receiver and parts.path not in ("", "/"))):
        raise ValueError("Use a credential-free http(s) URL. A gRPC receiver takes a host and port without a path.")
    _ = parts.port  # Validate the port if provided.
    return value.rstrip("/")


def dashboard_route(lab, ref, path):
    labels = ref.split("/")
    dns_label = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    if len(labels) != 2 or any(len(label) > 63 or not re.fullmatch(dns_label, label) for label in labels):
        raise ValueError("--ui-route takes NAMESPACE/NAME.")
    ns, name = labels
    route = json.loads(lab.oc("get", "route.route.openshift.io", name, "-n", ns, "-o", "json"))
    hosts = {i.get("host") for i in route.get("status", {}).get("ingress", [])
             if i.get("host") and any(c.get("type") == "Admitted" and c.get("status") == "True" for c in i.get("conditions", []))}
    host = route["spec"].get("host") or (next(iter(hosts)) if len(hosts) == 1 else None)
    if not route["spec"].get("tls") or host not in hosts:
        raise RuntimeError("The tracing UI Route must have TLS and an admitted hostname.")
    base = path if path is not None else route["spec"].get("path", "/")
    if not re.fullmatch(r"/[a-zA-Z0-9_./~-]*", base) or "//" in base or ".." in base.split("/"):
        raise ValueError("--ui-path must be an absolute path on the UI Route.")
    return url("https://" + host + base)


def resources(lab):
    return lab.manifests("nemo-tracing.yaml")


def prepare(lab, args):
    if not args.endpoint and any((args.query_url, args.query_api, args.ui_route, args.ui_path,
                                  args.ca_configmap, args.headers_secret)):
        raise ValueError("External backend options require --endpoint.")
    if args.ui_path and not args.ui_route:
        raise ValueError("--ui-path requires --ui-route.")
    if args.endpoint and not args.query_url:
        raise ValueError("Supply --query-url for the Jaeger Query or Tempo HTTP API so the lab can verify exported traces.")
    external = bool(args.endpoint)
    receiver = url(args.endpoint, receiver=True) if external else DEMO_ENDPOINT
    query = url(args.query_url) if external else DEMO_QUERY
    if args.headers_secret and not receiver.startswith("https://"):
        raise ValueError("An exporter headers Secret requires an HTTPS receiver.")
    if args.ca_configmap:
        cm = lab.get("configmap", args.ca_configmap)
        if "-----BEGIN CERTIFICATE-----" not in (cm or {}).get("data", {}).get("ca.crt", ""):
            raise ValueError("The receiver CA ConfigMap in the lab project needs a PEM ca.crt key.")
    if args.headers_secret:
        present = lab.oc("get", "secret/" + args.headers_secret,
                         "-o", 'go-template={{if index .data "headers"}}present{{end}}')
        if present != "present":
            raise ValueError("The OTLP auth Secret in the project needs a nonempty headers key.")
    ui = dashboard_route(lab, args.ui_route, args.ui_path) if args.ui_route else ""
    mode = "external" if external else "demo"
    values = {"mode": mode, "endpoint": receiver, "query": query, "api": args.query_api or "jaeger",
              "ui": ui, "ca": args.ca_configmap or "", "headers": args.headers_secret or ""}
    docs = [] if external else resources(lab)
    for obj in docs:
        prior = lab.get(obj["kind"].lower(), obj["metadata"]["name"])
        if prior and not owned(lab, prior):
            raise RuntimeError("Existing non-workshop resource: " + obj["kind"] + "/" + obj["metadata"]["name"])
    prior = lab.get("configmap", SETTINGS)
    if prior and not owned(lab, prior):
        raise RuntimeError("An unrelated ConfigMap already uses the Module 7 settings name.")
    lab.build(IMAGE, "proxy", dockerfile="Dockerfile.otel")
    for doc in docs:
        lab.apply(doc)
    if docs:
        lab.rollout("grlab-jaeger")
    settings_map = lab.resource("v1", "ConfigMap", SETTINGS, data=values)
    settings_map["metadata"]["labels"] = {"app.kubernetes.io/component": "nemo-tracing-" + mode}
    lab.apply(settings_map)
    print(f"Prepared {mode} backend for {lab.NS}. Use ./grlab otel enable on the active NeMo deployment.")


def settings(lab):
    cfg = lab.get("configmap", SETTINGS)
    if not cfg or not owned(lab, cfg):
        raise RuntimeError("The instructor must first run ./grlab otel prepare in this project.")
    return cfg["data"]


def policy(data, capture):
    cfg = yaml.safe_load(data["config.yaml"])
    if capture is None:
        cfg.pop("tracing", None)
    else:
        cfg["tracing"] = {"enabled": True, "span_format": "opentelemetry", "enable_content_capture": capture,
                          "adapters": [{"name": "OpenTelemetry"}]}
    return {**data, "config.yaml": yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)}


def annotations(dep):
    return (dep or {}).get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {})


def nemo(pod):
    return next(c for c in pod["containers"] if c["name"] == "nemo")


def keep_tracing(data, old_dep):
    ann = annotations(old_dep)
    return policy(data, ann[CAPTURE] == "true") if BASE in ann else data


def remove_wiring(template):
    pod = template["spec"]
    container = nemo(pod)
    container["env"] = [v for v in container.get("env", []) if v["name"] not in MANAGED]
    container["volumeMounts"] = [v for v in container.get("volumeMounts", []) if v["name"] != CA_VOLUME]
    pod["volumes"] = [v for v in pod["volumes"] if v["name"] != CA_VOLUME]


def wire(template, cfg, image, base_image, capture, namespace):
    remove_wiring(template)
    pod = template["spec"]
    container = nemo(pod)
    container["image"] = image
    mark = template["metadata"].setdefault("annotations", {})
    mark[BASE], mark[CAPTURE] = base_image, str(capture).lower()
    env = {"OTEL_SERVICE_NAME": SERVICE, "OTEL_RESOURCE_ATTRIBUTES": "k8s.namespace.name=" + namespace,
           "OTEL_EXPORTER_OTLP_ENDPOINT": cfg["endpoint"], "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
           "OTEL_METRICS_EXPORTER": "none", "OTEL_LOGS_EXPORTER": "none", "GRLAB_OTEL_CAPTURE_CONTENT": mark[CAPTURE],
           "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": mark[CAPTURE]}
    if cfg.get("ca"):
        pod["volumes"].append({"name": CA_VOLUME, "configMap": {"name": cfg["ca"], "items": [{"key": "ca.crt", "path": "ca.crt"}]}})
        container["volumeMounts"].append({"name": CA_VOLUME, "mountPath": "/etc/grlab-otel", "readOnly": True})
        env["OTEL_EXPORTER_OTLP_CERTIFICATE"] = "/etc/grlab-otel/ca.crt"
    container["env"].extend({"name": k, "value": v} for k, v in env.items())
    if cfg.get("headers"):
        container["env"].append({"name": "OTEL_EXPORTER_OTLP_HEADERS", "valueFrom": {
            "secretKeyRef": {"name": cfg["headers"], "key": "headers"}}})


def preserve_template(dep, old):
    mark = annotations(old)
    if BASE not in mark:
        return
    new_tpl = dep["spec"]["template"]
    prev = old["spec"]["template"]["spec"]
    pod = new_tpl["spec"]
    new_tpl["metadata"].setdefault("annotations", {}).update({BASE: mark[BASE], CAPTURE: mark[CAPTURE]})
    container, before = nemo(pod), nemo(prev)
    container["image"] = before["image"]
    container["env"].extend(v for v in before["env"] if v["name"] in MANAGED)
    container["volumeMounts"].extend(v for v in before["volumeMounts"] if v["name"] == CA_VOLUME)
    pod["volumes"].extend(v for v in prev["volumes"] if v["name"] == CA_VOLUME)


def swap(lab, before, after):
    patch = [{"op": "test", "path": "/spec/template", "value": before},
             {"op": "replace", "path": "/spec/template", "value": after}]
    reply = lab.oc("patch", "deployment/grlab-rails", "--type=json", "--patch-file=/dev/stdin", "-o", "json",
                   data=json.dumps(patch))
    return json.loads(reply)["spec"]["template"]


def live_proxy(lab):
    dep = lab.get("deployment", "grlab-rails")
    if not dep or not owned(lab, dep) or dep["metadata"].get("annotations", {}).get(lab.PROFILE) not in STAGES:
        raise RuntimeError("Deploy Module 1 first. After Module 5, run ./grlab nemo again to restore the proxy.")
    return dep


def switch(lab, enable, capture=False):
    dep = live_proxy(lab)
    original = dep["spec"]["template"]
    tpl = copy.deepcopy(original)
    ann = annotations(dep)
    if not enable and BASE not in ann:
        print("NeMo tracing is already disabled.")
        return
    _, old_data = current(lab, dep)
    if enable and BASE not in ann and ("tracing" in yaml.safe_load(old_data["config.yaml"])
            or any(v["name"] in MANAGED for v in nemo(original["spec"]).get("env", []))):
        raise RuntimeError("The proxy has independent tracing settings. Review that configuration before using Module 7.")
    updated = policy(old_data, capture if enable else None)
    pod = tpl["spec"]
    container = nemo(pod)
    if enable:
        cfg = settings(lab)
        wire(tpl, cfg, lab.built_image(IMAGE), ann.get(BASE, container["image"]), capture, lab.NS)
    else:
        remove_wiring(tpl)
        container["image"] = ann[BASE]
        for k in (BASE, CAPTURE):
            tpl["metadata"]["annotations"].pop(k)
    profile = dep["metadata"]["annotations"][lab.PROFILE]
    digest = hashlib.sha256(json.dumps(updated, sort_keys=True).encode()).hexdigest()[:12]
    name = f"grlab-{profile}-{digest}"
    next(v for v in pod["volumes"] if v["name"] == "policy")["configMap"]["name"] = name
    if tpl != original:
        lab.apply(lab.resource("v1", "ConfigMap", name, data=updated, immutable=True))
        live = swap(lab, original, tpl)
        try:
            lab.rollout("grlab-rails")
        except (OSError, RuntimeError, subprocess.CalledProcessError):
            swap(lab, live, original)
            lab.rollout("grlab-rails")
            raise
    print("NeMo tracing enabled. Verify with ./grlab otel test." if enable else "NeMo tracing disabled. The current rules are still mounted.")


def demo_url(lab):
    try:
        showroom = lab.get("route", "grlab-showroom")
    except subprocess.CalledProcessError:
        # The instructor recorded this origin when publishing the portal Route.
        cm = lab.get("configmap", "grlab-showroom-settings") or {}
        origin = cm.get("data", {}).get("SHOWROOM_ORIGIN", "")
        if origin.startswith("https://"):
            return url(origin) + DEMO_UI
        raise
    if showroom and showroom["spec"].get("tls"):
        for ingress in showroom.get("status", {}).get("ingress", []):
            if ingress.get("host") and any(c.get("type") == "Admitted" and c.get("status") == "True"
                                           for c in ingress.get("conditions", [])):
                return "https://" + ingress["host"] + DEMO_UI
    return ""


def link(lab, cfg):
    return demo_url(lab) if cfg["mode"] == "demo" else cfg.get("ui", "")


def remove_demo(lab):
    proxy = lab.get("deployment", "grlab-rails")
    if proxy and BASE in annotations(proxy):
        env = nemo(proxy["spec"]["template"]["spec"])["env"]
        if any(v["name"] == "OTEL_EXPORTER_OTLP_ENDPOINT" and v.get("value") == DEMO_ENDPOINT for v in env):
            raise RuntimeError("Disable tracing or switch the active proxy to a shared receiver before removing the demo backend.")
    demo = resources(lab)
    for obj in demo:
        kind, name = obj["kind"].lower(), obj["metadata"]["name"]
        existing = lab.get(kind, name)
        if existing and not owned(lab, existing):
            raise RuntimeError(f"Ownership conflict at {kind}/{name}.")
    for obj in reversed(demo):
        kind, name = obj["kind"].lower(), obj["metadata"]["name"]
        lab.oc("delete", kind, "-l", "app.kubernetes.io/part-of=guardrail-agent-showroom",
               "--field-selector=metadata.name=" + name, "--ignore-not-found", capture=False)
    cfg = lab.get("configmap", SETTINGS)
    if cfg and owned(lab, cfg) and cfg["data"].get("mode") == "demo":
        lab.oc("delete", "configmap", "-l", "app.kubernetes.io/part-of=guardrail-agent-showroom,app.kubernetes.io/component=nemo-tracing-demo",
               "--field-selector=metadata.name=" + SETTINGS, "--ignore-not-found", capture=False)
    print("Removed the project's in-memory Jaeger demo. Its stored traces are discarded.")


def run(lab, argv):
    parser = argparse.ArgumentParser(prog="grlab otel", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("precheck", help="read-only survey of Tempo, Kiali, collector, Routes and workshop readiness")
    prep = sub.add_parser("prepare", help="instructor: build the optional proxy image and prepare Jaeger or an existing backend")
    for command in (check, prep):
        command.add_argument("--observability-namespace", metavar="NAMESPACE", help="restrict shared tracing discovery to this project")
    prep.add_argument("--endpoint", help="existing OTLP/gRPC collector or Tempo distributor URL")
    prep.add_argument("--query-url", help="HTTP base URL of the existing Jaeger Query or Tempo API")
    prep.add_argument("--query-api", choices=["jaeger","tempo"], help="Jaeger v3 or Tempo HTTP (default: jaeger)")
    prep.add_argument("--ui-route", metavar="NAMESPACE/NAME", help="existing authenticated tracing UI Route")
    prep.add_argument("--ui-path", help="custom browser path on the UI Route")
    prep.add_argument("--ca-configmap", help="project ConfigMap containing the receiver's ca.crt")
    prep.add_argument("--headers-secret", help="project Secret with a headers key for OTLP/gRPC authentication")
    on = sub.add_parser("enable", help="instrument the existing NeMo Deployment and retain the active rules")
    on.add_argument("--capture-content", action="store_true", help="include messages in traces; use fictional lab data only")
    test = sub.add_parser("test", help="send test requests and read back their exact trace IDs from storage")
    test.add_argument("--proxy", default="http://grlab-rails/v1", help="NeMo base URL, including /v1")
    test.add_argument("--query-url", help="override query URL, for example a local port-forward")
    test.add_argument("--query-ca-file", help="PEM CA for HTTPS backend queries")
    test.add_argument("--query-token-file", help="private bearer-token file for an HTTPS query endpoint")
    test.add_argument("--tenant", help="Tempo tenant for HTTP trace lookup (X-Scope-OrgID)")
    sub.add_parser("link", help="print the tracing UI address")
    sub.add_parser("status", help="report the proxy's tracing state")
    sub.add_parser("disable", help="restore the regular proxy image and keep the current banking policy")
    sub.add_parser("remove-demo", help="instructor: remove the project-local in-memory backend and its traces")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "precheck":
            lab.oc("whoami")
            if not lab.NS:
                # A kubeconfig context without an explicit project uses default.
                lab.NS = lab.oc("config", "view", "--minify", "-o", "jsonpath={.contexts[0].context.namespace}") or "default"
            if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", lab.NS):
                raise ValueError("Select one OpenShift namespace for the read-only inventory.")
        else:
            lab.check_target()
    except FileNotFoundError as exc:
        raise RuntimeError("The OpenShift CLI (oc) and a logged-in cluster context are required for the cluster precheck and deploy commands.") from exc
    if args.cmd in ("precheck", "prepare"):
        from nemo_precheck import inspect
        inspect(lab, args.observability_namespace)
        if args.cmd == "prepare":
            prepare(lab, args)
    elif args.cmd in ("enable", "disable"):
        switch(lab, args.cmd == "enable", getattr(args, "capture_content", False))
    elif args.cmd == "remove-demo":
        remove_demo(lab)
    elif args.cmd == "status":
        dep = lab.get("deployment", "grlab-rails")
        ann = annotations(dep)
        print("Proxy:", "present" if dep else "absent", "| tracing:", "enabled" if BASE in ann else "off",
              "| capture:", ann.get(CAPTURE, "false"), "| project:", lab.NS)
    else:
        cfg = settings(lab)
        ui = link(lab, cfg)
        if args.cmd == "link":
            if ui:
                print(ui.rstrip("/") + "/")
            elif cfg["mode"] == "demo":
                print(f"oc -n {lab.NS} port-forward svc/grlab-jaeger 16686:16686\nhttp://localhost:16686/nemo-traces/")
            else:
                print("Use the tracing backend's authenticated UI. Supply --ui-route during prepare to publish its link.")
            return
        dep = live_proxy(lab)
        if BASE not in annotations(dep):
            raise RuntimeError("Run ./grlab otel enable first.")
        from nemo_trace_probe import exercise
        profile = dep["metadata"]["annotations"][lab.PROFILE]
        _, data = current(lab, dep)
        config = yaml.safe_load(data["config.yaml"])
        model = next(m["model"] for m in config["models"] if m.get("type") == "main")
        active = selected(data)
        credential = profile in ("rules", "content") and (active is None or "credential" in active)
        query = url(args.query_url or cfg["query"])
        local_demo = cfg["mode"] == "demo"
        if local_demo and not ui and urlsplit(query).hostname in ("localhost", "127.0.0.1", "::1"):
            ui = query
        exercise(url(args.proxy), model, profile, credential, query, cfg["api"], ui,
                 args.query_ca_file, args.query_token_file, args.tenant, annotations(dep)[CAPTURE] == "true", local_demo)
