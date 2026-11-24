"""Render the selected NeMo policy. Provider credentials stay in Secrets."""
from pathlib import Path
import json
import re
import yaml
import rulebook

POLICIES = Path(__file__).resolve().parents[1] / "policies"
STAGES = ("bootstrap", "rules", "content")


def render(stage, model, url, subscription="", classifier_url="http://nemotron-safety-guard-vllm:8000/v1", active_ids=None):
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage}")
    parameters = {"base_url": url}
    if subscription:
        parameters["default_headers"] = {"X-MaaS-Subscription": subscription}
    main = {"type": "main", "engine": "openai", "model": model, "api_key_env_var": "OPENAI_API_KEY", "parameters": parameters}
    config = {"colang_version": "1.0", "passthrough": True, "models": [main],
              "rails": {"input": {"flows": []}, "output": {"flows": [], "streaming": {"enabled": False}}}}
    data = {"rails.co": (POLICIES / "rails.co").read_text()}
    if stage != "bootstrap":
        _, entries = rulebook.catalog()
        active = rulebook.resolve(entries, rulebook.selection(entries)) if active_ids is None else set(active_ids)
        rules = yaml.safe_load((POLICIES / "rules.yaml").read_text())
        for pattern in rules["patterns"]:
            re.compile(pattern)
        rails = config["rails"]
        rails["config"] = {"regex_detection": {"input": {"patterns": rules["patterns"], "case_insensitive": True}}}
        rails["input"]["flows"] = [f for f in rules["input"] if f != "regex check input" or "training-patterns" in active]
        rails["output"]["flows"] = list(rules["output"])
        for name in ("banking.co", "banking.py", "actions.py"):
            data[name] = (POLICIES / name).read_text()
        data["active_rules.json"] = json.dumps(rulebook.runtime(entries, active, rules["patterns"], stage), sort_keys=True)
        if stage == "content":
            safety = yaml.safe_load((POLICIES / "content.yaml").read_text())
            config["models"].append({"type": "content_safety", "engine": "openai", "model": safety["model"],
                                     "api_key_env_var": "NEMOTRON_API_KEY", "parameters": {"base_url": classifier_url}})
            for direction in ("input", "output"):
                if "model-" + direction in active:
                    rails[direction]["flows"].extend(safety[direction])
            data["prompts.yml"] = rulebook.prompts(entries, active)
    data["config.yaml"] = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    return data
