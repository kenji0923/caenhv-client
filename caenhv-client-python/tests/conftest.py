import os
import sys

# The package is not installed in the test env; put its src/ on the path so the
# tests exercise the working-tree sources.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
