import concurrent.futures
import json
from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from shared import cli_reference as reference


def fixture(tmp_path):
    root = tmp_path / 'plugin'
    (root / 'scripts').mkdir(parents=True)
    (root / '.codex-plugin').mkdir()
    (root / '.codex-plugin/plugin.json').write_text('{"version":"1.2.3"}')
    (root / 'scripts/dk.py').write_text('router')
    counter = tmp_path / 'calls'
    helper = root / 'scripts/helper.py'
    helper.write_text('from pathlib import Path\n'
        f'p=Path({str(counter)!r});p.write_text(str(int(p.read_text())+1) if p.exists() else "1")\n'
        'print("usage: helper [--safe]")\n')
    return root, {'fixture': ('scripts/helper.py', None, None, False)}, helper, counter


def test_root_agents_share_one_real_help_call_and_retained_text(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    root, routes, helper, counter = fixture(tmp_path)
    created = reference.create(root, routes, tmp_path, 'root-token')
    path = created['reference']
    assert reference.create(root, routes, tmp_path, 'root-token') == created

    def read():
        return reference.help_result(path, root, routes, 'fixture', ['--help'],
                                     [sys.executable, str(helper), '--help'], tmp_path, None)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: read(), range(4)))
    assert counter.read_text() == '1'
    assert sum(not r['cached'] for r in results) == 1
    record = json.loads(Path(path).read_text())
    key = results[0]['section'].rsplit('/', 1)[1]
    assert record['help'][key]['stdout'] == 'usage: helper [--safe]\n'
    assert len(json.dumps(results[0])) < 1000


def test_source_and_version_changes_invalidate_without_running_stale_help(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    root, routes, helper, counter = fixture(tmp_path)
    first = reference.create(root, routes, tmp_path, 'workflow')['reference']
    for target, text in ((helper, helper.read_text() + '# modified\n'),
                         (root / '.codex-plugin/plugin.json', '{"version":"1.2.4"}')):
        target.write_text(text)
        with pytest.raises(ValueError, match='stale'):
            reference.help_result(first, root, routes, 'fixture', ['--help'],
                [sys.executable, str(helper), '--help'], tmp_path, None)
        second = reference.create(root, routes, tmp_path, 'workflow')['reference']
        assert second != first and not counter.exists()
        first = second


def test_reference_never_overwrites_another_workflow_or_repository(tmp_path):
    root, routes, _, _ = fixture(tmp_path)
    path = tmp_path / 'reference.json'
    reference.create(root, routes, tmp_path, 'one', path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='different root workflow'):
        reference.create(root, routes, tmp_path, 'two', path)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='outside'):
        reference.create(root, routes, tmp_path, 'one', root / 'reference.json')


def test_real_cli_reference_is_reused_for_same_help_query(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    script = ROOT / 'scripts/dk.py'
    context = [sys.executable, str(script), '--host', 'codex', '--cwd', str(tmp_path)]
    created = subprocess.run([*context, '--parent', 'workflow', 'reference'],
                             capture_output=True, text=True, check=True)
    path = json.loads(created.stdout)['reference']
    query = [*context, '--reference', path, '--repo', 'o/r', '--pr', '42', 'run-state', '--help']
    for cached in (False, True):
        result = subprocess.run(query, capture_output=True, text=True, check=True)
        receipt = json.loads(result.stdout)
        assert receipt['cached'] is cached and receipt['reference'] == path
    record = json.loads(Path(path).read_text())
    assert len(record['help']) == 1
    assert 'start-run' in next(iter(record['help'].values()))['stdout']
