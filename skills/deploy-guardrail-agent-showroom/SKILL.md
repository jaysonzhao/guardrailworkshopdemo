---
name: deploy-guardrail-agent-showroom
description: Deploy, teach or verify the guardrail-agent-showroom repository ZIP on OpenShift. Use for the Antora Showroom, the LangGraph banking application, NeMo rules-only labs, and optional GPU Nemotron Safety Guard activation.
---

# Deploy the Guardrail Agent Showroom

Use this skill with the supplied `guardrail-agent-showroom.zip` or its extracted repository. Recognize the root by `site.yml`, `scripts/lab.py`, `deploy/guardrails.yaml` and `policies/rules.yaml`. The full ZIP includes the rendered guide. The smaller `lab-files.zip` attachment serves participants and supports the application modules. If only this skill file is available, obtain the companion repository from the user or the workspace.

## Establish the target and requested stopping point

Use the logged-in OpenShift CLI. Inspect `oc whoami`, `oc whoami --show-server`, and the assigned project. Set `LAB_NAMESPACE` to an explicitly chosen dedicated project. Create it if provisioning that project is part of the request. Keep one project per participant. Discover whether the user wants only the guide, a particular module, or the complete rules-only installation. A full workshop deployment defaults to the guide plus Pre-1, 1, 2 and 4. Run Module 3 when the user chooses the GPU exercise.

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

## Publish the Showroom

Run from the full repository root. If the prebuilt `showroom/www/index.html` is missing, or content has changed, run `bash scripts/build-site.sh` first with Node.js 20+, npm and Git available. Building a site from the ZIP does not require publishing a Git repository.

~~~bash
python scripts/lab.py showroom
site_host=$(oc -n "$LAB_NAMESPACE" get route grlab-showroom -o jsonpath='{.spec.host}')
curl -fsSL "https://$site_host/modules/en/index.html" >/dev/null
curl -fsSL "https://$site_host/modules/en/_attachments/lab-files.zip" -o /dev/null
~~~

Return the guide URL once those requests succeed. For a guide-only request, finish here. A managed RHDP Showroom can consume the root playbook and `ui-config.yml` when its provisioner is pointed at the user's chosen Git repository and branch.

## Run Pre-1 and the required rule modules

Require a tool-capable, OpenAI-compatible main model. Take its exact serving ID in `MAIN_MODEL_ID`, its credential-free base URL ending in `/v1` in `MAIN_BASE_URL`, and an optional `MAAS_SUBSCRIPTION` header value. Obtain the credential from an existing project Secret named `grlab-main` with key `api-key`, or set `MAIN_API_KEY_FILE` to a private one-line file outside the repository. Use the hidden terminal prompt in the Pre-1 guide when appropriate. Keep credential values out of shell arguments, Git, ConfigMaps, logs and chat. Remove only a temporary file created for this run after the Secret exists. The labs use the configured model and gateway in both direct and guarded mode.

Before rerunning a module, inspect the existing workload and the active stage.

~~~bash
oc -n "$LAB_NAMESPACE" get deploy,svc,route -l app.kubernetes.io/part-of=guardrail-agent-showroom
oc -n "$LAB_NAMESPACE" get deploy grlab-rails --ignore-not-found \
  -o jsonpath='{.metadata.annotations.showroom\.rhpds\.io/profile}{"\n"}'
~~~

Execute the outstanding stages in order. Use the printed tests as completion gates.

1. Run `python scripts/lab.py pre-1`. The bank app connects directly to the main model. Require a helpful password-reset reply and the synthetic $2,450.00 checking balance. Direct mode deliberately reports `guardrails_reachable=false`.
2. Run `python scripts/lab.py nemo`. The script publishes the empty `bootstrap` NeMo profile, sets the app's `BASE_URL=http://grlab-rails/v1`, waits for readiness, and repeats the tool lookup through NeMo. Preserve an existing guarded deployment on retries.
3. Run `python scripts/lab.py rules` to activate the local banking input, regex and output checks. For settings changes, edit `policies/rules.yaml`, `policies/banking.co` or `policies/actions.py` first, then rerun the stage. Confirm only the main model appears in the mounted `config.yaml`. A basic lab needs neither GPU capacity nor a classifier credential.
4. Run `python scripts/lab.py test --expect rules` for rules-only signoff or use `--expect content` after the optional module. It verifies both allowed and blocked application requests, synthetic tool output, the human-handoff disclosure and the browser's streaming response.

For the banking playground, supply `oc -n "$LAB_NAMESPACE" port-forward svc/grlab-agent 18080:8080` and the local address `http://127.0.0.1:18080/`. The built-in app API uses `/chat/completions`. NeMo uses `/v1/chat/completions`. Restart the port-forward after a pod replacement. The published guide is the only externally routed component.

## Add and validate the optional classifier

Check the project quota, an available NVIDIA GPU with enough free VRAM and BF16 support, the cluster's image pull access to `registry.redhat.io/rhaiis/vllm-cuda-rhel9:3.3.0`, and 30 GiB RWO storage. Inspect `deploy/content-model.yaml`. Set `MODEL_STORAGE_CLASS` before first use if required. Add a site-specific GPU node selector to that manifest on mixed hardware. Initial population needs access to the pinned model revision on Hugging Face. An optional Secret `grlab-hub/token` supplies repository access if required. A prepared PVC can serve the same revision offline.

~~~bash
python scripts/lab.py content
python scripts/lab.py test --expect content
~~~

The content step deploys NVIDIA Llama 3.1 Nemotron Safety Guard 8B v3 on vLLM, waits for a real advertised model and safe JSON inference, and only then mounts the combined rule configuration. Preserve the local checks on both sides of the main LLM. Require the direct classifier reason on the proxy's adverse test, the client-facing refusal, and the allowed banking controls. A malformed verdict, missing model, or timeout is a failed classifier or a fail-closed refusal. Keep it distinct from a successful semantic classification.

## Diagnose and report

Use `oc -n "$LAB_NAMESPACE" get pods,builds,pvc,svc` and recent events first. For a failed image, inspect its Build log. For a GPU startup problem, inspect the named init container `fetch-model-if-missing`, the vLLM container, PVC binding and node allocation. For model errors, check the provider's model ID, URL, trust, key and subscription. Compare safe chat with the synthetic tool lookup to isolate a tool-passthrough failure. Inspect only the necessary synthetic NeMo logs.

To restore the simpler live policy, run `python scripts/lab.py rules` and `python scripts/lab.py test`. Once they pass, scale `deployment/nemotron-safety-guard-vllm` to zero if the user wants to release that GPU. Keep its cache for the next run. A failed activation automatically restores the previous proxy spec if it exists. Verify the restored profile, then report the failing gate. If cluster access is unavailable, distinguish local integration checks from deployment or inference on the user's cluster.

Report the guide URL, project, active policy, agent access method, acceptance checks and GPU deployment state. Label any stage skipped or still awaiting evidence.
