#!/usr/bin/env python3
"""Run deterministic offline tests; never invokes a model."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
