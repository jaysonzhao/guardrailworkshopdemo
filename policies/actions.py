"""Banking-demo extension of the supplied LangGraph passthrough ConfigMap.

Local guards BLOCK sensitive text rather than rewriting only user_message:
passthrough may otherwise forward untouched history/tool content.
Regex checks are demonstrative, not comprehensive PII recognition.
Model endpoints/credentials are configured in config.yaml, not in this module.
No external packages beyond the existing NeMo runtime are added.
"""

import asyncio
import json
import logging
import importlib.util
from pathlib import Path
from typing import Any, Dict, Optional

from nemoguardrails.actions.actions import action
try:
    from nemoguardrails.llm.call import llm_call
except ImportError:
    from nemoguardrails.actions.llm.utils import llm_call
from nemoguardrails.context import llm_call_info_var
try:
    from nemoguardrails.context import raw_llm_request
except ImportError:
    raw_llm_request = None
from nemoguardrails.logging.explain import LLMCallInfo

log = logging.getLogger(__name__)
# Bound the optional classifier call and refuse on a timeout.
CHECK_TIMEOUT_SECONDS = 90.0

# NeMo loads actions.py by its pathname. Resolve helpers from the same immutable
# revision so loading a second configuration cannot reuse a different policy.
_folder = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("workshop_banking", _folder / "banking.py")
banking = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(banking)
POLICY = json.loads((_folder / "active_rules.json").read_text())


@action(is_system_action=True)
async def banking_local_input(context: Optional[dict] = None, **kwargs):
    raw = raw_llm_request.get() if raw_llm_request is not None else None
    return banking.check_input(context or {}, POLICY, raw)


@action(is_system_action=True)
async def banking_local_output(context: Optional[dict] = None, **kwargs):
    return banking.check_output(context or {}, POLICY)


def _llm_response_text(result):
    content = getattr(result, "content", result)
    return content if isinstance(content, str) else ""

async def _run_classifier(llms, model_name, task, prompt, verdict_field, block_reason):
    try:
        llm = llms.get(model_name)
        if llm is None:
            log.warning(
                "banking_classifier unavailable model=%s task=%s",
                model_name,
                task,
            )
            return {"allowed": False, "reason": "classifier_unavailable"}
        llm_call_info_var.set(LLMCallInfo(task=task))
        result = await asyncio.wait_for(
            llm_call(llm, prompt, llm_params={"temperature": 0.0, "max_tokens": 64}),
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        verdict, _ = banking.read_safety_verdict(_llm_response_text(result), verdict_field)
        if verdict == "invalid":
            log.warning("banking_classifier invalid verdict task=%s", task)
            return {"allowed": False, "reason": "invalid_classifier_verdict"}
        allowed = verdict == "safe"
        if not allowed:
            log.warning(
                "banking_classifier decision=block task=%s reason=%s",
                task,
                block_reason,
            )
        return {
            "allowed": allowed,
            "reason": "allow" if allowed else block_reason,
        }
    except Exception as exc:
        # Keep the diagnostic useful without logging the prompt, model output,
        # identifiers, or potentially sensitive exception messages.
        log.warning(
            "banking_classifier failed task=%s exception_type=%s",
            task,
            type(exc).__name__,
        )
        return {"allowed": False, "reason": "classifier_unavailable"}

@action(is_system_action=True)
async def banking_content_input(llms: Dict[str, Any], llm_task_manager: Any,
                                context: Optional[dict] = None, **kwargs):
    # Defensive repeat prevents a direct call from skipping the local privacy gate.
    local = await banking_local_input(context=context)
    if not local["allowed"]:
        return {"allowed": False, "reason": local["reason"]}
    task = "content_safety_check_input $model=content_safety"
    content_safety_config = getattr(
        getattr(getattr(llm_task_manager.config, "rails", None), "config", None),
        "content_safety",
        None,
    )
    reasoning = getattr(content_safety_config, "reasoning", None)
    prompt = llm_task_manager.render_task_prompt(
        task=task,
        context={
            "user_input": (context or {}).get("user_message", ""),
            "reasoning_enabled": bool(getattr(reasoning, "enabled", False)),
        },
    )
    return await _run_classifier(
        llms, "content_safety", task, prompt, "User Safety", "content_safety"
    )

@action(is_system_action=True)
async def banking_content_output(llms: Dict[str, Any], llm_task_manager: Any,
                                 context: Optional[dict] = None, **kwargs):
    local = await banking_local_output(context=context)
    if not local["allowed"]:
        return {"allowed": False, "reason": local["reason"]}
    text = (context or {}).get("bot_message", "")
    if not text:
        return {"allowed": True, "reason": "allow"}
    # Recheck enabled local detectors before sending response context to the classifier.
    user_reason = banking.sensitive_reason(banking._normalise((context or {}).get("user_message", "")), POLICY["active"])
    if user_reason:
        return {"allowed": False, "reason": user_reason}
    task = "content_safety_check_output $model=content_safety"
    content_safety_config = getattr(
        getattr(getattr(llm_task_manager.config, "rails", None), "config", None),
        "content_safety",
        None,
    )
    reasoning = getattr(content_safety_config, "reasoning", None)
    prompt = llm_task_manager.render_task_prompt(
        task=task,
        context={
            "user_input": (context or {}).get("user_message", ""),
            "bot_response": text,
            "reasoning_enabled": bool(getattr(reasoning, "enabled", False)),
        },
    )
    return await _run_classifier(
        llms, "content_safety", task, prompt, "Response Safety", "unsafe_output"
    )

@action(is_system_action=True)
async def workshop_refusal(rail: str, reason: str):
    """Return a fixed message and a bounded reason marker for the agent transport."""
    messages = {
        "banking_input_gate": "I cannot process this request here. Please remove sensitive details and use the bank's secure channel for account access, payments or specialist help.",
        "banking_output_gate": "I cannot safely verify that response. Please use the bank's official customer-service channel for assistance.",
        "content_safety_input": "I can help with supported banking questions, redacted complaints and document checklists. Please use the bank's official channel for other assistance.",
        "content_safety_output": "I cannot safely verify that response. Please use the bank's official customer-service channel for assistance.",
    }
    return f"{messages[rail]} [[guardrail:{rail}:{banking.reason_code(reason)}]]"
