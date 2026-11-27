"""Read-only inventory for the NeMo tracing exercise, including scoped OpenShift logins."""
import json
import re
import subprocess

from mlflow_lab import quiet_oc

APIS = (
    ("TempoStack", "tempostacks.tempo.grafana.com"),
    ("TempoMonolithic", "tempomonolithics.tempo.grafana.com"),
    ("Kiali", "kialis.kiali.io"),
    ("OpenTelemetry Collector", "opentelemetrycollectors.opentelemetry.io"),
)
DEMO_ACCESS = (
    ("create", "buildconfigs.build.openshift.io"),
    ("create", "buildconfigs.build.openshift.io", "--subresource=instantiatebinary"),
    ("delete", "buildconfigs.build.openshift.io"),
    ("create", "imagestreams.image.openshift.io"),
    ("get", "imagestreamtags.image.openshift.io"),
    ("create", "configmaps"), ("create", "deployments.apps"), ("create", "services"),
    ("create", "networkpolicies.networking.k8s.io"),
)


def read(lab, *args):
    """Only a confirmed missing API/resource is absent. Preserve permission and API failures."""
    try:
        raw = quiet_oc(lab, "get", *args, "-o", "json", "--request-timeout=8s")
        return ("found", json.loads(raw)) if raw else ("absent", None)
    except subprocess.CalledProcessError as error:
        msg = (error.stderr or "").lower()
        if "forbidden" in msg:
            return "access restricted", None
        if "doesn't have a resource type" in msg or "could not find the requested resource" in msg or "(notfound)" in msg:
            return "absent", None
        if "unauthorized" in msg:
            return "login required", None
        return "API read failed", None


def state(obj):
    status = obj.get("status", {})
    conditions = {str(c.get("type", "")).lower(): c.get("status") for c in status.get("conditions", [])}
    for key in ("ready", "available", "successful", "running"):
        if key in conditions:
            return key + "=" + str(conditions[key])
    return str(status.get("phase") or "readiness unreported")


def inventory(lab, label, kind, scope):
    crd, doc = read(lab, "crd", kind, "--ignore-not-found")
    if crd == "absent":
        print(f"  {label}: operator API absent")
        return set(), 0
    api_status = crd
    if doc:
        established = any(c.get("type") == "Established" and c.get("status") == "True"
                          for c in doc.get("status", {}).get("conditions", []))
        api_status = "established" if established else "registered, establishment unconfirmed"
    flags = ("-n", scope) if scope else ("-A",)
    result, items = read(lab, kind, *flags)
    where = scope or "all namespaces"
    if result == "access restricted" and not scope:
        result, items = read(lab, kind, "-n", lab.NS)
        where = lab.NS + " only (cluster list access restricted)"
    if result == "absent":
        print(f"  {label}: API unavailable to discovery (CRD read: {api_status})")
        return set(), 0
    if result != "found":
        print(f"  {label}: API {api_status}; instances unknown ({result}, {where})")
        return set(), 0
    records = items.get("items", [])
    print(f"  {label}: API {api_status}; {len(records)} instance(s) in {where}")
    spaces = set()
    for item in records:
        meta = item["metadata"]
        spaces.add(meta["namespace"])
        print(f"    {meta['namespace']}/{meta['name']}  {state(item)}")
    return spaces, len(records)


def labeled_pods(lab, scope):
    """Also discover common Helm or manually labelled installations without an operator CR."""
    groups = {}
    seen = set()
    restricted = False
    for selector in ("app.kubernetes.io/name in (tempo,tempo-distributed,kiali)", "app in (tempo,kiali)"):
        flags = ("-n", scope) if scope else ("-A",)
        status, doc = read(lab, "pods", "-l", selector, *flags)
        if status == "access restricted" and not scope:
            restricted = True
            status, doc = read(lab, "pods", "-l", selector, "-n", lab.NS)
        if status != "found":
            print(f"  Labelled pod discovery: {status}")
            continue
        for pod in doc.get("items", []):
            meta = pod["metadata"]
            key = (meta["namespace"], meta["name"])
            if key in seen:
                continue
            seen.add(key)
            labels = meta.get("labels", {})
            names = {labels.get("app"), labels.get("app.kubernetes.io/name")}
            name = "Kiali" if "kiali" in names else "Tempo"
            members = groups.setdefault((meta["namespace"], name), [])
            members.append(any(c.get("type") == "Ready" and c.get("status") == "True"
                               for c in pod.get("status", {}).get("conditions", [])))
    for (ns, name), members in sorted(groups.items()):
        print(f"  {name} labelled pods in {ns}: {sum(members)}/{len(members)} Ready")
    if restricted:
        print(f"  Labelled pod inventory is limited to {lab.NS} by RBAC.")
    return {ns for ns, _ in groups}


def targets(lab, namespace):
    status, obj = read(lab, "services", "-n", namespace)
    if status == "found":
        for svc in obj.get("items", []):
            for port in svc.get("spec", {}).get("ports", []):
                name = port.get("name", "").lower()
                if port.get("port") == 4317 or ("otlp" in name and "grpc" in name):
                    print(f"  gRPC Service candidate: {namespace}/{svc['metadata']['name']} port {port['port']} (verify TLS and tenant)")
    else:
        print(f"  Service discovery in {namespace}: {status}")
    status, obj = read(lab, "routes.route.openshift.io", "-n", namespace)
    if status != "found":
        print(f"  UI Route discovery in {namespace}: {status}")
        return
    for route in obj.get("items", []):
        spec, meta = route["spec"], route["metadata"]
        identity = " ".join([meta["name"], spec.get("to", {}).get("name", "")]).lower()
        if not any(term in identity for term in ("kiali", "grafana", "jaeger", "tempo", "tracing")):
            continue
        if any(term in identity for term in ("distributor", "collector", "otlp", "ingest")):
            continue
        for entry in route.get("status", {}).get("ingress", []):
            if (spec.get("tls") and entry.get("host") and
                    any(c.get("type") == "Admitted" and c.get("status") == "True" for c in entry.get("conditions", []))):
                print(f"  UI Route candidate: {namespace}/{meta['name']} https://{entry['host']}{spec.get('path') or '/'} (verify sign-in and tenant)")
                break


def inspect(lab, scope=None):
    if scope and not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", scope):
        raise ValueError("--observability-namespace takes a single OpenShift namespace.")
    print(f"NeMo tracing precheck | project {lab.NS} | read-only")
    if lab.NS == "default" or lab.NS in ("kube-system", "openshift") or lab.NS.startswith(("openshift-", "kube-")):
        print("Using the current namespace for inventory only. Set LAB_NAMESPACE to a dedicated workshop project before prepare.")
    spaces = {scope or lab.NS}
    found_tempo = 0
    for label, kind in APIS:
        found, count = inventory(lab, label, kind, scope)
        spaces.update(found)
        if label in ("TempoStack", "TempoMonolithic"):
            found_tempo += count
    spaces.update(labeled_pods(lab, scope))
    for ns in sorted(spaces):
        targets(lab, ns)
    print("Workshop project")
    for label, kind, name in (("NeMo Module 1", "deployment", "grlab-rails"), ("Demo Jaeger", "deployment", "grlab-jaeger"),
                              ("Observer image", "imagestreamtag.image.openshift.io", "grlab-rails-otel:latest"),
                              ("Backend settings", "configmap", "grlab-nemo-otel-settings")):
        status, item = read(lab, kind, name, "--ignore-not-found")
        detail = state(item) if item and kind == "deployment" else status
        if item and kind == "configmap":
            detail = item.get("data", {}).get("mode", "present")
        print(f"  {label}: {detail}")
    rejected = []
    for permission in DEMO_ACCESS:
        try:
            answer = quiet_oc(lab, "auth", "can-i", *permission, "--request-timeout=8s")
        except subprocess.CalledProcessError as err:
            answer = (err.output or "").strip() or "unknown"
        if answer != "yes":
            rejected.append(" ".join(permission) + " (" + answer + ")")
    if rejected:
        print("  Instructor demo permissions to resolve: " + ", ".join(rejected))
    else:
        print("  Instructor demo permission checks: passed")
    print("Shared path: review the discovered instances, OTLP receiver, query API and tenant." if found_tempo else
          "Shared path: no operator-managed Tempo instance confirmed in the visible scope. An independently managed backend can be supplied explicitly.")
    print("Project-local path: ./grlab otel prepare provisions private Jaeger. Tempo, Kiali and a Collector operator are optional.")
    print("A participant can use an instructor-prepared backend. A stored trace from ./grlab otel test verifies the data path.")
