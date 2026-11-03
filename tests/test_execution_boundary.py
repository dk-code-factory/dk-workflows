"""Keep removed host execution policy outside the shipped native-skill runtime."""
import importlib.util
from pathlib import Path
import shutil

import pytest


ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def distribution(tmp_path, monkeypatch):
    installed=tmp_path/'plugin'
    shutil.copytree(ROOT,installed,ignore=shutil.ignore_patterns('.git','__pycache__','.pytest_cache'))
    spec=importlib.util.spec_from_file_location('execution_boundary_check',installed/'scripts/check.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',installed)
    return installed,module


@pytest.mark.parametrize('relative,source,marker',[
    ('runtime/shared/executors.py','argv = ["codex", "exec", "--ephemeral"]\n','--ephemeral'),
    ('runtime/shared/provider.py','command = "codex exec"\n','codex exec'),
    ('runtime/dk-pr-review/scripts/provider.py','command = "claude -p"\n','claude -p'),
    ('scripts/providers/review.py','import shared.executors\n','shared.executors'),
    ('runtime/shared/preparation.py','preparation_graph = {}\n','preparation_graph'),
    ('runtime/shared/delivery.py','architecture_packet = {}\n','architecture_packet'),
])
def test_nested_execution_policy_is_rejected(distribution,relative,source,marker):
    installed,module=distribution
    path=installed/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(source)
    with pytest.raises(AssertionError,match='Unexpected core execution/domain integration') as error:
        module.check()
    assert marker in str(error.value)
    assert relative in str(error.value)


def test_execution_examples_in_test_fixtures_remain_allowed(distribution):
    installed,module=distribution
    for relative in ('tests/fixtures/legacy.py','runtime/shared/tests/fixtures/legacy.py'):
        path=installed/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('argv = ["codex", "exec", "--ephemeral"]\ncommand = "codex exec"\n')
    assert module.check()['valid']
