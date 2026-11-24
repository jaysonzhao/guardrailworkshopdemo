---
name: deploy-guardrail-agent-showroom
description: Deploy, teach or verify the guardrail-agent-showroom repository ZIP on OpenShift. Use for the OpenShift OAuth Showroom, OCP Console and participant Terminal tabs, executable workshop CLI, the LangGraph banking application, NeMo rules-only labs, and optional GPU Nemotron Safety Guard activation.
---

# Deploy the Guardrail Agent Showroom

Use this skill with the supplied `guardrail-agent-showroom.zip` or its extracted repository. Recognize the root by `grlab`, `site.yml`, `showroom/portal/`, `deploy/guardrails.yaml` and `policies/rules.yaml`. The full ZIP includes the rendered guide. The smaller `lab-files.zip` attachment serves participants and supports the application modules. If only this skill file is available, obtain the companion repository from the user or the workspace.

## Establish the target and requested stopping point

Use the logged-in OpenShift CLI. Inspect `oc whoami`, `oc whoami --show-server`, and the assigned project. Set `LAB_NAMESPACE` to an explicitly chosen dedicated project. Create it if provisioning that project is part of the request. Use a dedicated project and a different Showroom instance per participant. Discover whether the user wants only the guide, a particular module, or the complete rules-only installation. A full workshop deployment defaults to the guide plus Pre-1, 1, 2 and 4. Run Module 3 when the user chooses the GPU exercise.

Verify Python 3.10+, PyYAML 6 and a working OpenShift 4.x CLI. Check participant deployment permissions separately from the instructor's image-build permissions.

~~~bash
oc -n "$LAB_NAMESPACE" auth can-i create deployments.apps
oc -n "$LAB_NAMESPACE" auth can-i create pods --subresource=exec
oc -n "$LAB_NAMESPACE" auth can-i create networkpolicies.networking.k8s.io
oc -n "$LAB_NAMESPACE" auth can-i create routes.route.openshift.io
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
~~~

Use the supplied manifests and scripts. During instructor provisioning, the installer uses binary Docker builds for the guide, app and NeMo images, then removes their BuildConfig objects. This prevents a participant's scoped SSO token from selecting a build strategy or submitting arbitrary binary build input. The installer identity must be allowed to use Docker builds. The builds need package-index and Red Hat UBI registry access; the NeMo build also installs compiler packages from UBI RPM repositories. Participants need ImageStreamTag read access plus ConfigMap, Secret, Deployment, Service and NetworkPolicy access, but no BuildConfig strategy permission. The primary path uses a namespaced NeMo 0.24.1 Deployment with HTTP tool passthrough. It runs independently of an OpenShift AI operator. Use synthetic banking data for prompts and logs.

## Publish the authenticated Showroom

Run this step on the instructor workstation from the full repository. The installer identity needs permission to create and run binary Docker builds, in addition to the ServiceAccounts, Secrets, ConfigMaps, service serving certificates, Builds, Deployments, NetworkPolicies and Routes used by the Showroom. Verify the participant has project edit rights. When provisioning includes the role grant, use `oc adm policy add-role-to-user edit "$SHOWROOM_USER" -n "$LAB_NAMESPACE"`. Use the current human `oc whoami` by default. When provisioning on behalf of another person, export `SHOWROOM_USER` with the exact username of the intended account. If the deployer is a service account, supply the human username explicitly. The CLI records the first owner in an immutable project ConfigMap and preserves that assignment on retries. Use a separate project for that person.

Discover the actual console with `oc whoami --show-console`. Supply `SHOWROOM_CONSOLE_URL` if discovery is unavailable. Set `SHOWROOM_CONSOLE_EMBED_URL` only when the platform owner supplies an HTTPS console endpoint that allows this Showroom origin. With the normal console URL the Console tab opens a new browser tab. The portal does not change the cluster's framing configuration.

Check that the presenter can resolve the current release components `cli` and `oauth-proxy`. Supply `RELEASE_REGISTRY_CONFIG` for an authenticated release lookup, or provide both `SHOWROOM_CLI_IMAGE` and `SHOWROOM_OAUTH_IMAGE` as accessible pullspecs from that release. The portal build needs GitHub's ttyd 1.7.7 release, PyPI and the UBI base image. The installer also builds the agent and NeMo runtime images before publishing the Route. Set `SHOWROOM_ROUTE_CA` to a trusted PEM CA file if the Route uses a private CA.

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
2. Sign in as the assigned user. In **Terminal**, run `oc whoami`, `oc config view --minify -o 'jsonpath={.contexts[0].context.namespace}'`, `command -v oc grlab` and `./grlab --help`. Confirm the assigned username and project. Use **Run in terminal** on the guide's identity block and check the visible output. The guide, terminal and previously visited module should stay available when switching tabs.
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

1. Run `./grlab pre-1`. The command uses the instructor-prebuilt `grlab-agent:latest` image digest and deploys it without creating a BuildConfig. The bank app connects directly to the main model. Require a helpful password-reset reply and the synthetic $2,450.00 checking balance. Direct mode deliberately reports `guardrails_reachable=false`.
2. Run `./grlab nemo`. The command uses the instructor-prebuilt `grlab-rails:latest` image, publishes the empty `bootstrap` NeMo profile, sets the app's `BASE_URL=http://grlab-rails/v1`, waits for readiness, and repeats the tool lookup through NeMo. Preserve an existing guarded deployment on retries.
3. Run `./grlab rules` to activate the local banking input, regex and output checks. For settings changes, edit `policies/rules.yaml`, `policies/banking.co` or `policies/actions.py` first, then rerun the stage. Confirm only the main model appears in the mounted `config.yaml`. A basic lab needs neither GPU capacity nor a classifier credential.
4. Run `./grlab test --expect rules` for rules-only signoff or use `--expect content` after the optional module. It verifies both allowed and blocked application requests, synthetic tool output, the human-handoff disclosure and the browser's streaming response.

In the hosted guide use **Banking playground** above the instructions. From the hosted Terminal, use `http://grlab-agent:8080`. For a local workstation supply `oc -n "$LAB_NAMESPACE" port-forward svc/grlab-agent 18080:8080` and the local address `http://127.0.0.1:18080/`. The built-in app API uses `/chat/completions`. NeMo uses `/v1/chat/completions`. Restart the port-forward after a pod replacement. The portal serves the playground through the same authenticated Route.

## Add and validate the optional classifier

Check the project quota, an available NVIDIA GPU with enough free VRAM and BF16 support, the cluster's image pull access to `registry.redhat.io/rhaiis/vllm-cuda-rhel9:3.3.0`, and 30 GiB RWO storage. Inspect `deploy/content-model.yaml`. Set `MODEL_STORAGE_CLASS` before first use if required. Add a site-specific GPU node selector to that manifest on mixed hardware. Initial population needs access to the pinned model revision on Hugging Face. An optional Secret `grlab-hub/token` supplies repository access if required. A prepared PVC can serve the same revision offline.

~~~bash
./grlab content
./grlab test --expect content
~~~

The content step deploys NVIDIA Llama 3.1 Nemotron Safety Guard 8B v3 on vLLM, waits for a real advertised model and safe JSON inference, and only then mounts the combined rule configuration. Preserve the local checks on both sides of the main LLM. Require the direct classifier reason on the proxy's adverse test, the client-facing refusal, and the allowed banking controls. A malformed verdict, missing model, or timeout is a failed classifier or a fail-closed refusal. Keep it distinct from a successful semantic classification.

## Diagnose and report

Use `oc -n "$LAB_NAMESPACE" get pods,builds,pvc,svc` and recent events first. For a failed image, inspect its Build log. For a GPU startup problem, inspect the named init container `fetch-model-if-missing`, the vLLM container, PVC binding and node allocation. For model errors, check the provider's model ID, URL, trust, key and subscription. Compare safe chat with the synthetic tool lookup to isolate a tool-passthrough failure. Inspect only the necessary synthetic NeMo logs.

To restore the simpler live policy, run `./grlab rules` and `./grlab test`. Once they pass, scale `deployment/nemotron-safety-guard-vllm` to zero if the user wants to release that GPU. Keep its cache for the next run. A failed activation automatically restores the previous proxy spec if it exists. Verify the restored profile, then report the failing gate. If cluster access is unavailable, distinguish local integration checks from deployment or inference on the user's cluster.

Report the Route, participant and project, Console presentation mode, Terminal identity and executable checks, active policy, agent access method and GPU state. Distinguish a verified OAuth redirect from a completed interactive login. Label any stage skipped or still awaiting evidence.
