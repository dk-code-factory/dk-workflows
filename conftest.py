"""Fixture tests must never accidentally launch authenticated model workflows."""
import subprocess
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def prevent_unmocked_model_calls(monkeypatch):
    original = subprocess.run
    def run(argv, *args, **kwargs):
        if isinstance(argv, list) and argv and (
            Path(argv[0]).name == 'codex' and 'exec' in argv or Path(argv[0]).name == 'claude' and '-p' in argv):
            raise AssertionError('Fixture attempted an unmocked model execution')
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', run)
