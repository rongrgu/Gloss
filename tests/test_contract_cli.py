"""Contract tests against the real CLIs (use a little quota). Off by default:

    GLOSS_CONTRACT=1 uv run pytest tests/test_contract_cli.py            # every CLI that is installed
    GLOSS_CONTRACT=claude uv run pytest tests/test_contract_cli.py       # just one

Run after upgrading claude or codex: a short prompt must give session -> text -> done, and a
resume must continue the same conversation.
"""

import asyncio
import os

import pytest

from gloss.providers import RunRequest
from gloss.providers.adapters import ADAPTERS
from gloss.providers.cli_runner import CLIProvider

WANT = os.environ.get("GLOSS_CONTRACT", "")
MODELS = {"claude": "haiku", "codex": None}


def provider(name, tmp_path):
    adapter = ADAPTERS[name]()
    if not adapter.find():
        pytest.skip(f"{name} is not installed")
    return CLIProvider(adapter, tmp_path, model=MODELS[name], timeout=180)


def collect(p, req):
    async def go():
        return [e async for e in p.run(req)]
    return asyncio.run(go())


@pytest.mark.skipif(not WANT, reason="set GLOSS_CONTRACT=1 (or a CLI name) to run against real CLIs")
@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_session_text_done_then_resume(name, tmp_path):
    if WANT not in ("1", name):
        pytest.skip(f"GLOSS_CONTRACT={WANT}")
    p = provider(name, tmp_path)
    first = collect(p, RunRequest("Remember the number 7341. Reply with exactly: ok", persist=True,
                                  system="Be very brief.", cwd=tmp_path))
    kinds = [e.kind for e in first]
    assert kinds[0] == "session" and kinds[-1] == "done", first[-1]
    assert any(k in ("text_delta", "text_final") for k in kinds)
    sid = first[-1].session_id or first[0].session_id
    second = collect(p, RunRequest("What number did I ask you to remember? Digits only.", session_id=sid, cwd=tmp_path))
    assert second[-1].kind == "done", second[-1]
    assert "7341" in second[-1].text
