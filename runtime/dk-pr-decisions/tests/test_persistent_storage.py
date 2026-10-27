"""Cross-process storage regressions using the real command/scan/write pipeline."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'decisions.py'
spec = importlib.util.spec_from_file_location('persistent_decisions', SCRIPT)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
URL = 'https://github.com/base/project/pull/34'

# GitHub/model boundaries are deterministic, but CLI dispatch, transactions,
# refresh, JSON storage, publication receipts and process locks are real.
DRIVER = '''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
import decisions as core

live_path = Path(os.environ['TEST_LIVE'])
def live():
    return json.loads(live_path.read_text())
def pause(stage):
    if os.environ.get('TEST_PAUSE') == stage:
        Path(os.environ['TEST_MARKER']).touch()
        while True:
            time.sleep(.01)
def gh(args):
    data = live()
    if args[0] == 'api':
        assert '-X' in args and args[args.index('-X') + 1] == 'PATCH'
        data['pr']['body'] = args[-1].removeprefix('body=')
        live_path.write_text(json.dumps(data))
        pause('publication')
        return data['pr']
    assert args[:2] == ['pr', 'view'], args
    pause('snapshot')
    time.sleep(float(os.environ.get('TEST_SCAN_DELAY', '0')))
    return data['pr']
core.gh_json = gh
core.run = lambda *a, **k: 'viewer'
core.pr_diff = lambda pr: ''
core.linked_issues = lambda *a: []
core.collect_metadata = lambda *a: (live().get('comments', {}), {})
core.fetch_bodies = lambda ids: {cid: live()['comments'][cid]['body'] for cid in ids}
core.analyze = lambda *a, **k: (live().get('decisions', []), {})
if os.environ.get('TEST_PAUSE') == 'replacement':
    original = core.storage.os.replace
    def replace(*args):
        original(*args)
        pause('replacement')
    core.storage.os.replace = replace
if os.environ.get('TEST_REPORT'):
    import importlib.util
    spec = importlib.util.spec_from_file_location('report', os.environ['TEST_REPORT'])
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    report.core = core
    report.main(context_json=True, pr=sys.argv[sys.argv.index('--pr') + 1])
else:
    sys.exit(core.main())
'''


@pytest.fixture
def harness(tmp_path, monkeypatch):
    # Ignore any store configured by the host running the test suite.
    monkeypatch.delenv('DK_PR_DECISIONS_HOME', raising=False)
    driver = tmp_path / 'driver.py'
    driver.write_text(DRIVER)
    live = tmp_path / 'live.json'
    live.write_text(json.dumps({'pr': {'url': URL, 'number': 34, 'title': 'Fixture',
                                      'body': '', 'headRefOid': 'a' * 40,
                                      'headRepository': {'nameWithOwner': 'fork/project'}}}))
    env = dict(os.environ, DK_PR_DECISIONS_HOME=str(tmp_path / 'persistent'),
               XDG_DATA_HOME=str(tmp_path / 'disposable-a'), TEST_LIVE=str(live))

    class Harness:
        def start(self, *args, url=URL, **overrides):
            return subprocess.Popen([sys.executable, str(driver), str(SCRIPT.parent),
                                     '--pr', url, *args], cwd=tmp_path,
                                    env=dict(env, **overrides), text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def finish(self, process, code=0):
            out, err = process.communicate(timeout=15)
            assert process.returncode == code, (out, err)
            assert not err, err
            return json.loads(out)

        def invoke(self, *args, code=0, **kwargs):
            return self.finish(self.start(*args, **kwargs), code)

        def add(self, text, instruction=None, **kwargs):
            return self.invoke('add', '--json', json.dumps({'text': text, 'references': []}),
                               '--user-instruction', instruction or text, **kwargs)

        def update(self, **fields):
            data = json.loads(live.read_text())
            data.update(fields)
            live.write_text(json.dumps(data))

        def path(self):
            return Path(env['DK_PR_DECISIONS_HOME']) / 'v3' / core.storage.identity(URL)

    result = Harness()
    result.env, result.live, result.root = env, live, tmp_path
    return result


def wait_marker(process, marker):
    deadline = time.monotonic() + 10
    while not marker.exists():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, 'child did not reach interruption point'
        time.sleep(.01)


def test_independent_runs_recover_exact_agreement_without_mutation(harness):
    instruction = 'Keep the public API unchanged.\nUse the existing parser exactly as requested.'
    first = harness.add('Keep the public API', instruction)['data']['decision']
    disposable = Path(harness.env['XDG_DATA_HOME'])
    disposable.mkdir(exist_ok=True)
    (disposable / 'artifact').write_text('discarded')
    shutil.rmtree(disposable)
    harness.env['XDG_DATA_HOME'] = str(harness.root / 'disposable-b')
    recovered = harness.invoke('context')['data']['decisions']
    assert recovered == [first]
    assert recovered[0]['source'] == instruction
    assert json.loads(harness.live.read_text())['pr']['body'] == ''
    harness.add('Keep the public API', 'Later paraphrase must not erase the instruction')
    assert harness.invoke('list')['data']['decisions'] == [first]
    assert not Path(harness.env['XDG_DATA_HOME']).exists()


def test_isolation_and_exclusive_root(harness):
    harness.add('Only PR 34')
    for url in (URL.replace('/34', '/35'), URL.replace('/base/', '/other/'),
                URL.replace('/project/', '/other/'), URL.replace('github.com', 'github.example')):
        result = harness.invoke('list', url=url, code=1)
        assert 'No cached decisions' in result['error']['message']
    other = str(harness.root / 'empty-store')
    assert harness.invoke('list', DK_PR_DECISIONS_HOME=other, code=1)['status'] == 'error'
    assert not Path(other).exists()
    assert harness.path() == harness.root / 'persistent/v3/github.com/base/project/pr-34.json'


def test_competing_adds_and_refreshes_do_not_lose_changes(harness):
    processes = []
    for i in range(8):
        processes.append(harness.start('add', '--json', json.dumps({'text': f'Choice {i}', 'references': []}),
                                       '--user-instruction', f'Exact instruction {i}', TEST_SCAN_DELAY='.04'))
        if i % 2 == 0:
            processes.append(harness.start('scan', TEST_SCAN_DELAY='.04'))
    conflicts = []
    for process in processes:
        out, err = process.communicate(timeout=15)
        assert not err, err
        result = json.loads(out)
        assert process.returncode in (0, 1), result
        if process.returncode:
            assert result['error']['code'] == 'metadata_conflict'
            proposal = json.loads(Path(result['error']['proposal_path']).read_text())
            conflicts.append(proposal)
    # Explicitly inspect and reapply retained choices against the latest revision.
    for proposal in conflicts:
        request = proposal['request']
        if request['command'] == 'add':
            choice = json.loads(request['json'])
            harness.add(choice['text'], request['user_instruction'])
    records = harness.invoke('context')['data']['decisions']
    assert {r['text'] for r in records} == {f'Choice {i}' for i in range(8)}
    assert {r['source'] for r in records} == {f'Exact instruction {i}' for i in range(8)}
    assert json.loads(harness.path().read_text())['revision'] > 0


def test_competing_replacements_preserve_one_chain_across_restarts(harness):
    original = harness.add('First choice')['data']['decision']
    processes = [harness.start('replace', '--id', original['id'], '--json',
                               json.dumps({'text': text, 'references': []}),
                               '--user-instruction', text, TEST_SCAN_DELAY='.05')
                 for text in ('Second choice', 'Competing choice')]
    outputs = [p.communicate(timeout=15) for p in processes]
    assert sorted(p.returncode for p in processes) == [0, 1], outputs
    records = harness.invoke('context')['data']['decisions']
    second = next(r for r in records if r['status'] == 'active')
    harness.invoke('replace', '--id', second['id'], '--json',
                   json.dumps({'text': 'Final choice', 'references': []}),
                   '--user-instruction', 'Use final behavior')
    harness.add('First choice', code=1)
    records = harness.invoke('context')['data']['decisions']
    assert [r['text'] for r in records if r['status'] == 'active'] == ['Final choice']
    old = {r['id']: r for r in records}
    assert old[original['id']]['superseded_by'] == second['id']
    assert old[second['id']]['superseded_by'] == core.decision_id('Final choice')


@pytest.mark.parametrize('stage', ['snapshot', 'replacement'])
def test_killed_writer_leaves_complete_previous_or_replaced_json(harness, stage):
    harness.add('Existing choice')
    before = harness.path().read_bytes()
    marker = harness.root / 'interrupted'
    process = harness.start('scan', TEST_PAUSE=stage, TEST_MARKER=str(marker))
    try:
        wait_marker(process, marker)
    finally:
        process.kill()
        process.communicate(timeout=5)
    state = json.loads(harness.path().read_text())
    assert state['manual_decisions'] == [{'text': 'Existing choice', 'references': []}]
    if stage == 'snapshot':
        assert harness.path().read_bytes() == before
    else:
        assert state['run_number'] == json.loads(before)['run_number'] + 1
    harness.add('After interruption')  # OS releases the lock even after SIGKILL.


def test_different_pr_can_finish_while_one_pr_is_locked(harness):
    marker = harness.root / 'locked'
    process = harness.start('scan', TEST_PAUSE='snapshot', TEST_MARKER=str(marker))
    try:
        wait_marker(process, marker)
        other_live = harness.root / 'other-live.json'
        data = json.loads(harness.live.read_text())
        data['pr'].update(url=URL.replace('/34', '/35'), number=35)
        other_live.write_text(json.dumps(data))
        assert harness.invoke('scan', url=data['pr']['url'], TEST_LIVE=str(other_live))['status'] == 'ok'
        assert process.poll() is None
    finally:
        process.kill()
        process.communicate(timeout=5)


def test_uncertain_publication_receipt_survives_kill_and_refresh(harness):
    harness.add('Retain this choice')
    marker = harness.root / 'published'
    process = harness.start('publish', TEST_PAUSE='publication', TEST_MARKER=str(marker))
    try:
        wait_marker(process, marker)
    finally:
        process.kill()
        process.communicate(timeout=5)
    state = json.loads(harness.path().read_text())
    intent = state['publication_intent']
    assert intent['status'] == 'pending'
    assert state['publication_history'][-1] == intent
    live = json.loads(harness.live.read_text())
    live['pr']['headRefOid'] = 'b' * 40
    harness.update(**live)
    blocked = harness.invoke('scan', code=1)
    assert blocked['error']['code'] == 'metadata_conflict'
    assert json.loads(harness.path().read_text())['publication_intent'] == intent
    assert harness.invoke('publish')['status'] == 'pending'
    result = harness.invoke('publish', '--reconcile')
    assert result['status'] == 'pending'
    assert result['diagnostic']['action']['argv'][-1] == 'context'
    assert result['publication']['reconciled'] == 'published'
    recovered = json.loads(harness.path().read_text())
    assert recovered['publication_intent']['status'] == 'verified'
    assert recovered['publication_intent']['snapshot'] == intent['snapshot']
    assert recovered['pr']['headRefOid'] == state['pr']['headRefOid']
    assert harness.invoke('publish')['status'] == 'pending'
    context = harness.invoke(result['diagnostic']['action']['argv'][-1])
    assert context['status'] == 'ok'
    assert context['data']['verified'] is True
    assert context['data']['head_sha'] == live['pr']['headRefOid']
    refreshed = json.loads(harness.path().read_text())
    assert refreshed['publication_history'][-1] == recovered['publication_intent']
    assert json.loads(harness.live.read_text()) == live


def test_source_edits_and_deletion_retain_agreement_and_original_text(harness):
    ref = URL + '#issuecomment-123'
    comment = {'id': 'c', 'url': ref, 'body': 'We agreed: keep the parser.\nExact original rationale.',
               'createdAt': '2026-01-01', 'updatedAt': '2026-01-01', 'kind': 'general'}
    harness.update(comments={'c': comment}, decisions=[{'text': 'Keep parser', 'references': [ref]}])
    first = harness.invoke('context')['data']['decisions'][0]
    assert json.loads(first['source'])['comments'] == [comment]
    for comments in ({'c': {**comment, 'body': 'Changed to request a different parser', 'updatedAt': '2026-02-01'}}, {}):
        harness.update(comments=comments, decisions=[])
        current = harness.invoke('context')['data']['decisions'][0]
        assert current['id'] == first['id'] and current['status'] == 'active'
        source = json.loads(current['source'])
        assert source['agreement_evidence']['comments'] == [comment]
        assert source['current_comments'] == [comments.get('c')]
        assert source != json.loads(first['source'])  # Preparation fingerprint changes.
    saved = json.loads(harness.path().read_text())['saved_decisions'][first['id']]
    assert saved['source'] == first['source']


def test_deleted_description_choice_remains_available_for_reconciliation(harness):
    data = json.loads(harness.live.read_text())
    data['pr']['body'] = 'Original scope\n\n## Decisions\n\n- Keep public API\n'
    harness.update(**data)
    first = harness.invoke('context')['data']['decisions'][0]
    data['pr']['body'] = 'Scope now asks for a different API'
    harness.update(**data)
    current = harness.invoke('context')['data']['decisions'][0]
    assert current['id'] == first['id'] and current['publication_status'] == 'unpublished'
    assert json.loads(current['source'])['body'].startswith('Original scope')


def test_temporary_imports_and_report_share_storage_without_publication(harness):
    harness.add('Persistent choice')
    local = harness.root / 'local.json'
    local.write_text(json.dumps([{'text': 'Temporary choice', 'source': 'Temporary handoff'}]))
    result = harness.invoke('context', '--local-decisions-file', str(local))
    assert {r['text'] for r in result['data']['decisions']} == {'Persistent choice', 'Temporary choice'}
    assert [r['text'] for r in harness.invoke('context')['data']['decisions']] == ['Persistent choice']
    report = SCRIPT.parents[2] / 'dk-pr-read-comments/scripts/report_pr_comments.py'
    result = harness.invoke('scan', TEST_REPORT=str(report))
    assert result['decisions'][0]['text'] == 'Persistent choice'
    assert harness.path().with_suffix('.md').is_file()
    assert json.loads(harness.live.read_text())['pr']['body'] == ''


def test_offline_list_has_no_writes_or_lock_creation(harness):
    harness.add('Saved')
    harness.path().with_name(harness.path().name + '.lock').unlink()
    root = harness.root / 'persistent'
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
    bin_dir = harness.root / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'git').symlink_to(shutil.which('git'))
    result = subprocess.run([sys.executable, str(SCRIPT), '--pr', URL, 'list'],
                            env=dict(harness.env, PATH=str(bin_dir)), cwd=harness.root,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    result = json.loads(result.stdout)
    assert result['data']['cached'] is True
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('value', ['', 'relative', 'file', 'blocked', 'repository', 'symlink'])
def test_invalid_store_fails_without_fallback(harness, value):
    harness.add('Default must not leak')
    if value in ('', 'relative'):
        configured = value
    else:
        target = harness.root / value
        if value == 'file':
            target.write_text('not a directory')
        else:
            target.mkdir()
        if value == 'blocked':
            target.chmod(0)
        if value in ('repository', 'symlink'):
            subprocess.run(['git', 'init', '-q', str(target)], check=True)
        if value == 'symlink':
            link = harness.root / 'link'
            link.symlink_to(target, target_is_directory=True)
            target = link
        configured = str(target)
    try:
        result = harness.invoke('list', DK_PR_DECISIONS_HOME=configured, code=1)
        assert result['status'] == 'error'
        assert 'DK_PR_DECISIONS_HOME' in result['error']['message']
    finally:
        if value == 'blocked':
            (harness.root / value).chmod(0o700)


@pytest.mark.parametrize('corrupt', ['{broken', '[]', '{"pr": {}}', 'invalid-choice', 'invalid-evidence',
                                   'invalid-receipt'])
def test_corrupt_store_is_not_replaced_or_read_from_another_root(harness, corrupt):
    harness.add('Keep previous choice')
    if corrupt in ('invalid-choice', 'invalid-evidence', 'invalid-receipt'):
        state = json.loads(harness.path().read_text())
        if corrupt == 'invalid-choice':
            state['decisions'] = [{}]
        elif corrupt == 'invalid-evidence':
            item = next(iter(state['saved_decisions'].values()))
            item.update(source_type='discussion', source='not JSON evidence')
        else:
            state['publication_intent'] = 'corrupt uncertain publication receipt'
        corrupt = json.dumps(state)
    harness.path().write_text(corrupt)
    result = harness.invoke('scan', code=1)
    assert 'restore a valid backup' in result['error']['message']
    assert harness.path().read_text() == corrupt


def test_default_legacy_paths_and_private_synchronized_writes(tmp_path, monkeypatch):
    monkeypatch.delenv('DK_PR_DECISIONS_HOME', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    path = core.cache_path(URL)
    assert path == tmp_path / 'data/dk-pr-decisions/v3/github.com/base/project/pr-34.json'
    with patch.object(core.storage, 'sync_directory', wraps=core.storage.sync_directory) as sync:
        core.atomic_write(path, '{}\n')
    assert path.stat().st_mode & 0o777 == 0o600
    for directory in (path.parent, path.parent.parent, tmp_path / 'data/dk-pr-decisions'):
        assert directory.stat().st_mode & 0o777 == 0o700
    assert sync.call_args_list[-1].args == (path.parent,)
    monkeypatch.delenv('XDG_DATA_HOME')
    with patch.object(Path, 'home', return_value=tmp_path):
        assert core.cache_path(URL) == tmp_path / '.local/share/dk-pr-decisions/v3' / core.storage.identity(URL)
