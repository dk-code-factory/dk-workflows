"""CLI failures must not become successful exact-head CI observations."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2]/'dk-ci/scripts/ci_wait.py'
spec = importlib.util.spec_from_file_location('ci_wait', SCRIPT)
ci_wait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_wait)


@pytest.mark.parametrize('arguments,code', [
    (['pr', 'view', '7', '--json', 'headRefOid'], 1),
    (['pr', 'checks', '7', '--json', 'bucket'], 2),
])
def test_error_exit_rejects_partial_json(monkeypatch, arguments, code):
    monkeypatch.setattr(ci_wait, 'github_run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], code, '[{"bucket":"pass"}]', 'read failed'))
    with pytest.raises(RuntimeError, match='read failed'):
        ci_wait.gh_json(arguments)


@pytest.mark.parametrize('code', [0, 1, 8])
def test_checks_accept_documented_terminal_and_pending_exits(monkeypatch, code):
    checks = [{'bucket': {0: 'pass', 1: 'fail', 8: 'pending'}[code]}]
    monkeypatch.setattr(ci_wait, 'github_run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], code, json.dumps(checks), ''))
    assert ci_wait.gh_json(['pr', 'checks', '7']) == checks
