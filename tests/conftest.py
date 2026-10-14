import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from gloss.store import Course, Slide  # noqa: E402


@pytest.fixture
def course(tmp_path):
    c = Course.create(tmp_path / "c.gloss", "Test course")
    c.replace_slides([Slide(p, f"slide {p}", b"img", []) for p in (1, 2, 3)])
    yield c
    c.close()
