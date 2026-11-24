"""Render one NeMo configuration. No provider credentials enter the ConfigMap."""
from pathlib import Path
import re
import yaml

POLICIES = Path(__file__).resolve().parents[1] / "policies"
STAGES = ("bootstrap", "rules", "content")


def render(stage, model, url, subscription="", classifier_url="http://nemotron-safety-guard-vllm:8000/v1"):
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage}")
    parameters = {"base_url": url}
    if subscription:
        parameters["default_headers"] = {"X-MaaS-Subscription": subscription}
    main = {"type": "main", "engine": "openai", "model": model,
            "api_key_env_var": "OPENAI_API_KEY", "parameters": parameters}
    config = {"colang_version": "1.0", "passthrough": True, "models": [main],
              "rails": {"input": {"flows": []}, "output": {"flows": [], "streaming": {"enabled": False}}}}
    data = {"rails.co": (POLICIES / "rails.co").read_text()}
    if stage != "bootstrap":
        rules = yaml.safe_load((POLICIES / "rules.yaml").read_text())
        for pattern in rules["patterns"]:
            re.compile(pattern)
        rails = config["rails"]
        rails["config"] = {"regex_detection": {"input": {"patterns": rules["patterns"], "case_insensitive": True}}}
        rails["input"]["flows"] = list(rules["input"])
        rails["output"]["flows"] = list(rules["output"])
        data["banking.co"] = (POLICIES / "banking.co").read_text()
        data["actions.py"] = (POLICIES / "actions.py").read_text()
        if stage == "content":
            safety = yaml.safe_load((POLICIES / "content.yaml").read_text())
            config["models"].append({"type": "content_safety", "engine": "openai", "model": safety["model"],
                                     "api_key_env_var": "NEMOTRON_API_KEY", "parameters": {"base_url": classifier_url}})
            for direction in ("input", "output"):
                rails[direction]["flows"].extend(safety[direction])
            data["prompts.yml"] = (POLICIES / "prompts.yml").read_text()
    data["config.yaml"] = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    return data
