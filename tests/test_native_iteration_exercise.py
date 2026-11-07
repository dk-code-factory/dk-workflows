"""Exercise-only safety and trace checks; no native agent or service is launched."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
INTERFACE = ROOT/'tests/fixtures/native_iteration_exercise.py'


def test_frozen_interface_records_failed_reads_and_rejects_cross_task_claim(tmp_path):
    arm = tmp_path/'arm'
    initialized = subprocess.run([sys.executable, str(INTERFACE), '--root', str(arm), 'init', 'before'],
                                 capture_output=True, text=True)
    assert initialized.returncode == 0, initialized.stdout+initialized.stderr
    manifest = json.loads((arm/'exercise.json').read_text())
    frozen = arm/'frozen/interface.py'
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == manifest['interface_sha256']
    assert (arm/'checkout/code.py').read_text() == 'VALUE = 2\n'
    def command(*args):
        return subprocess.run([sys.executable, str(frozen), '--root', str(arm), *args],
                              capture_output=True, text=True)
    missing = command('read', 'missing.json')
    assert missing.returncode == 2 and json.loads(missing.stdout)['error'] == 'FileNotFoundError'
    blocked = command('read', str(tmp_path/'outside.json'))
    assert blocked.returncode == 2 and json.loads(blocked.stdout)['error'] == 'ValueError'
    started = command('state', 'start', '--spec', 'scope-partition', '--run', 'exercise')
    assert started.returncode == 0, started.stdout+started.stderr
    claim = json.loads(started.stdout)['claim']['id']
    mismatched = command('invoke', 'series-cleanup', claim)
    assert mismatched.returncode == 2
    invoked = command('invoke', 'scope-partition', claim)
    assert invoked.returncode == 0 and json.loads(invoked.stdout)['result']['complete'] is True
    malformed = [command('read'), command('finish'), command('state', 'start', '--spec')]
    for result in malformed:
        assert result.returncode == 2, result.stdout+result.stderr
        assert json.loads(result.stdout)['error'] == 'IndexError'
        assert not result.stderr
    trace = [json.loads(line) for line in (arm/'trace.jsonl').read_text().splitlines()]
    assert [row['ok'] for row in trace] == [False, False, True, False, True, False, False, False]
    assert trace[0]['output_bytes'] == len(missing.stdout.rstrip('\n').encode())
    for row, result in zip(trace[-3:], malformed):
        assert row['output_bytes'] == len(result.stdout.rstrip('\n').encode())


def test_frozen_interface_counts_guidance_by_resolved_path(tmp_path):
    arm = tmp_path/'arm'
    initialized = subprocess.run([sys.executable, str(INTERFACE), '--root', str(arm), 'init', 'before'],
                                 capture_output=True, text=True)
    assert initialized.returncode == 0, initialized.stdout+initialized.stderr
    frozen = arm/'frozen/interface.py'
    def command(*args):
        return subprocess.run([sys.executable, str(frozen), '--root', str(arm), *args],
                              capture_output=True, text=True)
    guidance = arm/'guidance/runtime/pr-iter.md'
    paths = ('guidance/runtime/pr-iter.md', './guidance/runtime/pr-iter.md', str(guidance))
    for path in paths:
        read = command('read', path)
        assert read.returncode == 0, read.stdout+read.stderr
        assert read.stdout == guidance.read_text()+'\n'
    evidence = command('read', 'exercise.json')
    assert evidence.returncode == 0, evidence.stdout+evidence.stderr
    finished = command('finish', '{}')
    assert finished.returncode == 0, finished.stdout+finished.stderr
    summarized = command('summarize')
    assert summarized.returncode == 0, summarized.stdout+summarized.stderr
    summary = json.loads(summarized.stdout)
    assert summary['guidance_reads'] == 3
    assert summary['guidance_read_bytes'] == 3*len(guidance.read_bytes())
    assert summary['evidence_reads'] == 1
    assert summary['evidence_read_bytes'] == len((arm/'exercise.json').read_bytes())
    reads = [json.loads(line) for line in (arm/'trace.jsonl').read_text().splitlines()
             if json.loads(line)['command'] == 'read']
    assert [row['args'][0] for row in reads[:3]] == list(paths)
    assert [row['read_path'] for row in reads] == ['guidance/runtime/pr-iter.md']*3+['exercise.json']


@pytest.mark.parametrize('arguments,error', [
    (('read',), 'IndexError'),
    (('invoke',), 'ValueError'),
    (('invoke', 'unknown-task', 'claim'), 'ValueError'),
    (('finish',), 'IndexError'),
    (('state', 'start', '--spec'), 'IndexError'),
    (('finish', '['*60000+']'*60000), 'RecursionError'),
], ids=['missing-read-operand', 'missing-invoke-operands', 'unknown-invoke-task',
        'missing-finish-operand', 'missing-state-spec', 'deeply-nested-assessment'])
def test_frozen_interface_summarizes_dispatched_argument_and_data_failures(tmp_path, arguments, error):
    arm = tmp_path/'arm'
    initialized = subprocess.run([sys.executable, str(INTERFACE), '--root', str(arm), 'init', 'before'],
                                 capture_output=True, text=True)
    assert initialized.returncode == 0, initialized.stdout+initialized.stderr
    frozen = arm/'frozen/interface.py'
    def command(*args):
        return subprocess.run([sys.executable, str(frozen), '--root', str(arm), *args],
                              capture_output=True, text=True)
    failed = command(*arguments)
    assert failed.returncode == 2, failed.stdout+failed.stderr
    assert json.loads(failed.stdout)['error'] == error
    assert not failed.stderr
    finished = command('finish', '{}')
    assert finished.returncode == 0, finished.stdout+finished.stderr
    summarized = command('summarize')
    assert summarized.returncode == 0, summarized.stdout+summarized.stderr
    summary = json.loads(summarized.stdout)
    assert summary['failed_operations'] == 1
    assert summary['operation_counts'][arguments[0]] == (2 if arguments[0] == 'finish' else 1)
    assert summary['task_invocations'] == []
    assert summary['assessment'] == {}
    assert summary['output_bytes'] == len(failed.stdout.rstrip('\n').encode())+len(finished.stdout.rstrip('\n').encode())
    trace = [json.loads(line) for line in (arm/'trace.jsonl').read_text().splitlines()]
    assert [row['ok'] for row in trace] == [False, True, True]
    assert trace[0]['args'] == list(arguments[1:])
    assert trace[0]['output_bytes'] == len(failed.stdout.rstrip('\n').encode())


def test_current_runtime_and_explicit_baseline_are_frozen_independently(tmp_path):
    revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    baseline = '620f8df51b1066e44faeb69878235adcd3c49317'
    baseline_guidance = subprocess.check_output(['git', '-C', str(ROOT),
                                                'show', baseline+':runtime/pr-iter.md'])
    assert baseline_guidance != subprocess.check_output(['git', '-C', str(ROOT),
                                                         'show', revision+':runtime/pr-iter.md'])
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    manifests = []
    for guidance in ('before', 'after'):
        arm = tmp_path/guidance
        initialized = subprocess.run([sys.executable, str(INTERFACE), '--root', str(arm),
                                      'init', guidance, revision, baseline],
                                     capture_output=True, text=True)
        assert initialized.returncode == 0, initialized.stdout+initialized.stderr
        manifest = json.loads((arm/'exercise.json').read_text())
        manifests.append(manifest)
        assert manifest['runtime'] == revision
        if (ROOT/'runtime/skill_config.py').exists():
            assert 'codex/skills/dk-pr-fix/config.json' in manifest['runtime_hashes']
            assert 'codex/skills/dk-pr-fix/config.schema.json' in manifest['runtime_hashes']
        assert manifest['guidance_revision'] == (baseline if guidance == 'before' else None)
        assert manifest['source_sha256'] == hashlib.sha256((arm/'sources.json').read_bytes()).hexdigest()
        for name, digest in manifest['runtime_hashes'].items():
            frozen = arm/'frozen'/name
            assert hashlib.sha256(frozen.read_bytes()).hexdigest() == digest
            assert frozen.read_bytes() == subprocess.check_output(['git', '-C', str(ROOT),
                                                                  'show', revision+':'+name])
        # Newer state/config helpers import shared dependencies; exercising the
        # frozen CLI proves the selected runtime can run outside its checkout.
        observed = subprocess.run([sys.executable, str(arm/'frozen/interface.py'), '--root', str(arm),
                                   'state', 'status', '--compact', '--run', 'exercise',
                                   '--spec', 'reviewer-a', '--spec', 'reviewer-b'],
                                  capture_output=True, text=True, cwd=tmp_path, env=env)
        assert observed.returncode == 0, observed.stdout+observed.stderr
        if guidance == 'before':
            assert (arm/'guidance/runtime/pr-iter.md').read_bytes() == baseline_guidance
        else:
            assert (arm/'guidance/runtime/pr-iter.md').read_bytes() == (ROOT/'runtime/pr-iter.md').read_bytes()
        for task in ('reviewer-a', 'scope-partition'):
            spec = json.loads((arm/'specs'/(task+'.json')).read_text())
            handoff = json.loads(Path(spec['inputs']['preferences']).read_text())
            assert handoff['selection']['records'] == []
            assert handoff['selection']['paths'] == ['code.py']
            assert handoff['selection']['mode'] == ('review' if task == 'reviewer-a' else 'implementation')
            assert handoff['decisions'] == []
    assert manifests[0]['runtime_hashes'] == manifests[1]['runtime_hashes']
    assert manifests[0]['source_sha256'] == manifests[1]['source_sha256']
