"""Environment check. Same as `uv run gloss doctor`."""

import sys

from gloss.doctor import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
