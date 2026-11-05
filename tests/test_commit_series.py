"""Read-only inspection agrees with independently assembled real Git histories."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT/'scripts/commit_series.py'


def git(checkout, *args, input=None, ok=True):
    result = subprocess.run(['git', '-C', str(checkout), *args], input=input,
                            text=True, capture_output=True)
    if ok:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Fixture Author')
    git(tmp_path, 'config', 'user.email', 'fixture@example.test')
    git(tmp_path, 'config', 'commit.gpgsign', 'false')
    (tmp_path/'code').write_text('base\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'Initial library')
    return tmp_path


def inspect(checkout, target='main', head='HEAD', ok=True, env=None):
    result = subprocess.run([sys.executable, str(CLI), '--checkout', str(checkout),
                             '--target='+target, '--head='+head], text=True, capture_output=True,
                            env=env)
    assert result.returncode == (0 if ok else 2), result.stdout+result.stderr
    return json.loads(result.stdout if ok else result.stderr)


def snapshot(checkout):
    return {str(p.relative_to(checkout)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in checkout.rglob('*') if p.is_file()}


def test_linear_series_full_messages_paths_modes_binary_and_no_mutation(repository):
    p = repository
    base = git(p, 'rev-parse', 'HEAD')
    git(p, 'switch', '-qc', 'topic')
    unusual = 'space\tline\n雪'
    (p/unusual).write_bytes(b'\x00\xff\n')
    (p/'executable').write_text('#!/bin/sh\nexit 0\n')
    (p/'executable').chmod(0o755)
    git(p, 'add', '.')
    git(p, 'commit', '-qm', 'Add binary and executable\n\nKeep full message.\nSecond paragraph.')
    first = git(p, 'rev-parse', 'HEAD')
    git(p, 'mv', 'code', 'renamed')
    git(p, 'rm', '-q', 'executable')
    git(p, 'commit', '-qm', 'Rename library path and remove executable')
    last = git(p, 'rev-parse', 'HEAD')
    (p/'untracked').write_text('preserve this\n')
    (p/'renamed').write_text('dirty\n')
    before = snapshot(p)
    value = inspect(p)
    assert snapshot(p) == before
    assert value['target'] == value['merge_base'] == base
    assert value['head'] == last
    assert value['reviewed_tree'] == git(p, 'rev-parse', 'HEAD^{tree}')
    a, b = value['commits']
    assert [a['oid'], b['oid']] == [first, last]
    assert a['parents'] == [base] and b['parents'] == [first]
    assert a['changed_paths'] == ['executable', unusual]
    assert b['changed_paths'] == ['code', 'executable', 'renamed']
    assert a['message'] == 'Add binary and executable\n\nKeep full message.\nSecond paragraph.\n'
    assert a['author']['name'] == 'Fixture Author'
    assert a['author']['email'] == 'fixture@example.test'
    assert a['author']['date'] == git(p, 'show', '-s', '--format=%aI', first)


def test_empty_series_and_same_tree_message_rewrite(repository):
    p = repository
    assert inspect(p)['commits'] == []
    base = git(p, 'rev-parse', 'HEAD')
    git(p, 'switch', '-qc', 'topic')
    git(p, 'commit', '--allow-empty', '-qm', 'Release marker')
    a = inspect(p)
    git(p, 'commit', '--amend', '--allow-empty', '-qm', 'Describe release marker')
    b = inspect(p)
    assert a['reviewed_tree'] == b['reviewed_tree']
    assert a['head'] != b['head']
    assert b['commits'][0]['parents'] == [base]
    assert b['commits'][0]['changed_paths'] == []
    assert b['commits'][0]['message'] == 'Describe release marker\n'


def test_merge_metadata_includes_both_parents_and_parent_relative_paths(repository):
    p = repository
    base = git(p, 'rev-parse', 'HEAD')
    git(p, 'switch', '-qc', 'side')
    (p/'side').write_text('side\n')
    git(p, 'add', '.')
    git(p, 'commit', '-qm', 'Add side')
    side = git(p, 'rev-parse', 'HEAD')
    git(p, 'switch', '-qc', 'topic', base)
    (p/'topic').write_text('topic\n')
    git(p, 'add', '.')
    git(p, 'commit', '-qm', 'Add topic')
    topic = git(p, 'rev-parse', 'HEAD')
    git(p, 'merge', '--no-ff', '-qm', 'Merge side', 'side')
    value = inspect(p)
    commits = value['commits']
    assert {c['oid'] for c in commits[:-1]} == {side, topic}
    assert commits[-1]['parents'] == [topic, side]
    assert commits[-1]['changed_paths'] == ['side', 'topic']
    seen = {base}
    for item in commits:
        assert set(item['parents']) <= seen
        seen.add(item['oid'])


def test_criss_cross_history_rejects_multiple_merge_bases(repository):
    p = repository
    tree = git(p, 'rev-parse', 'HEAD^{tree}')
    base = git(p, 'rev-parse', 'HEAD')
    a = git(p, 'commit-tree', tree, '-p', base, input='A\n')
    b = git(p, 'commit-tree', tree, '-p', base, input='B\n')
    left = git(p, 'commit-tree', tree, '-p', a, '-p', b, input='Left\n')
    right = git(p, 'commit-tree', tree, '-p', b, '-p', a, input='Right\n')
    before = snapshot(p)
    assert 'unique merge base' in inspect(p, left, right, ok=False)['error']
    assert snapshot(p) == before


@pytest.mark.parametrize('target,head', [('missing', 'HEAD'), ('main', 'missing'),
                                        ('--help', 'HEAD'), ('main', '--help')])
def test_invalid_or_option_shaped_refs_fail_without_mutation(repository, target, head):
    before = snapshot(repository)
    assert inspect(repository, target, head, ok=False)['error']
    assert snapshot(repository) == before


def test_unrelated_histories_fail_clearly(repository):
    p = repository
    tree = git(p, 'rev-parse', 'HEAD^{tree}')
    unrelated = git(p, 'commit-tree', tree, input='Unrelated\n')
    assert inspect(p, 'main', unrelated, ok=False)['error']


def test_git_replacement_does_not_change_pinned_inspection(repository):
    p = repository
    git(p, 'switch', '-qc', 'topic')
    git(p, 'commit', '--allow-empty', '-qm', 'Original boundary')
    head = git(p, 'rev-parse', 'HEAD')
    tree = git(p, 'rev-parse', 'HEAD^{tree}')
    replacement = git(p, 'commit-tree', tree, '-p', 'main', input='Replacement boundary\n')
    git(p, 'replace', head, replacement)
    value = inspect(p)
    assert value['head'] == head
    assert value['commits'][0]['message'] == 'Original boundary\n'


@pytest.mark.parametrize('encoding', ['UTF-16', 'ISO-8859-1'])
def test_log_output_encoding_cannot_change_metadata_framing(repository, encoding):
    git(repository, 'switch', '-qc', 'topic')
    git(repository, 'commit', '--allow-empty', '-qm', 'Preserve café and 雪\n\nFull message.')
    expected = inspect(repository)
    git(repository, 'config', 'i18n.logOutputEncoding', encoding)
    before = snapshot(repository)
    assert inspect(repository) == expected
    assert snapshot(repository) == before


@pytest.mark.parametrize('variable', ['GIT_DIR', 'GIT_COMMON_DIR'])
def test_repository_environment_cannot_redirect_checkout(repository, tmp_path, variable):
    foreign = tmp_path/'foreign'
    git(tmp_path, 'clone', '-q', str(repository), str(foreign))
    git(repository, 'switch', '-qc', 'topic')
    git(repository, 'commit', '--allow-empty', '-qm', 'Requested repository boundary')
    expected = inspect(repository)
    before = snapshot(repository)
    value = inspect(repository, env={**os.environ, variable: str(foreign/'.git')})
    assert value == expected
    assert snapshot(repository) == before


@pytest.mark.parametrize('external', [False, True])
def test_grafts_cannot_hide_commits_or_change_parents(repository, tmp_path, external):
    base = git(repository, 'rev-parse', 'HEAD')
    git(repository, 'switch', '-qc', 'topic')
    git(repository, 'commit', '--allow-empty', '-qm', 'First boundary')
    first = git(repository, 'rev-parse', 'HEAD')
    git(repository, 'commit', '--allow-empty', '-qm', 'Second boundary')
    head = git(repository, 'rev-parse', 'HEAD')
    expected = inspect(repository)
    grafts = tmp_path/'external-grafts' if external else repository/'.git/info/grafts'
    grafts.write_text(f'{head} {base}\n')
    env = {**os.environ, 'GIT_NO_REPLACE_OBJECTS': '1'}
    if external:
        env['GIT_GRAFT_FILE'] = str(grafts)
    altered = subprocess.run(['git', '-C', str(repository), 'rev-list', base+'..'+head],
                             text=True, capture_output=True, env=env, check=True)
    assert altered.stdout.splitlines() == [head]
    before = snapshot(repository)
    value = inspect(repository, env=env)
    assert value == expected
    assert [c['oid'] for c in value['commits']] == [first, head]
    assert value['commits'][1]['parents'] == [first]
    assert snapshot(repository) == before


def test_shallow_checkout_fails_without_fetching(repository, tmp_path):
    clone = tmp_path/'shallow'
    git(tmp_path, 'clone', '-q', '--depth=1', repository.as_uri(), str(clone))
    before = snapshot(clone)
    assert 'shallow' in inspect(clone, ok=False)['error']
    assert snapshot(clone) == before


def test_missing_promisor_trees_fail_without_implicit_fetch(repository, tmp_path, monkeypatch):
    git(repository, 'config', 'uploadpack.allowFilter', 'true')
    clone = tmp_path/'partial'
    git(tmp_path, 'clone', '-q', '--no-checkout', '--filter=tree:0', repository.as_uri(), str(clone))
    before = snapshot(clone)
    trace = tmp_path/'git-events.jsonl'
    monkeypatch.setenv('GIT_TRACE2_EVENT', str(trace))
    assert inspect(clone, ok=False)['error']
    assert snapshot(clone) == before
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert not any('fetch' in event.get('argv', []) for event in events)


def test_git_without_no_lazy_fetch_support_fails_before_inspection(repository, tmp_path_factory):
    shim = tmp_path_factory.mktemp('git-shim')
    trace = shim/'calls.jsonl'
    executable = shim/'git'
    executable.write_text(f'''#!{sys.executable}
import json, os, sys
with open({str(trace)!r}, 'a') as out:
    out.write(json.dumps(sys.argv[1:])+'\\n')
if '--no-lazy-fetch' in sys.argv:
    sys.stderr.write('unknown option: --no-lazy-fetch\\n')
    sys.exit(129)
os.execv({shutil.which('git')!r}, ['git', *sys.argv[1:]])
''')
    executable.chmod(0o755)
    before = snapshot(repository)
    error = inspect(repository, ok=False, env={**os.environ, 'PATH': str(shim)+os.pathsep+os.environ['PATH']})
    assert 'unknown option: --no-lazy-fetch' in error['error']
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(calls) == 1 and '--no-lazy-fetch' in calls[0]
    assert snapshot(repository) == before
