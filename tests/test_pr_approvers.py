"""Override handoffs are read-only, scope-correct and distinct from discovery."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import config
from state import write

spec = importlib.util.spec_from_file_location('pr_approvers', ROOT/'scripts/pr_approvers.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
REPOSITORY = 'github.com/owner/repo'
PR = 'https://' + REPOSITORY + '/pull/1'
HEAD = 'a'*40
PUBLISHER = 'dk-workflows:dk-pr-publish'


def configure(repository, value):
    write(config.path(repository), {'skill_defaults': {PUBLISHER: {'options': {'review_requests': value}}}})


@pytest.mark.parametrize('recipients', [
    {'reviewers': ['alice'], 'team_reviewers': ['maintainers']},
    {'reviewers': [], 'team_reviewers': []},
])
def test_exact_override_short_circuits_discovery_without_calls_or_writes(recipients):
    configure(REPOSITORY, recipients)
    path = config.path(REPOSITORY)
    before = path.read_bytes(), path.stat().st_mtime_ns
    for _ in range(2):
        # A fresh process must select the same branch without any network access.
        child = subprocess.run([sys.executable, str(ROOT/'scripts/pr_approvers.py'),
                                '--pr', PR, '--head', HEAD], capture_output=True, text=True, timeout=30)
        assert child.returncode == 0, child.stdout + child.stderr
        result = json.loads(child.stdout)
        assert result['action'] == 'return' and result['source'] == 'override'
        assert result['selection'] == recipients
        assert result['pr'] == PR and result['head'] == HEAD
        assert result['provenance'] == str(path)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_absent_or_explicit_null_requests_discovery_without_inventing_recipients():
    assert helper.selection(PR, HEAD)['action'] == 'discover'
    configure(None, {'reviewers': ['global-user'], 'team_reviewers': []})
    upstream = 'github.com/upstream/repo'
    configure(upstream, {'reviewers': [], 'team_reviewers': ['upstream-team']})
    assert helper.selection(PR, HEAD, upstream=upstream)['selection']['team_reviewers'] == ['upstream-team']
    configure(REPOSITORY, None)
    automatic = helper.selection(PR, HEAD, upstream=upstream)
    assert automatic['action'] == 'discover' and automatic['source'] == 'automatic'
    assert automatic['selection'] is None
    assert 'reviewers' not in automatic and 'complete' not in automatic
    local = {'review_requests': {'reviewers': ['local-user'], 'team_reviewers': []}}
    assert helper.selection(PR, HEAD, publisher_options=local)['selection'] == local['review_requests']
    assert helper.selection('https://GITHUB.COM/Owner/Repo/pull/01/', HEAD)['pr'] == PR


def test_cli_preserves_upstream_and_invocation_local_publisher_options(tmp_path):
    upstream = 'github.com/upstream/repo'
    configure(upstream, {'reviewers': ['upstream-user'], 'team_reviewers': []})
    options = tmp_path/'publisher.json'
    write(options, {'review_requests': None})
    child = subprocess.run([sys.executable, str(ROOT/'scripts/pr_approvers.py'), '--pr', PR,
                            '--head', HEAD, '--upstream', upstream,
                            '--publisher-options', str(options)], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    selected = json.loads(child.stdout)
    assert selected['action'] == 'discover'
    assert selected['provenance'] == 'invocation'


@pytest.mark.parametrize('pr,head', [
    ('http://github.com/owner/repo/pull/1', HEAD),
    (PR + '?view=all', HEAD), (PR + '#review', HEAD),
    ('https://user@github.com/owner/repo/pull/1', HEAD),
    ('https://github.com:443/owner/repo/pull/1', HEAD),
    ('https://github.com/owner/repo/pull/0', HEAD),
    (PR, 'a'*39), (PR, None),
])
def test_invalid_pr_or_head_cannot_produce_a_selection(pr, head):
    with pytest.raises(ValueError):
        helper.selection(pr, head)
