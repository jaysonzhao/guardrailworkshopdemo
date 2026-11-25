import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from os import getenv


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


_EXPERIMENT_ID = None
_REQUEST_TRACE_ID = ContextVar("grlab_request_trace_id", default=None)


def experiment_id():
    return _EXPERIMENT_ID


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
    if not _EXPERIMENT_ID:
        yield None
        return

    import mlflow

    with mlflow.start_span(name="guardrailed_agent_request") as span:
        try:
            _REQUEST_TRACE_ID.set(span.trace_id)
            mlflow.update_current_trace(tags={"workshop.backend": getenv("GRLAB_BACKEND_MODE", "direct")})
            if inputs is not None:
                span.set_inputs(_sanitize_trace_value(inputs))
        except Exception as exc:
            logger.debug("[Tracing] Could not set request inputs (%s)", type(exc).__name__)
        yield span


def clear_pending_guardrail_rejection() -> None:
    _REQUEST_TRACE_ID.set(None)
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
    if not _EXPERIMENT_ID:
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
            trace_id = _REQUEST_TRACE_ID.get()
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


def enable_tracing() -> None:
    """Initialize the application tracer using the SDK's authenticated experiment API."""
    global _EXPERIMENT_ID
    _EXPERIMENT_ID = None
    uri = getenv("MLFLOW_TRACKING_URI")
    if not uri:
        return
    try:
        import mlflow
        import mlflow.langchain

        mlflow.set_tracking_uri(uri)
        experiment = mlflow.set_experiment(getenv("MLFLOW_EXPERIMENT_NAME", "guardrail-banking-agent"))
        mlflow.langchain.autolog(run_tracer_inline=True)
        _EXPERIMENT_ID = experiment.experiment_id
        logger.info("MLflow tracing active for experiment %s", _EXPERIMENT_ID)
    except Exception as exc:
        # Credential-bearing gateway responses do not belong in workshop logs.
        logger.warning("MLflow setup failed (%s). Check SDK access, workspace RBAC and gateway TLS; "
                       "restart grlab-agent after repair. Banking requests can continue.", type(exc).__name__)
