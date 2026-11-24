"""Run the real agent and NeMo over loopback with a deterministic model service."""
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError

from model_double import server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rulebook
import rule_sample
from policy import render
from verify import OUTPUT, PRIVACY, chat, request, require, run


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def service(args, env, cwd, log, ready):
    with log.open("w+") as stream:
        proc = subprocess.Popen(args, env=env, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
        try:
            for _ in range(180):
                if proc.poll() is not None:
                    raise RuntimeError(f"{args[0]} exited")
                try:
                    request(ready)
                    break
                except (URLError, OSError):
                    time.sleep(0.5)
            else:
                raise RuntimeError(f"startup timed out for {ready}")
            yield
        except Exception:
            stream.flush()
            stream.seek(0)
            print(stream.read()[-6500:], file=sys.stderr)
            raise
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=12)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main():
    fixture = server()
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{fixture.server_port}/v1"
    env = dict(os.environ, OPENAI_API_KEY="fixture-main", NEMOTRON_API_KEY="fixture-safety", API_KEY="fixture-local",
               MODEL_ID="workshop-main", PYTHONDONTWRITEBYTECODE="1", TOKENIZERS_PARALLELISM="false")
    env.pop("MLFLOW_TRACKING_URI", None)
    for key in list(env):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            del env[key]
    env["NO_PROXY"] = "localhost,127.0.0.1"
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            nemo_bin = os.getenv("GRLAB_NEMO_BIN", str(Path(sys.executable).parent / "nemoguardrails"))
            agent_python = os.getenv("GRLAB_AGENT_PYTHON", sys.executable)
            stages = sys.argv[1:] or ["direct", "bootstrap", "rules", "content", "appendix-rules", "appendix-content"]
            _, catalog = rulebook.catalog()
            for name in stages:
                appendix = name.startswith("appendix-")
                stage = name.removeprefix("appendix-")
                selected = None
                if appendix:
                    selected = (rulebook.defaults(catalog) | {"marketing-consent","collections-tone","s16"}) - {"training-patterns"}
                with contextlib.ExitStack() as active:
                    proxy = None
                    if stage != "direct":
                        folder = tmp / name / "configs" / stage
                        folder.mkdir(parents=True)
                        data = render(stage, "workshop-main", upstream, subscription="workshop-demo", classifier_url=upstream, active_ids=selected)
                        for filename, value in data.items():
                            (folder / filename).write_text(value)
                        p = port()
                        proxy = f"http://127.0.0.1:{p}/v1"
                        active.enter_context(service([nemo_bin, "server",
                            "--config", str(folder.parent), "--default-config-id", stage, "--port", str(p),
                            "--disable-chat-ui"], env, tmp, tmp / "proxy.log", proxy + "/rails/configs"))
                    a = port()
                    agent = f"http://127.0.0.1:{a}"
                    active.enter_context(service([agent_python, "-m", "uvicorn", "main:app", "--port", str(a)],
                        dict(env, BASE_URL=proxy or upstream, API_KEY="fixture-local" if proxy else "fixture-main",
                             MAAS_SUBSCRIPTION="" if proxy else "workshop-demo", GRLAB_BACKEND_MODE="direct" if stage == "direct" else "proxy"),
                        ROOT / "app", tmp / "agent.log", agent + "/health"))
                    fixture.calls.clear()
                    run(stage, agent, proxy, "workshop-main", active_ids=selected)
                    path = agent + "/chat/completions"
                    if appendix:
                        fixture.calls.clear()
                        require(chat(path,catalog["marketing-consent"]["sample"]).startswith(PRIVACY) and not fixture.calls,
                                "opt-in marketing consent input rule")
                        require(chat(path,"Please try a collections draft rehearsal.").startswith(OUTPUT), "opt-in collections output rule")
                        require("secure reset steps" in chat(path,catalog["training-patterns"]["sample"]), "removed rule still blocking")
                        if stage == "content":
                            require(chat(path, catalog["s16"]["sample"]).startswith("I can help with supported banking questions"), "S16 input through app")
                            saved_key = os.environ.get("NEMOTRON_API_KEY")
                            try:
                                os.environ["NEMOTRON_API_KEY"] = env["NEMOTRON_API_KEY"]
                                rule_sample.demonstrate(catalog["s16"], folder, json.loads(data["active_rules.json"]), live=True)
                            finally:
                                if saved_key is None:
                                    os.environ.pop("NEMOTRON_API_KEY",None)
                                else:
                                    os.environ["NEMOTRON_API_KEY"] = saved_key
                        print("PASS local integration", name, flush=True)
                        continue
                    if stage in ("rules", "content"):
                        fixture.calls.clear()
                        require(chat(path, "My OTP is 000000.").startswith(PRIVACY) and not fixture.calls,
                                "local privacy rule must stop before any model call")
                        require(chat(path, "Run the output canary exercise.").startswith(OUTPUT), "local output canary escaped")
                        fixture.calls.clear()
                        if stage == "rules":
                            text = chat(path, "Please request the training override.")
                            require(not fixture.calls and "secure reset steps" not in text, "regex input should stop inference")
                        else:
                            text = chat(path, "Please run a classifier output rehearsal.")
                            require(text.startswith(OUTPUT) and fixture.calls == ["classifier", "main", "classifier"],
                                    "classifier output did not block after input and generation")
                            require("guardrail:content_safety_input:invalid_classifier_verdict" in
                                    chat(proxy + "/chat/completions", "Please run a malformed verdict rehearsal.", "workshop-main"),
                                    "malformed model output was counted as a valid safety prediction")
                            fixture.calls.clear()
                            text = chat(path, "Please run a malformed verdict rehearsal.")
                            require(fixture.calls == ["classifier"] and "secure reset steps" not in text,
                                    "malformed classifier output must fail closed")
                    print("PASS local integration", stage, flush=True)
    finally:
        fixture.shutdown()
        fixture.server_close()


if __name__ == "__main__":
    main()
