"""Execute inside the vLLM pod. The launcher supplies PROMPT from the NeMo template."""
import json
from urllib.request import Request, urlopen

base = "http://127.0.0.1:8000/v1"
model_id = "nvidia/Llama-3.1-Nemotron-Safety-Guard-8B-v3"
with urlopen(base + "/models", timeout=15) as response:
    ids = {row["id"] for row in json.load(response)["data"]}
if model_id not in ids:
    raise RuntimeError("vLLM did not advertise the workshop model")
body = {"model": model_id, "messages": [{"role": "user", "content": PROMPT}], "temperature": 0, "max_tokens": 64}
req = Request(base + "/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
with urlopen(req, timeout=120) as response:
    content = json.load(response)["choices"][0]["message"]["content"]
verdict = json.loads(content)
if verdict.get("User Safety") != "safe" or verdict.get("Safety Categories") not in (None, "", [], "None", "none"):
    raise RuntimeError("The classifier failed the benign banking control")
print("PASS model ID and benign JSON verdict")
