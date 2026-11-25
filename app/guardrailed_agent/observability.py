"""Read a workshop trace and publish a run visible in the integrated Experiments table."""
from os import getenv
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Path

from .tracing import experiment_id

router = APIRouter(prefix="/workshop/mlflow", include_in_schema=False)


@router.get("/link")
def experiment_link():
    project = getenv("GRLAB_MLFLOW_DASHBOARD_URL")
    if not project:
        raise HTTPException(409, "Enable optional Module 6 for this project.")
    eid = experiment_id()
    if not eid:
        return {"url": project, "experiment_id": None}
    # These are the 3.5 dashboard's host routes (not the standalone MLflow UI).
    page = project.split("?", 1)[0] + "/" + quote(str(eid), safe="") + "/runs"
    query = urlencode({"workspace": getenv("MLFLOW_WORKSPACE", ""), "searchFilter": ""})
    return {"url": page + "?" + query, "experiment_id": eid}


@router.post("/evidence/{trace_id}")
def record_evidence(trace_id: str = Path(pattern=r"^tr-[a-f0-9]{32}$")):
    """Use the app's identity to read back the exact trace returned by its chat response."""
    eid = experiment_id()
    if not eid:
        raise HTTPException(503, "MLflow tracing is inactive. Check the grlab-agent startup log.")
    import mlflow
    from mlflow.exceptions import MlflowException

    try:
        trace = mlflow.MlflowClient().get_trace(trace_id, display=False, flush=True)
    except MlflowException as exc:
        if exc.error_code == "RESOURCE_DOES_NOT_EXIST":
            raise HTTPException(404, "Trace has not reached MLflow yet.") from None
        raise HTTPException(502, f"MLflow trace read failed ({exc.error_code}). Check workspace access and TLS.") from None
    if str(trace.info.experiment_id) != str(eid):
        raise HTTPException(404, "Trace is outside the workshop experiment.")
    roots = [s for s in trace.data.spans if s.parent_id is None]
    if len(roots) != 1 or roots[0].name != "guardrailed_agent_request":
        raise HTTPException(422, "Expected the banking application's request span.")
    mode = trace.info.tags.get("workshop.backend")
    if mode not in ("direct", "proxy"):
        raise HTTPException(422, "The trace has no workshop backend tag.")
    state = getattr(trace.info.state, "value", str(trace.info.state))
    if state != "OK":
        raise HTTPException(422, "The synthetic request trace did not finish successfully.")
    spans = [s.name for s in trace.data.spans]
    metrics = {"request_latency_ms": trace.info.execution_duration, "span_count": len(spans)}
    with mlflow.start_run(experiment_id=eid, run_name="banking-smoke-" + trace_id[-8:],
                          tags={"workshop.trace_id": trace_id, "workshop.backend": mode}) as run:
        mlflow.log_metrics(metrics)
        run_id = run.info.run_id
    page = experiment_link()["url"].split("?", 1)[0]
    run_url = page + "/" + quote(run_id, safe="") + "?" + urlencode({"workspace": getenv("MLFLOW_WORKSPACE", "")})
    return {"trace_id": trace_id, "experiment_id": eid, "run_id": run_id, "url": run_url,
            "backend": mode, "metrics": metrics, "spans": spans}
