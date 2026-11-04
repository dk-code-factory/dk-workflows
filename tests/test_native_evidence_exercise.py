"""Validate the offline operations; native coordinators run separately."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INTERFACE = ROOT/'tests/fixtures/native_evidence_exercise.py'


def initialize(tmp_path, arm='before'):
    path = tmp_path/arm
    result = subprocess.run([sys.executable, str(INTERFACE), '--root', str(path), 'init', arm],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout+result.stderr
    def command(*args, ok=True):
        result = subprocess.run([sys.executable, str(path/'frozen/interface.py'), '--root', str(path), *args],
                                capture_output=True, text=True)
        assert result.returncode == (0 if ok else 2), result.stdout+result.stderr
        return json.loads(result.stdout)
    return path, command


def test_native_evidence_real_pagination_stability_and_shared_analysis(tmp_path):
    root, command = initialize(tmp_path)
    manifest = json.loads((root/'exercise.json').read_text())
    assert hashlib.sha256((root/'frozen/interface.py').read_bytes()).hexdigest() == manifest['interface_sha256']
    for name, expected in manifest['runtime_hashes'].items():
        assert hashlib.sha256((root/'frozen'/name).read_bytes()).hexdigest() == expected
    original = (root/'instructions.txt').read_bytes()
    read = command('read', 'instructions.txt')
    assert read['text'].encode('utf-8') == original
    assert read['bytes'] == len(original)
    assert read['sha256'] == hashlib.sha256(original).hexdigest()
    first = command('collect')['receipt_file']
    for role in ('feedback', 'decisions', 'read-comments'):
        view = command('view', role, first)
        assert [thread['id'] for thread in view['threads']] == ['T1', 'T2']
        assert len(view['threads'][1]['comments']) == 2  # Real reply pagination.
        assert len(view['conversation']) == 2
        assert Path(view['instructions'][0]['path']).read_bytes() == (root/'instructions.txt').read_bytes()
        assert command('verify', first)['unchanged']
    summary = command('summarize')
    assert summary['initial_collections'] == 1
    assert summary['acceptance_verifications'] == 3
    # Each stability loop repeats metadata, two thread pages, one reply page,
    # paginated reviews/comments and the head check twice: 8+6 transport reads.
    assert summary['remote_reads'] == 14*4
    assert summary['failed_operations'] == 0
    assert summary['returned_bytes'] == sum(row['output_bytes'] for row in summary['operations'])
    assert summary['remote_returned_bytes'] == sum(read['bytes'] for row in summary['operations'] for read in row['remote_reads'])
    second = command('collect')['receipt_file']
    left = command('view', 'feedback', first)
    right = command('view', 'feedback', second)
    # Original instruction artifacts use private unique paths; source bytes match.
    assert left.pop('instructions')[0]['sha256'] == right.pop('instructions')[0]['sha256']
    assert left == right


def test_native_evidence_trace_failures_and_unchanged_head_drift(tmp_path):
    root, command = initialize(tmp_path)
    receipt = command('collect')['receipt_file']
    assert command('read', 'missing.json', ok=False)['error'] == 'FileNotFoundError'
    assert command('read', str(tmp_path/'outside.json'), ok=False)['error'] == 'ValueError'
    assert command('view', 'bogus', receipt, ok=False)['error'] == 'ValueError'
    source = json.loads((root/'sources.json').read_text())
    source['comments'][0]['body'] = 'Changed human decision at the same head'
    (root/'sources.json').write_text(json.dumps(source))
    assert 'evidence changed' in command('verify', receipt, ok=False)['detail']
    summary = command('summarize')
    assert summary['failed_operations'] == 4
    assert summary['remote_reads'] == 28
    assert summary['operations'][-1]['remote_reads']


def test_native_evidence_arms_pin_identical_runtime_and_sources(tmp_path):
    before, _ = initialize(tmp_path, 'before')
    after, _ = initialize(tmp_path, 'after')
    a = json.loads((before/'exercise.json').read_text())
    b = json.loads((after/'exercise.json').read_text())
    for field in ('runtime', 'runtime_hashes', 'source_sha256', 'interface_sha256', 'direct_instructions'):
        assert a[field] == b[field]
