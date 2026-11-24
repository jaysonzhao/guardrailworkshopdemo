"""Evaluate synthetic examples using the same decision functions mounted in the NeMo pod."""
import importlib.util
import json
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen
import yaml


def load_banking(folder):
    spec = importlib.util.spec_from_file_location("banking_rule_sample", folder / "banking.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def local_result(banking, rule, policy, sample):
    phase = rule["phase"]
    context = {"user_message": rule.get("context", "Please explain this banking procedure.") if phase == "output" else sample,
               "bot_message": sample}
    if phase == "output":
        result = banking.check_output(context, policy)
        if result["allowed"] and result["bot_message"] != sample:
            return "rewrite", result["bot_message"]
    else:
        result = banking.check_input(context, policy)
        if result["allowed"] and "training-patterns" in policy["active"] and any(
                re.search(p, sample, re.I) for p in policy["keyword_patterns"]):
            return "block", "regex check input"
    if result["handoff"]:
        return "handoff", "human_requested"
    return ("allow" if result["allowed"] else "block"), result["reason"]


def model_result(banking, rule, folder, policy, text):
    direction = "output" if rule["phase"] == "output" else "input"
    if "model-" + direction not in policy["active"]:
        if rule.get("taxonomy") or rule["id"] == "model-banking-policy":
            direction = "output" if direction == "input" else "input"
        if "model-" + direction not in policy["active"]:
            raise ValueError("The corresponding classifier direction is disabled.")
    cfg = yaml.safe_load((folder/"config.yaml").read_text())
    model = next(m for m in cfg["models"] if m["type"] == "content_safety")
    prompts = yaml.safe_load((folder/"prompts.yml").read_text())["prompts"]
    template = next(item["content"] for item in prompts if item["task"].startswith("content_safety_check_" + direction + " "))
    prompt = template.replace("{{ user_input }}", text if direction == "input" else "Please draft a customer-service reply.")
    prompt = prompt.replace("{{ bot_response }}", text)
    payload = {"model":model["model"],"messages":[{"role":"user","content":prompt}],"temperature":0,"max_tokens":64}
    import os
    headers = {"Content-Type":"application/json","Authorization":"Bearer "+os.environ[model["api_key_env_var"]]}
    req = Request(model["parameters"]["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers)
    with urlopen(req,timeout=100) as response:
        raw = json.load(response)["choices"][0]["message"]["content"]
    field = "Response Safety" if direction=="output" else "User Safety"
    # A timeout or malformed JSON cannot count as a successful classifier decision.
    decision, names = banking.read_safety_verdict(raw, field)
    if decision == "invalid":
        raise ValueError("Classifier returned an invalid or unattributed verdict.")
    if decision == "unsafe" and rule.get("taxonomy"):
        def normalize(value):
            return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()
        labels = {normalize(rule["model_label"]), normalize(rule["title"]), normalize(rule["taxonomy"])}
        matched = any(normalize(name) in labels or re.match(r"^" + rule["taxonomy"] + r"\b", str(name).strip(), re.I) for name in names)
        if not matched:
            raise ValueError(f"Blocked via {names}. The target category {rule['taxonomy']} was not demonstrated.")
    return ("allow" if decision == "safe" else "block"), (", ".join(names) if names else "safe")


def demonstrate(rule, folder, policy, live=False):
    banking = load_banking(folder)
    if rule["engine"] == "model" and not live:
        print("Model prediction requires Module 3. Run ./grlab catalog test " + rule["id"] + " against the active classifier.")
        return
    if live and rule["id"] not in policy["active"]:
        raise ValueError("This rule is disabled in the deployed selection.")
    for field, expected in (("sample",rule["action"]),("control","allow")):
        sample = rule[field]
        if rule["engine"] == "model":
            actual,detail = model_result(banking,rule,folder,policy,sample)
        else:
            actual,detail = local_result(banking,rule,policy,sample)
        print(f"{field}: {sample}\n  {actual} — {detail}")
        if actual != expected:
            raise ValueError(f"{rule['id']} {field}: expected {expected}, observed {actual}")
        if rule["engine"]=="local" and field=="sample":
            allowed_reasons={rule["id"].replace("-","_")}
            if rule["id"]=="training-patterns":
                allowed_reasons.add("regex check input")
            if actual=="rewrite":
                if "example.invalid" in detail:
                    raise ValueError("Unapproved host survived the rewrite.")
            elif detail not in allowed_reasons:
                raise ValueError(f"The sample hit {detail} before {rule['id']}.")
    print("PASS " + rule["id"] + " sample and control")


if __name__ == "__main__":
    # The caller pipes this script into the running NeMo container with a synthetic rule case.
    root = Path(sys.argv[1])
    chosen = json.loads(sys.argv[2])
    demonstrate(chosen, root, json.loads((root/"active_rules.json").read_text()), live=True)
