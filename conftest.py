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
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("workflow-data")))


@pytest.fixture(autouse=True)
def offline_architecture_dependency(request, monkeypatch):
    """Existing workflow fixtures isolate their new independent model dependency.

    Architecture's own tests exercise policy, nonempty catalogs and invalid model
    output explicitly. Here the fixture repos have no architectural obligations;
    retain real packets/results while replacing only the independent judgment.
    """
    if not any(part in request.node.path.parts for part in (
            'dk-pr-decisions', 'dk-pr-review', 'dk-pr-iter', 'dk-pr-split-commits')):
        return
    from shared import architecture
    def analyze(prompt, checkout, validate, *, on_call=None):
        if 'Act as dk-architecture-review' in prompt:
            result = {'complete': True, 'reason': '', 'coverage': []}
        else:
            path = architecture.git(checkout, 'ls-files').splitlines()[0]
            item = {'text': 'Fixture has no additional architecture policy.', 'origin': 'inferred',
                    'rationale': 'Inspected the fixture source.', 'sources': [{'path': path, 'start': 1, 'end': 1}]}
            result = {'knowledge': {key: [item] for key in architecture.KNOWLEDGE},
                      'rules': [], 'proposals': [], 'conflicts': []}
        validate(result)
        return result
    monkeypatch.setattr(architecture, 'analyze', analyze)
