"""Deterministic local OpenAI-compatible fixture. No inference service or credentials."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_json({"data": [{"id": "workshop-main"}, {"id": "nvidia/Llama-3.1-Nemotron-Safety-Guard-8B-v3"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = req["messages"]
        last = msgs[-1]
        content = last.get("content", "") or ""
        model = req["model"]
        safety = model.startswith("nvidia/")
        expected = "fixture-safety" if safety else "fixture-main"
        if self.headers.get("Authorization") != "Bearer " + expected or (
                not safety and self.headers.get("X-MaaS-Subscription") != "workshop-demo"):
            self.send_error(403, "Wrong model credentials or missing subscription")
            return
        out = {"role": "assistant"}
        if model.startswith("nvidia/"):
            # Match the conversation portion only. Policy descriptions name unsafe cases.
            self.server.calls.append("classifier")
            verdict = {"User Safety": "safe"}
            policy, _, exchange = content.partition("<BEGIN CONVERSATION>")
            if "S10:" in policy and "humiliating" in exchange.lower():
                verdict = {"User Safety": "unsafe", "Safety Categories": "Harassment"}
            if "S16:" in policy and "false endorsement" in exchange.lower():
                verdict = {"User Safety": "unsafe", "Safety Categories":"Fraud/Deception"}
            if "response: agent:" in exchange.lower():
                verdict["Response Safety"] = "unsafe" if "CLASSIFIER_OUTPUT_FIXTURE" in exchange else "safe"
                if verdict["Response Safety"] == "unsafe":
                    verdict["Safety Categories"] = "Harassment"
            out["content"] = "non-verdict" if "malformed verdict rehearsal" in content.lower() else json.dumps(verdict)
        else:
            self.server.calls.append("main")
            phrase = content.lower()
            if last["role"] == "tool":
                out["content"] = "The demo checking balance is $2,450.00 and savings balance is $12,800.50."
            elif "acct-12345" in phrase and req.get("tools"):
                out.update(content=None, tool_calls=[{"id": "call_demo", "type": "function", "function": {
                    "name": "check_balance", "arguments": json.dumps({"account_id": "ACCT-12345"})}}])
            elif "output canary exercise" in phrase:
                out["content"] = "B_ONLY_CANARY_7Q2"
            elif "collections draft rehearsal" in phrase:
                out["content"] = "You should be ashamed of your late payment."
            elif "classifier output rehearsal" in phrase:
                out["content"] = "CLASSIFIER_OUTPUT_FIXTURE"
            else:
                out["content"] = "Open the official bank app, select Forgot password, and follow the secure reset steps."
        self.send_json({"id": "chatcmpl-lab", "created": 1, "object": "chat.completion", "model": model,
                        "choices": [{"index": 0, "message": out, "finish_reason": "tool_calls" if out.get("tool_calls") else "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})


def server():
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    fixture.calls = []
    return fixture
