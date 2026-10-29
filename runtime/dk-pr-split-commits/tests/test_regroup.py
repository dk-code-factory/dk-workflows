import json
import importlib.util
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
import json, os, subprocess, sys
remote = REMOTE
def git(*args):
    return subprocess.check_output(['git', '--git-dir', remote, *args], text=True).strip()
head = git('rev-parse', 'refs/heads/topic')
if sys.argv[1] == 'pr':
    result = dict(number=1, url='https://github.com/tester/repo/pull/1',
                  headRefName='topic', headRefOid=head, baseRefName='target',
                  isCrossRepository=False, headRepository=dict(nameWithOwner='tester/repo'))
    if os.environ.get('GH_MODERN_IDENTITY'):
        result['headRepository'] = dict(id='R_fixture', name='repo', nameWithOwner='')
elif '/pulls/' in sys.argv[-1]:
    result = dict(number=1, html_url='https://github.com/tester/repo/pull/1',
        head=dict(sha=head, ref='topic', repo=dict(full_name='tester/repo', name='repo', node_id='R_fixture')),
        base=dict(sha=git('rev-parse', 'refs/heads/target'), ref='target',
                  repo=dict(full_name='tester/repo', name='repo', node_id='R_fixture')))
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


@pytest.mark.parametrize('mode', ['split', 'regroup'])
@pytest.mark.parametrize('fault', ['before_submission', 'after_application', 'receipt_persistence', 'verification'])
def test_durable_push_boundaries_never_repeat_a_mutation(repo, tmp_path, monkeypatch, mode, fault):
    work, _, base, git, _ = repo
    prepare(repo, mode)
    git('reset', '--mixed', base)
    if mode == 'split':
        git('add', 'a'); git('commit', '-qm', 'first logical change')
        git('add', 'b'); git('commit', '-qm', 'second logical change')
    else:
        git('add', '.'); git('commit', '-qm', 'coherent logical change')
    flags = []
    if mode == 'regroup':
        flags = ['--cleanup-precheck', cleanup_receipt(repo, tmp_path)['path']]
    spec = importlib.util.spec_from_file_location('injected_split_guard', GUARD)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    real_run, real_write, real_reconcile = mod.run, mod.write_state, mod.reconcile
    sends = []
    def run(*args, **kwargs):
        result = real_run(*args, **kwargs)
        if args[:2] == ('git', 'push'):
            sends.append(args)
            if fault == 'after_application':
                raise RuntimeError('response lost after push')
        return result
    def write(value):
        if (fault == 'before_submission' and value.get('publication') == 'pending'
                or fault == 'receipt_persistence' and value.get('publication') == 'published'):
            raise OSError('storage exhausted')
        real_write(value)
    monkeypatch.setattr(mod, 'run', run)
    monkeypatch.setattr(mod, 'write_state', write)
    if fault == 'verification':
        monkeypatch.setattr(mod, 'reconcile', lambda value: (_ for _ in ()).throw(ValueError('verification unavailable')))
    args = ['split_guard.py', 'push', '--allow-force-push', '--review-state', TOKEN, *flags]
    monkeypatch.setattr(sys, 'argv', args)
    from shared.diagnostics import Diagnostic
    with pytest.raises(OSError if fault == 'before_submission' else Diagnostic):
        mod.main()
    if fault == 'before_submission':
        assert not sends
        assert 'publication' not in json.loads((work / '.git/pr-split-state.json').read_text())
    else:
        saved = json.loads((work / '.git/pr-split-state.json').read_text())
        assert saved['publication'] == 'pending' and saved['publication_id']
    monkeypatch.setattr(mod, 'write_state', real_write)
    monkeypatch.setattr(mod, 'reconcile', real_reconcile)
    if fault != 'before_submission':
        monkeypatch.setattr(sys, 'argv', ['split_guard.py', 'reconcile'])
        mod.main()
    else:
        mod.main()
    # A second submission only verifies the existing receipt.
    monkeypatch.setattr(sys, 'argv', args)
    mod.main()
    assert len(sends) == 1
    saved = json.loads((work / '.git/pr-split-state.json').read_text())
    assert saved['publication'] == 'published'
    assert saved['publication_receipt']['head'] == git('rev-parse', 'HEAD')


def assessment(repo, tmp_path):
    reasons = tmp_path / 'reasons.json'
    reasons.write_text(json.dumps(['Feature and its regression coverage form one coherent change.']))
    output = tmp_path / 'assessment.json'
    flags = []
    data = json.loads((repo[0] / '.git/pr-split-state.json').read_text())
    if repo[3]('rev-parse', 'HEAD') == data['original_head']:
        flags = ['--cleanup-precheck', cleanup_receipt(repo, tmp_path)['path']]
    result = repo[-1]('assess', *flags, '--review-state', TOKEN, '--reasons-file', str(reasons),
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
    precheck = cleanup_receipt(repo, tmp_path)
    result = guard('push', '--allow-force-push', '--review-state', TOKEN,
                   '--cleanup-precheck', precheck['path'])
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
    receipt = cleanup_receipt(repo, tmp_path)
    data.update(publication='pending', final_head=final, cleanup_precheck=receipt)
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
    receipt = cleanup_receipt(repo, tmp_path)
    args = ('--review-state', TOKEN, '--reasons-file', str(reasons), '--cleanup-precheck', receipt['path'])
    assert 'boundary reasons' in guard('assess', *args, '--output', str(tmp_path / 'bad.json')).stderr
    reasons.write_text('["coherent feature"]')
    assert 'outside the repository' in guard('assess', *args, '--output', str(repo[0] / 'evidence.json')).stderr
    assert 'completed split' in guard('assess', '--review-state', encode('split', 1),
                                     '--reasons-file', str(reasons), '--output', str(tmp_path / 'bad.json')).stderr
    assert not (tmp_path / 'bad.json').exists()


def cleanup_receipt(repo, tmp_path):
    """A real exhausted iteration and isolated preparation for this guard's head."""
    import importlib.util
    from shared import cleanup_precheck, executors, review_sessions
    from shared.private_state import write
    from unittest.mock import patch
    sys.path.insert(0, str(ROOT / 'dk-pr-decisions/tests'))
    from preparation_fixtures import Package, analysis, descriptor, prep, retain, bind_agreements
    spec = importlib.util.spec_from_file_location('regroup_run_state', ROOT / 'dk-pr-iter/scripts/run_state.py')
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    work, _, _, git, _ = repo
    data = json.loads((work / '.git/pr-split-state.json').read_text())
    directory = tmp_path / 'review-evidence'
    directory.mkdir()
    p = Package(directory)
    isolated = directory / 'reviewed'
    git('clone', '-q', str(work), str(isolated))
    subprocess.check_call(['git', '-C', str(isolated), 'checkout', '-q', '--detach', data['original_head']])
    p.context.update(url=PR, repository=str(work), checkout=str(isolated),
                     head_sha=data['original_head'], base_sha=data['base'])
    p.decisions.update(pr_url=PR, head_sha=data['original_head'])
    bind_agreements(p.context, p.decisions)
    p.write('context.json', p.context)
    p.write('decisions.json', p.decisions)
    p.manifest['mode'] = 'iteration'
    p.manifest['evidence']['production'] = [str(isolated / 'a')]
    p.write('reviewer.json', descriptor('example', isolated / 'a'))
    p.write('manifest.json', p.manifest)
    original_path = p.prepare()['artifact']
    original = prep.read(original_path)
    record = {}
    record_path = directory / 'run.json'
    state.start_run(record)
    state.attach_preparation(record, original_path, PR, data['original_head'])
    for _ in range(5): state.next_cycle(record, data['original_head'])
    binding = state.review_session(record, data['original_head'], record_path)
    session = review_sessions.Session(p.context, original, ['example'], path=binding['session_file'])
    result = {'complete': True, 'executed_reviewers': ['example'], 'reused_reviewers': [],
              'skipped_reviewers': [], 'findings': []}
    reference = session.finish(result)
    state.register_review(record, {'review_artifact': reference}, data['original_head'])
    refreshed = analysis()
    refreshed['sync_reason'] = 'Feedback refreshed before publication'
    p.manifest['evidence']['prior_preparation'] = [original_path]
    p.write('manifest.json', p.manifest)
    changes = retain(original)
    for item in changes['dispositions']:
        if original['graph'][item['id']]['location'] == '/sync_required':
            item.update(action='replace', value={key: refreshed[key] for key in ('sync_required', 'sync_reason')})
    current = prep.prepare(p.manifest_path, p.root / 'versions', previous_preparation=original_path,
                           full_analysis=True, analyzer=lambda *_: changes)['artifact']
    state.attach_preparation(record, current, PR, data['original_head'],
                             expected_parent=original['preparation_id'])
    write(record_path, record)
    def compare(prompt, checkout, *, attempt=0):
        binding = json.JSONDecoder().raw_decode(prompt[prompt.index('{"binding":'):])[0]['binding']
        def verdict(category, key):
            evidence = ['original/analysis', 'current/analysis']
            if category == 'sources':
                evidence += [side + '/' + key for side in ('original', 'current')
                             if side + '/' + key in binding['evidence']]
            return {'verdict': 'equivalent', 'rationale': 'Same engineering meaning', 'evidence': evidence}
        return json.dumps({'equivalent': True, 'rationale': 'Same reviewed requirements',
            'fields': {key: verdict('fields', key) for key in binding['fields']},
            'sources': {key: verdict('sources', key) for key in binding['changed_sources']}, 'decisions': {}})
    with patch.object(executors, 'execute', compare):
        receipt = cleanup_precheck.precheck(record_path, data['original_head'], git('rev-parse', 'HEAD'),
                                            work / '.git/pr-split-state.json', state.reconciliation_api())
    assert receipt['status'] == 'prechecked', receipt
    return receipt['precheck']


@pytest.mark.parametrize('rewrite', [False, True])
def test_modern_cli_identity_reaches_guarded_publication(repo, tmp_path, monkeypatch, rewrite):
    monkeypatch.setenv('GH_MODERN_IDENTITY', 'yes')
    data = prepare(repo)
    if rewrite:
        repo[3]('commit', '--amend', '-qm', 'Coherent cleanup')
        receipt = cleanup_receipt(repo, tmp_path)
        result = repo[-1]('push', '--allow-force-push', '--review-state', TOKEN,
                         '--cleanup-precheck', receipt['path'])
        assert result.returncode == 0, result.stderr
    result = assessment(repo, tmp_path)
    assert result['head_repo'] == 'tester/repo'
    assert result['reviewed_tree'] == data['original_tree']
