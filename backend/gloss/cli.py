"""`gloss` command: start the local server."""

from __future__ import annotations

import argparse

import uvicorn

from .config import Config
from .server import create_app


def main() -> None:
    ap = argparse.ArgumentParser(prog="gloss")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    uvicorn.run(create_app(Config.load()), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
