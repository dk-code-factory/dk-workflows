import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/dk.py'
spec = importlib.util.spec_from_file_location('dk_cli', SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.mark.parametrize('pr,repo', [('https://github.com/O/R/pull/42', None),
                                   ('42', 'o/r'), ('42', 'github.com/o/r')])
def test_run_state_context_normalizes_url_and_number(pr, repo, tmp_path):
    args, cwd, env = cli.invocation(ROOT, 'run-state', ['show'], tmp_path, pr, repo, 'codex', 'parent')
    assert args[2:] == ['--pr', '42', '--repo', 'github.com/o/r', 'show']
    assert cwd == tmp_path
    assert env['DK_HOST'] == 'codex' and env['DK_UPDATE_PARENT'] == 'parent'


def test_enterprise_context_and_review_selection_stay_explicit(tmp_path):
    selected = 'https://ghe.example/O/R/pull/2#pullrequestreview-123'
    args, _, _ = cli.invocation(ROOT, 'replies', ['next'], tmp_path, selected, 'o/r', 'claude', None)
    assert args[args.index('--pr') + 1] == selected.lower()
    with pytest.raises(ValueError, match='review selections'):
        cli.invocation(ROOT, 'decisions', ['context'], tmp_path, selected, None, 'codex', None)


@pytest.mark.parametrize('pr,repo', [('42', None), ('0', 'o/r'),
    ('https://github.com/o/r/pull/1', 'other/r'),
    ('https://user:secret@github.com/o/r/pull/1', None),
    ('https://github.com/o/r/pull/1?query=x', None),
    ('https://github.com/o/r/pull/1#discussion_r1', None),
    (None, '/local/checkout'), (None, 'host/../r')])
def test_invalid_or_conflicting_context_fails_before_dispatch(pr, repo, tmp_path):
    with pytest.raises(ValueError):
        cli.invocation(ROOT, 'run-state', ['show'], tmp_path, pr, repo, 'codex', None)


def test_native_context_cannot_override_normalized_identity(tmp_path):
    with pytest.raises(ValueError, match='context flags before'):
        cli.invocation(ROOT, 'run-state', ['--repo', 'other/r', 'show'], tmp_path,
                       'https://github.com/o/r/pull/1', None, 'codex', None)


def test_registry_paths_exist_and_commands_available_outside_checkout(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), 'commands'], cwd=tmp_path,
                            text=True, capture_output=True, check=True)
    commands = json.loads(result.stdout)['commands']
    assert len(commands) == len(cli.ROUTES)
    for row in commands.values():
        assert (ROOT / row['script']).is_file()


def test_real_helper_dispatch_uses_context_and_preserves_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'private'))
    monkeypatch.delenv('DK_HOST', raising=False)
    result = subprocess.run([sys.executable, str(SCRIPT), '--full', '--host', 'codex', '--cwd', str(tmp_path),
        '--pr', 'https://github.com/o/r/pull/42', 'run-state', 'show'], cwd=tmp_path,
        text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['pr'] == 42
    failed = subprocess.run([sys.executable, str(SCRIPT), '--host', 'codex', '--cwd', str(tmp_path),
        'review-loop', 'check', '--kind', 'split', '--state', 'invalid'], text=True, capture_output=True)
    assert failed.returncode == 1
    assert 'invalid review state' in failed.stderr


def test_discovery_places_cwd_after_subcommand_without_a_shell(tmp_path):
    args, _, _ = cli.invocation(ROOT, 'discovery', ['lookup', '--kind', 'commit-format'],
                                tmp_path, None, None, 'codex', None)
    assert args[2:] == ['lookup', '--kind', 'commit-format', '--cwd', str(tmp_path)]


def test_dry_run_exposes_only_workflow_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('UNRELATED_SECRET', 'hidden')
    result = subprocess.run([sys.executable, str(SCRIPT), '--host', 'claude', '--parent', 'token',
        '--cwd', str(tmp_path), '--dry-run', 'review-loop', 'start', '--kind', 'split'],
        text=True, capture_output=True, check=True)
    output = json.loads(result.stdout)
    assert output['environment'] == {'DK_HOST': 'claude', 'DK_UPDATE_PARENT': 'token'}
    assert 'hidden' not in result.stdout



def test_discovery_lookup_receives_explicit_pr_after_subcommand(tmp_path):
    url = 'https://github.com/o/r/pull/42'
    args, _, _ = cli.invocation(ROOT, 'discovery', ['lookup', '--kind', 'pr-scope'],
                                tmp_path, url, None, 'codex', None)
    assert args[-4:] == ['--cwd', str(tmp_path), '--pr', url]
    with pytest.raises(ValueError, match='uses its ticket'):
        cli.invocation(ROOT, 'discovery', ['submit'], tmp_path, url, None, 'codex', None)


def test_artifact_helpers_do_not_silently_ignore_conflicting_context(tmp_path):
    with pytest.raises(ValueError, match='identity from its artifacts'):
        cli.invocation(ROOT, 'reviewers', ['--describe'], tmp_path,
                       'https://github.com/o/r/pull/1', None, 'codex', None)
    with pytest.raises(ValueError, match='requires a PR URL'):
        cli.invocation(ROOT, 'config', ['refresh'], tmp_path, None, 'other/r', 'codex', None)



def test_routed_publication_still_requires_completed_review_state(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), '--host', 'codex', '--cwd', str(tmp_path),
        '--pr', 'https://github.com/o/r/pull/42', 'edit-pr', '--review-state', 'invalid',
        '--title-file', 'title.txt', '--body-file', 'body.txt'], text=True, capture_output=True)
    assert result.returncode == 1
    assert 'invalid review state token' in result.stderr



@pytest.mark.parametrize('name', sorted(cli.ROUTES))
def test_every_route_accepts_native_help_from_an_unrelated_directory(name, tmp_path):
    _, _, pr_kind, repo_option = cli.ROUTES[name]
    context = ['--pr', 'https://github.com/o/r/pull/42'] if pr_kind or repo_option else []
    arguments = ['lookup', '--help'] if name == 'discovery' else ['--help']
    result = subprocess.run([sys.executable, str(SCRIPT), '--host', 'codex',
        '--cwd', str(tmp_path), *context, name, *arguments], cwd=tmp_path,
        text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert result.returncode == 0, result.stderr



def test_signaled_helper_returns_conventional_shell_exit_code(tmp_path, monkeypatch):
    helper = tmp_path / 'helper.py'
    helper.write_text('import os,signal\nos.kill(os.getpid(), signal.SIGTERM)\n')
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.setattr(cli, 'ROUTES', {'fixture': ('helper.py', None, None, False)})
    monkeypatch.setattr(sys, 'argv', ['dk.py', '--host', 'codex', '--cwd', str(tmp_path), 'fixture'])
    assert cli.main() == 143
