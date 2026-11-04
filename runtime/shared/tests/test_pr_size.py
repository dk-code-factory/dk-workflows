import itertools
import json
from pathlib import Path
import subprocess

import pytest

from shared import pr_size

CHAIN = ['remote:github.com/fork/repo', 'remote:github.com/base/repo']


def git(cwd, *args):
    return subprocess.check_output(['git', '-C', str(cwd), *args], text=True, stderr=subprocess.PIPE).strip()


def test_defaults_independent_inheritance_and_reset():
    assert pr_size.load(CHAIN)['pr_size'] == pr_size.DEFAULTS
    assert all(row['source'] == 'default' for row in pr_size.load(CHAIN)['settings'].values())
    pr_size.configure(CHAIN, 'set', scope='user', settings={'added_lines_threshold': 800, 'split_related': 'manual'})
    pr_size.configure(CHAIN[1:], 'set', settings={'evacuate_unrelated': 'disabled'})
    pr_size.configure(CHAIN, 'set', settings={'added_lines_threshold': 1200})
    result = pr_size.load(CHAIN)
    assert result['pr_size'] == {'added_lines_threshold': 1200, 'evacuate_unrelated': 'disabled', 'split_related': 'manual'}
    assert {key: row['source'] for key, row in result['settings'].items()} == {
        'added_lines_threshold': 'repo', 'evacuate_unrelated': 'upstream', 'split_related': 'user'}
    assert pr_size.configure(CHAIN, 'reset', keys=['added_lines_threshold'])['pr_size']['added_lines_threshold'] == 800
    pr_size.configure(CHAIN, 'reset', scope='user')
    assert pr_size.load(CHAIN)['pr_size']['split_related'] == 'automatic'
    pr_size.configure(CHAIN[1:], 'reset')
    assert pr_size.load(CHAIN)['pr_size'] == pr_size.DEFAULTS


@pytest.mark.parametrize('evacuation,splitting', itertools.product(pr_size.MODES, repeat=2))
def test_modes_resolve_independently(evacuation, splitting):
    result = pr_size.configure(CHAIN, 'set', settings={'evacuate_unrelated': evacuation, 'split_related': splitting})
    assert result['pr_size']['evacuate_unrelated'] == evacuation
    assert result['pr_size']['split_related'] == splitting
    assert result['pr_size']['added_lines_threshold'] == 1000


@pytest.mark.parametrize('settings', [{'added_lines_threshold': value} for value in (0, -1, True, 1.5, '1000', None)] +
                         [{'split_related': value} for value in ('yes', None, 1, [])] + [{'unknown': 'automatic'}])
def test_invalid_config_is_not_saved(settings):
    with pytest.raises(ValueError):
        pr_size.configure(CHAIN, 'set', settings=settings)
    assert not pr_size.path(CHAIN[0]).exists()


def test_malformed_config_does_not_fall_back():
    target = pr_size.path(CHAIN[0])
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({'schema_version': 1, 'identity': CHAIN[0], 'pr_size': {'split_related': 'bad'}}))
    with pytest.raises(ValueError):
        pr_size.load(CHAIN)
    assert pr_size.configure(CHAIN, 'reset')['pr_size'] == pr_size.DEFAULTS


def init(tmp_path):
    git(tmp_path, 'init', '-qb', 'main')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.test')
    (tmp_path / 'original').write_text('existing\n' * 1200)
    (tmp_path / 'binary').write_bytes(b'\x00old\xff')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'base')
    return git(tmp_path, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('lines', [999, 1000, 1001])
def test_threshold_boundaries_and_final_diff_not_commit_churn(tmp_path, lines):
    base = init(tmp_path)
    (tmp_path / 'generated').write_text('generated\n' * (lines + 100))
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'generated text')
    (tmp_path / 'generated').write_text('generated\n' * lines)
    (tmp_path / 'original').write_text('')  # Deletions never offset additions.
    (tmp_path / 'binary').write_bytes(b'\x00new\xfe' * 20)
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'reduce final diff')
    assert pr_size.additions(tmp_path, base, 'HEAD') == lines
    assert (pr_size.additions(tmp_path, base, 'HEAD') >= 1000) is (lines >= 1000)


def test_rename_detection_binary_and_unusual_paths(tmp_path):
    base = init(tmp_path)
    git(tmp_path, 'mv', 'original', 'renamed\twith\nnewlines')
    (tmp_path / 'renamed\twith\nnewlines').write_text('existing\n' * 1200 + 'extra\n')
    (tmp_path / 'copied prerequisite').write_text('copy\n' * 50)
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'rename and copied prerequisite')
    assert pr_size.additions(tmp_path, base, 'HEAD') == 51


def test_additions_include_the_whole_repository_from_a_subdirectory(tmp_path):
    base = init(tmp_path)
    nested = tmp_path/'nested'
    nested.mkdir()
    (nested/'inside').write_text('inside\n')
    (tmp_path/'outside').write_text('outside\n' * 3)
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'changes across directories')
    git(tmp_path, 'config', 'diff.relative', 'true')
    assert pr_size.additions(nested, base, 'HEAD') == 4
