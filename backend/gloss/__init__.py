"""Gloss: ask questions right on your lecture slides."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("glossr")  # the PyPI distribution name
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "dev"
