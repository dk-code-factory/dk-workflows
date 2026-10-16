import json
from pathlib import Path
import subprocess

import pytest

from shared import series_assessment as series

HEAD = 'a' * 40
REVIEWED = 'b' * 40
TREE = 'c' * 40
TIP = 'd' * 40
PR = 'https://github.com/base/repo/pull/7'


def evidence():
    return dict(schema_version=1, pr_url=PR, head_repo='fork/repo',
                reviewed_head=REVIEWED, reviewed_tree=TREE, target_tip=TIP,
                target_branch='release/next', final_head=HEAD, outcome='published',
                commit_count=2, reasons=['API change.', 'Caller migration and tests.'])


def test_artifacts_are_external_immutable_and_validated(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    path = tmp_path / 'assessment.json'
    assert series.save(path, evidence(), repo) == path
    assert series.load(path, repo) == evidence()
    with pytest.raises(FileExistsError):
        series.save(path, evidence(), repo)
    for internal in (repo / 'assessment.json', repo / '.git/assessment.json'):
        with pytest.raises(ValueError, match='outside'):
            series.save(internal, evidence(), repo)
    alias = tmp_path / 'alias'
    alias.symlink_to(repo, target_is_directory=True)
    with pytest.raises(ValueError, match='outside'):
        series.save(alias / 'assessment.json', evidence(), repo)
    malformed = tmp_path / 'bad.json'
    malformed.write_text('{')
    with pytest.raises(ValueError):
        series.load(malformed, repo)
    with pytest.raises(FileNotFoundError):
        series.load(tmp_path / 'missing.json', repo)


@pytest.mark.parametrize('data', [None, [], {}, {'schema_version': True},
                                  evidence() | {'outcome': []},
                                  evidence() | {'pr_url': '7'},
                                  evidence() | {'head_repo': None},
                                  evidence() | {'commit_count': 0},
                                  evidence() | {'reasons': [None]}])
def test_malformed_assessments_raise_value_error(data):
    with pytest.raises(ValueError):
        series.validate(data)


@pytest.mark.parametrize('change', ['head', 'target', 'repo', 'tree', 'not_rebased'])
def test_live_snapshot_rejects_concurrent_changes_or_unbased_head(monkeypatch, tmp_path, change):
    meta = dict(url=PR, number=7, headRefOid=HEAD, baseRefName='release/next',
                headRepository={'nameWithOwner': 'fork/repo'})
    counts = {'meta': 0, 'tip': 0}
    calls = []
    def command(cwd, *args):
        calls.append(args)
        if args[:3] == ('gh', 'pr', 'view'):
            counts['meta'] += 1
            later = counts['meta'] > 1
            if change == 'head' and later:
                return json.dumps(meta | {'headRefOid': REVIEWED})
            if change == 'repo' and later:
                return json.dumps(meta | {'headRepository': {'nameWithOwner': 'other/repo'}})
            return json.dumps(meta)
        if args[:2] == ('gh', 'api'):
            if '/git/ref/' in args[-1]:
                assert 'release%2Fnext' in args[-1]
                counts['tip'] += 1
                return json.dumps({'object': {'sha': REVIEWED if change == 'target' and counts['tip'] > 1 else TIP}})
            assert args[-1] == f'repos/fork/repo/git/commits/{HEAD}'
            return json.dumps({'tree': {'sha': TREE}})
        if args[:2] == ('git', 'rev-parse'):
            return REVIEWED if change == 'tree' else TREE
        if args[:2] == ('git', 'merge-base'):
            return REVIEWED if change == 'not_rebased' else TIP
        return '2'
    monkeypatch.setattr(series, 'command', command)
    with pytest.raises(RuntimeError):
        series.live_snapshot(PR, tmp_path)
    assert all(args[0] in {'git', 'gh'} for args in calls)


def test_live_snapshot_fork_and_commit_count(monkeypatch, tmp_path):
    responses = iter([
        json.dumps(dict(url=PR, number=7, headRefOid=HEAD, baseRefName='main',
                        headRepository={'nameWithOwner': 'fork/repo'})),
        json.dumps({'object': {'sha': TIP}}),
        json.dumps({'tree': {'sha': TREE}}),
        json.dumps(dict(url=PR, number=7, headRefOid=HEAD, baseRefName='main',
                        headRepository={'nameWithOwner': 'fork/repo'})),
        json.dumps({'object': {'sha': TIP}}), TREE, TIP, '2',
    ])
    monkeypatch.setattr(series, 'command', lambda *a: next(responses))
    assert series.live_snapshot(PR, tmp_path) == dict(
        pr_url=PR, head_repo='fork/repo', final_head=HEAD, final_tree=TREE,
        target_tip=TIP, target_branch='main', commit_count=2)
