# Guardrails for a banking agent on OpenShift

Deploy a working banking chatbot, place NVIDIA NeMo Guardrails in its model path, write local policy and optionally add a GPU-backed content-safety classifier. The ZIP contains the English Antora guide, its rendered HTML, a downloadable participant code bundle, the LangGraph/FastAPI application and playground, and the OpenShift manifests and deployment scripts.

## Open the guide

Open `showroom/www/modules/en/index.html` from the extracted ZIP. For a local HTTP preview, run `python3 -m http.server 8080 --directory showroom/www` in the repository root and open [the preview](http://127.0.0.1:8080/). The guide includes all participant commands and expected results. The signed-in tabs are available at the deployed Route.

## Deploy the signed-in Showroom

Use an OpenShift 4.x cluster with integrated OAuth, a reachable web console, a router, service certificates and the internal registry. Use a separate project for each participant. The instructor needs project administration rights and an identity allowed to use binary Docker builds. The participant needs the built-in project `edit` role or equivalent rights for the exercises. During Showroom provisioning, the instructor builds the trusted portal, banking-agent and NeMo images, then the installer removes their BuildConfig objects. Participant commands deploy those prebuilt images and do not need BuildConfig strategy permissions.

From the **full repository** on the instructor's workstation, run the following after selecting the assigned project. It preserves the selected project and defaults the participant to the current `oc whoami`. When provisioning for someone else, set `SHOWROOM_USER` to the exact username shown by their `oc whoami` before publishing, and ensure that user has the project's `edit` binding. A project administrator can grant the workshop role with `oc adm policy add-role-to-user edit "$SHOWROOM_USER" -n "$LAB_NAMESPACE"` when needed.

~~~bash
export LAB_NAMESPACE="$(oc project -q)"
oc whoami
oc whoami --show-server
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
./grlab showroom
~~~

The installer selects the `cli` and `oauth-proxy` images from the cluster's current release, verifies that the terminal can execute the staged `oc` and workshop CLI, and checks the anonymous OAuth redirects for the guide, ZIP, terminal and playground. Give the printed HTTPS URL to the assigned participant.

| Deployment input | When to set it |
| --- | --- |
| `SHOWROOM_USER` | Instructor provisioning for another human cluster user. On reruns an existing assignment is reused. |
| `SHOWROOM_CONSOLE_URL` | Override the HTTPS console address when `oc whoami --show-console` cannot discover it. |
| `SHOWROOM_CONSOLE_EMBED_URL` | An HTTPS console endpoint the platform owner has configured to allow framing by this Showroom origin. The OCP Console tab otherwise opens the normal console in a new browser tab. An empty value restores that default. |
| `SHOWROOM_CLI_IMAGE`, `SHOWROOM_OAUTH_IMAGE` | Set both to accessible, matching OpenShift release component pullspecs when the installer cannot inspect the release. `RELEASE_REGISTRY_CONFIG` can supply the release-lookup registry credentials file. |
| `SHOWROOM_ROUTE_CA` | PEM CA bundle on the instructor workstation for a private ingress CA. Supply the same CA trust to participants' browsers. |

The portal image build downloads the checksum-verified ttyd 1.7.7 release from GitHub and pinned Python packages from PyPI. The app and NeMo images also install pinned Python packages; the NeMo build installs compiler packages from UBI RPM repositories. The CLI is staged from the cluster's CLI image. UBI and both release component images must be pullable by the build and participant project.

### Use the tabs

1. Open the printed Route at its root and sign in through the cluster's identity provider. Approve the workshop OAuth client on first use. An existing browser login can be reused depending on the cluster's identity-provider policy.
2. The **Terminal** tab starts Bash in `/workspace/lab` with an executable `oc`, `grlab` and editable participant sources. Run `oc whoami`, `oc config view --minify -o 'jsonpath={.contexts[0].context.namespace}'` and `grlab --help`. Confirm the username and project. The guide's **Run in terminal** buttons execute the marked examples in this same shell. Tab changes keep the shell open. Reloading the portal opens a new shell. Copy any edits you need to keep before replacing the Showroom pod.
3. Open **OCP Console** and compare the signed-in user in its menu. A console that allows this Showroom origin can be displayed in the workspace with `SHOWROOM_CONSOLE_EMBED_URL`. **Open console in a browser tab** remains available for identity-provider redirects and browsers with restrictive frame settings.
4. After Pre-1, **Banking playground** above the guide opens the banking application through the signed-in Route. The terminal can also call `http://grlab-agent:8080` directly. CLI acceptance checks run from the terminal against in-project Services and do not require `pods/exec` access.

### Identity and workspace boundaries

Each Route has its own OAuth client, exact participant assignment and encrypted session cookie. The proxy requests `user:info`, `user:check-access` and `role:edit:<project>:!`. The last scope permits the participant's existing edit rights, including the Secret used for the model credential, in this project. The gateway verifies the token against the OpenShift user API and matches the complete username before serving content or opening a terminal. It checks the browser origin for the terminal WebSocket and playground POST.

The proxy alone mounts its service-account client credential and serving TLS key. The shell receives the participant's scoped credential in a mode-0600 kubeconfig on a read-only memory volume. The gateway deletes the connection's file on disconnect and rechecks the identity during an open session. Only the TLS proxy is published. Use dedicated workshop projects with trusted project administrators. The workspace is an `emptyDir` and is recreated when the pod is replaced.

### RHDP-managed Showroom

The root `site.yml`, `ui-config.yml` and `content/en/antora.yml` follow the Dev Day repository layout. The managed tab configuration points to the provisioner's `/wetty` terminal and `${DOMAIN}` console. Enable the terminal in the RHDP provisioning role and configure its participant login and lab files there. Use RHDP's console-embedding integration when required. The standalone `./grlab showroom` installation above supplies its own OAuth, terminal and source files. Local HTML previews display the guide and downloads.

## Run the workshop

| Module | Command from the repository root | Expected result |
| --- | --- | --- |
| Pre-1 · Deploy the application | `./grlab pre-1` | Direct connection to the main LLM, working playground and synthetic balance tool |
| 1 · Deploy NeMo | `./grlab nemo` | Agent switches to the internal NeMo Service, with the `bootstrap` profile |
| 2 · Configure rules | `./grlab rules` | Editable banking input and output checks, one main model, zero classifier calls |
| 3 · Optional content safety | `./grlab content` | Private vLLM Service for Nemotron, input and output verdicts on top of the local checks |
| 4 · Test the application | `./grlab test` | Checks the active profile, including the browser stream and optional classifier reason |

For Pre-1, set `MAIN_BASE_URL` to the provider's actual OpenAI-compatible base URL ending in `/v1` and `MAIN_MODEL_ID` to its exact tool-capable model ID. Optionally set `MAAS_SUBSCRIPTION`. Provide a private one-line key file via `MAIN_API_KEY_FILE` or an existing `grlab-main` Secret with key `api-key`. Follow the hidden-prompt example in **Pre-1**. The deployer creates the Secret once. The protected app uses an internal dummy client key, and NeMo reads the real upstream key from the Secret. A cluster with private CAs must supply the provider trust chain to the app and NeMo images.

Use `./grlab test --expect rules` or `--expect content` for final signoff on a particular profile. The NeMo build installs compiler packages from UBI RPM repositories.

The participant app has a ClusterIP Service. For a local workstation, run `oc -n "$LAB_NAMESPACE" port-forward svc/grlab-agent 18080:8080` and open [the playground](http://127.0.0.1:18080/). Restart the port-forward if its selected pod is replaced.

## Source map

| Path | Purpose |
| --- | --- |
| `content/en/modules/ROOT/` | AsciiDoc lab pages, navigation and participant attachment |
| `showroom/www/`, `showroom/portal/` | Rendered guide, signed-in split view, terminal gateway and workspace startup |
| `app/` | Banking agent source, guarded LangGraph transport, synthetic tool, eight playground scenarios and image build |
| `proxy/` | Pinned NeMo server build |
| `policies/` | Editable flow lists, rules, deterministic actions and classifier prompts |
| `deploy/` | Namespace-scoped manifests for the guide, app, proxy, model and ingress policies |
| `grlab`, `scripts/lab.py`, `scripts/showroom.py`, `scripts/policy.py` | Executable CLI, portal provisioning, stage deployment and configuration rendering |
| `scripts/verify.py`, `scripts/probe_model.py`, `tests/` | In-cluster acceptance checks, GPU inference probe and repeatable local integration tests |
| `skills/deploy-guardrail-agent-showroom/SKILL.md` | OpenShift deployment skill |

The protected request path is **playground → LangGraph agent → NeMo → main LLM**. In the content profile, NeMo also calls **Nemotron Safety Guard 8B v3** for incoming and outgoing text. The generated answer and the safety verdict come from different model roles. The optional model uses the fork's weight revision and Red Hat AI Inference Server CUDA image. The guide and the agent work on plain OpenShift. This repository packages NeMo 0.24.1 as a namespaced Deployment to provide the HTTP tool-call passthrough used in the labs. The fork's TrustyAI CR, CPU topic classifier and tracing stack are documented in the source attribution.

## Rebuild or test locally

To rebuild the site and its downloadable code after an edit, install Node.js 20+, npm, Git and Python, then run `bash scripts/build-site.sh`. The source ZIP works as the build input. The script installs pinned Antora dependencies if needed and generates a temporary Git worktree. The theme bundle is included.

For the local integration suite, use Python 3.12, C++ build tools and a separate virtual environment for each runtime. The OpenShift exercise itself only needs Python and PyYAML on the participant machine.

~~~bash
python3.12 -m venv .test-agent
python3.12 -m venv .test-nemo
.test-agent/bin/pip install -r app/requirements.txt
.test-nemo/bin/pip install -r proxy/requirements.txt
GRLAB_AGENT_PYTHON="$PWD/.test-agent/bin/python" \
GRLAB_NEMO_BIN="$PWD/.test-nemo/bin/nemoguardrails" \
python tests/check_workshop.py
~~~

The suite runs the real agent, real NeMo server and mock OpenAI-compatible model endpoints on loopback. It checks tool use, credential and header routing, local decisions, optional input/output classifier calls, malformed verdicts and streaming. The in-cluster commands additionally exercise real serving infrastructure and model behavior. To test the portal separately, install `aiohttp==3.14.5` in a local Python environment and run `python3 tests/check_portal.py`. It exercises owner checks, browser-origin checks, WebSocket credential cleanup and the playground stream with local fixtures.

## Recovery and cleanup

Running `rules` after `content` reactivates the local policy. A failed model probe leaves the current proxy revision active. A failed policy rollout or acceptance check restores the preceding proxy spec when one exists. Inspect the failing stage before retrying. Compare allowed and refused responses, since an HTTP 200 can carry either outcome.

Once the rules-only tests pass, scale the optional GPU Deployment to zero to retain its cache and release the GPU. A subsequent `content` step scales it back up. Each resource created for the demo belongs to the chosen project. Remove the project only when the owner chooses to discard its workloads and data.

Sources, theme licensing and workshop adaptations appear in [NOTICE.md](NOTICE.md).
