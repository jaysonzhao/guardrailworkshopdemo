---
name: deploy-guardrail-agent-showroom
description: Deploy, teach or verify the guardrail-agent-showroom repository ZIP on OpenShift. Use for the OpenShift OAuth Showroom, an embedded OCP Console without a popup, a dedicated workshop console ingress, participant Terminal tabs, executable workshop CLI, the LangGraph banking application, NeMo rules-only labs, optional GPU Nemotron Safety Guard, per-category rule selection, optional NeMo removal with a verified direct model connection, and optional banking-app observability with OpenShift AI 3.5.x MLflow and Route-discovered dashboard links.
---

# Deploy the Guardrail Agent Showroom

Use this skill with the supplied `guardrail-agent-showroom.zip` or its extracted repository. Recognize the root by `grlab`, `site.yml`, `showroom/portal/`, `deploy/guardrails.yaml` and `policies/rules.yaml`. The full ZIP includes the rendered guide. The smaller `lab-files.zip` attachment serves participants and supports the application modules. Use the companion repository for a new deployment or a code update. An already-running standalone portal can be switched to iframe mode with its project name and workstation CLI.

## Start with the participant's requested action

For OpenShift AI MLflow, tracing, runs or a dashboard link, jump to **Optional Module 6 · observe the banking app with MLflow**. For a console-only change, jump to **Embed OCP Console in the Showroom**. A running portal can be updated independently of the banking app and NeMo modules. For an appendix browse, selection, dry run or local test, jump directly to **Use the appendix at any time**. In the hosted Terminal the files and Python dependency are already present. For an offline workstation, use the extracted participant or repository directory and install `requirements.txt` if necessary. Skip OpenShift sign-in, permission checks, build checks and provider setup for this path. Use the cluster setup below for publishing, live testing or deploying the Showroom.

## Establish the target and requested stopping point

Use the logged-in OpenShift CLI. Inspect `oc whoami`, `oc whoami --show-server`, and the assigned project. Set `LAB_NAMESPACE` to an explicitly chosen dedicated project. Create it if provisioning that project is part of the request. Use a dedicated project and a different Showroom instance per participant. Discover whether the user wants only the guide, a particular module, or the complete rules-only installation. A full workshop deployment defaults to the guide plus Pre-1, 1, 2 and 4. Run Module 3 when the user chooses the GPU exercise. Run optional Module 5 only for a request to remove NeMo or restore a direct model connection. Run Module 6 when the user asks for application observability. It works after Pre-1 with or without NeMo. The appendix is independent of the numbered modules. Use it before Pre-1, during a module, or after cleanup.

Verify Python 3.10+, PyYAML 6 and a working OpenShift 4.x CLI. Check the permissions and build APIs needed for the selected steps.

~~~bash
oc -n "$LAB_NAMESPACE" auth can-i create buildconfigs.build.openshift.io
oc -n "$LAB_NAMESPACE" auth can-i create buildconfigs.build.openshift.io --subresource=instantiatebinary
oc -n "$LAB_NAMESPACE" auth can-i create deployments.apps
oc -n "$LAB_NAMESPACE" auth can-i create pods --subresource=exec
oc -n "$LAB_NAMESPACE" auth can-i create networkpolicies.networking.k8s.io
oc -n "$LAB_NAMESPACE" auth can-i create routes.route.openshift.io
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
~~~

Use the supplied manifests and scripts. The guide, app and NeMo use separate OpenShift binary Docker builds and the internal registry. The app and proxy builds need package-index and Red Hat UBI registry access. The NeMo build also installs compiler packages from UBI RPM repositories. Verify ImageStream and tag access, build/log access, ConfigMap and Service access, and pod port-forward privileges for the playground. The primary path uses a namespaced NeMo 0.24.1 Deployment with HTTP tool passthrough. It runs independently of an OpenShift AI operator. Use synthetic banking data for prompts and logs.

## Publish the authenticated Showroom

Run this step on the instructor workstation from the full repository. The existing project must permit ServiceAccounts, Secrets, ConfigMaps, service serving certificates, Builds, Deployments, NetworkPolicies and Routes. Verify the participant has project edit rights. When provisioning includes the role grant, use `oc adm policy add-role-to-user edit "$SHOWROOM_USER" -n "$LAB_NAMESPACE"`. Use the current human `oc whoami` by default. When provisioning on behalf of another person, export `SHOWROOM_USER` with the exact username of the intended account. If the deployer is a service account, supply the human username explicitly. The CLI records the first owner in an immutable project ConfigMap and preserves that assignment on retries. Use a separate project for that person.

For an embedded console, first complete the console-side configuration below and export `SHOWROOM_CONSOLE_EMBED_URL`. Run the publish command once. Supply `SHOWROOM_CONSOLE_URL` separately if ordinary console URL discovery is unavailable.

Check that the presenter can resolve the current release components `cli` and `oauth-proxy`. Supply `RELEASE_REGISTRY_CONFIG` for an authenticated release lookup, or provide both `SHOWROOM_CLI_IMAGE` and `SHOWROOM_OAUTH_IMAGE` as accessible pullspecs from that release. The portal build needs GitHub's ttyd 1.7.7 release, PyPI and the UBI base image. Set `SHOWROOM_ROUTE_CA` to a trusted PEM CA file if the Route uses a private CA.

~~~bash
test -x ./grlab
./grlab --help
./grlab showroom
oc -n "$LAB_NAMESPACE" rollout status deploy/grlab-showroom
~~~

On a ZIP extractor that discarded Unix modes, restore `chmod +x grlab scripts/build-site.sh scripts/lab.py`. If guide or participant source changed, rebuild with `./scripts/build-site.sh` using Node.js 20+, npm and Git before running the installer. The staged CLI and participant bundle are already built in the supplied ZIP.

The installer creates a Route-backed ServiceAccount OAuth client, its dedicated token Secret, a cookie Secret, a service serving certificate and a re-encrypt Route. Only the OAuth proxy mounts the client credential. Its token requests include the participant's project edit role plus a separate project-filtered role that grants only `use` of `restricted-v2`; this lets live `pods/exec` checks validate the existing pod SCC without granting other SCCs. The gateway verifies the actual OpenShift username, then writes a per-connection kubeconfig to a memory volume and removes it on disconnect. The terminal uses that participant identity. Keep the token out of command output. If an existing portal's OAuth scope changes, restart the Showroom and have the participant sign out and sign in again. The single-user workspace is ephemeral across pod replacements.

## Embed OCP Console in the Showroom

The standalone portal reads `SHOWROOM_CONSOLE_EMBED_URL` when it starts. A value makes the main **OCP Console** tab load an iframe next to the guide. The separate `SHOWROOM_CONSOLE_URL` is the ordinary console link. The browser also checks whether the console response permits the Showroom to frame it. Configure both sides.

### Identify the two URLs

From the instructor workstation, set `LAB_NAMESPACE` to the participant's project. For a running standalone Showroom, discover the exact parent origin and canonical console URL.

~~~bash
: "${LAB_NAMESPACE:?Set LAB_NAMESPACE to the participant project}"
showroom_host=$(oc -n "$LAB_NAMESPACE" get route grlab-showroom -o jsonpath='{.spec.host}') &&
  test -n "$showroom_host" &&
  printf 'Showroom origin: https://%s\n' "$showroom_host"
oc whoami --show-console
~~~

Use the HTTPS origin visible in the participant's address bar, including a nondefault port if applicable. Use the platform owner's documented console URL if CLI discovery is unavailable. For a new Showroom, obtain the intended Route hostname from the provisioner, or publish the portal once to discover it. Use the canonical console host for cluster SSO unless the platform supplies a separate embed endpoint with a working OAuth callback. Keep URLs free of credentials and tokens.

### Dedicated console ingress for this workshop

For a **separate console hostname and load balancer**, use the dedicated-ingress guide. It configures a second OpenShift IngressController for a trusted application namespace, a release-matched workshop console frontend, its own OAuth callback and an exact Showroom framing origin. On this isolated router only, it deletes `X-Frame-Options` and sets the approved `frame-ancestors` policy; browser verification showed the Console otherwise refused to render. The default controller normally accepts all Routes, so the guide verifies exclusive admission before exposing the frontend. Platform console and OAuth Routes continue on their existing controller. A cluster administrator and a browser-reachable DNS record and certificate for the workshop hostname are required. The guide also describes a gateway option for retaining all plugins on the existing console. [Dedicated console ingress for Showroom](references/showroom-console-ingress.md)

When the dedicated endpoint is ready, set `SHOWROOM_CONSOLE_EMBED_URL` to its **workshop** URL and follow the standalone or managed portal instructions below. For a running standalone portal, preserve the participant’s appendix choices before restarting. The discovery command `oc whoami --show-console` returns the standard cluster console.

### Enable framing on the console side

- **Endpoint already allows the Showroom.** Use that HTTPS console endpoint directly. This is the shortest path for an existing RHDP workshop with console embedding provisioned.
- **Shared console endpoint with an owner-managed gateway.** The platform owner can allow the exact parent on the authenticated Console HTML at an HTTPS gateway for the canonical console host. For example, with the Showroom at `https://grlab-showroom-user1.apps.example.com`, the effective response policy can be `Content-Security-Policy: frame-ancestors 'self' https://grlab-showroom-user1.apps.example.com`, and the gateway may need to remove `X-Frame-Options: DENY`. The browser in the tested cluster still refused to render the Console while that header remained. Preserve other CSP directives and existing approved origins; if another enforced policy restricts `frame-ancestors`, its owner must also allow the same origin. On a shared cluster, grant access only to a trusted Showroom origin. If participants can replace that origin’s content, use an instructor-controlled parent or an embed endpoint restricted to workshop accounts. Keep the provider's sign-in page protected and use the same-tab sign-in procedure below. [NGINX response headers](https://nginx.org/en/docs/http/ngx_http_headers_module.html#add_header) · [Browser framing rules](https://www.w3.org/TR/CSP/#frame-ancestors-and-frame-options)

~~~nginx
# Existing gateway for the canonical console only. Substitute the actual Showroom origin.
proxy_hide_header X-Frame-Options;
add_header Content-Security-Policy "frame-ancestors 'self' https://grlab-showroom-user1.apps.example.com" always;
~~~

The Console Operator may reconcile its managed Route, so use a persistent platform configuration for this shared-host option. Where the Console forbids framing and the participant only has project permissions, record the exact parent and give it to the Console owner for an approved endpoint or policy change. A change to the Showroom Route's headers or CORS settings cannot grant permission on the Console. The dedicated-ingress procedure above is the isolated alternative.

- **Dedicated AgnosticD/RHDP demo cluster.** If you own the catalog item and intend the cluster-wide behavior, place `agnosticd.showroom.ocp4_workload_ocp_console_embed` before `agnosticd.showroom.ocp4_workload_showroom` in its workload list. The template documents collection tag `v1.5.6`. At that tag the embed role changes response headers on the **default IngressController**, allows the apps-domain wildcard, and uses an admission webhook to maintain re-encrypt TLS on `openshift-authentication/oauth-openshift`. This affects other Routes served by that controller. Review the role at the tag used by your catalog. A participant updating their portal only needs the endpoint after provisioning. [Catalog configuration](https://rhpds.github.io/showroom_template_nookbag/modules/agnosticv-config.html) · [Role implementation at v1.5.6](https://github.com/agnosticd/showroom/blob/v1.5.6/roles/ocp4_workload_ocp_console_embed/tasks/main.yml)

~~~yaml
# AgnosticV catalog item (keep other existing workloads)
workloads:
  - agnosticd.showroom.ocp4_workload_ocp_console_embed
  - agnosticd.showroom.ocp4_workload_showroom
~~~

For the managed Showroom UI, edit the existing **OCP Console** entry under `tabs` in `ui-config.yml`. Use its `url` as shown below when the cluster uses the standard console hostname. For a custom console use the actual approved HTTPS URL. Preserve the Terminal entry and the provisioner's existing Wetty and SSO settings. Redeploy the managed configuration through that provisioner. For a standalone portal running on that same provisioned cluster, use the ConfigMap procedure below. [Reference Showroom layout](https://github.com/rhpds/ocp-dev-days-rdshw-showroom/blob/main/ui-config.yml)

~~~yaml
# Entry under the existing tabs list
- name: OCP Console
  url: 'https://console-openshift-console.${DOMAIN}'
~~~

If you run the standalone portal on a **disposable demo cluster** without an AgnosticV catalog, the cluster owner can provision just that same RHDP integration. This option has the default-ingress and OAuth scope described above. Use an Ansible control host with cluster-admin access, the Python Kubernetes client and the same kubeconfig/context as `oc`. Save the following play as `embed-console.yml`.

~~~yaml
- name: Provision the RHDP console embed integration
  hosts: localhost
  connection: local
  gather_facts: false
  roles:
    - agnosticd.showroom.ocp4_workload_ocp_console_embed
~~~

~~~bash
(
  set -eu
  apps_domain=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
  test -n "$apps_domain"
  cluster_context=$(oc config current-context)
  test -n "$cluster_context"
  export K8S_AUTH_CONTEXT="$cluster_context"
  ansible-galaxy collection install kubernetes.core 'git+https://github.com/agnosticd/showroom.git,v1.5.6'
  ansible-playbook -i localhost, embed-console.yml -e "ocp_console_embed_domain=$apps_domain"
)
~~~

Wait for the role to finish and confirm the cluster console and sign-in still work. Then set the portal URL below. An already provisioned RHDP cluster can skip the role entirely.

### Set the standalone portal to iframe mode

For either installation, export the URL in the instructor’s shell. Use discovery when the canonical console allows the Showroom origin. If the platform provided a separate embed endpoint, use `export SHOWROOM_CONSOLE_EMBED_URL='https://your-approved-console-host'` with the real hostname instead.

~~~bash
SHOWROOM_CONSOLE_EMBED_URL=$(oc whoami --show-console) &&
  test -n "$SHOWROOM_CONSOLE_EMBED_URL" &&
  export SHOWROOM_CONSOLE_EMBED_URL
~~~

For a **new installation**, run `./grlab showroom` from the full repository with that variable exported, then verify the embedded console and SSO below.

For an **existing installation**, use a separate workstation with `oc`. Confirm the portal edition and the presence of its settings before replacing a pod.

~~~bash
oc -n "$LAB_NAMESPACE" get configmap grlab-showroom-settings -o name
oc -n "$LAB_NAMESPACE" get deployment grlab-showroom \
  -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}'
~~~

The standalone portal edition has a `terminal` container and a signed-in `/session` response containing the `embeddedConsole` field, which can initially be empty. A provisioner-managed Showroom uses the managed tab instructions above. If an older standalone image serves only a static guide or lacks the iframe feature, use the full portal-edition repository identified at the beginning of this skill, preserve existing participant work if present, and publish it with the embed variable exported. A ConfigMap patch alone cannot add portal code to an older image.

If `embeddedConsole` already shows the approved URL, reload the portal and try the tab before restarting. The following block changes one ConfigMap key and restarts only the Showroom Deployment. That restart replaces the pod and ends its terminal sessions. Save open work and have the participant pause editing. The block saves and restores the `policies` directory, including appendix selections. Include any other edited paths under `/workspace/lab` in `keep` before running. The commands use Python in the workshop Terminal and on the workstation. The private backup remains on the workstation. Session credentials live outside the lab directory.

~~~bash
(
  set -euo pipefail
  : "${LAB_NAMESPACE:?Set the participant project}"
  : "${SHOWROOM_CONSOLE_EMBED_URL:?Set the approved HTTPS console URL}"
  ocn=(oc -n "$LAB_NAMESPACE")
  keep=(policies)
  umask 077
  backup=$(mktemp -d)
  printf 'Workstation backup: %s\n' "$backup"

  "${ocn[@]}" get configmap grlab-showroom-settings -o json |
    python3 -c 'import json,sys
key = "SHOWROOM_CONSOLE_EMBED_URL"
print(json.dumps({"data": {key: json.load(sys.stdin)["data"].get(key)}}))' \
    > "$backup/previous-setting.json"
  "${ocn[@]}" exec deploy/grlab-showroom -c terminal -- \
    python3 -c 'import os, sys, tarfile
os.chdir("/workspace/lab")
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
    for path in sys.argv[1:]:
        archive.add(path)' "${keep[@]}" > "$backup/lab.tar"
  python3 -m tarfile --test "$backup/lab.tar"

  patch=$(python3 -c 'import json,sys
from urllib.parse import urlsplit
url = sys.argv[1]
p = urlsplit(url)
if p.scheme != "https" or not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment:
    sys.exit("Use the approved HTTPS console URL without credentials, a query or a fragment.")
print(json.dumps({"data": {"SHOWROOM_CONSOLE_EMBED_URL": url}}))' "$SHOWROOM_CONSOLE_EMBED_URL")

  "${ocn[@]}" patch configmap grlab-showroom-settings --type=merge -p "$patch"
  "${ocn[@]}" rollout restart deployment/grlab-showroom
  "${ocn[@]}" rollout status deployment/grlab-showroom --timeout=5m
  "${ocn[@]}" exec -i deploy/grlab-showroom -c terminal -- \
    python3 -c 'import sys, tarfile
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
    archive.extractall("/workspace/lab", filter="data")' < "$backup/lab.tar"
)
~~~

If rollout or restoration is interrupted, keep the printed backup directory. After the new pod is ready, rerun the last restore command using `oc -n "$LAB_NAMESPACE"` and the saved `lab.tar` path. To revert the portal setting, first save any newer participant edits. Apply the saved prior value with `oc -n "$LAB_NAMESPACE" patch configmap grlab-showroom-settings --type=merge --patch-file="$backup_dir/previous-setting.json"` using the printed directory as `backup_dir`. Restart the Showroom, wait for readiness and restore the latest saved work. Remove a workshop-specific gateway allowance when retiring that Showroom.

### Verify the embedded console and cluster SSO

1. Sign in to the Showroom as the assigned human user. Select **OCP Console** with a normal click. The Showroom URL should remain in the address bar, the guide should stay visible in split view, and the console should render in its pane without creating a browser tab.
2. If the first sign-in stalls at an OAuth or external identity-provider page, copy the Showroom URL. In the **same browser tab**, navigate to the approved console URL, finish cluster sign-in and any consent, then navigate back to the Showroom URL in the address bar. Click **OCP Console** again. This establishes the console's own browser session. Repeat after session expiry if needed. A valid Showroom session alone does not guarantee a console session. Keep the participant's own identity and project RBAC on both sides. [OpenShift OAuth iframe behavior](https://access.redhat.com/solutions/7088178)
3. Check the console user menu and open the participant's project. Switch to **Terminal**, run `oc whoami`, and compare the username. Switch back to **OCP Console** and verify the project loads inside the same browser tab. If you restored a workspace, reopen the appendix picker and confirm that the participant’s previously selected rules are still selected. If the browser blocks cross-site cookies, use same-site HTTPS workshop and console hostnames where feasible, then retest with the participant's browser policy.
4. If a new tab still opens, reload the Showroom root and inspect the signed-in `/session` response in browser DevTools. `embeddedConsole` must contain the intended URL. If the pane opens but is blank, use DevTools **Network → Doc** to identify the blocked document's hostname and its enforced `Content-Security-Policy` and `X-Frame-Options`. A denial on the console HTML calls for a console-side framing change. A denial on an identity-provider document calls for the same-tab sign-in above. If the parent page reports a `frame-src` violation, check the Showroom's own policy for the console host. Inspect the final authenticated document because an anonymous redirect does not prove the console can render. Do not export cookies, tokens or HAR files as evidence.

## Verify the participant experience

1. Record the published HTTPS root and the installer's anonymous redirect checks for the guide, ZIP, terminal and playground. In a private browser window, follow the root Route to cluster sign-in and approve the OAuth client.
2. Sign in as the assigned user. In **Terminal**, run `oc whoami`, `oc config view --minify -o 'jsonpath={.contexts[0].context.namespace}'`, `command -v oc grlab` and `./grlab --help`. Confirm the assigned username and project. Use **Run in terminal** on the guide's identity block and check the visible output. The guide, terminal and previously visited module should stay available when switching tabs.
3. For embedded mode, complete **Verify the embedded console and cluster SSO**. For a deployment using an external link, open the console, complete sign-in, and compare its user menu with `oc whoami`. Other users must be denied access to this participant's portal. If a second testing identity is unavailable, record that check as pending.
4. Open the participant archive from the signed-in guide. Verify that its `grlab` is executable. For a guide-only request, return the Route and the results. The *Banking playground* link becomes usable after Pre-1.

## Run Pre-1 and the required rule modules

Require a tool-capable, OpenAI-compatible main model. Take its exact serving ID in `MAIN_MODEL_ID`, its credential-free base URL ending in `/v1` in `MAIN_BASE_URL`, and an optional `MAAS_SUBSCRIPTION` header value. Obtain the credential from an existing project Secret named `grlab-main` with key `api-key`, or set `MAIN_API_KEY_FILE` to a private one-line file outside the repository. Use the hidden terminal prompt in the Pre-1 guide when appropriate. Keep credential values out of shell arguments, Git, ConfigMaps, logs and chat. Remove only a temporary file created for this run after the Secret exists. The labs use the configured model and gateway in both direct and guarded mode.

Before rerunning a module, inspect the existing workload and the active stage.

~~~bash
oc -n "$LAB_NAMESPACE" get deploy,svc,route -l app.kubernetes.io/part-of=guardrail-agent-showroom
oc -n "$LAB_NAMESPACE" get deploy grlab-rails --ignore-not-found \
  -o jsonpath='{.metadata.annotations.showroom\.rhpds\.io/profile}{"\n"}'
~~~

For the numbered learning path, execute the outstanding stages in order. Use the printed tests as completion gates.

1. Run `./grlab pre-1`. The bank app connects directly to the main model. Require a helpful password-reset reply and the synthetic $2,450.00 checking balance. Direct mode deliberately reports `guardrails_reachable=false`.
2. Run `./grlab nemo`. The script publishes the empty `bootstrap` NeMo profile, sets the app's `BASE_URL=http://grlab-rails/v1`, waits for readiness, and repeats the tool lookup through NeMo. Preserve an existing guarded deployment on retries.
3. Run `./grlab rules` to activate the local banking input, regex and output checks. For settings changes, edit `policies/rules.yaml`, `policies/banking.co` or `policies/banking.py` first, then rerun the stage. Confirm only the main model appears in the mounted `config.yaml`. A basic lab needs neither GPU capacity nor a classifier credential.
4. Run `./grlab test --expect rules` for rules-only signoff or use `--expect content` after the optional module. It verifies both allowed and blocked application requests, synthetic tool output, the human-handoff disclosure and the browser's streaming response.

In the hosted guide use **Banking playground** above the instructions. From the hosted Terminal, use `http://grlab-agent:8080`. For a local workstation supply `oc -n "$LAB_NAMESPACE" port-forward svc/grlab-agent 18080:8080` and the local address `http://127.0.0.1:18080/`. The built-in app API uses `/chat/completions`. NeMo uses `/v1/chat/completions`. Restart the port-forward after a pod replacement. The portal serves the playground through the same authenticated Route.

## Add and validate the optional classifier

Check the project quota, an available NVIDIA GPU with enough free VRAM and BF16 support, the cluster's image pull access to `registry.redhat.io/rhaiis/vllm-cuda-rhel9:3.3.0`, and 30 GiB RWO storage. Inspect `deploy/content-model.yaml`. Set `MODEL_STORAGE_CLASS` before first use if required. Add a site-specific GPU node selector to that manifest on mixed hardware. Initial population needs access to the pinned model revision on Hugging Face. An optional Secret `grlab-hub/token` supplies repository access if required. A prepared PVC can serve the same revision offline.

~~~bash
./grlab content
./grlab test --expect content
~~~

The content step deploys NVIDIA Llama 3.1 Nemotron Safety Guard 8B v3 on vLLM, waits for a real advertised model and safe JSON inference, and only then mounts the combined rule configuration. Preserve the local checks on both sides of the main LLM. Require the direct classifier reason on the proxy's adverse test, the client-facing refusal, and the allowed banking controls. A malformed verdict, missing model, or timeout is a failed classifier or a fail-closed refusal. Keep it distinct from a successful semantic classification.

## Use the appendix at any time

Start here when the participant asks about the appendix, even on a new project, from a bootstrap proxy or after cleanup. Local browsing, staging, examples and dry runs need Python and PyYAML. Open the rule picker in the guide or use `./grlab catalog choose`. The 53 entries span 27 local banking rules, 23 classifier categories and three classifier settings.

~~~bash
./grlab catalog add marketing-consent
./grlab catalog apply --dry-run
./grlab catalog test --local marketing-consent
~~~

Expect enrollment without consent to be blocked and the preferences question to pass. A dry run with cluster access reports the app and NeMo profile, compares mounted choices if available, and shows which live steps would be needed. Before a deployment it previews the workshop defaults plus the participant’s saved choices. An inaccessible cluster is reported as unavailable. For an explicitly offline preview use `./grlab catalog apply --local --dry-run`. Keep the output for model-only examples distinct from actual model inference.

For a requested live policy, use the participant’s dedicated `LAB_NAMESPACE` and `./grlab catalog apply`. In an empty project supply a reachable `MAIN_BASE_URL`, exact `MAIN_MODEL_ID`, the optional MaaS subscription and either a preprovisioned `grlab-main/api-key` or `MAIN_API_KEY_FILE`. The CLI provisions only the missing core stages using the existing workshop commands. It reuses the app and provider after cleanup, activates local rules on bootstrap, and retains an existing content profile. Use `./grlab catalog test --live RULE_ID` to require evidence from a deployed rule. A subsequent named-example failure is separate from a failed rollout or application acceptance check.

To build one rule at a time, stage `./grlab catalog baseline empty`, add the first rule and preview. Restore the workshop choices with `./grlab catalog baseline defaults`. To test a model category, stage its ID and an applicable classifier direction. Preview `./grlab catalog apply --dry-run --with-content`. Run `./grlab catalog apply --with-content` only when the participant requests the GPU-backed classifier. Require valid model attribution and a safe control. Browsing, testing or previewing after cleanup leaves the direct app serving.

If the existing Terminal prints “Complete Module 2 or Module 3 before publishing appendix rules” for a dry run, it is running the previous CLI. From a workstation authenticated to the cluster, enter the updated full repository, set `LAB_NAMESPACE` and run `./scripts/update-running-appendix.sh`. This updates the Terminal code in place and preserves its original rule files and local selections. To seed future terminals and refresh the hosted guide, publish the updated repository with `./grlab showroom`. Preserve any participant edits before replacing the Showroom pod.

## Optional Module 5 · return the running app to the original model

Use `./grlab cleanup --dry-run` to show the project, eligible proxy resources and optional model state. Use `./grlab cleanup` for the requested default cutover. It preserves the banking Deployment and Service, Showroom, existing main-model ConfigMap, Secret and optional model resources. It switches the app's model ID, URL, API key reference and MaaS subscription back to the direct configuration and waits for rollout. Direct verification must pass before removing the proxy.

~~~bash
./grlab cleanup --dry-run
./grlab cleanup
./grlab test --expect direct
oc -n "$LAB_NAMESPACE" get deploy/grlab-rails svc/grlab-rails --ignore-not-found -o name
~~~

Require successful reset guidance, the streamed check_balance tool result for ACCT-12345, the $2,450.00 checking balance in the assistant answer, direct-mode health and absence of the proxy Service and Deployment. If direct verification initially fails, the CLI restores the previous app connection and retains the proxy. If deletion is interrupted after a verified cutover, rerun cleanup to finish. Default cleanup retains the optional classifier and can leave its GPU allocated. Use `--remove-model` when release of the classifier is requested. Add `--delete-model-cache` only when the user also requests permanent removal of the cache PVC. Apply `--dry-run` with exactly the intended flags first. To resume a guarded workshop, run `./grlab nemo`, `./grlab rules` and `./grlab test --expect rules`. Restore Module 3 if wanted.

## Optional Module 6 · observe the banking app with MLflow

Use the companion repository's executable `./grlab mlflow`. Start with the dedicated project in `LAB_NAMESPACE` and an existing Pre-1 banking application for the participant steps. Use the updated portal to expose the toolbar link on an existing Showroom. Save terminal-local edits before replacing its pod.

Start with the read-only `./grlab mlflow precheck`. It reports the MLflow Operator, API, shared server, dashboard ingress, project preparation and banking app tracing state. Use instructor cluster read permissions for a complete platform result; the participant Terminal may report cluster-wide checks as unknown while checking its own project. Then inspect the operator-managed shared server and prepare the assigned project. Discover the actual dashboard hostname from the admitted `Route/data-science-gateway` in `openshift-ingress`. The CLI can resolve a concrete Gateway HTTPS listener on a Gateway-only install. For custom ingress supply the actual canonical dashboard Route as `--route NAMESPACE/NAME`. Do this with the instructor context. The participant reads the saved project URL with their existing Terminal permissions. For a link-only request, run `./grlab mlflow link` immediately. Without prepared settings it discovers the dashboard Route and prints the integrated project URL. Use `./grlab mlflow link --route NAMESPACE/NAME` to check a custom or changed Route.

~~~bash
./grlab mlflow platform
./grlab mlflow prepare
~~~

If the operator or server is absent and the requested deployment includes provisioning a demo instance on a dedicated workshop cluster, run `./grlab mlflow platform --create-demo`, then prepare. This enables the DataScienceCluster MLflow Operator, creates a missing `MLflow/mlflow` server with a 10 GiB RWO PVC and SQLite and waits for availability. Keep any existing shared server, database and artifacts. The file-backed demo stores trace spans in the MLflow backend database and does not need artifact serving; do not set `serveArtifacts` with this PVC configuration. On a cluster using a private gateway CA, create a project ConfigMap containing `ca-bundle.crt` and pass `--ca-configmap NAME` to prepare. The prepare command builds a separate SDK-enabled image, creates a project writer identity and grants the assigned participant read access. Supply `--viewer USER` if preparing without a Showroom assignment. Use synthetic banking data.

From the Showroom Terminal execute the participant steps in direct or guarded mode.

~~~bash
./grlab mlflow enable
./grlab mlflow test
./grlab mlflow link
~~~

Match the chat trace ID to the persisted MLflow trace, check the `guardrailed_agent_request` root and verify the saved smoke run, latency, span count and current backend. Open **MLflow in OpenShift AI** beside **Banking playground** or the Module 6 page link. It enters the integrated experiment in the workshop project. Give the user the full clickable OpenShift AI URL printed by `test` for the exact run and by `link` for the project's Experiments page. Use `/mlflow` on the dashboard host as the SDK tracking endpoint. Its integrated browser path starts at `/develop-train/mlflow/experiments`. If operating outside the cluster, forward `svc/grlab-agent` to local port 18080 and use `./grlab mlflow test --agent http://127.0.0.1:18080`.

To turn off app observability on request, run `./grlab mlflow disable` followed by `./grlab test`. The current model backend is preserved. The experiment stays accessible. For RBAC checks, private CA configuration, trace details or troubleshooting, see [Module 6 operations](references/mlflow-observability.md).

## Diagnose and report

Use `oc -n "$LAB_NAMESPACE" get pods,builds,pvc,svc` and recent events first. For a failed image, inspect its Build log. For a GPU startup problem, inspect the named init container `fetch-model-if-missing`, the vLLM container, PVC binding and node allocation. For model errors, check the provider's model ID, URL, trust, key and subscription. Compare safe chat with the synthetic tool lookup to isolate a tool-passthrough failure. Inspect only the necessary synthetic NeMo logs.

To restore the simpler live policy, run `./grlab rules` and `./grlab test`. Once they pass, scale `deployment/nemotron-safety-guard-vllm` to zero if the user wants to release that GPU. Keep its cache for the next run. A failed activation automatically restores the previous proxy spec if it exists. Verify the restored profile, then report the failing gate. If cluster access is unavailable, distinguish local integration checks from deployment or inference on the user's cluster.

Report the Route, participant and project, Console presentation mode, Terminal identity and executable checks, active policy, agent access method and GPU state. For embedded mode, record whether the console document rendered inside the original tab and whether same-tab SSO bootstrap was needed. Distinguish a verified OAuth redirect from a completed interactive login. Label any stage skipped or still awaiting evidence.
