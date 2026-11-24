# Guardrails for a banking agent on OpenShift

Deploy a working banking chatbot, place NVIDIA NeMo Guardrails in its model path, write local policy and optionally add a GPU-backed content-safety classifier. The ZIP contains the English Antora guide, its rendered HTML, a downloadable participant code bundle, the LangGraph/FastAPI application and playground, and the OpenShift manifests and deployment scripts.

## Open the guide

Open `showroom/www/modules/en/index.html` from the extracted ZIP. For a local HTTP preview, run `python3 -m http.server 8080 --directory showroom/www` in the repository root and open [the preview](http://127.0.0.1:8080/). The guide includes all participant commands and expected results.

## Publish the guide on OpenShift

Use an authenticated OpenShift CLI context and a dedicated, existing project with access to binary Docker builds, the internal registry, Deployments, Services, Routes and NetworkPolicies. Create the project with `oc new-project guardrail-lab-user1` if needed. Each participant uses their own project. The guide itself needs no model credentials.

~~~bash
export LAB_NAMESPACE=guardrail-lab-user1
oc whoami
oc whoami --show-server
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/lab.py showroom
~~~

Open the reported HTTPS Route. From the overview, participants can download the exercise code without access to the private source repository. For an RHDP-managed Showroom, publish this repository to a Git location the Showroom can read and point its content source at that repository and branch. The root `site.yml`, `ui-config.yml`, and `content/en/antora.yml` follow the Dev Day content layout. The managed shell supplies the OpenShift Console tab. The standalone Route serves the same rendered guide.

## Run the workshop

| Module | Command from the repository root | Expected result |
| --- | --- | --- |
| Pre-1 · Deploy the application | `python scripts/lab.py pre-1` | Direct connection to the main LLM, working playground and synthetic balance tool |
| 1 · Deploy NeMo | `python scripts/lab.py nemo` | Agent switches to the internal NeMo Service, with the `bootstrap` profile |
| 2 · Configure rules | `python scripts/lab.py rules` | Editable banking input and output checks, one main model, zero classifier calls |
| 3 · Optional content safety | `python scripts/lab.py content` | Private vLLM Service for Nemotron, input and output verdicts on top of the local checks |
| 4 · Test the application | `python scripts/lab.py test` | Checks the active profile, including the browser stream and optional classifier reason |

For Pre-1, set `MAIN_BASE_URL` to the provider's actual OpenAI-compatible base URL ending in `/v1` and `MAIN_MODEL_ID` to its exact tool-capable model ID. Optionally set `MAAS_SUBSCRIPTION`. Provide a private one-line key file via `MAIN_API_KEY_FILE` or an existing `grlab-main` Secret with key `api-key`. Follow the hidden-prompt example in **Pre-1**. The deployer creates the Secret once. The protected app uses an internal dummy client key, and NeMo reads the real upstream key from the Secret. A cluster with private CAs must supply the provider trust chain to the app and NeMo images.

Use `python scripts/lab.py test --expect rules` or `--expect content` for final signoff on a particular profile. The NeMo build installs compiler packages from UBI RPM repositories.

The participant app has a ClusterIP Service. Run `oc -n "$LAB_NAMESPACE" port-forward svc/grlab-agent 18080:8080` and open [the playground](http://127.0.0.1:18080/). Restart the port-forward if its selected pod is replaced.

## Source map

| Path | Purpose |
| --- | --- |
| `content/en/modules/ROOT/` | AsciiDoc lab pages, navigation and participant attachment |
| `showroom/www/` | Ready-to-serve HTML from the included RHDP theme |
| `app/` | Banking agent source, guarded LangGraph transport, synthetic tool, eight playground scenarios and image build |
| `proxy/` | Pinned NeMo server build |
| `policies/` | Editable flow lists, rules, deterministic actions and classifier prompts |
| `deploy/` | Namespace-scoped manifests for the guide, app, proxy, model and ingress policies |
| `scripts/lab.py`, `scripts/policy.py` | Stage deployment, configuration rendering and rollback |
| `scripts/verify.py`, `scripts/probe_model.py`, `tests/` | In-cluster acceptance checks, GPU inference probe and repeatable local integration tests |
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

Once the rules-only tests pass, scale the optional GPU Deployment to zero to retain its cache and release the GPU. A subsequent `content` step scales it back up. Each resource created for the demo belongs to the chosen project. Remove the project only when the owner chooses to discard its workloads and data.

Sources, theme licensing and workshop adaptations appear in [NOTICE.md](NOTICE.md).
