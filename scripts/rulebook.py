"""Load the workshop rule catalog, manage a local selection and render runtime settings."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import yaml

POLICIES = Path(__file__).resolve().parents[1] / "policies"

def catalog():
    doc = yaml.safe_load((POLICIES / "catalog.yaml").read_text())
    rules = {}
    for rule in doc["rules"]:
        key = rule["id"]
        if not re.fullmatch(r"[a-z][a-z0-9-]*", key) or key in rules or rule["category"] not in doc["categories"]:
            raise ValueError(f"Invalid catalog entry {key}")
        if "pattern" in rule:
            re.compile(rule["pattern"], re.I)
        rules[key] = rule
    return doc["categories"], rules

def defaults(rules):
    return {key for key, rule in rules.items() if rule["default"]}

def selection(rules, path=None):
    path = path or POLICIES / "selection.yaml"
    obj = yaml.safe_load(path.read_text()) if path.exists() else {"enable": [], "disable": []}
    if not isinstance(obj, dict) or set(obj) != {"enable", "disable"}:
        raise ValueError("selection.yaml requires enable and disable lists.")
    for key in obj:
        if not isinstance(obj[key], list) or not all(isinstance(x, str) and x in rules for x in obj[key]):
            raise ValueError(f"Unknown or invalid rule in selection.yaml {key}")
    if set(obj["enable"]) & set(obj["disable"]):
        raise ValueError("A rule cannot appear in both selection lists.")
    return obj

def resolve(rules, edits, prior=None):
    base = defaults(rules) if prior is None else set(prior)
    unknown = base - rules.keys()
    if unknown:
        raise ValueError("Live policy contains rules unavailable in this ZIP: " + ", ".join(sorted(unknown)))
    return (base | set(edits["enable"])) - set(edits["disable"])

@contextmanager
def edit_lock(path):
    with (path.parent / ".grlab-selection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield

def change(key, enable, rules, path=None):
    path = path or POLICIES / "selection.yaml"
    if key not in rules:
        raise ValueError("Unknown rule " + key)
    with edit_lock(path):
        plan = selection(rules, path)
        for name in plan:
            plan[name] = [x for x in plan[name] if x != key]
        plan["enable" if enable else "disable"].append(key)
        save_selection(path, plan)


def save_selection(path, plan):
    """Caller holds the lock. Publish both override lists as one filesystem change."""
    plan = {key: sorted(values) for key, values in plan.items()}
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        name = Path(tmp.name)
        try:
            yaml.safe_dump(plan, tmp, sort_keys=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            name.unlink(missing_ok=True)
            raise
    try:
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def baseline(mode, rules, path=None):
    """Stage an explicit replacement so the next apply can also remove live choices."""
    path = path or POLICIES / "selection.yaml"
    if mode not in ("empty", "defaults"):
        raise ValueError("Choose empty or defaults.")
    enabled = defaults(rules) if mode == "defaults" else set()
    with edit_lock(path):
        save_selection(path, {"enable": enabled, "disable": set(rules) - enabled})


def runtime(rules, active, patterns, stage):
    return {"version": 1, "profile": stage, "active": sorted(active),
            "patterns": [{"id": key, "phase": row["phase"], "pattern": row["pattern"]}
                         for key, row in rules.items() if key in active and "pattern" in row],
            "keyword_patterns": list(patterns)}

def prompts(rules, active):
    template = yaml.safe_load((POLICIES / "prompts.yml").read_text())
    for item in template["prompts"]:
        p = item["content"]
        start = "<BEGIN UNSAFE CONTENT CATEGORIES>\n"
        end = "\n<END UNSAFE CONTENT CATEGORIES>"
        prefix, rest = p.split(start, 1)
        old, suffix = rest.split(end, 1)
        original = {line.split(":", 1)[0]: line for line in old.splitlines() if line}
        lines = []
        for key, row in rules.items():
            if key in active and "taxonomy" in row:
                code = row["taxonomy"]
                lines.append(original.get(code, f"{code}: {row['model_label']}. Assess {row['scope']}."))
        if "model-banking-policy" not in active:
            suffix = "\n\nAssess only the selected taxonomy. Return a JSON verdict.\n\n<BEGIN CONVERSATION>" + suffix.split("<BEGIN CONVERSATION>", 1)[1]
        if not lines and "model-banking-policy" not in active and active & {"model-input", "model-output"}:
            raise ValueError("Select a Nemotron category or the banking classifier policy before enabling a classifier direction.")
        item["content"] = prefix + start + "\n".join(lines) + end + suffix
    return yaml.safe_dump(template, sort_keys=False, allow_unicode=True)

def describe(row):
    kind = "candidate reply" if row["phase"] == "output" else "customer prompt"
    lines = [f"{row['id']} — {row['title']}", f"Category: {row['category']} | {row['engine']} | {row['phase']}",
             row["explanation"]]
    if "context" in row:
        lines.append("Customer context: " + row["context"])
    lines += [kind.capitalize() + ": " + row["sample"], "Expected: " + row["action"], "Allowed control: " + row["control"]]
    return "\n".join(lines)
