"""Fixture tests must never accidentally launch authenticated model workflows."""
import subprocess
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def prevent_unmocked_model_calls(monkeypatch):
    def guarded(original):
        def invoke(*args, **kwargs):
            argv = args[0] if args else kwargs.get('args')
            if isinstance(argv, (list, tuple)) and argv and (
                    Path(argv[0]).name == 'codex' and 'exec' in argv or Path(argv[0]).name == 'claude' and '-p' in argv):
                raise AssertionError('Fixture attempted an unmocked model execution')
            return original(*args, **kwargs)
        return invoke
    monkeypatch.setattr(subprocess, 'run', guarded(subprocess.run))
    monkeypatch.setattr(subprocess, 'Popen', guarded(subprocess.Popen))


@pytest.fixture(autouse=True)
def private_workflow_state(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("workflow-state")))
