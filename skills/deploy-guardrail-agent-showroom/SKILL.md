---
name: deploy-guardrail-agent-showroom
description: Deploy, teach or verify the guardrail-agent-showroom repository ZIP on OpenShift. Use for the OpenShift OAuth Showroom, OCP Console and participant Terminal tabs, executable workshop CLI, the LangGraph banking application, NeMo rules-only labs, optional GPU Nemotron Safety Guard, per-category rule selection, and optional NeMo removal with a verified direct model connection.
---

# Deploy the Guardrail Agent Showroom

Use this skill with the supplied `guardrail-agent-showroom.zip` or its extracted repository. Recognize the root by `grlab`, `site.yml`, `showroom/portal/`, `deploy/guardrails.yaml` and `policies/rules.yaml`. The full ZIP includes the rendered guide. The smaller `lab-files.zip` attachment serves participants and supports the application modules. If only this skill file is available, obtain the companion repository from the user or the workspace.

## Establish the target and requested stopping point

Use the logged-in OpenShift CLI. Inspect `oc whoami`, `oc whoami --show-server`, and the assigned project. Set `LAB_NAMESPACE` to an explicitly chosen dedicated project. Create it if provisioning that project is part of the request. Use a dedicated project and a different Showroom instance per participant. Discover whether the user wants only the guide, a particular module, or the complete rules-only installation. A full workshop deployment defaults to the guide plus Pre-1, 1, 2 and 4. Run Module 3 when the user chooses the GPU exercise. Run optional Module 5 only for a request to remove NeMo or restore a direct model connection. The appendix can be used at either guarded stage.

Verify Python 3.10+, PyYAML 6 and a working OpenShift 4.x CLI. Check the instructor's image-build permissions and the participant's project-scoped deployment permissions separately.

~~~bash
oc -n "$LAB_NAMESPACE" auth can-i create deployments.apps
oc -n "$LAB_NAMESPACE" auth can-i get imagestreamtags.image.openshift.io
oc -n "$LAB_NAMESPACE" auth can-i create networkpolicies.networking.k8s.io
oc -n "$LAB_NAMESPACE" auth can-i create routes.route.openshift.io
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
~~~

Use the supplied manifests and scripts. The instructor runs `./grlab showroom` with a build-enabled identity. It builds the guide, agent and NeMo images, publishes their ImageStreamTags, and deletes each BuildConfig. App and proxy builds need package-index and Red Hat UBI registry access; the NeMo build also needs UBI RPM repositories. Participants deploy those prebuilt digests and do not need BuildConfig strategy permission. The Showroom grants the assigned user `restricted-v2` SCC use only in this project for the named live rule checks that use `pods/exec`. The primary path uses a namespaced NeMo 0.24.1 Deployment with HTTP tool passthrough and runs independently of an OpenShift AI operator. Use synthetic banking data for prompts and logs.

## Publish the authenticated Showroom

Run this step on the instructor workstation from the full repository. The existing project must permit BuildConfigs and binary builds for the instructor, plus ServiceAccounts, Secrets, ConfigMaps, service serving certificates, Deployments, NetworkPolicies and Routes. Verify the participant has project edit rights. When provisioning includes the role grant, use `oc adm policy add-role-to-user edit "$SHOWROOM_USER" -n "$LAB_NAMESPACE"`. Use the current human `oc whoami` by default. When provisioning on behalf of another person, export `SHOWROOM_USER` with the exact username of the intended account. If the deployer is a service account, supply the human username explicitly. The CLI records the first owner in an immutable project ConfigMap and preserves that assignment on retries. Use a separate project for that person. It also grants the assigned user the `restricted-v2` SCC in this namespace, never `anyuid` or `privileged`.

Discover the actual console with `oc whoami --show-console`. Supply `SHOWROOM_CONSOLE_URL` if discovery is unavailable. Set `SHOWROOM_CONSOLE_EMBED_URL` only when the platform owner supplies an HTTPS console endpoint that allows this Showroom origin. With the normal console URL the Console tab opens a new browser tab. The portal does not change the cluster's framing configuration.

Check that the presenter can resolve the current release components `cli` and `oauth-proxy`. Supply `RELEASE_REGISTRY_CONFIG` for an authenticated release lookup, or provide both `SHOWROOM_CLI_IMAGE` and `SHOWROOM_OAUTH_IMAGE` as accessible pullspecs from that release. The portal build needs GitHub's ttyd 1.7.7 release, PyPI and the UBI base image. Set `SHOWROOM_ROUTE_CA` to a trusted PEM CA file if the Route uses a private CA.

~~~bash
test -x ./grlab
./grlab --help
./grlab showroom
oc -n "$LAB_NAMESPACE" rollout status deploy/grlab-showroom
~~~

On a ZIP extractor that discarded Unix modes, restore `chmod +x grlab scripts/build-site.sh scripts/lab.py`. If guide or participant source changed, rebuild with `./scripts/build-site.sh` using Node.js 20+, npm and Git before running the installer. The staged CLI and participant bundle are already built in the supplied ZIP.

The installer creates a Route-backed ServiceAccount OAuth client, its dedicated token Secret, a cookie Secret, a service serving certificate and a re-encrypt Route. Only the OAuth proxy mounts the client credential. It requests basic identity plus edit scope including Secrets in this one project, restricted by the participant's existing RBAC. The gateway verifies the actual OpenShift username, then writes a per-connection kubeconfig to a memory volume and removes it on disconnect. The terminal uses that participant identity. Keep the token out of command output. The single-user workspace is ephemeral across pod replacements.

### Verify the participant experience

1. Record the published HTTPS root and the installer's anonymous redirect checks for the guide, ZIP, terminal and playground. In a private browser window, follow the root Route to cluster sign-in and approve the OAuth client.
2. Sign in as the assigned user. In **Terminal**, run `oc whoami`, `oc config view --minify -o 'jsonpath={.contexts[0].context.namespace}'`, `oc auth can-i use scc/restricted-v2 -n "$LAB_NAMESPACE"`, `command -v oc grlab` and `./grlab --help`. Confirm the assigned username and project. Use **Run in terminal** on the guide's identity block and check the visible output. The guide, terminal and previously visited module should stay available when switching tabs.
3. Open **OCP Console**, complete any console authorization prompt, and compare the console's user menu with `oc whoami`. For configured embedding, also test the new-tab link. Other users must be denied access to this participant's portal. If a second testing identity is unavailable, record that check as pending.
4. Open the participant archive from the signed-in guide. Verify that its `grlab` is executable. For a guide-only request, return the Route and the results. The *Banking playground* link becomes usable after Pre-1.

A managed RHDP provisioner can consume `ui-config.yml` with `/wetty` and its own console domain. Managed Wetty authentication, startup files and console embedding belong to that provisioner. Use the standalone installer for the preconfigured cluster SSO terminal described here.

## Run Pre-1 and the required rule modules

Require a tool-capable, OpenAI-compatible main model. Take its exact serving ID in `MAIN_MODEL_ID`, its credential-free base URL ending in `/v1` in `MAIN_BASE_URL`, and an optional `MAAS_SUBSCRIPTION` header value. Obtain the credential from an existing project Secret named `grlab-main` with key `api-key`, or set `MAIN_API_KEY_FILE` to a private one-line file outside the repository. Use the hidden terminal prompt in the Pre-1 guide when appropriate. Keep credential values out of shell arguments, Git, ConfigMaps, logs and chat. Remove only a temporary file created for this run after the Secret exists. The labs use the configured model and gateway in both direct and guarded mode.

Before rerunning a module, inspect the existing workload and the active stage.

~~~bash
oc -n "$LAB_NAMESPACE" get deploy,svc,route -l app.kubernetes.io/part-of=guardrail-agent-showroom
oc -n "$LAB_NAMESPACE" get deploy grlab-rails --ignore-not-found \
  -o jsonpath='{.metadata.annotations.showroom\.rhpds\.io/profile}{"\n"}'
~~~

Execute the outstanding stages in order. Use the printed tests as completion gates.

1. Run `./grlab pre-1`. The command deploys the instructor-prebuilt `grlab-agent` image and runs HTTP checks from the hosted Terminal against the in-project Service; no participant build or `pods/exec` is needed. The bank app connects directly to the main model. Require helpful password-reset guidance and the synthetic $2,450.00 checking balance. Direct mode deliberately reports `guardrails_reachable=false`.
2. Run `./grlab nemo`. The script publishes the empty `bootstrap` NeMo profile, sets the app's `BASE_URL=http://grlab-rails/v1`, waits for readiness, and repeats the tool lookup through NeMo. Preserve an existing guarded deployment on retries.
3. Run `./grlab rules` to activate the local banking input, regex and output checks. For settings changes, edit `policies/rules.yaml`, `policies/banking.co` or `policies/banking.py` first, then rerun the stage. Confirm only the main model appears in the mounted `config.yaml`. A basic lab needs neither GPU capacity nor a classifier credential.
4. Run `./grlab test --expect rules` for rules-only signoff or use `--expect content` after the optional module. It verifies both allowed and blocked application requests, synthetic tool output, the human-handoff disclosure and the browser's streaming response. `./grlab catalog test RULE_ID` inspects a selected rule's live NeMo configuration and uses `pods/exec`; the instructor provisioner supplies the namespace-scoped `restricted-v2` grant for that check.

In the hosted guide use **Banking playground** above the instructions. From the hosted Terminal, use `http://grlab-agent:8080`. For a local workstation supply `oc -n "$LAB_NAMESPACE" port-forward svc/grlab-agent 18080:8080` and the local address `http://127.0.0.1:18080/`. The built-in app API uses `/chat/completions`. NeMo uses `/v1/chat/completions`. Restart the port-forward after a pod replacement. The portal serves the playground through the same authenticated Route.

## Add and validate the optional classifier

Check the project quota, an available NVIDIA GPU with enough free VRAM and BF16 support, the cluster's image pull access to `registry.redhat.io/rhaiis/vllm-cuda-rhel9:3.3.0`, and 30 GiB RWO storage. Inspect `deploy/content-model.yaml`. Set `MODEL_STORAGE_CLASS` before first use if required. Add a site-specific GPU node selector to that manifest on mixed hardware. Initial population needs access to the pinned model revision on Hugging Face. An optional Secret `grlab-hub/token` supplies repository access if required. A prepared PVC can serve the same revision offline.

~~~bash
./grlab content
./grlab test --expect content
~~~

The content step deploys NVIDIA Llama 3.1 Nemotron Safety Guard 8B v3 on vLLM, waits for a real advertised model and safe JSON inference through its in-project Service, and only then mounts the combined rule configuration. Preserve the local checks on both sides of the main LLM. Require the direct classifier reason on the proxy's adverse test, the client-facing refusal, and the allowed banking controls. A malformed verdict, missing model, or timeout is a failed classifier or a fail-closed refusal. Keep it distinct from a successful semantic classification.

## Select and demonstrate a rule by category

Use the appendix or `./grlab catalog list`. The 53 individually selectable entries span eight categories, covering 27 local rules, 23 model categories and three classifier settings. Use `./grlab catalog choose` interactively, or select a named ID. Start with the user's chosen category. For local rules, read the published sample, its control and the intended decision. Output samples are candidate replies. Stage one addition at a time and use the existing active profile.

~~~bash
./grlab catalog list customer-care
./grlab catalog sample marketing-consent
./grlab catalog add marketing-consent
./grlab catalog apply --dry-run
./grlab catalog apply
./grlab catalog test marketing-consent
./grlab catalog status
~~~

Expect a block for “Enroll this customer in marketing without consent.” Expect the question “How does a customer change marketing preferences?” to pass. Run the check in the deployed policy and try the same prompts in the playground. To undo one change, run `./grlab catalog remove marketing-consent` followed by `./grlab catalog apply`. Publication references a new immutable ConfigMap, checks the running application and restores the prior revision if that acceptance check fails. The subsequent named sample is a separate check. If the named sample fails, inspect the reason and remove or revise the choice before republishing. Keep the local overrides in `policies/selection.yaml` for reuse after a workspace rebuild.

When the participant wants to build a policy one rule at a time from zero, stage `./grlab catalog baseline empty`, add their first local rule and preview before publishing. The baseline command replaces the saved local overrides. Restore the shipped catalog with `./grlab catalog baseline defaults` and a new apply.

Model categories only execute in the content profile. The initial prompt includes S1–S13. To demonstrate S16 with the GPU, select `s16`, confirm at least one classifier direction is enabled, publish the change and run `./grlab catalog test s16`. Require a valid matching model attribution for the adverse example and a valid safe control. Report different categories, false positives and unavailable inference separately. The appendix also includes the complete pinned upstream flow reference for extending the lab.

## Optional Module 5 · return the running app to the original model

Use `./grlab cleanup --dry-run` to show the project, eligible proxy resources and optional model state. Use `./grlab cleanup` for the requested default cutover. It preserves the banking Deployment and Service, Showroom, existing main-model ConfigMap, Secret and optional model resources. It switches the app's model ID, URL, API key reference and MaaS subscription back to the direct configuration and waits for rollout. Direct verification must pass before removing the proxy.

~~~bash
./grlab cleanup --dry-run
./grlab cleanup
./grlab test --expect direct
oc -n "$LAB_NAMESPACE" get deploy/grlab-rails svc/grlab-rails --ignore-not-found -o name
~~~

Require successful reset guidance, the streamed check_balance tool result for ACCT-12345, the $2,450.00 checking balance in the assistant answer, direct-mode health and absence of the proxy Service and Deployment. If direct verification initially fails, the CLI restores the previous app connection and retains the proxy. If deletion is interrupted after a verified cutover, rerun cleanup to finish. Default cleanup retains the optional classifier and can leave its GPU allocated. Use `--remove-model` when release of the classifier is requested. Add `--delete-model-cache` only when the user also requests permanent removal of the cache PVC. Apply `--dry-run` with exactly the intended flags first. To resume a guarded workshop, run `./grlab nemo`, `./grlab rules` and `./grlab test --expect rules`. Restore Module 3 if wanted.

## Diagnose and report

Use `oc -n "$LAB_NAMESPACE" get pods,builds,pvc,svc` and recent events first. For a failed image, inspect its Build log. For a GPU startup problem, inspect the named init container `fetch-model-if-missing`, the vLLM container, PVC binding and node allocation. For model errors, check the provider's model ID, URL, trust, key and subscription. Compare safe chat with the synthetic tool lookup to isolate a tool-passthrough failure. Inspect only the necessary synthetic NeMo logs.

To restore the simpler live policy, run `./grlab rules` and `./grlab test`. Once they pass, scale `deployment/nemotron-safety-guard-vllm` to zero if the user wants to release that GPU. Keep its cache for the next run. A failed activation automatically restores the previous proxy spec if it exists. Verify the restored profile, then report the failing gate. If cluster access is unavailable, distinguish local integration checks from deployment or inference on the user's cluster.

Report the Route, participant and project, Console presentation mode, Terminal identity and executable checks, active policy, agent access method and GPU state. Distinguish a verified OAuth redirect from a completed interactive login. Label any stage skipped or still awaiting evidence.
