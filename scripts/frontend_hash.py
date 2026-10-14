"""Hash of the frontend sources, stored next to the committed build.

`npm run build` writes it (postbuild); tests/test_frontend_build.py fails if the
sources changed without a rebuild. Usage: python scripts/frontend_hash.py [--write]
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
STAMP = ROOT / "backend" / "gloss" / "static" / "SOURCE_HASH"


def source_files() -> list[Path]:
    files = [p for p in (FRONTEND / "src").rglob("*") if p.is_file() and not p.name.endswith(".test.ts")]
    files += [FRONTEND / f for f in ("index.html", "package-lock.json", "vite.config.ts", "tsconfig.app.json")]
    return sorted(files)


def source_hash() -> str:
    h = hashlib.sha256()
    for p in source_files():
        h.update(p.relative_to(FRONTEND).as_posix().encode() + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


if __name__ == "__main__":
    if "--write" in sys.argv:
        STAMP.write_text(source_hash() + "\n")
        print(f"wrote {STAMP.relative_to(ROOT)}")
    else:
        print(source_hash())
