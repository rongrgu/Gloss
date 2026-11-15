import time

import pytest
from test_server import env, import_deck  # noqa: F401  (fixture)

from gloss.folders import FolderError, Folders, key_between, natural_key
from gloss.store import Course, Slide


def lecture(path, title, created_at=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Course.create(path, title) as c:
        c.replace_slides([Slide(1, "x", b"img", [])])
        if created_at:
            c.set_meta("created_at", created_at)


def test_natural_sort():
    names = ["Lecture 10", "lecture 2", "Lecture 1", "Appendix"]
    assert sorted(names, key=natural_key) == ["Appendix", "Lecture 1", "lecture 2", "Lecture 10"]


def test_key_between():
    assert key_between(None, None) == 0
    assert key_between(None, 5) == 4 and key_between(5, None) == 6 and key_between(1, 2) == 1.5


def test_listing_kinds_and_import_order(tmp_path):
    root = tmp_path / "lib"
    course = root / "CMU" / "Fall 2026" / "11-768"
    # imported in this order, file names deliberately in the opposite order
    lecture(course / "b-intro.gloss", "Intro", "2026-09-01T10:00:00+00:00")
    lecture(course / "a-attention.gloss", "Attention", "2026-09-08T10:00:00+00:00")
    (root / "CMU" / "Empty").mkdir()
    (root / ".hidden").mkdir()
    f = Folders(root)

    top = f.listing("")
    assert top["kind"] == "group" and [x["name"] for x in top["folders"]] == ["CMU"]
    cmu = f.listing("CMU")
    assert [(x["name"], x["kind"]) for x in cmu["folders"]] == [("Empty", "empty"), ("Fall 2026", "group")]
    assert [c["name"] for c in cmu["crumbs"]] == ["", "CMU"]

    lst = f.listing("CMU/Fall 2026/11-768")
    assert lst["kind"] == "course" and not lst["mixed"]
    assert [x["title"] for x in lst["lectures"]] == ["Intro", "Attention"]  # import order, not name order
    assert lst["lectures"][0]["slide_count"] == 1 and lst["lectures"][0]["path"] == "CMU/Fall 2026/11-768/b-intro.gloss"
    assert [x["title"] for x, in [(i,) for _, i in f.all_lectures(root)]] == ["Intro", "Attention"]


def test_paths_cannot_escape(tmp_path):
    f = Folders(tmp_path / "lib")
    for bad in ["..", "../x", "a/../../x", ".hidden"]:
        with pytest.raises(FolderError):
            f.resolve(bad)
    assert f.mkdir("", "../evil") == "_evil"  # a name, not a path: stays inside the library
    assert not (tmp_path / "evil").exists()
    with pytest.raises(FolderError):
        f.mkdir("", " .. ")


def test_mkdir_rename_move_rmdir(tmp_path):
    root = tmp_path / "lib"
    f = Folders(root)
    assert f.mkdir("", "CMU") == "CMU"
    assert f.mkdir("CMU", "ML") == "CMU/ML"
    with pytest.raises(FolderError):
        f.mkdir("CMU", "ML")
    lecture(root / "lec.gloss", "L")
    src, dst = f.move("lec.gloss", "CMU/ML")
    assert dst == root / "CMU" / "ML" / "lec.gloss" and dst.exists()
    with pytest.raises(FolderError):
        f.move("CMU", "CMU/ML")  # into itself
    src, dst = f.rename("CMU/ML/lec.gloss", "Lecture 1")
    assert dst.name == "Lecture 1.gloss"
    with pytest.raises(FolderError):
        f.rmdir("CMU/ML")  # not empty
    f.move("CMU/ML/Lecture 1.gloss", "")
    (root / "CMU" / "ML" / ".DS_Store").write_text("")
    f.rmdir("CMU/ML")
    assert not (root / "CMU" / "ML").exists()


def test_peek_while_open_does_not_block(tmp_path):
    p = tmp_path / "lib" / "c" / "l.gloss"
    lecture(p, "Open one")
    with Course.open(p) as c:
        c.set_note(1, "note")  # an open writer
        lst = Folders(tmp_path / "lib").listing("c")
        assert lst["lectures"][0]["title"] == "Open one"


# --- API ---

def test_api_import_into_folder_and_list(env):  # noqa: F811
    client, _, tmp = env
    assert client.post("/api/library/folders", json={"parent": "", "name": "Fall 2026"}).json()["path"] == "Fall 2026"
    client.post("/api/library/folders", json={"parent": "Fall 2026", "name": "ML"})
    r = client.post("/api/courses/import", data={"folder": "Fall 2026/ML"},
                    files={"file": ("lec1.pdf", __import__("make_pdfs").make_deck(), "application/pdf")})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["folder"] == "Fall 2026/ML" and c["path"] == str(tmp / "lib" / "Fall 2026" / "ML" / "lec1.gloss")
    lst = client.get("/api/library", params={"path": "Fall 2026/ML"}).json()
    assert lst["kind"] == "course" and lst["lectures"][0]["open"] is True
    assert client.get("/api/library", params={"path": "../x"}).status_code == 422
    assert client.get("/api/library", params={"path": "nope"}).status_code == 404
    assert [t["path"] for t in client.get("/api/library/tree").json()] == ["", "Fall 2026", "Fall 2026/ML"]


def test_api_move_closes_open_lecture_and_updates_recent(env):  # noqa: F811
    client, _, tmp = env
    c = import_deck(client, "lec1.pdf")
    client.post("/api/library/folders", json={"parent": "", "name": "ML"})
    # open but idle: closed automatically, then moved
    assert client.post("/api/library/move", json={"path": "lec1.gloss", "to": "ML"}).json()["path"] == "ML/lec1.gloss"
    assert client.get(f"/api/courses/{c['id']}").status_code == 404
    recent = client.get("/api/recent").json()
    assert recent[0]["path"] == str(tmp / "lib" / "ML" / "lec1.gloss") and recent[0]["exists"]
    # renaming the folder keeps the recent entry valid too
    client.post("/api/library/rename", json={"path": "ML", "name": "Machine Learning"})
    assert client.get("/api/recent").json()[0]["path"] == str(tmp / "lib" / "Machine Learning" / "lec1.gloss")


def test_api_reorder_writes_only_the_dragged_lecture(env):  # noqa: F811
    client, _, tmp = env
    client.post("/api/library/folders", json={"parent": "", "name": "C"})
    ids = []
    for name in ["l1.pdf", "l2.pdf", "l3.pdf"]:
        c = client.post("/api/courses/import", data={"folder": "C"},
                        files={"file": (name, __import__("make_pdfs").make_deck(), "application/pdf")}).json()
        ids.append(c["id"])
        time.sleep(1.05)  # created_at has 1-second resolution
    titles = lambda lst: [x["title"] for x in lst["lectures"]]  # noqa: E731
    assert titles(client.get("/api/library", params={"path": "C"}).json()) == ["l1", "l2", "l3"]
    client.post(f"/api/courses/{ids[0]}/close")  # one closed, others open: both write paths
    lst = client.post("/api/library/reorder", json={"path": "C/l3.gloss", "before": "C/l1.gloss"}).json()
    assert titles(lst) == ["l3", "l1", "l2"]
    lst = client.post("/api/library/reorder", json={"path": "C/l3.gloss", "before": None}).json()
    assert titles(lst) == ["l1", "l2", "l3"]
    lst = client.post("/api/library/reorder", json={"path": "C/l1.gloss", "before": "C/l3.gloss"}).json()
    assert titles(lst) == ["l2", "l1", "l3"]
    assert client.post("/api/library/reorder", json={"path": "C/l1.gloss", "before": "C/zz.gloss"}).status_code == 422


def test_api_reorder_renumbers_when_keys_collide(env):  # noqa: F811
    client, _, tmp = env
    client.post("/api/library/folders", json={"parent": "", "name": "C"})
    for name in ["a.pdf", "b.pdf", "c.pdf"]:
        client.post("/api/courses/import", data={"folder": "C"},
                    files={"file": (name, __import__("make_pdfs").make_deck(), "application/pdf")})
    for n, key in [("a", 1.0), ("b", 1.0 + 1e-7), ("c", 3.0)]:
        lst = client.get("/api/library", params={"path": "C"}).json()
        cid = next(x["course_id"] for x in lst["lectures"] if x["title"] == n)
        client.post(f"/api/courses/{cid}/close") if cid else None
        with Course.open(tmp / "lib" / "C" / f"{n}.gloss") as c:
            c.set_meta("sort_key", repr(key))
    lst = client.post("/api/library/reorder", json={"path": "C/c.gloss", "before": "C/b.gloss"}).json()
    assert [x["title"] for x in lst["lectures"]] == ["a", "c", "b"]
    assert [x["sort_key"] for x in lst["lectures"]] == [1.0, 2.0, 3.0]


def test_api_move_refused_while_claude_works(env):  # noqa: F811
    client, provider, tmp = env
    c = import_deck(client, "lec1.pdf")
    client.post("/api/library/folders", json={"parent": "", "name": "ML"})
    app_jobs = client.app.state.jobs

    from gloss.jobs import Job
    app_jobs._jobs[f"{c['id']}:explain:1"] = Job(f"{c['id']}:explain:1", "explain")  # unfinished
    try:
        r = client.post("/api/library/move", json={"path": "lec1.gloss", "to": "ML"})
        assert r.status_code == 409 and "Claude" in r.json()["detail"]
    finally:
        del app_jobs._jobs[f"{c['id']}:explain:1"]
