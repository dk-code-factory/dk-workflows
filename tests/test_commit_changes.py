"""Offline local evidence for native commit-scope decisions; no model calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT/'scripts/commit_changes.py'


def git(repo, *args, input=None):
    result = subprocess.run(['git', '-C', str(repo), *args], input=input,
                            capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Fixture Author')
    git(tmp_path, 'config', 'user.email', 'fixture@example.test')
    git(tmp_path, 'config', 'commit.gpgsign', 'false')
    (tmp_path/'routing.py').write_text(
        '# Route replicas\nroute_version = 1\n' + '# unchanged\n'*20 + 'other_feature = 0\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'Initial routing')
    return tmp_path


def inspect(repo, *, ok=True, env=None):
    result = subprocess.run([sys.executable, str(CLI), '--checkout', str(repo)],
                            capture_output=True, text=True, env=env)
    assert result.returncode == (0 if ok else 2), result.stdout+result.stderr
    return json.loads(result.stdout if ok else result.stderr)


def snapshot(repo):
    return {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in repo.rglob('*') if path.is_file()}


@pytest.mark.parametrize('object_format', ['sha1', 'sha256'])
@pytest.mark.parametrize('nested', [False, True])
def test_unborn_branch_captures_initial_candidates_without_mutation(tmp_path, object_format, nested):
    p = tmp_path
    git(p, 'init', '-q', '-b', 'main', '--object-format='+object_format)
    git(p, 'config', 'user.name', 'Fixture Author')
    git(p, 'config', 'user.email', 'fixture@example.test')
    git(p, 'config', 'commit.gpgsign', 'false')
    (p/'nested').mkdir()
    checkout = p/'nested' if nested else p
    before = snapshot(p)
    empty = inspect(checkout)
    assert empty['head'] is None
    assert empty['staged_patch'] == empty['unstaged_patch'] == ''
    assert empty['status'] == empty['untracked_paths'] == []
    assert snapshot(p) == before

    (p/'routing.py').write_text('route_version = 1\n')
    git(p, 'add', 'routing.py')
    (p/'routing.py').write_text('route_version = 2\n')
    (p/'draft').write_text('excluded draft\n')
    before = snapshot(p)
    initial = inspect(checkout)
    assert initial['head'] is None
    assert '+route_version = 1' in initial['staged_patch']
    assert '-route_version = 1\n+route_version = 2' in initial['unstaged_patch']
    assert set(initial['status']) == {'AM routing.py', '?? draft'}
    assert initial['untracked_paths'] == ['draft']
    assert inspect(checkout) == initial
    assert snapshot(p) == before

    git(p, 'commit', '-qm', 'Initial routing')
    committed = inspect(checkout)
    assert committed['head'] == git(p, 'rev-parse', 'HEAD').decode().strip()
    assert committed['staged_patch'] == ''
    assert committed['unstaged_patch'] == initial['unstaged_patch']
    assert committed['untracked_paths'] == ['draft']


@pytest.mark.parametrize('relative', [False, True])
def test_nested_capture_includes_repository_wide_patches_and_untracked_paths(repository, relative):
    p = repository
    nested = p/'nested'
    nested.mkdir()
    (nested/'code').write_text('initial\n')
    git(p, 'add', 'nested/code')
    git(p, 'commit', '-qm', 'Add nested code')
    git(p, 'config', 'diff.relative', str(relative).lower())
    source = p/'routing.py'
    source.write_text(source.read_text().replace('route_version = 1', 'route_version = 2'))
    (nested/'code').write_text('staged nested\n')
    git(p, 'add', 'routing.py', 'nested/code')
    source.write_text(source.read_text().replace('# Route replicas', '# Refresh routing'))
    (nested/'code').write_text('unstaged nested\n')
    outside_draft = 'outside \tline\n雪'
    (p/outside_draft).write_text('excluded outside draft\n')
    (nested/'draft').write_text('excluded nested draft\n')
    before = snapshot(p)
    value = inspect(nested)
    assert value == inspect(p)
    assert value['checkout'] == str(p)
    assert '+route_version = 2' in value['staged_patch']
    assert '+staged nested' in value['staged_patch']
    assert '+# Refresh routing' in value['unstaged_patch']
    assert '+unstaged nested' in value['unstaged_patch']
    assert 'diff --git a/nested/code b/nested/code' in value['staged_patch']
    assert set(value['untracked_paths']) == {outside_draft, 'nested/draft'}
    assert set(value['status']) == {'MM routing.py', 'MM nested/code',
                                    '?? '+outside_draft, '?? nested/draft'}
    assert inspect(nested) == value
    assert snapshot(p) == before


def test_head_pointing_to_noncommit_is_not_accepted_as_unborn(repository):
    p = repository
    blob = git(p, 'hash-object', '-w', '--stdin', input=b'not a commit\n').decode().strip()
    git(p, 'update-ref', 'refs/tags/noncommit', blob)
    git(p, 'symbolic-ref', 'HEAD', 'refs/tags/noncommit')
    before = snapshot(p)
    assert 'HEAD must reference a commit or an unborn branch' in inspect(p, ok=False)['error']
    assert snapshot(p) == before


def test_existing_comment_functional_and_mixed_hunks_remain_visible_and_untouched(repository):
    p = repository
    source = p/'routing.py'
    original = source.read_text()
    # A relevant existing comment and an unrelated already-staged hunk share a file.
    source.write_text(original.replace('# Route replicas', '# Use set_tablet to refresh routing')
                      .replace('other_feature = 0', 'other_feature = 9'))
    git(p, 'add', 'routing.py')
    # A relevant functional edit is still unstaged; neither age nor index decides scope.
    source.write_text(source.read_text().replace('route_version = 1', 'route_version = 2'))
    (p/'excluded.txt').write_text('explicitly excluded local draft\n')
    git(p, 'add', 'excluded.txt')
    (p/'excluded.txt').write_text('explicitly excluded local draft\nunstaged followup\n')
    before = snapshot(p)
    value = inspect(p)
    assert snapshot(p) == before
    assert value['head'] == git(p, 'rev-parse', 'HEAD').decode().strip()
    assert '+# Use set_tablet to refresh routing' in value['staged_patch']
    assert '+other_feature = 9' in value['staged_patch']
    assert '+explicitly excluded local draft' in value['staged_patch']
    assert '+route_version = 2' in value['unstaged_patch']
    assert '+unstaged followup' in value['unstaged_patch']
    assert '+route_version = 2' not in value['staged_patch']
    assert '+other_feature = 9' not in value['unstaged_patch']
    assert 'MM routing.py' in value['status']
    assert 'AM excluded.txt' in value['status']
    # A new process sees identical evidence without a second store or index refresh.
    assert inspect(p) == value
    assert snapshot(p) == before


def test_unstaged_comment_and_staged_functional_change_are_both_candidates(repository):
    p = repository
    source = p/'routing.py'
    source.write_text(source.read_text().replace('route_version = 1', 'route_version = 2'))
    git(p, 'add', 'routing.py')
    source.write_text(source.read_text().replace('# Route replicas', '# Refresh installed replicas'))
    value = inspect(p)
    assert '+route_version = 2' in value['staged_patch']
    assert '+# Refresh installed replicas' in value['unstaged_patch']


def test_fresh_capture_reflects_changed_index_worktree_and_head(repository):
    p = repository
    clean = inspect(p)
    assert clean['staged_patch'] == clean['unstaged_patch'] == ''
    source = p/'routing.py'
    source.write_text(source.read_text().replace('route_version = 1', 'route_version = 2'))
    local = inspect(p)
    assert local['head'] == clean['head'] and local['unstaged_patch']
    git(p, 'add', 'routing.py')
    staged = inspect(p)
    assert staged['staged_patch'] and staged['unstaged_patch'] == ''
    git(p, 'commit', '-qm', 'Update routing')
    committed = inspect(p)
    assert committed['head'] != staged['head']
    assert committed['staged_patch'] == committed['unstaged_patch'] == ''


def test_untracked_ignored_unusual_paths_and_intent_to_add(repository):
    p = repository
    unusual = 'draft \tline\n雪'
    (p/unusual).write_text('review separately\n')
    (p/'.gitignore').write_text('ignored\n')
    (p/'ignored').write_text('leave private content alone\n')
    (p/'intent').write_text('new candidate\n')
    git(p, 'add', '-N', 'intent')
    before = snapshot(p)
    value = inspect(p)
    assert set(value['untracked_paths']) == {unusual, '.gitignore'}
    assert '+new candidate' in value['unstaged_patch']
    assert value['staged_patch'] == ''
    assert snapshot(p) == before


def test_binary_and_modes_are_not_silently_omitted(repository):
    p = repository
    (p/'blob').write_bytes(bytes(range(256)))
    git(p, 'add', 'blob')
    (p/'routing.py').chmod(0o755)
    value = inspect(p)
    assert 'GIT binary patch' in value['staged_patch']
    assert 'new mode 100755' in value['unstaged_patch']


def test_external_diff_and_textconv_cannot_execute_during_capture(repository):
    p = repository
    marker = p/'executed'
    hook = p/'transform'
    hook.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n')
    hook.chmod(0o755)
    git(p, 'config', 'diff.external', str(hook))
    git(p, 'config', 'diff.fixture.textconv', str(hook))
    git(p, 'config', 'color.ui', 'always')
    (p/'.gitattributes').write_text('routing.py diff=fixture\n')
    (p/'routing.py').write_text('changed\n')
    value = inspect(p, env={**os.environ, 'GIT_EXTERNAL_DIFF': str(hook)})
    assert '+changed' in value['unstaged_patch']
    assert '\x1b' not in value['unstaged_patch']
    assert not marker.exists()


@pytest.mark.parametrize('process', [False, True])
@pytest.mark.parametrize('unborn', [False, True])
def test_content_filters_are_bypassed_without_losing_candidates(repository, process, unborn):
    p = repository
    if unborn:
        git(p, 'checkout', '--orphan', 'initial')
    marker = p/'filter-executed'
    hook = p/'filter-hook'
    hook.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\n'
                    'cat >/dev/null\nprintf "normalized route_version = 1\\n"\n')
    hook.chmod(0o755)
    git(p, 'config', 'filter.fixture.clean', str(hook))
    git(p, 'config', 'filter.fixture.required', 'true')
    (p/'.gitattributes').write_text('routing.py filter=fixture\n')
    git(p, 'add', '.gitattributes', 'routing.py')
    if not unborn:
        git(p, 'commit', '-qm', 'Store normalized content')
    if process:
        # A process filter must never even start; no protocol handshake is needed.
        process_hook = p/'process-hook'
        process_hook.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n')
        process_hook.chmod(0o755)
        git(p, 'config', 'filter.fixture.process', str(process_hook))
    marker.unlink()
    (p/'routing.py').write_text('candidate route_version = 2\n')
    before = snapshot(p)
    value = inspect(p)
    assert not marker.exists()
    assert snapshot(p) == before
    assert 'fixture' in value['disabled_filter_drivers']
    assert '-normalized route_version = 1' in value['unstaged_patch']
    assert '+candidate route_version = 2' in value['unstaged_patch']
    if unborn:
        assert '+normalized route_version = 1' in value['staged_patch']
    else:
        assert value['staged_patch'] == ''
    assert inspect(p) == value
    assert snapshot(p) == before


def test_submodule_local_filter_cannot_execute_during_parent_capture(repository):
    p = repository
    sub = p/'sub'
    sub.mkdir()
    git(sub, 'init', '-q', '-b', 'main')
    git(sub, 'config', 'user.name', 'Fixture Author')
    git(sub, 'config', 'user.email', 'fixture@example.test')
    git(sub, 'config', 'commit.gpgsign', 'false')
    marker = p/'sub-filter-executed'
    hook = p/'sub-filter-hook'
    hook.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\ncat\n')
    hook.chmod(0o755)
    git(sub, 'config', 'filter.subfixture.clean', str(hook))
    git(sub, 'config', 'filter.subfixture.required', 'true')
    (sub/'.gitattributes').write_text('code filter=subfixture\n')
    (sub/'code').write_text('original\n')
    git(sub, 'add', '.')
    git(sub, 'commit', '-qm', 'Initial submodule')
    git(p, 'add', 'sub')
    git(p, 'commit', '-qm', 'Record submodule')
    marker.unlink()
    (sub/'code').write_text('modified\n')
    before = snapshot(p)
    value = inspect(p)
    assert not marker.exists()
    assert snapshot(p) == before
    assert 'subfixture' in value['disabled_filter_drivers']
    assert ' M sub' in value['status']
    assert '-dirty' in value['unstaged_patch']


def test_fsmonitor_hook_cannot_execute_during_capture(repository):
    p = repository
    marker = p/'monitor-executed'
    hook = p/'monitor-hook'
    hook.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n')
    hook.chmod(0o755)
    git(p, 'config', 'core.fsmonitor', str(hook))
    (p/'routing.py').write_text('candidate\n')
    value = inspect(p)
    assert '+candidate' in value['unstaged_patch']
    assert not marker.exists()


@pytest.mark.parametrize('variable', ['GIT_DIR', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE'])
def test_repository_environment_cannot_redirect_capture(repository, tmp_path, variable):
    foreign = tmp_path/'foreign'
    git(tmp_path, 'clone', '-q', str(repository), str(foreign))
    (repository/'routing.py').write_text('local candidate\n')
    expected = inspect(repository)
    redirect = foreign/'.git/index' if variable == 'GIT_INDEX_FILE' else foreign/'.git'
    assert inspect(repository, env={**os.environ, variable: str(redirect)}) == expected


@pytest.mark.parametrize('nested', [False, True])
def test_unmerged_index_blocks_without_mutation(repository, nested):
    p = repository
    git(p, 'switch', '-qc', 'other')
    (p/'routing.py').write_text('other\n')
    git(p, 'commit', '-am', 'Other change', '-q')
    git(p, 'switch', '-q', 'main')
    (p/'routing.py').write_text('main\n')
    git(p, 'commit', '-am', 'Main change', '-q')
    merged = subprocess.run(['git', '-C', str(p), 'merge', 'other'], capture_output=True)
    assert merged.returncode == 1
    (p/'nested').mkdir()
    before = snapshot(p)
    assert 'Resolve index conflicts' in inspect(p/'nested' if nested else p, ok=False)['error']
    assert snapshot(p) == before


def test_direct_and_pr_finalization_routes_share_scope_and_permission_rules():
    guide = 'docs/pr/commit-preparation.md'
    # Routing is tested separately from Git capture: semantic decisions stay native.
    for host in ('codex', 'claude'):
        for skill in ('dk-commit-message', 'dk-pr-fix'):
            assert '../../../'+guide in (ROOT/host/'skills'/skill/'SKILL.md').read_text()
    recipe = (ROOT/'runtime/pr-iter.md').read_text()
    assert recipe.count('../'+guide) == 2
    cleanup = (ROOT/'docs/pr/commit-cleanup.md').read_text()
    assert '(commit-preparation.md)' in cleanup
    assert 'Require a clean worktree' in cleanup
    text = (ROOT/guide).read_text()
    for obligation in ('staged and unstaged hunk', 'origin or age alone must never exclude',
                       'existing authorization to commit', 'explicitly excluded edits',
                       'same file and existing staged changes',
                       'Commit authorization does not authorize pushing or rewriting published',
                       'restore that state after the commit', 'exact staged patch',
                       'Message-only drafting', 'appropriate existing commit only when'):
        assert obligation in text, obligation


def test_concurrent_edit_rejects_inconsistent_capture(repository, monkeypatch):
    module_spec = importlib.util.spec_from_file_location('commit_changes', CLI)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    real_run = module.subprocess.run
    changed = False
    index_before = (repository/'.git/index').read_bytes()

    def concurrent_run(command, **kwargs):
        nonlocal changed
        result = real_run(command, **kwargs)
        if '--others' in command and not changed:
            changed = True
            (repository/'routing.py').write_text('concurrent edit\n')
        return result

    monkeypatch.setattr(module.subprocess, 'run', concurrent_run)
    with pytest.raises(ValueError, match='moved during capture'):
        module.inspect_changes(repository)
    assert (repository/'.git/index').read_bytes() == index_before
    assert (repository/'routing.py').read_text() == 'concurrent edit\n'
