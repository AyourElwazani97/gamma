"""Check saved GEX levels against what ES / NQ actually did.

    python review.py        both ES and NQ
    python review.py ES     one product
"""
import sys

from gex.review import main

if __name__ == "__main__":
    sys.exit(main())
