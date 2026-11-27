"""Browse workshop rules, edit a participant's choices and publish one policy revision."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
import yaml

import rulebook
from rule_sample import demonstrate

def current(lab, proxy):
    if not proxy:
        return None, {}
    volumes = proxy["spec"]["template"]["spec"]["volumes"]
    name = next(v["configMap"]["name"] for v in volumes if v["name"] == "policy")
    doc = lab.get("configmap", name)
    if not doc:
        raise RuntimeError("The active NeMo ConfigMap is missing.")
    return name, doc["data"]

def selected(data):
    raw = data.get("active_rules.json")
    return json.loads(raw)["active"] if raw else None

def revise(lab, dry, rules, edits):
    from policy import render
    proxy = lab.get("deployment","grlab-rails")
    stage = (proxy or {}).get("metadata",{}).get("annotations",{}).get(lab.PROFILE)
    if stage not in ("rules","content"):
        raise RuntimeError("Complete Module 2 or Module 3 before publishing appendix rules.")
    old_name, old_data = current(lab, proxy)
    previous = selected(old_data)
    baseline = rulebook.defaults(rules) if previous is None else set(previous)
    desired = rulebook.resolve(rules, edits, baseline)
    print("Profile:",stage, "| add:",", ".join(sorted(desired-baseline)) or "none","| remove:",", ".join(sorted(baseline-desired)) or "none")
    model_edits = [k for k in edits["enable"]+edits["disable"] if rules[k]["engine"]=="model"]
    if stage == "rules" and model_edits:
        print("Classifier selections are saved for Module 3. The rules profile makes zero classifier calls.")
    provider = lab.get("configmap","grlab-provider")["data"]
    data = render(stage,provider["model"],provider["url"],provider.get("subscription",""),active_ids=desired)
    # A category edit preserves the live Module 2 teaching phrases, including edits
    # made by an earlier terminal. The regular rules stage can publish new file edits.
    live_cfg = yaml.safe_load(old_data["config.yaml"])
    live_regex = live_cfg.get("rails",{}).get("config",{}).get("regex_detection",{}).get("input",{}).get("patterns")
    if live_regex is not None:
        cfg = yaml.safe_load(data["config.yaml"])
        cfg["rails"]["config"]["regex_detection"]["input"]["patterns"] = live_regex
        data["config.yaml"] = yaml.safe_dump(cfg,sort_keys=False,allow_unicode=True)
        spec = json.loads(data["active_rules.json"])
        spec["keyword_patterns"] = live_regex
        data["active_rules.json"] = json.dumps(spec, sort_keys=True)
    if "tracing" in live_cfg:
        cfg = yaml.safe_load(data["config.yaml"])
        cfg["tracing"] = live_cfg["tracing"]
        data["config.yaml"] = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    digest = hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:12]
    new_name = f"grlab-{stage}-{digest}"
    print("NeMo ConfigMap:",old_name,"→",new_name)
    if dry or old_name==new_name:
        return
    lab.apply(lab.resource("v1","ConfigMap",new_name,data=data,immutable=True))
    pod = proxy["spec"]["template"]["spec"]
    vi = next(i for i,v in enumerate(pod["volumes"]) if v["name"]=="policy")
    ci = next(i for i,c in enumerate(pod["containers"]) if c["name"]=="nemo")
    before = pod["containers"][ci]["volumeMounts"]
    after = [v for v in before if v["name"]!="policy"] + [
        {"name":"policy","subPath":key,"mountPath":f"/opt/rail-configs/{stage}/{key}","readOnly":True} for key in sorted(data)]
    volume = f"/spec/template/spec/volumes/{vi}/configMap/name"
    mounts = f"/spec/template/spec/containers/{ci}/volumeMounts"
    def switch(expected, target, old_mounts, new_mounts):
        ops = [{"op":"test","path":volume,"value":expected},{"op":"test","path":mounts,"value":old_mounts},
               {"op":"replace","path":volume,"value":target},{"op":"replace","path":mounts,"value":new_mounts}]
        lab.oc("patch","deploy/grlab-rails","--type=json","--patch-file=/dev/stdin",data=json.dumps(ops))
    switch(old_name,new_name,before,after)
    try:
        lab.rollout("grlab-rails")
        lab.verify(stage)
    except (OSError,RuntimeError,subprocess.CalledProcessError):
        print("Verification failed. Restoring the preceding NeMo revision if it is still current.")
        switch(new_name,old_name,after,before)
        lab.rollout("grlab-rails")
        raise

def run(lab, argv):
    cats, rules = rulebook.catalog()
    parser = argparse.ArgumentParser(prog="grlab catalog", description=__doc__)
    sub = parser.add_subparsers(dest="op")
    ls = sub.add_parser("list",help="browse available rules offline")
    ls.add_argument("category",nargs="?",choices=tuple(cats))
    for op, help_text in (("show","read an example and its expected outcome"),("sample","rehearse a local rule in isolation"),
                          ("add","stage one rule"),("remove","stage removal of one rule"),("test","check the deployed rule sample and control")):
        sub.add_parser(op,help=help_text).add_argument("id",metavar="RULE")
    baseline = sub.add_parser("baseline", help="stage an empty catalog or the workshop defaults")
    baseline.add_argument("mode", choices=("empty", "defaults"))
    sub.add_parser("choose",help="select a category and rule interactively")
    sub.add_parser("status",help="compare local edits with the live profile")
    sub.add_parser("apply",help="publish changes to the current rules or content profile").add_argument("--dry-run",action="store_true")
    args = parser.parse_args(argv)
    op = args.op or "list"
    if op == "baseline":
        rulebook.baseline(args.mode, rules)
        print("Staged the " + args.mode + " catalog. Review ./grlab catalog apply --dry-run before publishing.")
        return
    if op=="choose":
        for number,(key,name) in enumerate(cats.items(),1):
            print(f"{number}. {name} ({key})")
        value=input("Category name or number: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(cats):
            value=list(cats)[int(value)-1]
        if value not in cats:
            raise ValueError("Unknown category")
        choices=[k for k,r in rules.items() if r["category"]==value]
        for n,k in enumerate(choices,1):
            print(f"{n}. {rules[k]['title']} ({k})")
        key=input("Rule ID or number: ").strip()
        if key.isdigit() and 1 <= int(key) <= len(choices):
            key=choices[int(key)-1]
        if key not in choices:
            raise ValueError("Unknown rule for this category.")
        print(rulebook.describe(rules[key]))
        choice=input("Enter add, remove, sample, or press Enter to leave: ").strip()
        if not choice:
            return
        if choice not in ("add","remove","sample"):
            raise ValueError("Unknown selection.")
        op,args.id=choice,key
    edits = rulebook.selection(rules)
    if op=="list":
        for c,title in cats.items():
            if getattr(args,"category",None) not in (None,c):
                continue
            print(f"\n{title} [{c}]")
            for key,row in rules.items():
                if row["category"]==c:
                    print(f"  {key:30} {row['phase']:7} {row['title']}")
        return
    if op in ("apply","status","test"):
        lab.check_target()
    if op=="apply":
        revise(lab,args.dry_run,rules,edits)
        return
    if op=="status":
        dep=lab.get("deployment","grlab-rails")
        stage=(dep or {}).get("metadata",{}).get("annotations",{}).get(lab.PROFILE)
        _,data=current(lab,dep)
        base=selected(data)
        print("Active profile:", stage or "none")
        print("Live enabled rules:",", ".join(sorted(rules[k]["id"] for k in (base if base is not None else rulebook.defaults(rules)) if k in rules and (stage=="content" or (stage=="rules" and rules[k]["engine"]=="local")))) or "none")
        if stage == "rules":
            stored = base if base is not None else rulebook.defaults(rules)
            print("Model choices saved for Module 3:", ", ".join(sorted(k for k in stored if k in rules and rules[k]["engine"]=="model")) or "none")
        print("Local enable overrides:",", ".join(edits["enable"]) or "none")
        print("Local disable overrides:",", ".join(edits["disable"]) or "none")
        return
    if args.id not in rules:
        parser.error("unknown rule ID. Browse with ./grlab catalog list")
    row=rules[args.id]
    print(rulebook.describe(row))
    if op in ("add","remove"):
        rulebook.change(args.id,op=="add",rules)
        print("Saved locally. Preview with ./grlab catalog apply --dry-run and publish with ./grlab catalog apply.")
    elif op=="sample":
        patterns=yaml.safe_load((rulebook.POLICIES/"rules.yaml").read_text())["patterns"]
        spec=rulebook.runtime(rules,{args.id},patterns,"rules")
        demonstrate(row,rulebook.POLICIES,spec)
    elif op=="test":
        proxy=lab.get("deployment","grlab-rails")
        stage=(proxy or {}).get("metadata",{}).get("annotations",{}).get(lab.PROFILE)
        if stage not in ("rules","content") or (row["engine"]=="model" and stage!="content"):
            raise RuntimeError("Deploy Module 3 for a classifier rule or Module 2 for a local rule.")
        _,data=current(lab,proxy)
        if "active_rules.json" not in data:
            raise RuntimeError("Run ./grlab catalog apply to upgrade the live policy to this edition.")
        if args.id not in selected(data):
            raise RuntimeError("Rule is disabled. Stage it with catalog add, then run catalog apply.")
        # Test against the active ConfigMap from the participant Terminal. This
        # follows the OAuth token's project scope and does not require pods/exec.
        with tempfile.TemporaryDirectory(prefix="grlab-rule-check-") as tmp:
            folder=Path(tmp)
            for name in ("banking.py","active_rules.json","config.yaml","prompts.yml"):
                if name in data:
                    (folder/name).write_text(data[name])
            if not (folder/"banking.py").is_file():
                raise RuntimeError("The active policy ConfigMap is missing banking.py.")
            if row["engine"]=="model":
                if not (folder/"config.yaml").is_file() or not (folder/"prompts.yml").is_file():
                    raise RuntimeError("The active content profile is missing its model configuration.")
                os.environ.setdefault("NEMOTRON_API_KEY","workshop-local")
            demonstrate(row,folder,json.loads(data["active_rules.json"]),live=True)
