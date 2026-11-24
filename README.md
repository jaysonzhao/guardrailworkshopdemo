# Guardrails for a banking agent on OpenShift

Deploy a working banking chatbot, place NVIDIA NeMo Guardrails in its model path, write local policy and optionally add a GPU-backed content-safety classifier. The ZIP contains the English Antora guide, its rendered HTML, a downloadable participant code bundle, the LangGraph/FastAPI application and playground, and the OpenShift manifests and deployment scripts.

## Open the guide

Open `showroom/www/modules/en/index.html` from the extracted ZIP. For a local HTTP preview, run `python3 -m http.server 8080 --directory showroom/www` in the repository root and open [the preview](http://127.0.0.1:8080/). The guide includes all participant commands and expected results.

## Publish the guide on OpenShift

Use an authenticated instructor context and a dedicated, existing project with access to binary Docker builds, the internal registry, Deployments, Services, Routes and NetworkPolicies. Create the project with `oc new-project guardrail-lab-user1` if needed. Each participant uses their own project. The guide itself needs no model credentials. `./grlab showroom` prebuilds the trusted portal, agent and NeMo images as the instructor, removes their BuildConfigs, and grants the assigned participant the `restricted-v2` SCC only in this project. The participant deploys the prebuilt image digests and does not need BuildConfig strategy access.

~~~bash
export LAB_NAMESPACE=guardrail-lab-user1
oc whoami
oc whoami --show-server
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
./grlab showroom
~~~

Open the reported HTTPS Route and sign in with the cluster identity assigned to the project. By default the presenter’s interactive `oc whoami` owns the Showroom. When provisioning for a participant, set `SHOWROOM_USER` to their exact OpenShift username before running `./grlab showroom`, and grant that participant edit rights on the dedicated project. The terminal uses the participant’s OAuth credential, a session-scoped kubeconfig, the release-matched `oc` and an executable `./grlab` in `/workspace/lab`. The OCP Console tab opens the cluster console with the same login. Its default top-level presentation works with standard console framing policies. The guide’s *Run* buttons send commands to the hosted Terminal, and *Banking playground* opens the protected app after Pre-1.

The presenter needs access to the cluster release’s `cli` and `oauth-proxy` component pullspecs, the OpenShift OAuth server, ingress and service-serving certificates. For clusters with private CAs or restricted image access, see the deployment instructions in `skills/deploy-guardrail-agent-showroom/SKILL.md`. The RHDP-managed layout is also present in `site.yml`, `ui-config.yml` and `content/en/antora.yml`.

## Run the workshop

| Module | Command from the repository root | Expected result |
| --- | --- | --- |
| Pre-1 · Deploy the application | `./grlab pre-1` | Direct connection to the main LLM, working playground and synthetic balance tool |
| 1 · Deploy NeMo | `./grlab nemo` | Agent switches to the internal NeMo Service, with the `bootstrap` profile |
| 2 · Configure rules | `./grlab rules` | Editable banking input and output checks, one main model, zero classifier calls |
| 3 · Optional content safety | `./grlab content` | Private vLLM Service for Nemotron, input and output verdicts on top of the local checks |
| 4 · Test the application | `./grlab test` | Checks the active profile, including the browser stream and optional classifier reason |
| 5 · Optional cleanup | `./grlab cleanup` | Reconnects the app to the original model, verifies chat and tool use, then removes NeMo |
| Appendix · Rule catalog | `./grlab catalog choose` | Choose a category, inspect the example and control, then stage one rule |

For Pre-1, set `MAIN_BASE_URL` to the provider's actual OpenAI-compatible base URL ending in `/v1` and `MAIN_MODEL_ID` to its exact tool-capable model ID. Optionally set `MAAS_SUBSCRIPTION`. Provide a private one-line key file via `MAIN_API_KEY_FILE` or an existing `grlab-main` Secret with key `api-key`. Follow the hidden-prompt example in **Pre-1**. The deployer creates the Secret once. The protected app uses an internal dummy client key, and NeMo reads the real upstream key from the Secret. A cluster with private CAs must supply the provider trust chain to the app and NeMo images.

Use `./grlab test --expect rules` or `--expect content` for final signoff on a particular profile. The NeMo build installs compiler packages from UBI RPM repositories.

The hosted playground uses the signed-in Showroom Route. In its Terminal, use `http://grlab-agent:8080`. Workstation users can run `oc -n "$LAB_NAMESPACE" port-forward svc/grlab-agent 18080:8080` and open [the playground](http://127.0.0.1:18080/).

## Choose and demonstrate a rule

Open [the appendix](showroom/www/modules/en/appendix-rules.html). Select a category and open a rule to see the input or candidate reply, its expected result, an allowed control and ready-to-run commands. The 53 choices cover 27 deterministic banking rules, 23 model categories and three classifier controls. Module 2 uses local checks. Module 3 initially configures S1–S13, with S14–S23 individually available. The appendix also indexes the 33 manifest families and 78 stock flow names in NeMo 0.24.1 for authors extending the workshop.

~~~bash
./grlab catalog list customer-care
./grlab catalog sample marketing-consent
./grlab catalog add marketing-consent
./grlab catalog apply --dry-run
./grlab catalog apply
./grlab catalog test marketing-consent
~~~

The first example blocks “Enroll this customer in marketing without consent.” Its control asks how a customer changes marketing preferences. Use `catalog remove marketing-consent` and then `catalog apply` to reverse this addition. To assemble a policy from scratch, stage `./grlab catalog baseline empty`, add the first rule and preview before publishing. To restore the shipped choices, stage `./grlab catalog baseline defaults` and apply. The local selection lives in `policies/selection.yaml`. Use `catalog status` to compare local overrides with the running profile. After changing an output rule, use the supplied candidate reply to obtain repeatable evidence.

## Source map

| Path | Purpose |
| --- | --- |
| `content/en/modules/ROOT/` | AsciiDoc lab pages, navigation and participant attachment |
| `showroom/www/` | Ready-to-serve HTML from the included RHDP theme |
| `app/` | Banking agent source, guarded LangGraph transport, synthetic tool, eight playground scenarios and image build |
| `proxy/` | Pinned NeMo server build |
| `policies/` | Catalog, saved choices, deterministic banking checks, flow lists and classifier prompts |
| `deploy/` | Namespace-scoped manifests for the guide, app, proxy, model and ingress policies |
| `scripts/lab.py`, `scripts/policy.py` | Stage deployment, configuration rendering and rollback |
| `scripts/verify.py`, `scripts/probe_model.py`, `tests/` | HTTP acceptance checks, model-service inference probe and repeatable local integration tests |
| `skills/deploy-guardrail-agent-showroom/SKILL.md` | OpenShift deployment skill |

The protected request path is **playground → LangGraph agent → NeMo → main LLM**. In the content profile, NeMo also calls **Nemotron Safety Guard 8B v3** for incoming and outgoing text. The generated answer and the safety verdict come from different model roles. The optional model uses the fork's weight revision and Red Hat AI Inference Server CUDA image. The guide and the agent work on plain OpenShift. This repository packages NeMo 0.24.1 as a namespaced Deployment to provide the HTTP tool-call passthrough used in the labs. The fork's TrustyAI CR, CPU topic classifier and tracing stack are documented in the source attribution.

## Rebuild or test locally

To rebuild the site and its downloadable code after an edit, install Node.js 20+, npm, Git and Python, then run `bash scripts/build-site.sh`. A clean ZIP works as the input. The script installs pinned Antora dependencies if needed and generates a temporary Git worktree. The theme bundle is included.

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

The suite runs the real agent, real NeMo server and mock OpenAI-compatible model endpoints on loopback. It checks tool use, credential and header routing, local decisions, optional input/output classifier calls, malformed verdicts and streaming. The in-cluster commands additionally exercise real serving infrastructure and model behavior.

## Recovery and cleanup

Running `rules` after `content` reactivates the local policy. A failed model probe leaves the current proxy revision active. A failed policy rollout or acceptance check restores the preceding proxy spec when one exists. Inspect the failing stage before retrying. Compare allowed and refused responses, since an HTTP 200 can carry either outcome.

[Module 5](showroom/www/modules/en/module-5.html) keeps the app serving after the workshop. Preview with `./grlab cleanup --dry-run`, then use `./grlab cleanup`. The CLI reconnects the app to the original model using its existing settings and Secret and tests both reset guidance and the $2,450.00 fixture balance before removing owned NeMo resources. If that first gate fails, it restores the prior app connection. Repeat `./grlab test --expect direct` after cleanup. The default retains the optional classifier and cache. Add `--remove-model` to release the optional model workload and GPU. Also supply `--delete-model-cache` only when you intend to discard the cached weights. You can redeploy with `./grlab nemo` and `./grlab rules`.

Sources, theme licensing and workshop adaptations appear in [NOTICE.md](NOTICE.md).
