"""
run_all.py - evaluate Hawk end to end with one command. Safe to stop at any point: run it again to continue.

    python evals/run_all.py                                   # new run
    python evals/run_all.py --resume evals/results/<run_id>   # continue a run that stopped

Steps (each saves after every item and skips what is already done):
  1. answers   send the 188 test questions to Hawk, save every reply      -> raw.jsonl     (run_e2e.py)
  2. judge     label each reply answer / escalate / refuse                 -> judged.jsonl  (judge.py)
  3. score     confusion matrix, recall/precision, key facts, latency      -> stats.json    (score.py)
  4. contexts  rebuild the handbook text behind each answer                -> contexts.jsonl (dump_contexts.py)
  5. ragas     official RAGAS metrics (needs the eval venv, see below)     -> ragas.json    (ragas_eval.py)
  6. report    one HTML page with everything                               -> report.html   (report.py)

Needs: the chatbot venv (run this file with it), Ollama running with the chatbot model and the judge model
pulled, and for RAGAS a second venv:  python -m venv .venv-eval  then  .venv-eval pip install -r evals/requirements-eval.txt
and  ollama pull all-minilm.  Without that venv the RAGAS step is skipped (rerun later with --resume to add it).
If Hawk isn't already answering at --url, this starts it on that port and stops it after the answers.
On a laptop that overheats, add --pause 3 (seconds between questions).
"""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
N_QUESTIONS = sum(1 for l in (EVALS / "testset_v1.jsonl").open(encoding="utf-8") if l.strip() and not l.startswith("#"))


def http_ok(url, body=None, timeout=5):
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"},
                                     method="POST" if body else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def lines(p: Path) -> int:
    return sum(1 for l in p.open(encoding="utf-8") if l.strip()) if p.exists() else 0


def git(*args) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def chatbot_model() -> str:
    cfg = (ROOT / "ots-chatbot-backend" / "config" / "config.yaml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^model:\s*"?([^"\s#]+)', cfg)
    return m.group(1) if m else "unknown"


def ollama_models() -> set[str] | None:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
            return {m["name"] for m in json.loads(r.read())["models"]}
    except Exception:
        return None


def ollama_info(model: str) -> dict:
    """Ollama version and whether the loaded chatbot model sits in GPU memory (call after a warm-up request)."""
    info = {}
    try:
        with urllib.request.urlopen("http://localhost:11434/api/version", timeout=5) as r:
            info["Ollama version"] = json.loads(r.read()).get("version")
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=5) as r:
            for m in json.loads(r.read()).get("models", []):
                if m["name"] in (model, f"{model}:latest"):
                    info["Model runs on"] = "GPU" if m.get("size_vram", 0) > 0 else "CPU"
    except Exception:
        pass
    return info


def eval_python(given: str | None) -> str | None:
    if given:
        return given
    for p in (ROOT / ".venv-eval" / "Scripts" / "python.exe", ROOT / ".venv-eval" / "bin" / "python"):
        if p.exists():
            return str(p)
    return None


def step(name: str, args: list[str], py: str = sys.executable):
    print(f"\n=== {name} ===", flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    rc = subprocess.run([py, *args], cwd=ROOT, env=env).returncode
    if rc:
        sys.exit(f"\n{name} stopped (exit {rc}). Fix the cause, then run again with --resume to continue.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", type=Path, help="results folder of a run to continue")
    ap.add_argument("--url", default="http://localhost:8001", help="where Hawk answers (started here if not running)")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between questions")
    ap.add_argument("--judge", default="gemma4:e4b", help="Ollama model that labels replies and grades RAGAS")
    ap.add_argument("--eval-python", help="python of the RAGAS venv (default: .venv-eval)")
    ap.add_argument("--skip-ragas", action="store_true")
    a = ap.parse_args()

    models = ollama_models()
    if models is None:
        sys.exit("Ollama isn't answering on localhost:11434. Start Ollama first.")
    for m in {chatbot_model(), a.judge}:
        if m not in models and f"{m}:latest" not in models:
            sys.exit(f"Ollama model '{m}' isn't pulled. Run: ollama pull {m}")

    run_dir = a.resume or EVALS / "results" / f"{datetime.now():%Y%m%d_%H%M}_{git('rev-parse', '--short', 'HEAD')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = run_dir / "run.json"
    if not meta.exists():
        meta.write_text(json.dumps({
            "Code": f"{git('rev-parse', '--abbrev-ref', 'HEAD')} @ {git('rev-parse', '--short', 'HEAD')}",
            "Chatbot model": chatbot_model(),
            "Judge model (labels + RAGAS)": a.judge,
            "Machine": f"{platform.system()} {platform.release()}, {platform.processor() or platform.machine()}",
            "Python": platform.python_version(),
            "Search picker time limit (HAWK_PICKER_TIMEOUT_S)": os.getenv("HAWK_PICKER_TIMEOUT_S", "config default (5 s)"),
            "Pause between questions (s)": a.pause,
            "Started": datetime.now().isoformat(timespec="seconds"),
        }, indent=1), encoding="utf-8")
    print(f"Run folder: {run_dir}")

    # 1. answers
    if lines(run_dir / "raw.jsonl") < N_QUESTIONS:
        server = None
        if not http_ok(a.url + "/"):
            port = a.url.rsplit(":", 1)[-1].strip("/")
            print(f"Starting Hawk on port {port} ...", flush=True)
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--app-dir",
                                       str(ROOT / "ots-chatbot-backend"), "--port", port],
                                      stdout=open(run_dir / "server.log", "a"), stderr=subprocess.STDOUT)
            for _ in range(150):
                if http_ok(a.url + "/"):
                    break
                time.sleep(2)
            else:
                sys.exit(f"Hawk didn't start; see {run_dir / 'server.log'}")
        try:
            http_ok(a.url + "/api/chat", json.dumps({"messages": [{"role": "user", "text": "hi"}]}).encode(), 600)
            info = ollama_info(chatbot_model())
            if info:
                meta.write_text(json.dumps({**json.loads(meta.read_text(encoding="utf-8")), **info}, indent=1),
                                encoding="utf-8")
            if info.get("Model runs on") == "CPU" and not os.getenv("HAWK_PICKER_TIMEOUT_S"):
                print("WARNING: the model runs on the CPU, where the search picker's 5 s limit is too short and Hawk "
                      "falls back to keyword routing, so answers differ from a GPU run. For comparable results stop "
                      "here, set HAWK_PICKER_TIMEOUT_S=30 (see README), and start again.", flush=True)
            os.environ["HAWK_URL"] = a.url
            step("answers", ["evals/run_e2e.py", "--resume", str(run_dir), "--pause", str(a.pause)])
        finally:
            if server:
                server.terminate()
                server.wait(timeout=30)
    if lines(run_dir / "raw.jsonl") < N_QUESTIONS:
        sys.exit("Not every question has an answer yet (errors?). Run again with --resume.")

    # 2-4. judge, score, contexts
    if lines(run_dir / "judged.jsonl") < N_QUESTIONS:
        step("judge", ["evals/judge.py", str(run_dir), "--judge", a.judge])
    step("score", ["evals/score.py", str(run_dir)])
    step("contexts", ["evals/dump_contexts.py", str(run_dir)])

    # 5. RAGAS
    py = eval_python(a.eval_python)
    if a.skip_ragas:
        print("\nRAGAS skipped (--skip-ragas).")
    elif py is None:
        print("\nRAGAS skipped: no eval venv. Create .venv-eval (see the top of this file), then run again with --resume.")
    else:
        if "all-minilm:latest" not in models:
            sys.exit("RAGAS needs the embedding model. Run: ollama pull all-minilm")
        step("ragas", ["evals/ragas_eval.py", str(run_dir), "--judge", a.judge, "--pause", str(a.pause)], py=py)

    # 6. report
    step("report", ["evals/report.py", str(run_dir)])
    print(f"\nDone. Open {run_dir / 'report.html'}")


if __name__ == "__main__":
    main()
