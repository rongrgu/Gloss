"""Stand-in for the `claude` executable in provider tests.

Behavior is chosen by FAKE_CLAUDE_MODE; argv and stdin are appended to FAKE_CLAUDE_LOG as JSON.
Event shapes copied from real `claude -p --output-format stream-json --verbose --include-partial-messages`
output (DESIGN.md §2.1).
"""

import json
import os
import sys
import time

args = sys.argv[1:]
stdin = sys.stdin.read()
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps({"args": args, "stdin": stdin, "has_api_key": "ANTHROPIC_API_KEY" in os.environ}) + "\n")

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
sid = args[args.index("--session-id") + 1] if "--session-id" in args else (
    args[args.index("--resume") + 1] if "--resume" in args else "ephemeral-session")


def out(ev):
    print(json.dumps(ev), flush=True)


def stream(ev):
    out({"type": "stream_event", "event": ev, "parent_tool_use_id": None, "session_id": sid})


def message(*chunks):
    stream({"type": "message_start", "message": {"content": []}})
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    for c in chunks:
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": c}})
    stream({"type": "message_stop"})


def result(text, is_error=False, **kw):
    out({"type": "result", "subtype": "success", "is_error": is_error, "result": text, "session_id": sid,
         "total_cost_usd": 0.01, **kw})


if mode == "missing" and "--resume" in args:
    # Real stream-json output for an unknown session (claude 2.1.299).
    msg = f"No conversation found with session ID: {sid}"
    sys.stderr.write(msg + "\n")
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": None,
         "errors": [msg], "session_id": sid})
    sys.exit(1)

out({"type": "system", "subtype": "init", "model": "claude-fake-1", "session_id": sid})

if mode in ("ok", "missing"):
    message("Hel", "lo")
    result("Hello")
elif mode == "tooluse":
    message("Let me look.")
    # subagent-ish noise that must be ignored
    out({"type": "stream_event", "parent_tool_use_id": "toolu_1",
         "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "IGNORED"}}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 200_000}]}})
    message("Final ", "answer")
    result("Final answer")
elif mode == "structured":
    data = {"echo_schema": json.loads(args[args.index("--json-schema") + 1])}
    result(json.dumps(data), structured_output=data)
elif mode == "error":
    result("Invalid API key · Fix external API key", is_error=True, api_error_status=401)
    sys.exit(1)
elif mode == "crash":
    sys.stderr.write("something exploded\n")
    sys.exit(2)
elif mode == "hang":
    message("partial")
    time.sleep(30)
