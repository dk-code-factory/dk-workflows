"""Contract inheritance and transport checks; native prose selection is separate."""
import importlib.util
from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_every_skill_explicitly_inherits_the_private_reporting_contract(host):
    paths = sorted((ROOT/host/'skills').glob('*/SKILL.md'))
    assert len(paths) == 25
    for path in paths:
        text = path.read_text()
        assert '[private workflow reporting](../../../docs/private-reporting.md)' in text
        assert text.index('[private workflow reporting]') < text.index('Set ')


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_package_validation_rejects_an_independent_skill_missing_the_contract(tmp_path, host):
    installed = tmp_path/'plugin'
    shutil.copytree(ROOT, installed, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
    path = installed/host/'skills/dk-config/SKILL.md'
    path.write_text(path.read_text().replace(
        '[private workflow reporting](../../../docs/private-reporting.md)', 'private reporting'))
    spec = importlib.util.spec_from_file_location('reporting_package_check', installed/'scripts/check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = installed
    with pytest.raises(AssertionError, match='Missing private reporting contract'):
        module.check()


def test_handoffs_and_publication_preserve_the_governing_boundary():
    for relative in ('docs/pr/contracts.md', 'docs/pr/publication.md', 'docs/native-review.md',
                     'docs/task-state.md', 'docs/finalizers.md', 'docs/cycle-hooks.md',
                     'runtime/pr-review.md', 'runtime/pr-iter.md', 'runtime/pr-reviewers.md'):
        assert '[private workflow reporting]' in (ROOT/relative).read_text(), relative
    contract = (ROOT/'docs/private-reporting.md').read_text()
    for requirement in ('paramount package-wide', 'takes precedence', 'withhold',
                        'failed or missing reviewers', 'incomplete verification',
                        'exhausted budgets', 'publication or reconciliation',
                        'unavailable tools', 'supporting sources', 'not a keyword ban',
                        'every text field', 'original intent'):
        assert requirement in contract
    post = (ROOT/'codex/skills/dk-pr-review-post/SKILL.md').read_text()
    review = (ROOT/'runtime/pr-review.md').read_text()
    assert 'always disclose coverage blockers' not in post
    assert 'if it states the remaining coverage blockers' not in post
    assert 'state remaining coverage or verification blockers' not in review
    assert 'report remaining coverage and verification blockers privately' in post
    assert 'Keep the general body empty otherwise' in post
    reply = (ROOT/'codex/skills/dk-pr-feedback-reply/SKILL.md').read_text()
    assert 'withhold the affected reply' in reply
    assert 'never substitute a diagnostic reply' in reply
