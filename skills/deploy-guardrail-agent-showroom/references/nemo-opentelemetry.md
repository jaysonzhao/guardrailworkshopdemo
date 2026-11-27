# Module 7 · Trace NeMo Guardrails with OpenTelemetry

Use this optional module to follow a request through the banking guardrails, identify the rule that stopped it, and measure how much time the main model used. The examples use fictional banking data. Module 1 supplies the running proxy. Module 2 supplies the credential rule for the full comparison. The optional safety model and Module 6 MLflow are independent. This guide accompanies the executable CLI and manifests in `guardrail-agent-showroom.zip`.

## Instructor setup

Use the full repository, an authenticated OpenShift CLI and the participant’s project. The instructor needs access to binary builds, the internal image registry, ConfigMaps, Deployments, Services and NetworkPolicies in that project. The build uses UBI repositories and PyPI. The private demo also pulls Jaeger 2.23.0. For an existing installation, save terminal-local edits before republishing the Showroom with the updated code. The updated portal includes the **NeMo traces** toolbar link.

```bash
export LAB_NAMESPACE=your-workshop-project
oc whoami
./grlab otel precheck
```

Precheck is read-only and can run before Module 1. It looks for the operator APIs and instances of TempoStack, TempoMonolithic, Kiali and OpenTelemetry Collector, reports their visible status, lists OTLP/gRPC Service and admitted UI Route candidates, and checks the workshop proxy, private Jaeger, image, settings and instructor permissions. For a known observability project, limit shared discovery with `./grlab otel precheck --observability-namespace "$TRACING_NAMESPACE"`. It also checks common pod labels for standalone Tempo and Kiali installations. With `LAB_NAMESPACE` unset, precheck uses the current `oc` project, including `default` or a platform namespace. Choose a dedicated participant project before running prepare. A scoped login can see fewer resources. An access restriction does not establish that an operator is missing.

| Finding | Workshop choice |
| --- | --- |
| No Tempo/Kiali operator APIs, or no tracing instances in a fully visible scope | Use private Jaeger. Tempo, Kiali and the Collector operator are optional. |
| A Tempo instance is present | Check readiness, receiver, query API and tenant with the operator. Pass verified endpoints explicitly. |
| Kiali is present on its own | Kiali displays service-mesh traces from a configured tracing backend. Use private Jaeger or an existing backend that can ingest NeMo spans. |
| Cluster-wide reads are restricted | An instructor can rerun the command. A participant can use an already-prepared project backend. |

For the private workshop path, run:

```bash
./grlab otel prepare
```

Prepare repeats the precheck before making changes. The default builds the SDK-enabled proxy image, starts a project-local Jaeger instance and records both endpoints in `ConfigMap/grlab-nemo-otel-settings`. Its gRPC receiver on 4317 accepts traces from the NeMo proxy. The Showroom pod can reach the query service on 16686. The browser uses the existing signed-in Showroom Route. A separate public Jaeger Route is unnecessary. The demo holds up to 1,000 traces in memory and starts with an empty store when its pod is replaced.

### Use a shared collector and Tempo

Get the receiver and query URLs from the operator of the shared tracing service. For a browser link, identify the existing authenticated tracing UI Route, for example a Grafana or Jaeger Route. The CLI discovers its admitted HTTPS host and configured path. Check the shared UI in a signed-out browser, then sign in as the participant and verify access to the workshop tenant. Run this command in the instructor’s context, then have the participant run `enable`, including after a backend change.

```bash
./grlab otel prepare \
  --endpoint "$OTLP_GRPC_URL" \
  --query-url "$TEMPO_QUERY_BASE_URL" --query-api tempo \
  --ui-route "$TRACING_UI_ROUTE"
```

| Input | How the module uses it |
| --- | --- |
| `--endpoint` | A full OTLP/gRPC receiver URL, including the scheme and the port used by the receiver. It can point to an OpenTelemetry Collector or Tempo distributor. |
| `--query-url` | Base of the HTTP read API, including any tenant gateway prefix. The probe appends `/api/traces/<id>` for Tempo or `/api/v3/traces/<id>` for a Jaeger v3 query service. |
| `--ui-route NAMESPACE/NAME` | Instructor reads the Route once. Use `--ui-path /path` to override its browser path. The participant opens the saved link and signs in to that UI. |
| `--ca-configmap NAME` | Project ConfigMap with PEM receiver CA under `ca.crt`. The CLI mounts it in the NeMo container. |
| `--headers-secret NAME` | Project Secret with key `headers`, containing OTLP’s comma-separated, URL-encoded header values. An HTTPS receiver is required. The pod references the Secret by name. |

For private collector trust, an instructor can supply the receiver CA and exporter headers from files. Use the header file produced for the workshop’s writer identity. Protect it and any query-token file with mode 0600. A tenant header required on ingestion belongs in the exporter headers Secret. A tenant required on reads belongs in `otel test --tenant`. Permit the two paths in the cluster’s network policies.

```bash
oc -n "$LAB_NAMESPACE" create configmap grlab-otel-receiver-ca \
  --from-file=ca.crt="$RECEIVER_CA_FILE" --dry-run=client -o yaml | oc -n "$LAB_NAMESPACE" apply -f -
oc -n "$LAB_NAMESPACE" create secret generic grlab-otel-writer \
  --from-file=headers="$OTLP_HEADERS_FILE" --dry-run=client -o yaml | oc -n "$LAB_NAMESPACE" apply -f -
./grlab otel prepare --endpoint "$OTLP_GRPC_URL" \
  --query-url "$TEMPO_QUERY_BASE_URL" --query-api tempo \
  --ui-route "$TRACING_UI_ROUTE" --ca-configmap grlab-otel-receiver-ca \
  --headers-secret grlab-otel-writer
```

## Participant exercise

In the Showroom Terminal, start with Module 1 running. For a two-request comparison, use the Module 2 or 3 policy with the `credential` rule selected. After Module 5 cleanup, `./grlab nemo` restores the proxy and `./grlab rules` activates the rules profile.

```bash
./grlab otel enable
./grlab otel status
./grlab otel test
./grlab otel link
```

A bootstrap workshop can run the allowed-only control and keep its original policy. The two-case exercise uses an existing rules profile; enabling tracing itself makes no rule selection. The CLI copies the mounted policy, enables the NeMo adapter and switches the existing proxy to the instrumented image. The rules, model settings and Secret references follow the current workshop profile. Active tracing carries forward when you change profiles or publish an appendix rule. The default exports span metadata. It includes rail names and decisions, action names and durations, model identifiers and available token counts. On fictional data, use `./grlab otel enable --capture-content` for a content-capture exercise. Run `./grlab otel enable` again to return to metadata mode. Previously recorded traces retain their original contents.

Open **NeMo traces** in the Showroom or follow the URL printed by `link`. Select service `nemo-guardrails`. The test supplies a fresh W3C trace ID for each request and retrieves that ID from storage. The private demo prints direct links to those traces. For a shared UI, open the saved link and search for the printed trace ID.

| Synthetic request | What to inspect |
| --- | --- |
| “How do I reset my online banking password?” | Find the HTTP span for `POST /v1/chat/completions`, expand its `guardrails.request` child and compare the input rail, model call and output rail durations. |
| “My OTP is 000000.” | The privacy refusal and an input rail with `rail.name=banking input gate` and `rail.stop=true` explain the outcome. With the default rules the main model is never called. |

Jaeger can display **Incomplete** for the CLI exercise because the test supplies a synthetic parent span ID and only the NeMo server exports spans. Jaeger can include the native request in its GenAI summary. Count actual model spans by their model identifier and check the stopped input rail to establish whether the main model ran.

A bootstrap-only proxy runs the allowed control. The CLI announces the skipped credential case if that rule is inactive. A protected shared query service can be tested with its reader identity. Supply only the options your backend requires.

```bash
./grlab otel test --query-ca-file "$QUERY_CA_FILE" \
  --query-token-file "$QUERY_TOKEN_FILE" --tenant "$TEMPO_TENANT"
```

For a workstation test, start each forward in its own terminal against the workshop project, then run the probe in a third terminal. Open the local Jaeger URL for the same evidence.

```bash
oc -n "$LAB_NAMESPACE" port-forward svc/grlab-rails 18000:80
oc -n "$LAB_NAMESPACE" port-forward svc/grlab-jaeger 16686:16686
./grlab otel test --proxy http://127.0.0.1:18000/v1 \
  --query-url http://127.0.0.1:16686/nemo-traces
```

Local viewer: <http://localhost:16686/nemo-traces/>.

## Mapping to the OpenShift AI 3.5 documentation

This Showroom uses `Deployment/grlab-rails` and pinned NeMo 0.24.1. Its optional image initializes an OpenTelemetry SDK provider, instruments the HTTP request, and registers a batch OTLP/gRPC exporter before NeMo starts. The enabled configuration adds this block inside the existing `config.yaml`.

```yaml
tracing:
  enabled: true
  span_format: opentelemetry
  enable_content_capture: false
  adapters:
    - name: OpenTelemetry
```

For a separate OpenShift AI **operator-managed** instance, identify the active configuration with `oc -n "$LAB_NAMESPACE" get nemoguardrails "$NEMO_CR" -o yaml`. Add the block to the existing ConfigMap referenced by `spec.nemoConfigs[].configMaps`, preserving its model definitions and flow files. If that ConfigMap is immutable, create a versioned copy and update the matching reference. Merge the following entries by name into the existing custom resource’s `spec.env`. Provision a Secret named `guardrails-telemetry` with the actual receiver URL in `endpoint` for this example. Keep the instance’s model credential references and authentication configuration.

```yaml
# Excerpt of trustyai.opendatahub.io/v1alpha1, kind: NemoGuardrails
spec:
  env:
    - name: OTEL_EXPORTER_OTLP_ENDPOINT
      valueFrom:
        secretKeyRef:
          name: guardrails-telemetry
          key: endpoint
    - name: OTEL_SERVICE_NAME
      value: nemo-guardrails
    - name: OTEL_EXPORTER_OTLP_PROTOCOL
      value: grpc
    - name: OTEL_METRICS_EXPORTER
      value: none
```

Apply the complete updated custom resource through its existing deployment process and watch `oc -n "$LAB_NAMESPACE" get nemoguardrails "$NEMO_CR" -w` for `Ready`. Send a synthetic request using that instance’s normal authenticated client, then find the HTTP and rail spans under service `nemo-guardrails`. The workshop CLI addresses the Showroom Deployment. [Red Hat OpenShift AI 3.5, NeMo Guardrails observability, span types and performance](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/enabling_ai_safety_with_guardrails/enabling-ai-safety-with-nemo-guardrails_nemo-guardrails#configuring-observability-for-nemo-guardrails-with-opentelemetry_nemo-guardrails)

[Tempo installation and instance types](https://docs.redhat.com/en/documentation/red_hat_openshift_distributed_tracing_platform/3.10/html/installing_distributed_tracing/distr-tracing-tempo-installing) · [Kiali tracing provider settings](https://kiali.io/docs/configuration/kialis.kiali.io/)

## Diagnose and finish

| Symptom | Next check |
| --- | --- |
| The chat succeeds but the trace ID is missing | Check `oc -n "$LAB_NAMESPACE" logs deploy/grlab-rails -c nemo --tail=80` for exporter failures. Verify the gRPC receiver, certificate trust, network path, storage and query tenant. Allow for batching. |
| Query returns 401 or 403 | Use a reader authorized for the target tenant, its HTTPS query API and its trust bundle. The writer Secret and browser session serve different clients. |
| A shared backend intermittently misses a test | The test sets the W3C sampled flag. Configure the collector’s sampling policy to retain the workshop service for the exercise. On higher-volume workloads, tune sampling and compare rail latency with a baseline. |
| Browser opens a login page or a project has no Showroom Route | Sign in with the UI’s normal identity. For the private demo without a Showroom Route, use the port-forward above. |
| New runtime is unhealthy | Inspect proxy rollout events, build logs, model connectivity and certificate configuration. A failed enable rollout attempts to restore the previous pod template. Recheck `./grlab otel status` and `./grlab test`. |

```bash
./grlab otel disable
./grlab test
```

Disable restores the regular proxy image and keeps the policy active at the time of disabling. Module 5 can still switch the app to direct model calls. Those requests bypass NeMo and its trace instrumentation. The optional app-level MLflow module can continue recording them. To discard the local Jaeger history and remove its workshop resources, the instructor runs `./grlab otel remove-demo`.
