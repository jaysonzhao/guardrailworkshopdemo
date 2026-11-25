# Module 6 · Observe the banking app in OpenShift AI 3.5.x

The optional module connects the workshop's banking app to the shared, operator-managed MLflow tracking server. Start after Pre-1 in direct mode, with NeMo active, or after Module 5. Normal chats produce traces. The verification command also writes a named run, with latency and span count, that participants can open in the dashboard's integrated Experiments table.

## Prepare the workshop project

Use the revised full repository, an authenticated instructor CLI and the assigned project in `LAB_NAMESPACE`. To refresh an older hosted Showroom, save any participant edits and run `./grlab showroom` from the new ZIP. Its pod has an ephemeral Terminal workspace.

Before changing anything, inspect the environment:

```bash
./grlab mlflow precheck
```

This command is read-only. It checks the DataScienceCluster MLflow Operator state, MLflow API, shared server availability, dashboard ingress, project configuration and app tracing annotations. Use instructor cluster read permissions for complete platform checks. A participant Terminal with project-only access reports cluster-wide items as unknown and still checks the project-local items.

```bash
oc whoami
oc project -q
./grlab mlflow platform
./grlab mlflow prepare
```

A cluster administrator runs `platform` to inspect the shared `MLflow/mlflow` instance and wait for availability. If MLflow is missing and the instructor explicitly chooses to provision a demo server on a dedicated workshop cluster, run `./grlab mlflow platform --create-demo`. This enables `spec.components.mlflowoperator.managementState: Managed` and creates a missing server with SQLite and a 10 GiB RWO PVC. Use a default StorageClass with dynamic provisioning. The file-backed demo does not set `serveArtifacts`: this module stores trace spans in the MLflow backend database and writes no binary artifacts. Red Hat AI 3.5 does not support artifact serving with `file://` storage when `spec.storage` is configured. Use supported object storage before enabling artifact serving for a workflow that needs artifacts. Existing servers retain their database and artifact configuration. Production installations can use the platform's existing PostgreSQL and object storage. Run `platform` once per cluster and `prepare` once per project or when the gateway, SDK image or CA changes.

Prepare reads the admitted OpenShift Route's host. The default lookup is:

```bash
oc -n openshift-ingress get route data-science-gateway \
  -o jsonpath='{.spec.host}{"\n"}'
```

To retrieve the project link before installing anything, run `./grlab mlflow link` with an identity that can read the gateway Route. To explicitly re-discover a changed or custom Route run `./grlab mlflow link --route NAMESPACE/NAME`.

On a Gateway-only installation it can use a single concrete HTTPS listener of `Gateway/data-science-gateway`. For a custom Route, pass the resource name as `--route NAMESPACE/NAME` to both commands. Choose the canonical dashboard ingress serving both the dashboard and the SDK path. The script reads the host from that object. An old dashboard redirect URL can send SDK requests to a login page. The app's network policy and cluster routing must allow it to reach the gateway and to verify its certificate.

For a private ingress CA:

```bash
oc -n "$LAB_NAMESPACE" create configmap grlab-ai-ingress-ca \
  --from-file=ca-bundle.crt=./gateway-ca.pem --dry-run=client -o yaml | oc -n "$LAB_NAMESPACE" apply -f -
./grlab mlflow prepare --ca-configmap grlab-ai-ingress-ca
```

Combine `--ca-configmap` with `--route` when needed. Reuse the same options when preparing that project again. Prepare builds the pinned MLflow 3.13.0 SDK image. It grants the app's dedicated `grlab-mlflow-writer` service account `get/list/create/update` on `experiments.mlflow.kubeflow.org` in this project and grants the assigned human user read access. Without a Showroom assignment, provide `--viewer USER`. The human also needs access to the project and the OpenShift AI dashboard. The application uses the projected service account token with `kubernetes-namespaced` authentication. No participant token is copied into its environment.

## Record and open a run

Run in the signed-in Showroom Terminal:

```bash
./grlab mlflow enable
./grlab mlflow test
./grlab mlflow link
```

The test sends a fictional password-reset question to the banking app. The response supplies the root trace ID. The app reads the trace from MLflow and writes a run named `banking-smoke-…` in `guardrail-banking-agent`. Look for a PASS line, the trace and run IDs and the full **Open this run in OpenShift AI** URL. From a workstation, use a separate `oc -n "$LAB_NAMESPACE" port-forward svc/grlab-agent 18080:8080` and pass `--agent http://127.0.0.1:18080` to the test.

| Where to click | What opens |
| --- | --- |
| **MLflow in OpenShift AI** beside **Banking playground** | The integrated experiment's runs. The project Experiments page opens if the app or experiment is unavailable. |
| The URL printed by `./grlab mlflow link` | The live dashboard Route and this project's Experiments list. |
| The URL printed by `./grlab mlflow test` | The exact run that recorded this request, with its workspace selected. |

In the dashboard, use **Develop & train → Experiments → guardrail-banking-agent**. Inspect `request_latency_ms`, `span_count`, `workshop.backend` and `workshop.trace_id`. Open the experiment's trace view if available, or use its **Launch MLflow** control for the standalone trace details. The root span is `guardrailed_agent_request`. For a guarded comparison, activate Module 2, repeat the test and try its fictional OTP case in the banking playground. A matched rule can attach `guardrail.outcome=rejected`, `guardrail.reason` and `guardrail.rail`. LangChain child spans can include prompts and tool results. Use synthetic data.

| URL purpose | Discovered path |
| --- | --- |
| SDK tracking | `https://<route-host>/mlflow` |
| Integrated project | `https://<route-host>/develop-train/mlflow/experiments?workspace=<project>` |
| Integrated run | `https://<route-host>/develop-train/mlflow/experiments/<experiment-id>/runs/<run-id>?workspace=<project>` |

## Disable tracing or diagnose a missing run

```bash
./grlab mlflow disable
./grlab test
```

Disable restores the workshop app's original image and token-free service account and keeps the active model path. Historical MLflow data and shared infrastructure remain available. Module 5 independently removes NeMo and can keep tracing enabled.

When a chat succeeds without a trace, check the agent's startup log, project and CA. A browser login page is not evidence of a successful SDK request. Use the authenticated readback in `mlflow test`.

```bash
oc -n "$LAB_NAMESPACE" logs deploy/grlab-agent -c agent --tail=80
oc -n "$LAB_NAMESPACE" get configmap grlab-mlflow-settings -o jsonpath='{.data.MLFLOW_TRACKING_URI}{"\n"}'
oc auth can-i update experiments.mlflow.kubeflow.org -n "$LAB_NAMESPACE" \
  --as="system:serviceaccount:$LAB_NAMESPACE:grlab-mlflow-writer"
```

The impersonation check is for an instructor with that permission. For a workspace or dashboard 403, verify the reader grant and sign in as the assigned user. For “workspace not found”, an instructor can inspect the server’s optional namespace filter with `oc get mlflows.mlflow.opendatahub.io mlflow -o jsonpath='{.spec.workspaceLabelSelector}'` and compare it with the project labels. For a certificate error, supply the issuing CA to prepare. After a repair, rerun enable, restart the agent if its configuration is unchanged, and repeat the smoke test. If a custom appendix rule blocks the test question, review that rule and its allowed example.

[Red Hat installation and storage](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-mlflow_mlflow) · [SDK authentication and versions](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-and-authenticating-mlflow-sdk_mlflow) · [Integrated experiments and runs](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/track-and-compare-mlflow-experiments_mlflow) · [Dashboard link implementation on the 3.5 release](https://github.com/red-hat-data-services/odh-dashboard/blob/080eae80a6a1e66348b0daae679d386b7a273be5/frontend/src/routes/pipelines/mlflow.ts)
