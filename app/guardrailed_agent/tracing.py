import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from os import getenv
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger("tracing")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

_TRACE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TRACE_REDACTIONS = (
    (
        "credential",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~-]{16,})\b"
            r"|\b(?:password|api[_ -]?key|access[_ -]?token|secret)\b\s*(?::|=)\s*[\"']?\S{4,}"
            r"|\b(?:password|api[_ -]?key|access[_ -]?token|secret)\b\s+is\s+"
            r"(?!(?:never|not|reset|change|update|secure|private|confidential|sensitive|protected|required|needed|optional|compromised|stolen|lost|forgotten|expired|invalid|incorrect|blocked|locked|strong|weak|used|shared|stored|visible|available|safe|usually|generally|typically|currently|the|a|an)\b)"
            r"(?!(?:open|select|click|choose|follow|visit|use|go|change|update|contact|reset)\b\s+\w+\b)\S{4,}",
            re.I,
        ),
    ),
    (
        "otp",
        re.compile(
            r"\b(?:otp|pin|one[- ]time (?:password|passcode)|verification code)\b"
            r"(?:\s+(?:is|code))?\s*[:=]?\s*\d{4,8}\b",
            re.I,
        ),
    ),
    ("hkid", re.compile(r"(?<![A-Z0-9])[A-Z]{1,2}\s*\d{3}\s*\d{3}\s*\([0-9A]\)(?![A-Z0-9])", re.I)),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("card", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?852[ .-]?)?[235689]\d{3}[ .-]?\d{4}(?!\d)")),
    ("account", re.compile(r"\b(?:account(?:\s+(?:number|no\.?))?|a/c)\s*(?:is\b|:|=)?\s*\d(?:[\d -]{4,}\d)", re.I)),
)
_TRACE_TEXT_LIMIT = 4000
_PENDING_GUARDRAIL_REJECTION: ContextVar[tuple[str, str] | None] = ContextVar(
    "pending_guardrail_rejection", default=None
)


def _sanitize_trace_value(value):
    """Keep useful trace payloads while redacting common customer secrets."""
    if isinstance(value, str):
        result = value
        for label, pattern in _TRACE_REDACTIONS:
            result = pattern.sub(f"[REDACTED:{label}]", result)
        return result[:_TRACE_TEXT_LIMIT]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _sanitize_trace_value(item)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_trace_value(item) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_trace_value(str(value))


def set_agent_request_outputs(span, outputs) -> None:
    """Set a privacy-scrubbed root-span output without affecting the request."""
    if span is None:
        return
    try:
        span.set_outputs(_sanitize_trace_value(outputs))
    except Exception as exc:
        logger.debug("[Tracing] Could not set request outputs (%s)", type(exc).__name__)


@contextmanager
def agent_request_span(inputs=None):
    """Provide a request-scoped MLflow parent span with scrubbed input data."""
    if not getenv("MLFLOW_TRACKING_URI"):
        yield None
        return

    import mlflow

    with mlflow.start_span(name="guardrailed_agent_request") as span:
        try:
            if inputs is not None:
                span.set_inputs(_sanitize_trace_value(inputs))
        except Exception as exc:
            logger.debug("[Tracing] Could not set request inputs (%s)", type(exc).__name__)
        yield span


def clear_pending_guardrail_rejection() -> None:
    _PENDING_GUARDRAIL_REJECTION.set(None)


def flush_pending_guardrail_rejection() -> None:
    """Persist marker-derived reason codes after the LangGraph trace closes."""
    pending = _PENDING_GUARDRAIL_REJECTION.get()
    _PENDING_GUARDRAIL_REJECTION.set(None)
    if pending:
        record_guardrail_rejection(
            reason=pending[0], rail=pending[1], allow_completed_trace=True
        )
        _PENDING_GUARDRAIL_REJECTION.set(None)


def record_guardrail_rejection(
    reason: str, rail: str, *, allow_completed_trace: bool = False
) -> None:
    """Attach a safe, categorical guardrail decision to the active MLflow trace.

    The tags intentionally accept only short codes. Never pass prompt text,
    model output, identifiers, or the raw NeMo error message here.
    """
    if not getenv("MLFLOW_TRACKING_URI"):
        return

    if not isinstance(reason, str) or not _TRACE_CODE_RE.fullmatch(reason):
        reason = "unclassified"
    if not isinstance(rail, str) or not _TRACE_CODE_RE.fullmatch(rail):
        rail = "unknown_rail"

    tags = {
        "guardrail.outcome": "rejected",
        "guardrail.reason": reason,
        "guardrail.rail": rail,
    }
    _PENDING_GUARDRAIL_REJECTION.set((reason, rail))
    try:
        import mlflow

        try:
            mlflow.update_current_trace(tags=tags)
        except Exception:
            # Some integrations close the LangGraph trace before the API
            # handler sees a NeMo block. Only the API exception handler opts in
            # to tagging a completed trace; marker handling requires an active
            # trace to avoid ever tagging a stale request.
            if not allow_completed_trace:
                return
            trace_id = mlflow.get_last_active_trace_id(thread_local=True)
            if trace_id:
                mlflow.flush_trace_async_logging()
                for key, value in tags.items():
                    mlflow.set_trace_tag(trace_id, key, value)
        span = mlflow.get_current_active_span()
        if span:
            for key, value in tags.items():
                span.set_attribute(key, value)
    except Exception as exc:
        # Tracing metadata must never turn a guardrail refusal into an error.
        logger.debug(
            "[Tracing] Could not attach guardrail rejection tags (%s)",
            type(exc).__name__,
        )


def check_mlflow_health(
    mlflow_tracking_uri: str, max_wait_time: int = 5, retry_interval: int = 1
) -> None:
    """
    Check MLflow health by trying the /health endpoint. If it fails, retry for a certain duration before giving up.
    args:
        mlflow_tracking_uri: base URI of the MLflow server
        max_wait_time: total time to keep retrying before giving up (in seconds)
        retry_interval: time to wait between retries (in seconds)
    """
    import requests

    mlflow_health_endpoint = "/health"
    mlflow_url = f"{mlflow_tracking_uri.rstrip('/')}{mlflow_health_endpoint}"

    # Build auth and TLS config. This is only needed for
    # the raw requests.get() health check. The MLflow client SDK
    # handles these env vars natively for all other calls.
    # See docs/mlflow-openshift-auth-and-tls.md for full details.
    headers = {}
    token = getenv("MLFLOW_TRACKING_TOKEN")
    tracking_auth = getenv("MLFLOW_TRACKING_AUTH", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        logger.debug("[Health Check] Using MLFLOW_TRACKING_TOKEN for authentication")
    elif tracking_auth in ("kubernetes", "kubernetes-namespaced"):
        sa_token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        if not os.path.isfile(sa_token_path):
            logger.warning(
                "[Health Check] MLFLOW_TRACKING_AUTH=%s but SA token not found at %s",
                tracking_auth,
                sa_token_path,
            )
        else:
            with open(sa_token_path) as f:
                headers["Authorization"] = f"Bearer {f.read().strip()}"
            logger.debug(
                "[Health Check] Using MLFLOW_TRACKING_AUTH=%s for authentication",
                tracking_auth,
            )

    cert_path = getenv("MLFLOW_TRACKING_SERVER_CERT_PATH")
    if getenv("MLFLOW_TRACKING_INSECURE_TLS", "").lower() in ("true", "1", "yes"):
        verify = False
        logger.warning(
            "[Health Check] TLS verification disabled (MLFLOW_TRACKING_INSECURE_TLS=true). "
            "Consider using MLFLOW_TRACKING_SERVER_CERT_PATH instead."
        )
    elif cert_path:
        if not os.path.isfile(cert_path):
            logger.warning(
                "[Health Check] MLFLOW_TRACKING_SERVER_CERT_PATH does not exist: %s — falling back to system CAs",
                cert_path,
            )
            verify = True
        else:
            verify = cert_path
            logger.debug("[Health Check] TLS verify using cert: %s", cert_path)
    else:
        verify = True

    start_time = time.time()

    while True:
        remaining = max_wait_time - (time.time() - start_time)
        if remaining <= 0:
            logger.error(
                f"MLflow server is unavailable after {max_wait_time} seconds of checking."
            )
            raise RuntimeError(
                "MLflow server is unavailable. Please start the server or check the URI."
            )

        try:
            response = requests.get(
                mlflow_url, timeout=min(5, remaining), headers=headers, verify=verify
            )
            if response.status_code == 200:
                logger.info(
                    f"MLflow health check passed at {mlflow_url} with status code {response.status_code}."
                )
                return  # Success, exit the function without error
            else:
                logger.warning(
                    f"MLflow returned status code {response.status_code} at {mlflow_url}\n"
                    f"  Status Code: {response.status_code}\n"
                    f"  Reason: {response.reason}"
                )
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to connect to MLflow at {mlflow_url}: {e}")

        logger.warning(f"Retrying in {retry_interval} seconds...")
        time.sleep(retry_interval)


def enable_tracing() -> None:
    """
    Enable MLflow tracing if MLFLOW_TRACKING_URI is set.

    Behavior:
    1. If MLFLOW_TRACKING_URI is not set: tracing is skipped.
    2. If MLFLOW_TRACKING_URI is set:
       - Try to connect to the server.
       - If the server is reachable: tracing is enabled.
       - If the server is unreachable: log a warning and continue without tracing.
    """
    load_dotenv()
    tracking_uri: Optional[str] = getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        logger.info("[Tracing] MLFLOW_TRACKING_URI not set. Tracing is disabled.")
        return

    try:
        import mlflow
        import mlflow.langchain
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "MLFLOW_TRACKING_URI is set but mlflow is not installed. "
            "Install it with: uv sync --extra tracing"
        ) from e

    # Check if server is reachable
    try:
        try:
            health_check_timeout = int(getenv("MLFLOW_HEALTH_CHECK_TIMEOUT", "5"))
        except ValueError:
            health_check_timeout = 5
        check_mlflow_health(
            mlflow_tracking_uri=tracking_uri, max_wait_time=health_check_timeout
        )
        logger.info(f"[Tracing] MLflow server is reachable at {tracking_uri}")
    except RuntimeError as e:
        logger.warning(
            f"[Tracing] MLflow server is unreachable at {tracking_uri}. "
            f"Tried connecting for {health_check_timeout}s. Continuing without tracing. Error: {e}"
        )
        return

    # Server is reachable → enable tracing
    try:
        mlflow.set_tracking_uri(tracking_uri)
        experiment_name: str = getenv(
            "MLFLOW_EXPERIMENT_NAME", "default-agent-experiment"
        )
        mlflow.set_experiment(experiment_name)
        mlflow.config.enable_async_logging()

        # LangGraph invokes async chains. Inline tracing preserves the active
        # manual request span as the root of the autologged graph trace.
        mlflow.langchain.autolog(run_tracer_inline=True)

        logger.info(
            f"[Tracing Enabled] MLflow -> {tracking_uri}, Experiment: {experiment_name}"
        )
    except Exception as e:
        logger.warning(
            f"[Tracing] Failed to configure MLflow tracing at {tracking_uri}. "
            f"Continuing without tracing. Error: {e}"
        )
