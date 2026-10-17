"""Make repo-root, src/ and ui/ importable with flat module names."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "ui")):
    if path not in sys.path:
        sys.path.insert(0, path)
