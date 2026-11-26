"""Stand-in for the `codex` executable (`codex exec --json …`). Mode from FAKE_CODEX_MODE.
argv, stdin and env flags are appended to FAKE_CODEX_LOG as JSON; the pid goes to FAKE_CODEX_PID if set.
Event shapes follow codex 0.172.0 exec_events.rs (not recorded from a real run: codex isn't installed here)."""

import json
import os
import sys
import time

args = sys.argv[1:]
stdin = sys.stdin.read()
with open(os.environ["FAKE_CODEX_LOG"], "a") as f:
    f.write(json.dumps({"args": args, "stdin": stdin, "cwd": os.getcwd(),
                        "has_openai_key": "OPENAI_API_KEY" in os.environ,
                        "has_codex_key": "CODEX_API_KEY" in os.environ}) + "\n")
if os.environ.get("FAKE_CODEX_PID"):
    open(os.environ["FAKE_CODEX_PID"], "w").write(str(os.getpid()))
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
tid = args[args.index("resume") + 1] if "resume" in args else "0199a213-0000-7000-8000-000000000001"


def out(ev):
    print(json.dumps(ev), flush=True)


if mode == "missing" and "resume" in args:
    sys.stderr.write(f"Error: no rollout found for thread id {tid}\n")
    sys.exit(1)
out({"type": "thread.started", "thread_id": tid})
out({"type": "turn.started"})
if mode == "ok":
    out({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Hello"}})
    out({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 2}})
elif mode == "structured":
    schema = json.load(open(args[args.index("--output-schema") + 1]))
    out({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                            "text": json.dumps({"title": "T", "subtitle": None, "echo": schema})}})
    out({"type": "turn.completed", "usage": {}})
elif mode == "failed":
    out({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again later."}})
    sys.exit(1)
elif mode == "hang":
    out({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "partial"}})
    time.sleep(60)
