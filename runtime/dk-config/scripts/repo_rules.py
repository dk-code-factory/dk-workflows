#!/usr/bin/env python3
"""Compatibility path for extracted domain settings, without workflow routing."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parents[3]/'scripts/config.py'),run_name='__main__')
