"""Provision the single-user Showroom and verify its public authentication boundary."""
from base64 import urlsafe_b64encode
import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler
from zipfile import ZipFile


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_url(value, setting):
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or any(c.isspace() for c in value)):
        raise RuntimeError(f"{setting} needs an HTTPS URL without embedded credentials, query or fragment.")
    return value.rstrip("/")


def images(oc):
    values = {name: os.getenv(env, "") for name, env in
              (("cli", "SHOWROOM_CLI_IMAGE"), ("oauth-proxy", "SHOWROOM_OAUTH_IMAGE"))}
    if all(values.values()):
        return values
    try:
        release = oc("get", "clusterversion/version", "-o", "jsonpath={.status.desired.image}")
        registry = os.getenv("RELEASE_REGISTRY_CONFIG")
        for name in values:
            if not values[name]:
                args = ["adm", "release", "info", release, "--image-for=" + name]
                if registry:
                    args += ["--registry-config=" + registry]
                values[name] = oc(*args)
                if not values[name]:
                    raise RuntimeError(f"Release component {name} is missing")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Set SHOWROOM_CLI_IMAGE and SHOWROOM_OAUTH_IMAGE to accessible cluster-release pullspecs, or supply RELEASE_REGISTRY_CONFIG for the release lookup.") from exc
    return values


def check_bundle(path):
    with ZipFile(path) as archive:
        cmd = archive.getinfo("guardrail-agent-lab/grlab")
        if archive.testzip() or not ((cmd.external_attr >> 16) & 0o111):
            raise RuntimeError("Rebuild the participant ZIP with scripts/build-site.sh.")


def check_route(origin, namespace, endpoint):
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=os.getenv("SHOWROOM_ROUTE_CA"))))
    expected_id = f"system:serviceaccount:{namespace}:grlab-showroom"
    paths = ("/", "/modules/en/_attachments/lab-files.zip", "/terminal/", "/terminal/ws", "/playground/")
    for path in paths:
        for attempt in range(15):
            try:
                with opener.open(origin + path, timeout=6):
                    raise RuntimeError(f"Anonymous access unexpectedly succeeded at {path}")
            except HTTPError as err:
                with err:
                    location = urlsplit(err.headers.get("Location", ""))
                    query = parse_qs(location.query)
                    if (err.code in (302, 303, 307) and location._replace(query="", fragment="").geturl() == endpoint
                            and query.get("client_id") == [expected_id]
                            and query.get("redirect_uri") == [origin + "/oauth/callback"]):
                        break
                    if err.code not in (502, 503, 504):
                        raise RuntimeError(f"Unexpected authentication response at {path} (HTTP {err.code}).")
            except (URLError, TimeoutError) as err:
                if attempt == 14:
                    raise RuntimeError(f"Cannot verify the HTTPS route at {path}. Check DNS, router and SHOWROOM_ROUTE_CA.") from err
            if attempt == 14:
                raise RuntimeError("The authenticated Showroom route did not become ready.")
            time.sleep(2)


def claim_owner(lab, owner, assignment):
    if not assignment:
        record = lab.resource("v1", "ConfigMap", "grlab-showroom-assignment", immutable=True, data={"user": owner})
        record["metadata"]["labels"] = lab.PART_OF
        try:
            lab.oc("create", "-f", "-", data=json.dumps(record))
            return
        except subprocess.CalledProcessError:
            assignment = lab.get("configmap", "grlab-showroom-assignment")
            if not assignment:
                raise
    if assignment["data"].get("user") != owner:
        raise RuntimeError("This Showroom belongs to another user. Use a separate project for this participant.")


def deploy(lab):
    www = lab.ROOT / "showroom/www"
    if not (www / "modules/en/index.html").is_file():
        raise RuntimeError("Run showroom from the full repository with the rendered guide.")
    bundle = www / "modules/en/_attachments/lab-files.zip"
    check_bundle(bundle)
    prev = lab.get("configmap", "grlab-showroom-settings")
    existing = prev["data"] if prev else {}
    assignment = lab.get("configmap", "grlab-showroom-assignment")
    assigned = assignment["data"].get("user") if assignment else None
    owner = os.getenv("SHOWROOM_USER") or assigned or existing.get("SHOWROOM_USER") or lab.oc("whoami")
    if not re.fullmatch(r"[^\s,:/#\"\x00-\x1f\x7f]+", owner) or owner.startswith("system:"):
        raise RuntimeError("Set SHOWROOM_USER to the participant's actual interactive OpenShift username.")
    if existing.get("SHOWROOM_USER", owner) != owner:
        raise RuntimeError("This Showroom belongs to another user. Use a separate project for this participant.")
    console = https_url(os.getenv("SHOWROOM_CONSOLE_URL") or existing.get("SHOWROOM_CONSOLE_URL") or lab.oc("whoami", "--show-console"), "SHOWROOM_CONSOLE_URL")
    embed = os.getenv("SHOWROOM_CONSOLE_EMBED_URL", existing.get("SHOWROOM_CONSOLE_EMBED_URL", ""))
    if embed:
        embed = https_url(embed, "SHOWROOM_CONSOLE_EMBED_URL")
    discovery = json.loads(lab.oc("get", "--raw", "/.well-known/oauth-authorization-server"))
    endpoint = https_url(discovery.get("authorization_endpoint", ""), "Cluster OAuth authorization endpoint")
    components = images(lab.oc)
    claim_owner(lab, owner, assignment)
    image = lab.build("grlab-showroom", "showroom")
    docs = {d["kind"]: d for d in lab.manifests("showroom.yaml")}
    for kind in ("NetworkPolicy", "ServiceAccount", "Secret", "Service", "Route"):
        lab.apply(docs[kind])
    host = lab.oc("get", "route/grlab-showroom", "-o", "jsonpath={.spec.host}")
    origin = https_url("https://" + host, "Showroom route")
    settings = {"SHOWROOM_USER": owner, "LAB_NAMESPACE": lab.NS, "SHOWROOM_ORIGIN": origin,
                "SHOWROOM_CONSOLE_URL": console, "SHOWROOM_CONSOLE_EMBED_URL": embed}
    lab.apply(lab.resource("v1", "ConfigMap", "grlab-showroom-settings", data=settings))
    if not lab.oc("get", "secret/grlab-showroom-cookie", "--ignore-not-found", "-o", "name"):
        # 'create' is atomic. Never overwrite another installer's encryption key on a retry.
        try:
            lab.oc("create", "secret", "generic", "grlab-showroom-cookie", "--from-file=key=/dev/stdin",
                   data=urlsafe_b64encode(secrets.token_bytes(32)).decode())
        except subprocess.CalledProcessError:
            if not lab.oc("get", "secret/grlab-showroom-cookie", "--ignore-not-found", "-o", "name"):
                raise
    dep = docs["Deployment"]
    template = dep["spec"]["template"]
    template["metadata"]["annotations"] = {"showroom.rhpds.io/settings": hashlib.sha256(
        json.dumps(settings, sort_keys=True).encode()).hexdigest()[:12]}
    refs = {"SITE_IMAGE": image, "CLI_IMAGE": components["cli"], "OAUTH_IMAGE": components["oauth-proxy"]}
    for container in template["spec"]["initContainers"] + template["spec"]["containers"]:
        container["image"] = refs[container["image"]]
    lab.apply(dep)
    lab.rollout("grlab-showroom")
    lab.oc("exec", "deployment/grlab-showroom", "-c", "terminal", "--", "/bin/bash", "-ec",
           "cd /workspace/lab && test -x grlab && ./grlab --help && /opt/oc/oc version --client && test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token",
           capture=False)
    print(f"Showroom for {owner}: {origin}", flush=True)
    check_route(origin, lab.NS, endpoint)
    print("PASS anonymous requests redirect to cluster OAuth. Sign in and compare Terminal 'oc whoami' with the console user.")
