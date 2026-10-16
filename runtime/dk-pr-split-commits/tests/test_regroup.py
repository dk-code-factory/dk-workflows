import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from shared import series_assessment as series
from shared.review_loop import encode

GUARD = ROOT / 'dk-pr-split-commits/scripts/split_guard.py'
TOKEN = encode('split', 1, True)
PR = 'https://github.com/tester/repo/pull/1'


@pytest.fixture
def repo(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    remote = tmp_path / 'remote.git'
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=work, text=True).strip()
    git('init', '-q')
    git('config', 'user.name', 'Tester')
    git('config', 'user.email', 'tester@example.com')
    (work / 'base').write_text('base')
    git('add', '.')
    git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    git('remote', 'add', 'origin', str(remote))
    git('push', '-q', 'origin', f'{base}:refs/heads/target')
    git('checkout', '-qb', 'topic')
    (work / 'a').write_text('a')
    (work / 'b').write_text('b')
    git('add', '.')
    git('commit', '-qm', 'feature')
    git('push', '-qu', 'origin', 'topic')
    binpath = tmp_path / 'bin'
    binpath.mkdir()
    fake = binpath / 'gh'
    fake.write_text('''#!/usr/bin/env python3
import json, subprocess, sys
remote = REMOTE
def git(*args):
    return subprocess.check_output(['git', '--git-dir', remote, *args], text=True).strip()
head = git('rev-parse', 'refs/heads/topic')
if sys.argv[1] == 'pr':
    result = dict(number=1, url='https://github.com/tester/repo/pull/1',
                  headRefName='topic', headRefOid=head, baseRefName='target',
                  isCrossRepository=False, headRepository=dict(nameWithOwner='tester/repo'))
elif '/git/ref/' in sys.argv[-1]:
    result = dict(object=dict(sha=git('rev-parse', 'refs/heads/target')))
else:
    result = dict(tree=dict(sha=git('rev-parse', head+'^{tree}')))
print(json.dumps(result))
'''.replace('REMOTE', repr(str(remote))))
    fake.chmod(0o755)
    monkeypatch.setenv('PATH', str(binpath) + os.pathsep + os.environ['PATH'])
    monkeypatch.chdir(work)
    def guard(*args):
        return subprocess.run([sys.executable, str(GUARD), *args], text=True, capture_output=True)
    return work, remote, base, git, guard


def prepare(repo, mode='regroup'):
    work, _, base, _, guard = repo
    result = guard('prepare', '--base', base, '--pr', PR, '--mode', mode)
    assert result.returncode == 0, result.stderr
    return json.loads((work / '.git/pr-split-state.json').read_text())


def assessment(repo, tmp_path):
    reasons = tmp_path / 'reasons.json'
    reasons.write_text(json.dumps(['Feature and its regression coverage form one coherent change.']))
    output = tmp_path / 'assessment.json'
    result = repo[-1]('assess', '--review-state', TOKEN, '--reasons-file', str(reasons),
                      '--output', str(output))
    assert result.returncode == 0, result.stderr
    return series.load(output, repo[0])


def test_unchanged_coherent_single_commit_assessed(repo, tmp_path):
    data = prepare(repo)
    # Preparation must preserve an interrupted, unassessed run.
    refused = repo[-1]('prepare', '--base', repo[2], '--mode', 'regroup')
    assert refused.returncode != 0 and 'recovery' in refused.stderr
    assert json.loads((repo[0] / '.git/pr-split-state.json').read_text()) == data
    evidence = assessment(repo, tmp_path)
    assert evidence['outcome'] == 'no_op'
    assert evidence['reviewed_head'] == evidence['final_head'] == data['original_head']
    assert evidence['commit_count'] == 1 and evidence['reasons']
    assert repo[3]('rev-parse', data['recovery_ref']) == data['original_head']
    assert series.check(evidence, series.live_snapshot(PR, repo[0]), evidence['final_head']) == 'clean'
    # Completed preparation is archived, permitting a later full assessment.
    assert not (repo[0] / '.git/pr-split-state.json').exists()
    archived = next((repo[0] / '.git/pr-split-history').glob('*.json'))
    assert json.loads(archived.read_text())['assessment'] == str(tmp_path / 'assessment.json')
    prepare(repo)



@pytest.mark.parametrize('split_mixed', [False, True])
def test_regroup_folds_fixup_or_splits_mixed_commit(repo, tmp_path, split_mixed):
    work, _, base, git, guard = repo
    if not split_mixed:
        (work / 'a').write_text('fixed')
        git('commit', '-qam', 'fixup! feature')
        git('push', '-q')
    data = prepare(repo)
    git('reset', '-q', '--soft', base)
    if split_mixed:
        git('reset', '-q')
        git('add', 'a')
        git('commit', '-qm', 'add a')
        git('add', 'b')
        git('commit', '-qm', 'add b')
    else:
        git('commit', '-qm', 'feature with fix')
    result = guard('verify')
    assert result.returncode == 0, result.stderr
    result = guard('push', '--allow-force-push', '--review-state', TOKEN)
    assert result.returncode == 0, result.stderr
    evidence = assessment(repo, tmp_path)
    assert evidence['outcome'] == 'published'
    assert evidence['commit_count'] == (2 if split_mixed else 1)
    assert evidence['reviewed_tree'] == git('rev-parse', 'HEAD^{tree}') == data['original_tree']
    assert evidence['final_head'] != data['original_head']
    archived = next((work / '.git/pr-split-history').glob('*.json'))
    assert json.loads(archived.read_text())['publication'] == 'published'


def test_regroup_requires_tree_and_unchanged_remote_target(repo):
    work, remote, base, git, guard = repo
    prepare(repo)
    (work / 'a').write_text('different')
    git('commit', '-qam', 'changed tree')
    assert 'tree differs' in guard('verify').stderr
    git('reset', '--hard', 'origin/topic')
    subprocess.run(['git', '--git-dir', str(remote), 'update-ref', 'refs/heads/target',
                    git('rev-parse', 'HEAD')], check=True)
    refused = guard('push', '--allow-force-push', '--review-state', TOKEN)
    assert refused.returncode != 0 and 'target tip changed' in refused.stderr
    assert (work / '.git/pr-split-state.json').exists()
    assert git('ls-remote', '--heads', 'origin', 'topic').split()[0] == git('rev-parse', 'HEAD')


def test_interrupted_publication_is_reconciled_without_repush(repo, tmp_path):
    work, _, base, git, guard = repo
    data = prepare(repo)
    git('reset', '-q', '--soft', base)
    git('commit', '-qm', 'coherent feature')
    final = git('rev-parse', 'HEAD')
    data.update(publication='pending', final_head=final)
    state_path = work / '.git/pr-split-state.json'
    state_path.write_text(json.dumps(data))
    refused = guard('reconcile')
    assert refused.returncode != 0 and 'uncertain' in refused.stderr
    assert json.loads(state_path.read_text())['publication'] == 'pending'
    assert 'already attempted' in guard('push', '--allow-force-push', '--review-state', TOKEN).stderr
    git('push', '-q', f"--force-with-lease=topic:{data['remote_sha']}", 'origin', 'HEAD:topic')
    result = guard('reconcile')
    assert result.returncode == 0, result.stderr
    assert assessment(repo, tmp_path)['outcome'] == 'published'
    assert git('rev-parse', data['recovery_ref']) == data['original_head']


def test_assessment_rejects_in_repo_or_incomplete_evidence(repo, tmp_path):
    prepare(repo)
    reasons = tmp_path / 'reasons.json'
    reasons.write_text('[]')
    guard = repo[-1]
    args = ('--review-state', TOKEN, '--reasons-file', str(reasons))
    assert 'boundary reasons' in guard('assess', *args, '--output', str(tmp_path / 'bad.json')).stderr
    reasons.write_text('["coherent feature"]')
    assert 'outside the repository' in guard('assess', *args, '--output', str(repo[0] / 'evidence.json')).stderr
    assert 'completed split' in guard('assess', '--review-state', encode('split', 1),
                                     '--reasons-file', str(reasons), '--output', str(tmp_path / 'bad.json')).stderr
    assert not (tmp_path / 'bad.json').exists()
