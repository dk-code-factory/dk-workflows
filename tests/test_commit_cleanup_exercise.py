"""Validate exercise mechanics without launching native agents or services."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTERFACE = ROOT/'tests/fixtures/commit_cleanup_exercise.py'
spec = importlib.util.spec_from_file_location('cleanup_exercise', INTERFACE)
exercise = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exercise)


@pytest.mark.parametrize('pair', ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'combined'])
def test_replay_sources_and_historical_restoration_patches_apply(tmp_path, pair):
    bundle = json.loads((ROOT/'docs/evidence/146-commit-cleanup-replay.json').read_text())
    source = tmp_path/'source'
    source.mkdir()
    baseline = '0f927a9d01066080665be1ab7e8bd20eb38ae1d5'
    archive = subprocess.check_output(['git', '-C', str(ROOT), 'archive', baseline])
    # The fixed repository baseline is trusted test input, not an uploaded archive.
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        files.extractall(source, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))

    def apply(patch):
        subprocess.run(['git', 'apply', '-'], input=patch, cwd=source,
                       text=True, capture_output=True, check=True)

    apply(bundle['accepted_source_patches']['1'])
    if pair not in {'c1', 'c2'}:
        apply(bundle['accepted_source_patches']['3'])
    if pair in {'c6', 'combined'}:
        apply(bundle['accepted_source_patches']['6'])
    if pair in {'c2', 'c4', 'c5'}:
        apply(bundle['dropped_proposal_patches'][pair[1:]])
    if pair == 'c3':
        helper = source/'scripts/commit_series.py'
        helper.write_bytes((ROOT/'scripts/commit_series.py').read_bytes())
        apply(bundle['helper_restore_patch_for_isolated_pair'])
        assert exercise.sha(helper.read_bytes()) == bundle['historical_helper_sha256']
    interface = source/'tests/fixtures/commit_cleanup_exercise.py'
    interface.write_bytes(INTERFACE.read_bytes())
    apply(bundle['interface_restore_patches_from_retained'][pair])
    assert exercise.sha(interface.read_bytes()) == bundle['historical_interface_sha256'][pair]
    evidence = json.loads((ROOT/'docs/evidence/146-commit-cleanup-evaluation.json').read_text())
    assert exercise.sha(interface.read_bytes()) == evidence['pairs'][pair]['after']['hashes']['interface']
    compile(interface.read_bytes(), str(interface), 'exec')


def test_mutations_do_not_count_as_inspection_operations(tmp_path):
    root = tmp_path/'arm'
    exercise.initialize(root, ROOT, 2)
    interface = [sys.executable, str(root/'frozen/interface.py'), '--root', str(root)]
    for args in [('git', 'alpha', 'branch', 'cleanup-recovery', 'HEAD'),
                 ('git', 'alpha', 'config', 'exercise.marker', 'written'),
                 ('git', 'alpha', 'log', '-1', '--output=log-output'),
                 ('git', 'alpha', 'log', '-1', '--output', 'other-log-output')]:
        subprocess.run([*interface, *args], capture_output=True, text=True, check=True)
    for args in [('git', 'alpha', 'status', '--porcelain'),
                 ('git', 'alpha', 'config', '--get', 'exercise.marker')]:
        subprocess.run([*interface, *args], capture_output=True, text=True, check=True)
    metrics = json.loads(subprocess.check_output([*interface, 'summarize'], text=True))
    assert metrics['native_git_or_inspect_operations'] == 6
    assert metrics['inspection_operations'] == 2
    # Unrecognized operations and other mutating spellings must also fail closed.
    for command in ('merge', 'cherry-pick', 'rm', 'revert', 'future-command'):
        exercise.append(root/'trace.jsonl', {'command': 'git', 'args': ['alpha', command], 'ok': False})
    assert exercise.dispatch(root, 'summarize', [])['inspection_operations'] == 2


def test_real_worktrees_traces_failures_and_tree_preserving_application(tmp_path):
    root = tmp_path/'arm'
    exercise.initialize(root, ROOT, 2)
    def command(*args):
        result = subprocess.run([sys.executable, str(root/'frozen/interface.py'),
                                 '--root', str(root), *args], capture_output=True, text=True)
        return result, json.loads(result.stdout)
    result, read = command('read', 'guidance/codex/skills/dk-commit-split/SKILL.md')
    assert result.returncode == 0
    assert read['bytes'] == len((root/read['path']).read_bytes())
    for operands in [('read', 'missing'), ('read', str(tmp_path/'outside')), ('apply',)]:
        result, value = command(*operands)
        assert result.returncode == 2 and value['error']
    info = exercise.task(root, 'beta')
    checkout = root/info['checkout']
    head = info['reviewed_head']
    exercise.git(root, checkout, 'update-ref', 'refs/cleanup/original', head)
    plan = [{'message': 'Set library value to three', 'files': {'core.py': head+':core.py'}},
            {'message': 'Document library usage', 'files': {
                'README.md': head+':README.md', 'examples.md': head+':examples.md'}}]
    result, rewritten = command('apply', 'beta', json.dumps(plan))
    assert result.returncode == 0, result.stdout+result.stderr
    assert rewritten['tree'] == info['reviewed_tree']
    assert len(rewritten['resulting_heads']) == 2
    assert exercise.git(root, checkout, 'rev-parse', 'refs/cleanup/original').strip() == head
    result, metrics = command('summarize')
    assert result.returncode == 0
    assert metrics['applications'] == 2  # Includes the traced argument failure.
    assert metrics['failed_operations'] == 3
    assert metrics['instruction_bytes'] == read['bytes']
    assert metrics['test_executions'] == 0  # Setup tests are not native operations.
    rows = [json.loads(line) for line in (root/'trace.jsonl').read_text().splitlines()]
    assert all('output_bytes' in row and 'ok' in row for row in rows)


def test_interrupted_local_push_reconciles_without_another_send(tmp_path):
    root = tmp_path/'arm'
    exercise.initialize(root, ROOT, 6)
    assert len((root/'pushes.jsonl').read_text().splitlines()) == 1
    path = json.loads((root/'operations/zeta-path.json').read_text())['operation']
    assert json.loads(Path(path).read_text())['status'] == 'pending'
    info = exercise.task(root, 'zeta')
    assert exercise.git(root, root/info['checkout'], 'rev-parse',
                        info['recovery_refs'][0]).strip() == info['reviewed_head']
    for _ in range(2):
        exercise.publication(root, 'zeta', 'reconcile')
        assert json.loads(Path(path).read_text())['status'] == 'verified'
    assert len((root/'pushes.jsonl').read_text().splitlines()) == 1


def test_task_events_use_production_history_sensitive_cache(tmp_path, monkeypatch):
    # The store deliberately lets the agent select history-sensitive capture.
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'state'))
    root = tmp_path/'arm'
    exercise.initialize(root, ROOT, 4)
    assert exercise.state(root, 'epsilon', 'start', 'history')['action'] == 'invoke'
    result = {'complete': True, 'findings': [], 'coverage': 'two boundaries'}
    exercise.state(root, 'epsilon', 'record', 'history', result)
    exercise.event(root, 'epsilon', 'unchanged')
    assert exercise.state(root, 'epsilon', 'start', 'history')['action'] == 'reuse'
    for kind in ('message', 'history', 'target', 'context'):
        exercise.event(root, 'epsilon', kind)
        assert exercise.state(root, 'epsilon', 'start', 'history')['action'] == 'invoke'
        exercise.state(root, 'epsilon', 'record', 'history', result)
    exercise.event(root, 'epsilon', 'lease')
    assert exercise.state(root, 'epsilon', 'start', 'history')['action'] == 'reuse'


def test_tree_mismatch_retains_original_recovery_and_remote(tmp_path):
    root = tmp_path/'arm'
    exercise.initialize(root, ROOT, 2)
    info = exercise.task(root, 'beta')
    checkout = root/info['checkout']
    original = info['reviewed_head']
    exercise.git(root, checkout, 'update-ref', 'refs/cleanup/recovery', original)
    result = exercise.apply(root, 'beta', [{'message': 'Incomplete library change',
                             'files': {'core.py': original+':core.py'}}])
    assert result['tree'] != info['reviewed_tree']
    assert exercise.git(root, checkout, 'rev-parse', 'refs/cleanup/recovery').strip() == original
    assert exercise.git(root, checkout, 'ls-remote', str(root/info['remote']),
                        'refs/heads/topic').split()[0] == original


def test_validation_evidence_variants_preserve_raw_invalid_inputs(tmp_path):
    root = tmp_path/'arm'
    exercise.initialize(root, ROOT, 5)
    info = exercise.task(root, 'beta')
    assert len(info['validation_requests']) == 4
    receipts = {item['receipt']: json.loads((root/item['receipt']).read_text())
                for item in info['validation_requests']}
    valid = json.loads((root/'receipts/beta-content.json').read_text())
    assert valid['complete'] and valid['history_independent']
    assert valid['environment'] == json.loads((root/'environment.json').read_text())
    assert receipts['receipts/beta-incomplete.json']['environment'] is None
    assert receipts['receipts/beta-changed-input.json']['inputs'] != valid['inputs']
    assert receipts['receipts/beta-changed-command.json']['command'] != valid['command']
    assert not receipts['receipts/beta-history-dependent.json']['history_independent']


def test_missing_state_mode_fails_before_claim_and_retains_original_output(tmp_path):
    root = tmp_path/'arm'
    exercise.initialize(root, ROOT, 4)
    result = subprocess.run([sys.executable, str(root/'frozen/interface.py'),
                             '--root', str(root), 'state', 'epsilon', 'start'],
                            capture_output=True, text=True)
    assert result.returncode == 2 and json.loads(result.stdout)['error'] == 'ValueError'
    assert not (root/'claims').exists()
    row = json.loads((root/'trace.jsonl').read_text().splitlines()[-1])
    original = (root/row['output_path']).read_bytes()
    assert not row['ok'] and original == result.stdout.rstrip('\n').encode()
    assert row['output_sha256'] == exercise.sha(original)
