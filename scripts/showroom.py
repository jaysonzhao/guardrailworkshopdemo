"""Publish a single-participant portal with OpenShift OAuth and an authenticated terminal."""
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
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or any(c.isspace() for c in value)):
        raise RuntimeError(f"{setting} needs an HTTPS URL without credentials, query or fragment.")
    return value.rstrip("/")

def images(oc):
    images = {n:os.getenv(k, "") for n,k in (("cli","SHOWROOM_CLI_IMAGE"),("oauth-proxy","SHOWROOM_OAUTH_IMAGE"))}
    if all(images.values()):
        return images
    try:
        release = oc("get","clusterversion/version","-o","jsonpath={.status.desired.image}")
        for name in images:
            if not images[name]:
                args = ["adm","release","info",release,"--image-for="+name]
                if os.getenv("RELEASE_REGISTRY_CONFIG"):
                    args += ["--registry-config="+os.environ["RELEASE_REGISTRY_CONFIG"]]
                images[name] = oc(*args)
                if not images[name]:
                    raise RuntimeError("Missing release component " + name)
    except subprocess.CalledProcessError as e:
        raise RuntimeError("Set SHOWROOM_CLI_IMAGE and SHOWROOM_OAUTH_IMAGE to accessible release component pullspecs, or provide RELEASE_REGISTRY_CONFIG.") from e
    return images

def check_route(origin, namespace, endpoint):
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=os.getenv("SHOWROOM_ROUTE_CA"))))
    for path in ("/", "/modules/en/_attachments/lab-files.zip", "/terminal/", "/terminal/ws", "/playground/", "/mlflow"):
        for attempt in range(15):
            try:
                with opener.open(origin+path, timeout=6):
                    raise RuntimeError("Unexpected anonymous access to " + path)
            except HTTPError as e:
                with e:
                    target = urlsplit(e.headers.get("Location",""))
                    q = parse_qs(target.query)
                    if (e.code in (302,303,307) and target._replace(query="",fragment="").geturl() == endpoint
                        and q.get("client_id") == [f"system:serviceaccount:{namespace}:grlab-showroom"]
                        and q.get("redirect_uri") == [origin+"/oauth/callback"]):
                        break
                    if e.code not in (502,503,504):
                        raise RuntimeError(f"Unexpected authentication response at {path}: HTTP {e.code}")
            except (TimeoutError, URLError) as e:
                if attempt==14:
                    raise RuntimeError("Check Route DNS, ingress and SHOWROOM_ROUTE_CA.") from e
            if attempt == 14:
                raise RuntimeError("Authenticated Showroom Route did not become ready.")
            time.sleep(2)

def claim_owner(lab, owner, existing):
    if not existing:
        record=lab.resource("v1","ConfigMap","grlab-showroom-assignment",immutable=True,data={"user":owner})
        record["metadata"]["labels"]=lab.PART_OF
        try:
            lab.oc("create","-f","-",data=json.dumps(record))
            return
        except subprocess.CalledProcessError:
            existing=lab.get("configmap","grlab-showroom-assignment")
            if not existing:
                raise
    if existing["data"].get("user") != owner:
        raise RuntimeError("This Showroom belongs to another user. Select a separate project.")

def grant_scc_exec_scope(lab, owner):
    # The OAuth proxy's role:edit scope filters out SCC use, even when the
    # user's namespace RoleBinding already grants it. Give the token a named,
    # namespace-filtered role containing only use of restricted-v2.
    role_name="grlab-showroom-restricted-v2-use"
    lab.apply(lab.resource("rbac.authorization.k8s.io/v1","ClusterRole",role_name,rules=[{
        "apiGroups":["security.openshift.io"],
        "resources":["securitycontextconstraints"],
        "resourceNames":["restricted-v2"],
        "verbs":["use"]
    }]))
    lab.apply(lab.resource("rbac.authorization.k8s.io/v1","RoleBinding",role_name,
        roleRef={"apiGroup":"rbac.authorization.k8s.io","kind":"ClusterRole","name":role_name},
        subjects=[{"apiGroup":"rbac.authorization.k8s.io","kind":"User","name":owner}]))

def deploy(lab):
    www=lab.ROOT/"showroom/www"
    if not (www/"modules/en/index.html").is_file():
        raise RuntimeError("Publish from the full repository with the rendered workshop guide.")
    with ZipFile(www/"modules/en/_attachments/lab-files.zip") as z:
        if z.testzip() or not (z.getinfo("guardrail-agent-lab/grlab").external_attr >> 16) & 0o111:
            raise RuntimeError("Rebuild the participant ZIP with scripts/build-site.sh.")
    existing=(lab.get("configmap","grlab-showroom-settings") or {}).get("data",{})
    assignment=lab.get("configmap","grlab-showroom-assignment")
    owner=os.getenv("SHOWROOM_USER") or (assignment or {}).get("data",{}).get("user") or existing.get("SHOWROOM_USER") or lab.oc("whoami")
    if not re.fullmatch(r"[^\s,:/#\"\x00-\x1f\x7f]+", owner) or owner.startswith("system:"):
        raise RuntimeError("Assign the participant's actual interactive OpenShift username in SHOWROOM_USER.")
    if existing.get("SHOWROOM_USER",owner) != owner:
        raise RuntimeError("This project is assigned to another user.")
    console=https_url(os.getenv("SHOWROOM_CONSOLE_URL") or existing.get("SHOWROOM_CONSOLE_URL") or lab.oc("whoami","--show-console"),"Console")
    embed=os.getenv("SHOWROOM_CONSOLE_EMBED_URL",existing.get("SHOWROOM_CONSOLE_EMBED_URL",""))
    if embed:
        embed=https_url(embed,"Embedded console")
    endpoint=https_url(json.loads(lab.oc("get","--raw","/.well-known/oauth-authorization-server")).get("authorization_endpoint",""),"OAuth authorization endpoint")
    refs=images(lab.oc)
    claim_owner(lab,owner,assignment)
    lab.oc("adm","policy","add-scc-to-user","restricted-v2",owner,"-n",lab.NS,capture=False)
    grant_scc_exec_scope(lab,owner)
    image=lab.build("grlab-showroom","showroom")
    # Build trusted participant runtimes with the instructor identity. The
    # signed-in participant only deploys the resulting image digests.
    lab.build("grlab-agent","app")
    lab.build("grlab-rails","proxy")
    docs={d["kind"]:d for d in lab.manifests("showroom.yaml")}
    for kind in ("NetworkPolicy","ServiceAccount","Secret","Service","Route"):
        lab.apply(docs[kind])
    origin=https_url("https://"+lab.oc("get","route/grlab-showroom","-o","jsonpath={.spec.host}"),"Route")
    settings={"SHOWROOM_USER":owner,"LAB_NAMESPACE":lab.NS,"SHOWROOM_ORIGIN":origin,"SHOWROOM_CONSOLE_URL":console,"SHOWROOM_CONSOLE_EMBED_URL":embed}
    lab.apply(lab.resource("v1","ConfigMap","grlab-showroom-settings",data=settings))
    def cookie_exists():
        return lab.oc("get","secret/grlab-showroom-cookie","--ignore-not-found","-o","name")
    if not cookie_exists():
        try:
            lab.oc("create","secret","generic","grlab-showroom-cookie","--from-file=key=/dev/stdin",data=urlsafe_b64encode(secrets.token_bytes(32)).decode())
        except subprocess.CalledProcessError:
            if not cookie_exists():
                raise
    dep=docs["Deployment"]
    template=dep["spec"]["template"]
    template["metadata"]["annotations"]={"showroom.rhpds.io/settings":hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()[:12]}
    names={"SITE_IMAGE":image,"CLI_IMAGE":refs["cli"],"OAUTH_IMAGE":refs["oauth-proxy"]}
    for c in template["spec"]["initContainers"] + template["spec"]["containers"]:
        c["image"]=names[c["image"]]
    lab.apply(dep)
    lab.rollout("grlab-showroom")
    lab.oc("exec","deployment/grlab-showroom","-c","terminal","--","/bin/bash","-ec",
           "cd /workspace/lab && ./grlab --help && /opt/oc/oc version --client && test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token",capture=False)
    check_route(origin,lab.NS,endpoint)
    print(f"Showroom for {owner}: {origin}\nPASS anonymous requests redirect to cluster OAuth.")
