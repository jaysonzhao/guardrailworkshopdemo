"""Read a fresh native NeMo trace from Jaeger v3 or Tempo using a W3C trace ID."""
import base64
import json
from pathlib import Path
import secrets
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener, urlopen

from verify import PRIVACY, REFUSALS, SAFE

OTP = "My OTP is 000000."


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def attributes(obj):
    return {entry["key"]: next(iter(entry.get("value", {}).values()), None) for entry in obj.get("attributes", [])}


def same_id(value, tid):
    if value.lower() == tid:
        return True
    try:
        return base64.b64decode(value, validate=True).hex() == tid
    except ValueError:
        return False


def spans(document, tid):
    payload = document.get("result", document)  # Jaeger v3 HTTP wraps the OTLP payload.
    if "trace" in payload:
        payload = payload["trace"]
    result = []
    for batch in payload.get("resourceSpans", payload.get("batches", [])):
        if attributes(batch.get("resource", {})).get("service.name") != "nemo-guardrails":
            continue
        for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])):
            for s in scope.get("spans", []):
                if same_id(s.get("traceId", ""), tid):
                    result.append(s)
    return result


def evidence(document, tid, profile, blocked, main_model):
    records = spans(document, tid)
    native = [s for s in records if s["name"] == "guardrails.request"]
    http = [s for s in records if s["name"] == "POST /v1/chat/completions" or
            attributes(s).get("http.route") == "/v1/chat/completions"]
    if not native or not http:
        raise ValueError("waiting for both HTTP and guardrails.request spans from nemo-guardrails")
    if not any(r.get("parentSpanId") == h.get("spanId") for r in native for h in http):
        raise ValueError("the native NeMo request is not a child of this HTTP request")
    rails = [s for s in records if attributes(s).get("rail.type")]
    models = [s for s in records if attributes(s).get("gen_ai.request.model") and s["name"] != "guardrails.request"]
    types = {attributes(s)["rail.type"] for s in rails}
    main_calls = [s for s in models if attributes(s).get("gen_ai.request.model") == main_model]
    stopped = any(attributes(s).get("rail.stop") is True and attributes(s).get("rail.type") == "input"
                  and attributes(s).get("rail.name") == "banking input gate" for s in rails)
    if blocked and main_calls:
        raise RuntimeError("The blocked synthetic credential reached the main model.")
    if blocked and not stopped:
        raise ValueError("waiting for a stopped banking input gate for the synthetic credential")
    if not blocked and profile in ("rules", "content") and not {"input", "output"} <= types:
        raise ValueError("waiting for the input and output rail spans")
    if not blocked and not main_calls:
        raise ValueError("waiting for the allowed request's main model span")
    return http[0], rails, models, [s for s in records if attributes(s).get("action.name")]


def duration(span):
    return (int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"])) / 1_000_000


def readback(base, api, tid, headers, opener, profile, blocked, main_model, timeout=45):
    endpoint = base.rstrip("/") + ("/api/v3/traces/" if api == "jaeger" else "/api/traces/") + tid
    end = time.monotonic() + timeout
    last = "no trace returned"
    while time.monotonic() < end:
        try:
            with opener.open(Request(endpoint, headers=headers), timeout=6) as res:
                doc = json.load(res)
            return evidence(doc, tid, profile, blocked, main_model)
        except HTTPError as exc:
            with exc:
                if exc.code in (401, 403):
                    raise RuntimeError(f"Query API returned {exc.code}. Check reader identity, workspace and tenant.") from None
                if exc.code not in (404, 429, 502, 503, 504):
                    raise RuntimeError(f"Query API returned {exc.code}. Check the query base path and API selection.") from None
                last = "HTTP " + str(exc.code)
        except json.JSONDecodeError:
            raise RuntimeError("The query endpoint returned non-JSON. Check the HTTP API URL, login and tenant.") from None
        except (URLError, TimeoutError, ValueError) as exc:
            last = str(exc)
        time.sleep(1)
    raise RuntimeError(f"Trace {tid} was not verified ({last}). Check OTLP export, receiver TLS, tenant, sampling and query access.")


def exercise(proxy, model, profile, credential, query_url, query_api, ui, ca=None, token_file=None, tenant=None, capture=False, deep_links=False):
    headers = {"Accept": "application/json"}
    if token_file:
        if urlsplit(query_url).scheme != "https":
            raise ValueError("--query-token-file requires an HTTPS query URL.")
        token = Path(token_file).read_text().strip()
        if not token or any(c.isspace() for c in token):
            raise ValueError("The query-token file must contain one token.")
        headers["Authorization"] = "Bearer " + token
    if tenant:
        if any(c.isspace() for c in tenant):
            raise ValueError("Use a single tenant identifier.")
        headers["X-Scope-OrgID"] = tenant
    client = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
    cases = [("allowed", SAFE, False)]
    if credential:
        cases.append(("blocked", OTP, True))
    else:
        print("SKIP synthetic OTP comparison. It requires the credential rule in a rules or content profile.")
    ids = []
    for label, prompt, blocked in cases:
        tid = secrets.token_hex(16)
        traceparent = "00-" + tid + "-" + secrets.token_hex(8) + "-01"
        payload = {"model": model, "stream": False, "messages": [{"role": "user", "content": prompt}]}
        req = Request(proxy.rstrip("/") + "/chat/completions", json.dumps(payload).encode(),
                      {"Content-Type": "application/json", "traceparent": traceparent})
        with urlopen(req, timeout=120) as res:
            reply = json.load(res)["choices"][0]["message"]["content"]
        if (not isinstance(reply, str) or not reply.strip() or
                (blocked and (PRIVACY not in reply or "000000" in reply)) or
                (not blocked and any(f in reply for f in REFUSALS))):
            raise RuntimeError(f"The {label} synthetic request returned an unexpected decision. Review the mounted rules.")
        http, rails, models, actions = readback(query_url, query_api, tid, headers, client, profile, blocked, model)
        ids.append(tid)
        print(f"PASS {label} | trace {tid} | HTTP {duration(http):.1f} ms | {len(actions)} action spans")
        for rail in rails:
            a = attributes(rail)
            print(f"  {a['rail.type']} | {a.get('rail.name', rail['name'])} | {duration(rail):.1f} ms | stop={a.get('rail.stop', False)}")
        for call in models:
            a = attributes(call)
            print(f"  model | {a.get('gen_ai.request.model')} | {duration(call):.1f} ms")
        if ui:
            if deep_links:
                print("Open trace:", ui.rstrip("/") + "/trace/" + tid)
            else:
                print("Open tracing UI:", ui)
    print("Content capture:", "on (fictional data only)" if capture else "off")
    return ids
