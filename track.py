"""Live alerts when ES / NQ reach today's GEX levels.

    python track.py                 both, from the 09:30 open
    python track.py --start 08:25   include the pre-market (e.g. jobs report days)
"""
import sys

from gex.track import main

if __name__ == "__main__":
    sys.exit(main())
