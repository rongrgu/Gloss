"""The built frontend is committed (users don't need Node). Make sure it matches the sources."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from frontend_hash import STAMP, source_hash  # noqa: E402


def test_committed_build_is_up_to_date():
    assert (STAMP.parent / "index.html").exists(), "frontend not built: cd frontend && npm run build"
    assert STAMP.read_text().strip() == source_hash(), (
        "frontend sources changed since the last build: cd frontend && npm run build, then commit backend/gloss/static")
