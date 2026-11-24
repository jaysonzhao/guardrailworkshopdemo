"""Use the rule appendix before, during or after the numbered workshop modules."""
import argparse
import json
import subprocess
import sys

import catalog_cli
import rulebook
from policy import render

GUARDED = {"rules", "content"}


def state(lab):
    agent = lab.get("deployment", "grlab-agent")
    proxy = lab.get("deployment", "grlab-rails")
    profile = (proxy or {}).get("metadata", {}).get("annotations", {}).get(lab.PROFILE)
    return agent, proxy, profile


def mode(agent):
    if agent:
        for container in agent["spec"]["template"]["spec"]["containers"]:
            if container["name"] == "agent":
                return next((v.get("value") for v in container.get("env", []) if v["name"] == "GRLAB_BACKEND_MODE"), None)
    return None


def describe_state(lab, agent, proxy, profile):
    print(f"Project: {lab.NS} | Banking app: {mode(agent) or ('unrecognized' if agent else 'absent')} | "
          f"NeMo: {profile or ('unrecognized' if proxy else 'absent')}")


def read_state(lab, local, required=False):
    if local:
        return None
    if not lab.NS and not required:
        print("Local workspace. Set LAB_NAMESPACE and sign in with oc to include project state.")
        return None
    try:
        lab.check_target()
        result = state(lab)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        if required:
            raise
        print(f"Project state unavailable ({exc}). Showing local choices only.", file=sys.stderr)
        return None
    describe_state(lab, *result)
    return result


def check_owned(lab, agent, proxy, profile):
    for name, resource in (("grlab-agent", agent), ("grlab-rails", proxy)):
        if resource and not all(resource["metadata"].get("labels", {}).get(k) == v for k, v in lab.PART_OF.items()):
            raise RuntimeError(f"{name} in {lab.NS} has a different owner. Inspect the deployment or choose the workshop project.")
    if proxy and profile not in {"bootstrap", *GUARDED}:
        raise RuntimeError(f"grlab-rails in {lab.NS} has an unrecognized {lab.PROFILE} annotation. Inspect the deployment.")
    if agent and mode(agent) not in ("proxy", "direct"):
        raise RuntimeError("The workshop agent has an unrecognized backend mode. Inspect deploy/grlab-agent.")


def target_profile(profile, with_content):
    return "content" if with_content or profile == "content" else "rules"


def classifier_needs_start(lab):
    workload = lab.get("deployment","nemotron-safety-guard-vllm")
    return not workload or workload.get("spec",{}).get("replicas",1) == 0 or not workload.get("status",{}).get("availableReplicas",0)


def plan(lab, current, rules, edits, with_content):
    agent, proxy, profile = current if current else (None, None, None)
    target = target_profile(profile, with_content)
    if current:
        check_owned(lab, agent, proxy, profile)
    prior = None
    if profile in GUARDED:
        _, mounted = catalog_cli.current(lab, proxy)
        prior = catalog_cli.selected(mounted)
        # Older participant checkouts use their original catalog when the in-place updater is used.
        if hasattr(rulebook, "include_mounted"):
            rules = rulebook.include_mounted(rules, mounted)
    base = rulebook.defaults(rules) if prior is None else set(prior)
    chosen = rulebook.resolve(rules, edits, base)
    # Rendering validates flow settings and classifier choices without provider access or an API token.
    render(target, "preview-model", "http://preview.invalid/v1", active_ids=chosen)
    local_ids = sorted(key for key in chosen if rules[key]["engine"] == "local")
    model_ids = sorted(chosen - set(local_ids))
    print(f"Target profile: {target} | Baseline: {'mounted policy' if prior is not None else 'workshop defaults'}")
    print("Enable:", ", ".join(sorted(chosen - base)) or "none")
    print("Disable:", ", ".join(sorted(base - chosen)) or "none")
    print(f"Selected local rules ({len(local_ids)}):", ", ".join(local_ids) or "none")
    model_label = "Selected classifier settings/categories" if target == "content" else "Saved classifier choices (inactive in rules mode)"
    print(f"{model_label} ({len(model_ids)}):", ", ".join(model_ids) or "none")
    if current:
        steps = []
        if not agent:
            steps.append("deploy and check the banking app using the main provider")
        if not proxy:
            steps.append("build and insert NeMo")
        if profile not in GUARDED:
            steps.append("activate the local rules")
        if profile in GUARDED and mode(agent) != "proxy":
            steps.append("reconnect the banking app to the existing NeMo service")
        if target == "content" and profile != "content":
            steps.append("deploy or reuse the GPU classifier, probe it and activate content safety")
        if with_content and profile == "content" and classifier_needs_start(lab):
            steps.append("restore and probe the optional classifier workload")
        if profile == target:
            steps.append("compare the mounted policy and publish only a changed revision")
        print("Live apply:", " → ".join(steps))
    else:
        print("Live changes require an authenticated workshop project. The exact deployment plan is available with oc access.")
    return target


def replace_env(lab, index, previous, new):
    path = f"/spec/template/spec/containers/{index}/env"
    ops = [{"op":"test","path":path,"value":previous},{"op":"replace","path":path,"value":new}]
    lab.oc("patch", "deployment/grlab-agent", "--type=json", "--patch-file=/dev/stdin", data=json.dumps(ops))


def activate(lab, agent, profile):
    """Keep a working direct connection if the first cutover fails after changing the app."""
    try:
        lab.activate(profile)
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        after = lab.get("deployment", "grlab-agent")
        if mode(agent) == "direct" and mode(after) == "proxy":
            old = next(c for c in agent["spec"]["template"]["spec"]["containers"] if c["name"]=="agent")
            containers = after["spec"]["template"]["spec"]["containers"]
            i = next(i for i,c in enumerate(containers) if c["name"]=="agent")
            if containers[i]["image"] == old["image"]:
                managed = {"GRLAB_BACKEND_MODE", "BASE_URL", "API_KEY", "MODEL_ID", "MAAS_SUBSCRIPTION"}
                previous = [x for x in containers[i]["env"] if x["name"] not in managed]
                previous.extend(x for x in old["env"] if x["name"] in managed)
                replace_env(lab, i, containers[i]["env"], previous)
                lab.rollout("grlab-agent")
        raise


def attach_existing(lab, agent, profile):
    """Reconnect an explicitly published policy while preserving other app settings."""
    template = agent["spec"]["template"]["spec"]
    index = next(i for i,c in enumerate(template["containers"]) if c["name"] == "agent")
    old = template["containers"][index]["env"]
    wanted = {"BASE_URL": {"name":"BASE_URL","value":"http://grlab-rails/v1"},
              "API_KEY": {"name":"API_KEY","value":"workshop-local"},
              "GRLAB_BACKEND_MODE": {"name":"GRLAB_BACKEND_MODE","value":"proxy"}}
    env = [wanted.get(v["name"], v) for v in old if v["name"] != "MAAS_SUBSCRIPTION"]
    env.extend(v for name,v in wanted.items() if all(item["name"]!=name for item in env))
    replace_env(lab, index, old, env)
    try:
        lab.rollout("grlab-agent")
        lab.verify(profile)
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        replace_env(lab, index, env, old)
        lab.rollout("grlab-agent")
        raise


def publish(lab, current, target, rules, edits, with_content):
    # Re-read each transition before deciding whether it is needed. Existing content stays active.
    agent, proxy, profile = current
    if not agent:
        print("Preparing the banking app. A new project needs MAIN_BASE_URL, MAIN_MODEL_ID and grlab-main/api-key or MAIN_API_KEY_FILE.", flush=True)
        lab.pre()
        agent, proxy, profile = state(lab)
        check_owned(lab, agent, proxy, profile)
    if not proxy:
        activate(lab, agent, "bootstrap")
        agent, proxy, profile = state(lab)
        check_owned(lab, agent, proxy, profile)
    if profile == "bootstrap":
        activate(lab, agent, "rules")
        agent, proxy, profile = state(lab)
        check_owned(lab, agent, proxy, profile)
    if profile not in GUARDED or not agent:
        raise RuntimeError("The requested workshop stage did not become ready. Check its rollout and retry.")
    repair_classifier = with_content and profile == "content" and classifier_needs_start(lab)
    if repair_classifier:
        lab.model()
    if mode(agent) != "proxy":
        attach_existing(lab, agent, profile)
    elif repair_classifier:
        lab.verify("content")
    if target == "content" and profile != "content":
        activate(lab, agent, "content")
    # Regular module transitions consume selection.yaml. Reusing the mounted-policy publisher
    # also handles legacy configurations, concurrent edits, no-op revisions and rollback.
    catalog_cli.revise(lab, False, rules, edits)


def run(lab, argv):
    op = argv[0] if argv else "list"
    if op not in ("apply", "status", "test"):
        return catalog_cli.run(lab, argv)
    parser = argparse.ArgumentParser(prog=f"grlab catalog {op}")
    parser.add_argument("--local", action="store_true", help="use the local catalog without querying OpenShift")
    if op == "apply":
        parser.add_argument("--dry-run", action="store_true", help="preview staged choices and available project state without writes")
        parser.add_argument("--with-content", action="store_true", help="also provision or reuse the optional GPU classifier")
    if op == "test":
        parser.add_argument("id", metavar="RULE")
        parser.add_argument("--live", action="store_true", help="require evidence from the active policy")
    args = parser.parse_args(argv[1:])
    if op == "apply" and args.local and not args.dry_run:
        parser.error("Use --local with --dry-run. The add/remove commands already saved your choices locally.")
    if op == "test" and args.live and args.local:
        parser.error("Choose either --local or --live.")
    _, rules = rulebook.catalog()
    edits = rulebook.selection(rules)
    if op == "test" and args.id not in rules:
        parser.error("Unknown rule ID. Use ./grlab catalog list.")
    required = (op == "apply" and not args.dry_run) or (op == "test" and args.live)
    current = read_state(lab, args.local, required)
    if op == "status":
        if current:
            agent, proxy, profile = current
            if profile in GUARDED:
                return catalog_cli.run(lab, ["status"])
        print("Local enable overrides:", ", ".join(edits["enable"]) or "none")
        print("Local disable overrides:", ", ".join(edits["disable"]) or "none")
        return
    if op == "test":
        profile = current[2] if current else None
        available = profile in GUARDED and (rules[args.id]["engine"] == "local" or profile == "content")
        if available:
            try:
                check_owned(lab, *current)
                _, data = catalog_cli.current(lab, current[1])
                selected = catalog_cli.selected(data)
                available = selected is not None and args.id in selected
            except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
                if args.live:
                    raise
                print(f"Live test unavailable: {exc}.")
                available = False
        if available:
            return catalog_cli.run(lab, ["test", args.id])
        if args.live:
            raise RuntimeError("This rule is not active in the live runtime. Stage it and publish. Model checks require a content profile.")
        if rules[args.id]["engine"] == "model":
            print(rulebook.describe(rules[args.id]))
            print("EXAMPLE ONLY — no classifier inference ran. Live evidence requires catalog apply --with-content and catalog test --live "+args.id+".")
            return
        print("Local rehearsal: testing this rule in isolation. The running policy has not been tested.")
        return catalog_cli.run(lab, ["sample", args.id])
    if args.dry_run:
        try:
            target = plan(lab, current, rules, edits, args.with_content)
            if current and current[2] == target:
                catalog_cli.revise(lab, True, rules, edits)
        except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            if not current:
                raise
            print(f"Live comparison unavailable: {exc}. Showing a workspace-only preview.", file=sys.stderr)
            plan(lab, None, rules, edits, args.with_content)
        print("DRY RUN complete. No cluster resources or saved choices changed.")
        return
    target = plan(lab, current, rules, edits, args.with_content)
    publish(lab, current, target, rules, edits, args.with_content)
