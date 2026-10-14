import json

import pytest
from fakes import FakeProvider
from fastapi.testclient import TestClient
from make_pdfs import make_deck

from gloss.config import REPO_ROOT, Config
from gloss.server import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSS_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    (ws / "prompts").mkdir(parents=True)
    src = REPO_ROOT / "claude_workspace"
    for p in [src / "CLAUDE.md", *(src / "prompts").glob("*.md")]:
        (ws / p.relative_to(src)).write_text(p.read_text())
    cfg = Config(workspace=ws, library=tmp_path / "lib", model="fake")
    provider = FakeProvider(reply="讲解内容")
    with TestClient(create_app(cfg, provider)) as client:
        yield client, provider, tmp_path


def sse(client, job_id):
    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def import_deck(client, name="lec7.pdf", **kw):
    r = client.post("/api/courses/import", files={"file": (name, make_deck(**kw), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def test_import_and_reopen(env):
    client, _, tmp = env
    c = import_deck(client)
    assert c["title"] == "lec7" and c["slide_count"] == 3
    assert c["path"] == str(tmp / "lib" / "lec7.gloss")
    c2 = import_deck(client)
    assert c2["path"].endswith("lec7-2.gloss")
    recent = client.get("/api/recent").json()
    assert [r["path"] for r in recent] == [c2["path"], c["path"]] and recent[0]["exists"]
    client.post(f"/api/courses/{c['id']}/close")
    assert client.get(f"/api/courses/{c['id']}").status_code == 404
    again = client.post("/api/courses/open", json={"path": c["path"]}).json()
    assert again["id"] == c["id"]


def test_import_bad_pdf(env):
    client, _, tmp = env
    r = client.post("/api/courses/import", files={"file": ("x.pdf", b"nope", "application/pdf")})
    assert r.status_code == 422
    assert not list((tmp / "lib").glob("*.gloss"))


def test_open_errors(env):
    client, _, tmp = env
    assert client.post("/api/courses/open", json={"path": str(tmp / "none.gloss")}).status_code == 404
    junk = tmp / "junk.gloss"
    junk.write_bytes(b"x" * 1000)
    assert client.post("/api/courses/open", json={"path": str(junk)}).status_code == 422


def test_slide_and_image(env):
    client, _, _ = env
    cid = import_deck(client)["id"]
    s = client.get(f"/api/courses/{cid}/slides/2").json()
    assert "Scaled dot-product" in s["text"] and s["explanation"] is None and s["threads"] == []
    img = client.get(f"/api/courses/{cid}/slides/2/image")
    assert img.headers["content-type"] == "image/png" and img.content.startswith(b"\x89PNG")
    assert client.get(f"/api/courses/{cid}/slides/9").status_code == 404


def test_explanation_stream_then_cached(env):
    client, provider, _ = env
    cid = import_deck(client)["id"]
    r = client.post(f"/api/courses/{cid}/slides/1/explanation").json()
    evs = sse(client, r["job"]["id"])
    assert "".join(e["text"] for e in evs if e["type"] == "delta") == "讲解内容"
    assert evs[-1]["explanation"]["content"] == "讲解内容"
    r2 = client.post(f"/api/courses/{cid}/slides/1/explanation").json()
    assert "job" not in r2 and r2["explanation"]["content"] == "讲解内容"
    assert len(provider.calls) == 1
    assert client.get(f"/api/courses/{cid}/slides/1").json()["explanation"]["content"] == "讲解内容"


def test_ask_follow_up_and_state(env):
    client, provider, _ = env
    cid = import_deck(client)["id"]
    provider.reply = "答案一"
    r = client.post(f"/api/courses/{cid}/slides/2/threads",
                    json={"rect": [0.1, 0.2, 0.5, 0.3], "question": "这是什么？"}).json()
    tid = r["thread"]["id"]
    assert r["thread"]["n"] == 1 and r["thread"]["rect"] == [0.1, 0.2, 0.5, 0.3]
    assert sse(client, r["job"]["id"])[-1]["message"]["content"] == "答案一"

    provider.reply = "答案二"
    r = client.post(f"/api/courses/{cid}/threads/{tid}/messages", json={"text": "再讲讲"}).json()
    sse(client, r["job"]["id"])

    s = client.get(f"/api/courses/{cid}/slides/2").json()
    (t,) = s["threads"]
    assert [m["content"] for m in t["messages"]] == ["这是什么？", "答案一", "再讲讲", "答案二"]
    assert s["answer_jobs"] == []
    crop = client.get(f"/api/courses/{cid}/threads/{tid}/crop")
    assert crop.content.startswith(b"\x89PNG")

    assert client.patch(f"/api/courses/{cid}/threads/{tid}", json={"minimized": True}).status_code == 200
    assert client.get(f"/api/courses/{cid}/slides/2").json()["threads"][0]["minimized"] is True

    log = client.get(f"/api/courses/{cid}/slides/2/session").json()
    assert len(log["messages"]) == 4 and log["messages"][2]["content"] == "[Thread #1 · 追问]\n再讲讲"

    summary = client.get(f"/api/courses/{cid}").json()
    assert summary["question_counts"] == {"2": 1}

    assert client.delete(f"/api/courses/{cid}/threads/{tid}").status_code == 200
    assert client.get(f"/api/courses/{cid}/slides/2").json()["threads"] == []


def test_ask_validation(env):
    client, _, _ = env
    cid = import_deck(client)["id"]
    url = f"/api/courses/{cid}/slides/1/threads"
    assert client.post(url, json={"rect": [0.8, 0, 0.5, 0.5], "question": "q"}).status_code == 422
    assert client.post(url, json={"rect": [0, 0, 0.5, 0.5], "question": ""}).status_code == 422
    assert client.post(f"/api/courses/{cid}/slides/7/threads", json={"rect": [0, 0, .5, .5], "question": "q"}).status_code == 404


def test_retry_after_error(env):
    client, provider, _ = env
    cid = import_deck(client)["id"]
    provider.fail = True
    r = client.post(f"/api/courses/{cid}/slides/1/threads", json={"rect": [0, 0, .5, .5], "question": "q"}).json()
    assert sse(client, r["job"]["id"])[-1] == {"type": "error", "message": "boom"}
    provider.fail = False
    job = client.post(f"/api/courses/{cid}/threads/{r['thread']['id']}/retry").json()["job"]
    assert sse(client, job["id"])[-1]["type"] == "done"
    assert client.post(f"/api/courses/{cid}/threads/{r['thread']['id']}/retry").status_code == 409


def test_notes_and_ui(env):
    client, _, _ = env
    cid = import_deck(client)["id"]
    assert client.put(f"/api/courses/{cid}/slides/3/note", json={"content": "记住这个"}).status_code == 200
    assert client.get(f"/api/courses/{cid}/slides/3").json()["note"] == "记住这个"
    assert client.get(f"/api/courses/{cid}").json()["pages_with_notes"] == [3]
    layout = {"mode": "side", "split": [0.6, 0.58]}
    client.put(f"/api/courses/{cid}/ui/layout", json={"value": layout})
    assert client.get(f"/api/courses/{cid}/ui/layout").json() == {"value": layout}
    assert client.put(f"/api/courses/{cid}/ui/layout", json=[1]).status_code == 422


def test_unknown_job(env):
    client, _, _ = env
    assert client.get("/api/jobs/nope/events").status_code == 404


def test_local_only_guard(env):
    client, _, _ = env
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    assert client.post("/api/courses/open", json={"path": "/x"},
                       headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"origin": "http://localhost:5173"}).status_code == 200


def test_serves_frontend_build(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSS_HOME", str(tmp_path / "home"))
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>gloss</html>")
    (static / "assets" / "a.js").write_text("x")
    cfg = Config(workspace=tmp_path / "ws", library=tmp_path / "lib")
    with TestClient(create_app(cfg, FakeProvider(), static_dir=static)) as client:
        assert client.get("/").text == "<html>gloss</html>"
        assert client.get("/assets/a.js").text == "x"
        assert client.get("/api/health").status_code == 200  # API still wins


def test_no_frontend_build_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSS_HOME", str(tmp_path / "home"))
    cfg = Config(workspace=tmp_path / "ws", library=tmp_path / "lib")
    with TestClient(create_app(cfg, FakeProvider(), static_dir=tmp_path / "nope")) as client:
        assert client.get("/").status_code == 404


def test_tree_endpoint_and_summary(env):
    client, provider, _ = env
    provider.structured = [{"title": "Attention", "subtitle": "", "children": [
        {"title": "全部", "summary": "", "slides": [1, 2, 3]}]}]
    c = import_deck(client)
    assert c["tree"] is None and c["tree_job"] is None
    assert c["aspect"] == pytest.approx(16 / 9, abs=0.01)
    r = client.post(f"/api/courses/{c['id']}/tree").json()
    evs = sse(client, r["job"]["id"])
    assert evs[-1]["tree"]["children"][0]["slides"] == [1, 2, 3]
    assert client.post(f"/api/courses/{c['id']}/tree").json() == {"tree": evs[-1]["tree"]}
    assert client.get(f"/api/courses/{c['id']}").json()["tree"]["title"] == "Attention"
