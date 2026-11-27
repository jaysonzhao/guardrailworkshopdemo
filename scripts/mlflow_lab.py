"""Optional application tracing through the OpenShift AI 3.5 dashboard gateway."""
import argparse
import copy
import hashlib
import json
import re
import sys
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from cleanup import agent_container, owned
from verify import REFUSALS, SAFE

SDK_IMAGE = "grlab-agent-mlflow"
SETTINGS = "grlab-mlflow-settings"
ACCOUNT = "grlab-mlflow-writer"
BASE_IMAGE = "showroom.rhpds.io/mlflow-original-image"
REVISION = "showroom.rhpds.io/mlflow-settings"
CA = "grlab-mlflow-ca"
MLFLOWS = "mlflows.mlflow.opendatahub.io"
DSC = "datascienceclusters.datasciencecluster.opendatahub.io"
DNS = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
PAGE = "/develop-train/mlflow/experiments"


def quiet_oc(lab, *args):
    """Run a read without exposing expected scoped-RBAC denials on stderr."""
    command = ["oc"]
    explicit_scope = any(arg in ("-n", "--namespace", "-A", "--all-namespaces")
                         or arg.startswith("--namespace=") for arg in args)
    if lab.NS and not explicit_scope:
        command.extend(("-n", lab.NS))
    command.extend(args)
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
    return (result.stdout or "").strip()


def quiet_get(lab, kind, name):
    raw = quiet_oc(lab, "get", kind, name, "--ignore-not-found", "-o", "json")
    return json.loads(raw) if raw else None


def lookup(lab, kind, name, namespace, quiet=False):
    args = ("get", kind, name, "-n", namespace, "--ignore-not-found", "-o", "json")
    raw = quiet_oc(lab, *args) if quiet else lab.oc(*args)
    return json.loads(raw) if raw else None


def dashboard(lab, route=None, quiet=False):
    """Prefer the admitted OpenShift Route serving the shared AI gateway."""
    namespace, name = "openshift-ingress", "data-science-gateway"
    if route:
        if not re.fullmatch(DNS + "/" + DNS, route):
            raise ValueError("--route takes namespace/route-name")
        namespace, name = route.split("/")
    obj = lookup(lab, "route.route.openshift.io", name, namespace, quiet=quiet)
    if obj:
        spec = obj["spec"]
        if not spec.get("tls") or spec.get("path", "/") not in (None, "", "/"):
            raise RuntimeError("Select the HTTPS Route serving the root of the OpenShift AI dashboard.")
        admitted = [ing for ing in obj.get("status", {}).get("ingress", [])
                    if any(c["type"] == "Admitted" and c["status"] == "True" for c in ing.get("conditions", []))]
        if not admitted:
            raise RuntimeError(f"Route {namespace}/{name} has not been admitted. Check its status and retry.")
        hosts = {spec.get("host") or ing.get("host", "") for ing in admitted}
        source = f"Route {namespace}/{name}"
    elif route:
        raise RuntimeError(f"Dashboard Route {route} was not found.")
    else:
        gw = lookup(lab, "gateways.gateway.networking.k8s.io", name, namespace, quiet=quiet)
        hosts = {l["hostname"] for l in (gw or {}).get("spec", {}).get("listeners", [])
                 if l.get("protocol") == "HTTPS" and l.get("hostname") and "*" not in l["hostname"]}
        source = f"Gateway {namespace}/{name}"
    if len(hosts) != 1 or not re.fullmatch(DNS + r"(?:\." + DNS + r")+", next(iter(hosts), "")):
        raise RuntimeError("Cannot select a unique dashboard host. Use --route NAMESPACE/NAME for the canonical AI dashboard Route.")
    host = hosts.pop()
    if not quiet:
        print(f"Discovered {source}: https://{host}", file=sys.stderr)
    return "https://" + host


def platform(lab, args):
    items = json.loads(lab.oc("get", DSC, "-o", "json"))["items"]
    if len(items) != 1:
        raise RuntimeError("Expected one installed OpenShift AI 3.5.x DataScienceCluster.")
    cluster = items[0]
    state = cluster["spec"].get("components", {}).get("mlflowoperator", {}).get("managementState")
    if state != "Managed":
        if not args.create_demo:
            raise RuntimeError(f"MLflow Operator state: {state}. Run platform --create-demo to enable the workshop server.")
        patch = {"spec": {"components": {"mlflowoperator": {"managementState": "Managed"}}}}
        lab.oc("patch", DSC, cluster["metadata"]["name"], "--type=merge", "-p", json.dumps(patch))
    for _ in range(60 if args.create_demo else 1):
        crd = lab.get("crd", MLFLOWS)
        if crd and any(c["type"] == "Established" and c["status"] == "True" for c in crd.get("status", {}).get("conditions", [])):
            break
        if args.create_demo:
            time.sleep(3)
    else:
        raise RuntimeError("MLflow CRD is not Established. Inspect the DataScienceCluster and operator.")
    ns = ["-n", args.applications_namespace] if crd["spec"]["scope"] == "Namespaced" else []
    def instance():
        raw = lab.oc("get", MLFLOWS, "mlflow", *ns, "--ignore-not-found", "-o", "json")
        return json.loads(raw) if raw else None
    existing = instance()
    if not existing:
        if not args.create_demo:
            raise RuntimeError("No shared MLflow/mlflow exists. Use platform --create-demo for the SQLite/PVC lab server.")
        spec = {"storage": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "10Gi"}}},
                "backendStoreUri": "sqlite:////mlflow/mlflow.db", "artifactsDestination": "file:///mlflow/artifacts"}
        doc = lab.resource("mlflow.opendatahub.io/v1", "MLflow", "mlflow", spec=spec)
        if ns:
            doc["metadata"]["namespace"] = args.applications_namespace
        # create (never apply) so a concurrently created shared server cannot be overwritten.
        lab.oc("create", *ns, "-f", "-", data=json.dumps(doc))
    print("Shared MLflow/mlflow:", "found; keeping its configuration" if existing else "created with a 10 GiB workshop PVC")
    lab.oc("wait", MLFLOWS + "/mlflow", *ns, "--for=condition=Available", "--timeout=10m", capture=False)
    print("Dashboard:", dashboard(lab, args.route) + PAGE)
    print("Run prepare for the assigned project. The first traced request verifies the serving gateway and workspace.")


def grant(lab, name, verbs, subjects):
    role = lab.resource("rbac.authorization.k8s.io/v1", "Role", name,
                        rules=[{"apiGroups": ["mlflow.kubeflow.org"], "resources": ["experiments"], "verbs": verbs}])
    binding = lab.resource("rbac.authorization.k8s.io/v1", "RoleBinding", name,
                           roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": name}, subjects=subjects)
    lab.apply(role)
    lab.apply(binding)


def prepare(lab, args):
    origin = dashboard(lab, args.route)
    env = {"MLFLOW_TRACKING_URI": origin + "/mlflow", "MLFLOW_TRACKING_AUTH": "kubernetes-namespaced",
           "MLFLOW_K8S_INTEGRATION": "true",
           "MLFLOW_WORKSPACE": lab.NS, "MLFLOW_EXPERIMENT_NAME": "guardrail-banking-agent",
           "GRLAB_MLFLOW_DASHBOARD_URL": origin + PAGE + "?" + urlencode({"workspace": lab.NS}),
           "MLFLOW_HTTP_REQUEST_TIMEOUT": "8", "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "1"}
    if args.ca_configmap:
        ca = lab.get("configmap", args.ca_configmap)
        if "-----BEGIN CERTIFICATE-----" not in (ca or {}).get("data", {}).get("ca-bundle.crt", ""):
            raise RuntimeError("The gateway CA ConfigMap must contain ca-bundle.crt in the lab project.")
        env["MLFLOW_TRACKING_SERVER_CERT_PATH"] = "/etc/grlab-mlflow/ca-bundle.crt"
    assignment = (lab.get("configmap", "grlab-showroom-assignment") or {}).get("data", {})
    lab.build(SDK_IMAGE, "app", dockerfile="Dockerfile.mlflow")
    lab.apply(lab.resource("v1", "ServiceAccount", ACCOUNT, automountServiceAccountToken=False))
    grant(lab, ACCOUNT, ["get", "list", "create", "update"], [{"kind": "ServiceAccount", "name": ACCOUNT, "namespace": lab.NS}])
    viewer = args.viewer or assignment.get("user")
    if viewer:
        grant(lab, "grlab-mlflow-reader", ["get", "list"],
              [{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": viewer}])
    cm = lab.resource("v1", "ConfigMap", SETTINGS, data=env)
    if args.ca_configmap:
        cm["metadata"]["annotations"] = {"showroom.rhpds.io/mlflow-ca": args.ca_configmap}
    lab.apply(cm)
    print("Project MLflow page:", env["GRLAB_MLFLOW_DASHBOARD_URL"])


def saved_dashboard(settings, namespace):
    """Return the configured dashboard origin only when both saved URLs agree."""
    data = (settings or {}).get("data", {})
    tracking = urlsplit(data.get("MLFLOW_TRACKING_URI", ""))
    page = urlsplit(data.get("GRLAB_MLFLOW_DASHBOARD_URL", ""))
    origin = f"{tracking.scheme}://{tracking.netloc}" if tracking.netloc else ""
    if (tracking.scheme != "https" or not tracking.hostname or tracking.username or tracking.password
            or tracking.path != "/mlflow" or tracking.query or tracking.fragment
            or page.scheme != "https" or page.netloc != tracking.netloc or page.username or page.password
            or page.path != PAGE or page.fragment or parse_qs(page.query) != {"workspace": [namespace]}
            or data.get("MLFLOW_WORKSPACE") != namespace):
        return None
    return origin


def precheck(lab, args):
    """Inspect platform and project readiness without writes or broad permissions."""
    print(f"MLflow environment precheck (read-only) | Project: {lab.NS}")
    try:
        settings = quiet_get(lab, "configmap", SETTINGS)
        project_known = True
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        settings, project_known = None, False
    prepared = bool(settings and settings.get("data", {}).get("MLFLOW_TRACKING_URI")
                    and settings.get("data", {}).get("MLFLOW_WORKSPACE") == lab.NS)
    configured_origin = saved_dashboard(settings, lab.NS)

    state = None
    operator_known = False
    try:
        items = json.loads(quiet_oc(lab, "get", DSC, "-o", "json"))["items"]
        if len(items) == 1:
            cluster = items[0]
            state = cluster.get("spec", {}).get("components", {}).get("mlflowoperator", {}).get("managementState", "Removed")
            operator_known = True
            label = f"enabled (managementState={state})" if state == "Managed" else f"not enabled (managementState={state})"
            print("MLflow Operator:", label)
        else:
            print(f"MLflow Operator: expected one DataScienceCluster, found {len(items)}")
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        print("MLflow Operator: not visible to this identity (cluster-level read required)")

    crd = None
    crd_known = False
    try:
        raw = quiet_oc(lab, "get", "customresourcedefinition", MLFLOWS, "--ignore-not-found", "-o", "json")
        crd = json.loads(raw) if raw else None
        crd_known = True
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        pass
    established = bool(crd and any(c.get("type") == "Established" and c.get("status") == "True"
                                    for c in crd.get("status", {}).get("conditions", [])))
    print("MLflow API:", "CRD established" if established else
          "not installed" if crd_known and not crd else
          "not established" if crd_known else "not visible to this identity (cluster-level read required)")

    instance = None
    server_known = False
    if established:
        ns = ["-n", args.applications_namespace] if crd["spec"].get("scope") == "Namespaced" else []
        try:
            raw = quiet_oc(lab, "get", MLFLOWS, "mlflow", *ns, "--ignore-not-found", "-o", "json")
            instance = json.loads(raw) if raw else None
            server_known = True
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
            pass
        if instance:
            conditions = instance.get("status", {}).get("conditions", [])
            available = any(c.get("type") == "Available" and c.get("status") == "True" for c in conditions)
            print("Shared MLflow server:", "available" if available else "present; waiting for Available")
        elif server_known:
            print("Shared MLflow server: missing")
        else:
            print("Shared MLflow server: not visible to this identity (instructor access required)")
    else:
        print("Shared MLflow server: not checked (MLflow API is not visible)")

    origin = None
    try:
        origin = dashboard(lab, args.route, quiet=True)
        print("Dashboard ingress: live route |", origin)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        if configured_origin:
            origin = configured_origin
            print("Dashboard ingress: saved project configuration |", origin)
        elif isinstance(exc, subprocess.CalledProcessError):
            print("Dashboard ingress: not visible to this identity (instructor route access required)")
        else:
            print("Dashboard ingress: unavailable |", str(exc))

    print("Project integration:", "prepared" if prepared else "not prepared" if project_known else "unknown (project read access required)")
    try:
        app = quiet_get(lab, "deployment", "grlab-agent")
        app_known = True
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        app, app_known = None, False
    annotations = {}
    if app:
        annotations = app.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {})
        tracing_enabled = bool(annotations.get(BASE_IMAGE) and annotations.get(REVISION))
        print("Banking app tracing:", "enabled" if tracing_enabled else "disabled")
    else:
        tracing_enabled = False
        print("Banking app tracing:", "app not deployed" if app_known else "unknown (project read access required)")

    available = bool(instance and any(c.get("type") == "Available" and c.get("status") == "True"
                                      for c in instance.get("status", {}).get("conditions", [])))
    if not crd_known:
        print("Platform detail: cluster-scoped checks need the instructor's cluster read context; project settings remain available here.")
    elif crd_known and not crd:
        print("Next: ask the platform administrator to enable the MLflow Operator or select an existing shared server.")
    elif crd_known and crd and not established:
        print("Next: inspect the MLflow Operator and wait for its API to become Established, then rerun this precheck.")
    if operator_known and state != "Managed" and not available:
        print("Next: ask the platform administrator to enable MLflow or select an existing shared server.")
    if server_known and not instance:
        print("Next: ask the instructor to select a shared server; use --create-demo only on a dedicated workshop cluster.")
    elif instance and not available:
        print("Next: wait for the shared MLflow server to become Available, then rerun this precheck.")
    if project_known and not prepared:
        print("Next: run ./grlab mlflow prepare with the instructor project context.")
    if app_known and not app:
        print("Next: deploy the banking app with Pre-1, then run ./grlab mlflow enable in the participant Terminal.")
    elif app and not tracing_enabled:
        print("Next: run ./grlab mlflow enable, then ./grlab mlflow test to verify live MLflow access.")
    if prepared and origin and app and not tracing_enabled:
        if available:
            print("Tracing readiness: ready to enable")
        elif not server_known:
            print("Tracing readiness: project prepared; run mlflow test after enabling to verify current server access.")
        else:
            print("Tracing readiness: not ready; resolve the shared server status above.")
    elif prepared and origin and app and tracing_enabled:
        print("Tracing readiness: tracing is configured; run ./grlab mlflow test to verify a persisted trace.")
    else:
        print("Tracing readiness: not fully confirmed; use the checks above to find the missing prerequisite.")
    print("Precheck made no changes.")


def decorate(template, settings, image, base):
    pod = template["spec"]
    container = next(c for c in pod["containers"] if c["name"] == "agent")
    meta = template["metadata"].setdefault("annotations", {})
    meta[BASE_IMAGE] = base
    ca = settings["metadata"].get("annotations", {}).get("showroom.rhpds.io/mlflow-ca")
    signature = json.dumps([settings["data"], ca], sort_keys=True)
    meta[REVISION] = hashlib.sha256(signature.encode()).hexdigest()[:16]
    pod.update(serviceAccountName=ACCOUNT, automountServiceAccountToken=True)
    # OpenShift can return both the canonical and deprecated serviceAccount fields.
    pod.pop("serviceAccount", None)
    container["image"] = image
    refs = [v for v in container.get("envFrom", []) if v.get("configMapRef", {}).get("name") != SETTINGS]
    container["envFrom"] = refs + [{"configMapRef": {"name": SETTINGS}}]
    container["volumeMounts"] = [v for v in container.get("volumeMounts", []) if v["name"] != CA]
    pod["volumes"] = [v for v in pod.get("volumes", []) if v["name"] != CA]
    if ca:
        pod["volumes"].append({"name": CA, "configMap": {"name": ca, "items": [{"key": "ca-bundle.crt", "path": "ca-bundle.crt"}]}})
        container["volumeMounts"].append({"name": CA, "mountPath": "/etc/grlab-mlflow", "readOnly": True})


def required_settings(lab):
    settings = lab.get("configmap", SETTINGS)
    if not settings:
        raise RuntimeError("The instructor must first run ./grlab mlflow prepare in this project.")
    return settings


def preserve(lab, deployment, current):
    old = (current or {}).get("spec", {}).get("template", {})
    base = old.get("metadata", {}).get("annotations", {}).get(BASE_IMAGE)
    if base:
        _, agent = agent_container(current)
        decorate(deployment["spec"]["template"], required_settings(lab), agent["image"], base)


def switch(lab, enable):
    dep = lab.get("deployment", "grlab-agent")
    if not dep or not owned(lab, dep):
        raise RuntimeError("Deploy the banking application with Pre-1 first.")
    original = dep["spec"]["template"]
    template = copy.deepcopy(original)
    pod = template["spec"]
    container = next(c for c in pod["containers"] if c["name"] == "agent")
    ann = template["metadata"].setdefault("annotations", {})
    base = ann.get(BASE_IMAGE)
    if enable:
        if not base and (pod.get("serviceAccountName", "default") != "default" or pod.get("automountServiceAccountToken") is not False):
            raise RuntimeError("Expected the workshop's default, token-free agent identity before enabling MLflow.")
        decorate(template, required_settings(lab), lab.built_image(SDK_IMAGE), base or container["image"])
    elif base:
        container["image"] = ann.pop(BASE_IMAGE)
        ann.pop(REVISION, None)
        pod.update(serviceAccountName="default", automountServiceAccountToken=False)
        pod.pop("serviceAccount", None)
        container["envFrom"] = [v for v in container.get("envFrom", []) if v.get("configMapRef", {}).get("name") != SETTINGS]
        container["volumeMounts"] = [v for v in container.get("volumeMounts", []) if v["name"] != CA]
        pod["volumes"] = [v for v in pod.get("volumes", []) if v["name"] != CA]
    if original != template:
        patch = [{"op": "test", "path": "/spec/template", "value": original},
                 {"op": "replace", "path": "/spec/template", "value": template}]
        lab.oc("patch", "deployment/grlab-agent", "--type=json", "--patch-file=/dev/stdin", data=json.dumps(patch))
    lab.rollout("grlab-agent")
    print("Tracing requested. Run ./grlab mlflow test in the Terminal." if enable else
          "App tracing disabled. Existing experiment, runs and traces remain in MLflow.")


def smoke(agent):
    chat = Request(agent + "/chat/completions", data=json.dumps({"messages": [{"role": "user", "content": SAFE}], "stream": False}).encode(),
                   headers={"Content-Type": "application/json"})
    with urlopen(chat, timeout=120) as response:
        trace_id = response.headers.get("X-Grlab-Trace-Id", "")
        reply = json.load(response)["choices"][0]["message"]["content"]
    if not reply or any(msg in reply for msg in REFUSALS):
        raise RuntimeError("The synthetic password-reset question failed or was refused. Check the app and active rules.")
    if not re.fullmatch(r"tr-[a-f0-9]{32}", trace_id):
        raise RuntimeError("Chat returned no MLflow trace ID. Check mlflow enable, workspace RBAC, TLS and the agent startup log.")
    evidence = Request(agent + "/workshop/mlflow/evidence/" + trace_id, data=b"", method="POST")
    for attempt in range(20):
        try:
            with urlopen(evidence, timeout=45) as response:
                result = json.load(response)
            break
        except HTTPError as exc:
            with exc:
                if exc.code == 404 and attempt < 19:
                    time.sleep(2)
                    continue
                raise RuntimeError(f"MLflow readback HTTP {exc.code}: " + exc.read(500).decode(errors="replace")) from None
    if result["trace_id"] != trace_id or not result.get("run_id"):
        raise RuntimeError("MLflow evidence does not match this chat request.")
    print(f"PASS {result['backend']} | trace {trace_id} | experiment {result['experiment_id']} | run {result['run_id']}")
    print(f"Request {result['metrics']['request_latency_ms']} ms | {result['metrics']['span_count']} spans: " + ", ".join(result["spans"]))
    print("Open this run in OpenShift AI:", result["url"])
    return result


def run(lab, argv):
    parser = argparse.ArgumentParser(prog="grlab mlflow", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    pre = sub.add_parser("precheck", help="read-only check of platform, dashboard and project readiness")
    pre.add_argument("--route", metavar="NAMESPACE/NAME", help="inspect this dashboard Route")
    pre.add_argument("--applications-namespace", default="redhat-ods-applications")
    platform_cmd = sub.add_parser("platform", help="instructor: inspect the MLflow server or opt into the demo installation")
    platform_cmd.add_argument("--create-demo", action="store_true", help="enable the operator and create a missing SQLite/PVC server")
    platform_cmd.add_argument("--applications-namespace", default="redhat-ods-applications")
    prep = sub.add_parser("prepare", help="instructor: resolve the Route, build the SDK image and grant project access")
    for cmd in (platform_cmd, prep):
        cmd.add_argument("--route", metavar="NAMESPACE/NAME", help="use this admitted canonical OpenShift AI dashboard Route")
    prep.add_argument("--viewer", help="grant dashboard experiment read access to this user (default: assigned Showroom user)")
    prep.add_argument("--ca-configmap", help="lab project ConfigMap with the dashboard gateway CA in ca-bundle.crt")
    sub.add_parser("enable", help="enable MLflow on the current banking deployment")
    test = sub.add_parser("test", help="send a chat, read back its MLflow trace and log dashboard run metrics")
    test.add_argument("--agent", default="http://grlab-agent:8080", help="agent Service or a local port-forward URL")
    link = sub.add_parser("link", help="print the saved project link or discover the dashboard Route")
    link.add_argument("--route", metavar="NAMESPACE/NAME", help="read a specific dashboard Route even if settings already exist")
    sub.add_parser("disable", help="restore the original app image and identity")
    args = parser.parse_args(argv)
    lab.check_target()
    if args.action == "precheck":
        precheck(lab, args)
    elif args.action == "platform":
        platform(lab, args)
    elif args.action == "prepare":
        prepare(lab, args)
    elif args.action in ("enable", "disable"):
        switch(lab, args.action == "enable")
    elif args.action == "test":
        smoke(args.agent.rstrip("/"))
    else:
        settings = lab.get("configmap", SETTINGS) if not args.route else None
        if settings:
            print(settings["data"]["GRLAB_MLFLOW_DASHBOARD_URL"])
        else:
            print(dashboard(lab, args.route) + PAGE + "?" + urlencode({"workspace": lab.NS}))
