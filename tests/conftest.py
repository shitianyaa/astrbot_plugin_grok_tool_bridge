from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLUGIN_ROOT.parent

for path in (str(PLUGIN_ROOT), str(WORKSPACE_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
