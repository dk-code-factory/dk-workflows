import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1]/'scripts/validate_result.py'
spec = importlib.util.spec_from_file_location('validate_result', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_validator_checks_full_contract_and_changed_line(tmp_path):
    patch = tmp_path/'diff.patch'
    patch.write_text('diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n')
    result = tmp_path/'result.json'
    finding = {'severity': 'Major', 'path': 'x.py', 'line': 1,
               'message': 'breaks callers', 'fix': 'restore behavior'}
    result.write_text(json.dumps({'findings': [finding]}))
    command = [sys.executable, str(SCRIPT), '--patch', str(patch), '--result', str(result)]
    assert subprocess.run(command, capture_output=True, text=True).returncode == 0
    result.write_text(json.dumps({'findings': [{**finding, 'line': 2}]}))
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 1
    assert 'added line' in failed.stderr
    with pytest.raises(ValueError, match='expected one JSON object'):
        module.validate([], module.added_lines(patch.read_text()))
