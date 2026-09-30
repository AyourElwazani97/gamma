"""Gamma levels for ES / NQ futures.

    python gex.py          both ES and NQ
    python gex.py ES       one product
    python gex.py --help   all options
"""
import sys

from gex.cli import main

if __name__ == "__main__":
    sys.exit(main())
