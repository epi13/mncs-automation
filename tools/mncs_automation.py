#!/usr/bin/env python3
"""mncs-automation entry point (thin shim; logic lives in tools/automation)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from automation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
