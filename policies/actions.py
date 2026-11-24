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
import os
import re
import unicodedata
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
MAX_REQUEST_CHARS = 100_000
# Credential-related guidance often contains phrases such as "your password is
# never shared". These words describe safety/procedure, not credential values.
_CREDENTIAL_GUIDANCE_WORDS = (
    "required|needed|optional|compromised|stolen|lost|forgotten|expired|invalid|"
    "incorrect|blocked|locked|secure|strong|weak|private|confidential|sensitive|"
    "protected|encrypted|hashed|case-sensitive|not|never|always|only"
)
_CREDENTIAL_STEP_WORDS = (
    "open|select|click|choose|follow|visit|use|go|change|update|contact|reset"
)
DEFAULT_DEMO_ACCOUNTS = frozenset(
    ("ACCT-12345", "DEMO-ACC-A-001", "DEMO-CUSTOMER-A")
)

def _normalise(text):
    text = unicodedata.normalize("NFKC", text or "")
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def _demo_accounts():
    configured = os.environ.get("BANKING_DEMO_ACCOUNT_IDS")
    if configured is None:
        return DEFAULT_DEMO_ACCOUNTS
    return frozenset(x.strip().upper() for x in configured.split(",") if x.strip())

_ACCOUNT_RE = re.compile(
    r"\b(?:ACCT-\d+|DEMO-ACC-[A-Z0-9-]+|DEMO-CUSTOMER-[A-Z0-9-]+)\b", re.I
)
_SENSITIVE_PATTERNS = (
    ("credential", re.compile(
        r"\b(?:otp|pin)\b(?:\s+(?:is|code))?\s*[:=]?\s*\d{4,8}\b"
        # An explicitly quoted assignment is always a value.
        r"|\b(?:password|api[_ -]?key|access[_ -]?token)\b\s*(?:is\b|:|=)\s*[\"'][^\"'\r\n]{4,}[\"']"
        # Equals is an unambiguous assignment, including simple values.
        r"|\b(?:password|api[_ -]?key|access[_ -]?token)\b\s*=\s*\S{4,}"
        # With natural-language 'is', skip standard guidance and action wording.
        r"|\b(?:password|api[_ -]?key|access[_ -]?token)\b\s*(?:is\b|:|=)\s*"
        r"(?!(?:" + _CREDENTIAL_GUIDANCE_WORDS + r")\b)"
        r"(?!(?:" + _CREDENTIAL_STEP_WORDS + r")\b\s+\w+\b)[^\s\"']{4,}"
        r"|(?:一次性密碼|一次性密码|驗證碼|验证码|密碼|密码)\s*(?:是|為|为|係|:|=)?\s*[A-Za-z0-9]{4,}"
        r"|\bsk-[A-Za-z0-9_-]{16,}\b", re.I)),
    ("hkid", re.compile(
        # HKID: one or two prefix letters, six digits, and a parenthesized check digit.
        r"(?<![A-Z0-9])[A-Z]{1,2}\s*\d{3}\s*\d{3}\s*\([0-9A]\)(?![A-Z0-9])", re.I)),
    ("email", re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)),
    ("card_or_long_identifier", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(
        r"(?<!\d)(?:\+?852[ .-]?)?[235689]\d{3}[ .-]?\d{4}(?!\d)"
        r"|\b\d{3}[-.]\d{3}[-.]\d{4}\b")),
    ("labelled_account", re.compile(
        r"(?:\baccount(?:\s+(?:number|no\.?))?|\ba/c|帳戶|賬戶|账户|戶口)"
        r"\s*(?:is\b|:|=)?\s*\d(?:[\d -]{4,}\d)", re.I)),
    ("labelled_address", re.compile(
        r"\b\d{1,5}\s+[\w ]{1,45}\s(?:street|avenue|road|drive|lane|boulevard)\b", re.I)),
)
_OUTPUT_CREDENTIAL_RE = re.compile(
    r"\b(?:otp|pin)\b(?:\s+(?:is|code))?\s*[:=]?\s*\d{4,8}\b"
    r"|\b(?:password|api[_ -]?key|access[_ -]?token)\b\s*(?::|=)\s*[\"']?\S{4,}"
    r"|\b(?:password|api[_ -]?key|access[_ -]?token)\b\s+is\s+[\"'][^\"'\r\n]{4,}[\"']"
    # Catch likely leaked secrets in prose while allowing ordinary phrases such
    # as "your password is never shared" or "password is reset through the app".
    r"|\b(?:password|api[_ -]?key|access[_ -]?token)\b\s+is\s+(?=\S{8,}\b)(?=\S*[0-9!@#$%^&*_+=-])\S+"
    r"|(?:一次性密碼|一次性密码|驗證碼|验证码|密碼|密码)\s*(?:是|為|为|係|:|=)?\s*[A-Za-z0-9]{4,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}\b",
    re.I,
)
_INJECTION_RE = re.compile(
    r"\b(?:ignore|forget|disregard|override|bypass)\b.{0,70}\b(?:rules|instructions|policy|guardrails)\b"
    r"|\b(?:reveal|print|show|dump|return)\b.{0,40}\b(?:system prompt|developer instructions|api keys?)\b"
    r"|\b(?:unrestricted audit mode|system update)\b"
    r"|(?:唔使跟|忽略|無視|无视|繞過|绕过).{0,25}(?:規則|规则|指令|驗證|验证)"
    r"|(?:當我係|当我是).{0,12}(?:管理員|管理员)", re.I | re.S
)
_WRITE_REQUEST_RE = re.compile(
    r"\b(?:call|execute|run)\s+(?:the\s+)?(?:transfer_funds|make_payment|approve_loan|export_customers)\b"
    r"|\b(?:send|transfer|move)\b.{0,35}\b(?:HKD|USD|dollars?|funds?|money)\b"
    r"|\b(?:skip|bypass)\b.{0,25}\b(?:confirmation|authentication|approval)\b"
    r"|\b(?:export|upload|send)\b.{0,55}\b(?:all customer|every customer|customer records|customer database)\b",
    re.I | re.S
)
_FABRICATE_RE = re.compile(
    r"\b(?:invent|fabricate|make up)\b.{0,45}\b(?:fee|rate|policy|citation|approval)\b"
    r"|\bguarantee\b.{0,45}\b(?:approved|approval|returns?)\b"
    r"|\b(?:recover|decode|reconstruct)\b.{0,50}\b(?:HKID|masked|token|identifier)\b",
    re.I | re.S
)
_HUMAN_RE = re.compile(
    r"\b(?:speak|talk|connect me)\b.{0,25}\b(?:person|human|agent)\b"
    r"|\b(?:opt out|do not want AI|don't want AI)\b"
    r"|(?:轉接|转接|搵|找|聯絡|联系).{0,8}(?:真人|人工|客服)"
    r"|(?:唔想|不要|不想).{0,6}(?:AI|人工智能)", re.I
)
_OUTPUT_ASSERTION_RE = re.compile(
    r"\b(?:your|the)\s+(?:loan|mortgage)\s+(?:is|has been)\s+(?:approved|accepted)\b"
    r"|\b(?:I have|we have|successfully)\s+(?:transferred|sent|paid|waived)\b"
    r"|\bguaranteed\s+(?:profit|return)\b"
    r"|(?:已經|已经|已).{0,6}(?:批准貸款|批准贷款|完成轉帳|完成转账)", re.I
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(\s*[^)]+\s*\)", re.I)
_DOMAIN_RE = re.compile(
    r"(?<![@\w])(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?:/[^\s<>\"']*)?",
    re.I,
)

def _decision(allowed, reason="allow"):
    # Do not log prompts, identifiers, candidate output or classifier raw text.
    if not allowed:
        log.warning("banking_guardrail decision=block reason=%s", reason)
    return {"allowed": allowed, "reason": reason, "handoff": reason == "human_requested"}


def _output_decision(allowed, reason, bot_message):
    result = _decision(allowed, reason)
    result["bot_message"] = bot_message
    return result


def _has_unapproved_link(text, allowed_hosts):
    from urllib.parse import urlsplit

    for url in _URL_RE.findall(text):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed_hosts
            or parsed.username
            or parsed.password
        ):
            return True
    for domain in _DOMAIN_RE.findall(text):
        hostname = domain.split("/", 1)[0].lower().removeprefix("www.")
        if hostname not in allowed_hosts:
            return True
    return False


def _rewrite_unapproved_links(text):
    replacement = "the official bank app or website opened manually"
    rewritten = _MARKDOWN_LINK_RE.sub(replacement, text)
    rewritten = _URL_RE.sub(replacement, rewritten)
    rewritten = _DOMAIN_RE.sub(replacement, rewritten)
    rewritten = re.sub(r"[ \t]{2,}", " ", rewritten).strip()
    return (
        rewritten
        + "\nFor security, open the bank's official app or enter its website manually; "
        "do not follow a link supplied here."
    )

def _reason_code(value):
    code = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return code[:64] or "unclassified"

def _sensitive_reason(text, *, output=False):
    for reason, pattern in _SENSITIVE_PATTERNS:
        if output and reason == "credential":
            if _OUTPUT_CREDENTIAL_RE.search(text):
                return reason
            continue
        if pattern.search(text):
            return reason
    return None

def _account_reason(text):
    if any(m.group(0).upper() not in _demo_accounts() for m in _ACCOUNT_RE.finditer(text)):
        return "unapproved_demo_account"
    return None

def _read_content(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
                raise ValueError("unsupported_multimodal_content")
            parts.append(block["text"])
        return "\n".join(parts)
    raise ValueError("unsupported_content")

def _request_parts(context):
    """Inspect available raw passthrough history; do not mutate message objects."""
    parts = []
    raw = raw_llm_request.get() if raw_llm_request is not None else None
    if isinstance(raw, list):
        for msg in raw:
            if not isinstance(msg, dict):
                raise ValueError("invalid_message")
            role = msg.get("role", "user")
            parts.append((role, _read_content(msg.get("content"))))
            if msg.get("tool_calls"):
                parts.append(("tool_arguments", json.dumps(msg["tool_calls"], ensure_ascii=False)))
    elif isinstance(raw, str):
        parts.append(("user", raw))
    elif raw is not None:
        raise ValueError("invalid_raw_request")
    user = (context or {}).get("user_message", "")
    parts.append(("user", _read_content(user)))
    # These fields are inspected if present; their presence does not prove provenance.
    for key in ("relevant_chunks", "tool_result"):
        if (context or {}).get(key):
            parts.append(("tool", _read_content(context[key])))
    return [(role, _normalise(text)) for role, text in parts]


@action(is_system_action=True)
async def banking_local_input(context: Optional[dict] = None, **kwargs):
    try:
        parts = _request_parts(context or {})
        if sum(len(text) for _, text in parts) > MAX_REQUEST_CHARS:
            return _decision(False, "request_too_large")
        for role, text in parts:
            reason = _sensitive_reason(text) or _account_reason(text)
            if reason:
                return _decision(False, reason)
            # Trusted system/developer instructions are not injection candidates.
            # The application must reject client-supplied system/developer roles.
            if role in ("user", "tool", "tool_arguments") and _INJECTION_RE.search(text):
                return _decision(False, "instruction_override")
        user = _normalise((context or {}).get("user_message", ""))
        if _HUMAN_RE.search(user):
            return _decision(False, "human_requested")
        if _WRITE_REQUEST_RE.search(user):
            return _decision(False, "prohibited_action")
        if _FABRICATE_RE.search(user):
            return _decision(False, "fabrication_or_reidentification")
        return _decision(True)
    except Exception:
        return _decision(False, "input_check_error")

@action(is_system_action=True)
async def banking_local_output(context: Optional[dict] = None, **kwargs):
    text = ""
    try:
        ctx = context or {}
        text = _normalise(_read_content(ctx.get("bot_message")))
        # Empty text can accompany a tool call. This guard does not validate tools.
        if not text:
            return _output_decision(True, "no_text_tool_validation_required", text)
        reason = _sensitive_reason(text, output=True) or _account_reason(text)
        if reason:
            return _output_decision(False, reason, text)
        if "B_ONLY_CANARY_7Q2" in text:
            return _output_decision(False, "demo_cross_customer_canary", text)
        # A fixture ID appearing only in the response gets no automatic exception.
        user_ids = {m.group(0).upper() for m in _ACCOUNT_RE.finditer(_normalise(ctx.get("user_message", "")))}
        output_ids = {m.group(0).upper() for m in _ACCOUNT_RE.finditer(text)}
        if not output_ids.issubset(user_ids):
            return _output_decision(False, "unexpected_account_reference", text)
        if _OUTPUT_ASSERTION_RE.search(text):
            return _output_decision(False, "unsupported_action_or_decision", text)
        allowed_hosts = {x.strip().lower() for x in os.environ.get("BANKING_APPROVED_LINK_HOSTS", "").split(",") if x.strip()}
        if _has_unapproved_link(text, allowed_hosts):
            text = _rewrite_unapproved_links(text)
            # Recheck the transformed output locally before the model-based output rail.
            if _sensitive_reason(text, output=True) or _account_reason(text):
                return _output_decision(False, "output_rewrite_check_failed", text)
            log.info("banking_guardrail output=rewritten reason=unapproved_link")
        # Exact fictional fixture only; this is not a general factuality verifier.
        if "DEMO-FEE-001" in ctx.get("user_message", ""):
            amounts = re.findall(r"\bHKD\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
            if any(float(x) != 20.0 for x in amounts) or re.search(r"\b(?:no monthly fee|fee is zero|zero fee|fee.free)\b", text, re.I):
                return _output_decision(False, "demo_fee_mismatch", text)
        return _output_decision(True, "allow", text)
    except Exception:
        return _output_decision(False, "output_check_error", text)

def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_verdict_key")
        result[key] = value
    return result

def _parse_safety_verdict(text, field):
    """Parse strict JSON and Llama Guard labels; fail closed on other output."""
    plain = (text or "").strip()
    if re.fullmatch(r"unsafe(?:\s+S\d+(?:\s*,\s*S\d+)*)?", plain, re.I):
        return False
    try:
        payload = json.loads(plain, object_pairs_hook=_unique_json_pairs)
        if not isinstance(payload, dict) or payload.get("User Safety") != "safe" or payload.get(field) not in ("safe", "unsafe"):
            return False
        if any(value != "safe" for key, value in payload.items() if key.endswith("Safety")):
            return False
        categories = payload.get("Safety Categories")
        if categories not in (None, "", [], "None", "none"):
            return False
        return payload[field] == "safe"
    except (ValueError, TypeError, AttributeError):
        return False

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
        allowed = _parse_safety_verdict(_llm_response_text(result), verdict_field)
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
    # User input should already have passed input rails; never forward residual PII.
    user_reason = _sensitive_reason(_normalise((context or {}).get("user_message", "")))
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
    return f"{messages[rail]} [[guardrail:{rail}:{_reason_code(reason)}]]"
