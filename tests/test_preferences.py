"""Deterministic preference selection and cross-process admission invariants."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import preferences as prefs
from shared.repository_scope import repository


def instruction(*, owner=None, include=None, exclude=None, condition=None, modes=None,
                prompt='Use pytest fixtures for shared test setup.'):
    result = {'owner': owner or {'type': 'user'}, 'prompt': prompt,
              'scope': {'include': include if include is not None else [{'type': 'directory', 'path': 'tests'}],
                        'exclude': exclude or [], 'condition': condition}}
    if modes is not None:
        result['applies_to'] = modes
    return result


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True, timeout=15)
    subprocess.run(['git', '-C', str(root), 'remote', 'add', 'origin', 'git@github.com:owner/repo.git'], check=True, timeout=15)
    return root


def save(checkout, *records, remove=()):
    proposal = prefs.prepare(checkout, {'upsert': list(records), 'remove': list(remove)})
    return proposal, prefs.commit(checkout, proposal)


def cli(checkout, *args, ok=True):
    if args[0] in {'commit', 'remove'} and ok:
        from config_request_admission import admitted
        proposal = (json.loads(Path(args[args.index('--file')+1]).read_text()) if args[0] == 'commit' else
                    {'repository': repository(checkout), 'revision': int(args[args.index('--revision')+1]),
                     'changes': {'remove': [args[index+1] for index, item in enumerate(args) if item == '--id']}})
        request_id = admitted({'domain':'preferences', 'action':'commit', 'proposal':proposal}, checkout=checkout)
        args = (*args, '--request-id', request_id)
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/preferences.py'),
                             '--checkout', str(checkout), *map(str, args)], text=True, capture_output=True, timeout=15)
    assert result.returncode == (0 if ok else 2), result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize('kind,pattern,path,expected', [
    ('file', 'tests/a.py', 'tests/a.py', True),
    ('file', 'tests/a.py', 'tests/a.py/more', False),
    ('file', 'tests/a.py', 'Tests/a.py', False),
    ('directory', 'tests', 'tests', True),
    ('directory', 'tests', 'tests/new/nested.py', True),
    ('directory', 'tests', 'tests2/a.py', False),
    ('directory', '.', '.github/workflows/new.yml', True),
    ('glob', '*.py', 'new.py', True),
    ('glob', '*.py', 'src/new.py', False),
    ('glob', 'src/?.py', 'src/a.py', True),
    ('glob', 'src/?.py', 'src/ab.py', False),
    ('glob', 'tests/[ab]*.py', 'tests/a_test.py', True),
    ('glob', 'tests/[!ab]*.py', 'tests/c_test.py', True),
    ('glob', 'tests/[ab]*.py', 'tests/c_test.py', False),
    ('glob', 'tests/**/*.py', 'tests/new.py', True),
    ('glob', 'tests/**/*.py', 'tests/a/b/new.py', True),
    ('glob', '**/test.py', 'test.py', True),
    ('glob', '**/test.py', 'a/test.py', True),
    ('glob', 'src/**/tests/**/*.py', 'src/tests/test.py', True),
    ('glob', 'src/**', 'src/a/deep/new.py', True),
    ('glob', '**', '.hidden/file', True),
    ('glob', 'tests/*.py', 'other/tests/new.py', False),
])
def test_selectors_are_root_anchored_and_case_sensitive(kind, pattern, path, expected):
    assert prefs.matches(prefs.selector({'type': kind, 'path': pattern}), path) is expected


@pytest.mark.parametrize('path', ['/tmp/test.py', '../test.py', 'src/../test.py', 'src//test.py',
                                  './test.py', 'src/./test.py', 'src/', '', 'C:/test.py',
                                  'src\\test.py', 'src/\x00test.py'])
def test_invalid_paths_are_rejected(path):
    with pytest.raises(ValueError):
        prefs.selector({'type': 'file', 'path': path})


def test_double_star_requires_a_complete_component():
    for pattern in ('src/**test.py', 'src/a**/test.py', '***'):
        with pytest.raises(ValueError, match='complete component'):
            prefs.selector({'type': 'glob', 'path': pattern})


@pytest.mark.parametrize('pattern,expected', [('**/file.py', True), ('**/missing.py', False),
                                            ('**/a/**/file.py', True)])
def test_deep_glob_paths_resolve_without_recursion(checkout, tmp_path, pattern, expected):
    path = 'a/' * 1100 + 'file.py'
    save(checkout, instruction(include=[{'type': 'glob', 'path': pattern}]))
    paths = tmp_path / 'deep-paths.json'
    paths.write_text(json.dumps([path]))
    selection = json.loads(cli(checkout, 'resolve', '--mode', 'review', '--paths', paths))
    assert bool(selection['records']) is expected
    if expected:
        assert selection['records'][0]['paths'] == [path]


def test_selection_personal_repository_modes_exclusions_and_rename(checkout, tmp_path):
    owner = {'type': 'repo', 'repository': repository(checkout)}
    save(checkout, instruction(exclude=[{'type': 'directory', 'path': 'tests/generated'}]),
         instruction(owner=owner, include=[{'type': 'file', 'path': 'src/added.py'}], modes=['implementation'], prompt='Use explicit types.'),
         instruction(owner=owner, include=[{'type': 'file', 'path': 'old/name.py'}], modes=['review'], prompt='Check old API compatibility.'),
         instruction(include=[{'type': 'file', 'path': 'docs/index.md'}, {'type': 'file', 'path': 'src/added.py'}], prompt='Use plain language.'))
    other = tmp_path / 'other'
    other.mkdir()
    save(other, instruction(owner={'type': 'repo', 'repository': repository(other)}, prompt='Other repository instruction.'))
    paths = ['tests/new/deep.py', 'tests/generated/file.py', 'src/added.py', 'docs/index.md',
             {'old_path': 'old/name.py', 'new_path': 'new/name.py'}]
    implementation = prefs.resolve(checkout, 'implementation', paths)
    by_prompt = {v['record']['prompt']: v for v in implementation['records']}
    assert len(by_prompt) == 3
    assert by_prompt['Use pytest fixtures for shared test setup.']['paths'] == ['tests/new/deep.py']
    assert by_prompt['Use explicit types.']['paths'] == ['src/added.py']
    assert by_prompt['Use plain language.']['paths'] == ['docs/index.md', 'src/added.py']
    assert not (checkout / 'src/added.py').exists()
    review = prefs.resolve(checkout, 'review', paths)
    assert {v['record']['prompt'] for v in review['records']} == {
        'Use pytest fixtures for shared test setup.', 'Check old API compatibility.', 'Use plain language.'}
    assert next(v for v in review['records'] if v['record']['prompt'].startswith('Check'))['paths'] == ['old/name.py']
    assert all(v['record']['prompt'] != 'Use explicit types.' for v in prefs.resolve(other, 'implementation', paths)['records'])


def test_condition_candidates_are_not_evaluated_or_ranked(checkout):
    save(checkout, instruction(include=[], condition='When implementing HTTP retries.'),
         instruction(condition='When sharing mutable setup.', exclude=[{'type': 'directory', 'path': 'tests/generated'}], prompt='Copy mutable fixture data.'))
    selection = prefs.resolve(checkout, 'review', ['tests/test_new.py', 'tests/generated/auto.py', 'src/api.py'])
    assert all(v['requires_condition'] and 'applicable' not in v for v in selection['records'])
    prompt_only = next(v for v in selection['records'] if not v['record']['scope']['include'])
    assert prompt_only['paths'] == ['src/api.py', 'tests/generated/auto.py', 'tests/test_new.py']
    assert next(v for v in selection['records'] if v['record']['scope']['include'])['paths'] == ['tests/test_new.py']
    assert len(prefs.resolve(checkout, 'implementation', [])['records']) == 1
    assert prefs.resolve(checkout, 'implementation', ['docs/readme.md'])['records'] == [{**prompt_only, 'paths': ['docs/readme.md']}]


def test_prompt_only_exclusion_still_removes_paths(checkout):
    save(checkout, instruction(include=[], condition='When adding retries.', exclude=[{'type': 'directory', 'path': 'generated'}]))
    assert not prefs.resolve(checkout, 'implementation', ['generated/a.py'])['records']


def test_prepare_update_duplicate_remove_and_markdown(checkout, tmp_path):
    original = instruction()
    proposal = prefs.prepare(checkout, {'upsert': [original]})
    assert proposal['revision'] == 0 and proposal['context']['records'] == []
    assert not prefs.catalog_path(checkout).exists()
    first = prefs.commit(checkout, proposal)
    persisted = first['records'][0]
    preference_id = persisted['id']
    assert first['revision'] == 1 and persisted['applies_to'] == ['implementation', 'review']
    for target in (prefs.catalog_path(checkout), prefs.catalog_path(checkout).with_suffix('.lock')):
        assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(cli(checkout, 'list'))['records'] == [persisted]
    duplicate = prefs.prepare(checkout, {'upsert': [original]})
    assert duplicate['changes'] == {'upsert': [], 'remove': []}
    assert duplicate['reused'][0]['id'] == preference_id
    assert prefs.commit(checkout, duplicate)['revision'] == 1
    updated = deepcopy(persisted)
    updated['prompt'] = 'Use fixtures only for shared setup.'
    updated['scope']['condition'] = 'When test setup is shared.'
    save(checkout, updated)
    source = tmp_path / 'persisted.json'
    source.write_text(cli(checkout, 'show', '--id', preference_id))
    markdown = cli(checkout, 'render', '--file', source)
    for text in (preference_id, 'Owner: user', 'implementation, review', 'directory: `tests`',
                 'Exclude: none', updated['scope']['condition'], updated['prompt']):
        assert text in markdown
    removed = json.loads(cli(checkout, 'remove', '--id', preference_id, '--revision', '2'))
    assert removed['revision'] == 3 and removed['records'] == []
    assert prefs.render(removed) == 'No saved preferences.\n'


def test_update_to_duplicate_reuses_existing_id(checkout):
    _, saved = save(checkout, instruction(), instruction(prompt='Use independent data.'))
    a, b = saved['records']
    proposal = prefs.prepare(checkout, {'upsert': [{**a, 'prompt': b['prompt']}]})
    assert proposal['reused'] == [{'proposed_id': a['id'], 'id': b['id']}]
    assert proposal['changes'] == {'upsert': [], 'remove': [a['id']]}
    assert prefs.commit(checkout, proposal)['records'] == [b]


def test_normalization_reuses_reordered_modes_and_selectors(checkout):
    value = instruction(include=[{'type': 'file', 'path': 'b'}, {'type': 'file', 'path': 'a'}])
    save(checkout, value)
    value['scope']['include'].reverse()
    value['scope']['include'].append({'type': 'file', 'path': 'a'})
    value['applies_to'] = ['review', 'implementation']
    proposal = prefs.prepare(checkout, {'upsert': [value]})
    assert proposal['reused'] and not proposal['changes']['upsert']


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(prompt='   '), lambda r: r.update(applies_to=[]),
    lambda r: r.update(applies_to=['review', 'review']), lambda r: r.update(applies_to=['write']),
    lambda r: r.update(applies_to='review'), lambda r: r.update(priority=1),
    lambda r: r.update(owner={'type': 'repo'}), lambda r: r.update(owner={'type': 'user', 'repository': 'extra'}),
    lambda r: r.update(scope={'include': []}), lambda r: r['scope'].update(condition=''),
    lambda r: r['scope'].update(include='tests'),
    lambda r: r['scope'].update(include=[{'type': 'regex', 'path': 'tests'}]),
    lambda r: r['scope'].update(include=[{'type': 'file', 'path': '/tests'}]),
    lambda r: r['scope'].update(exclude=None), lambda r: r['scope'].update(other=True),
])
def test_malformed_records_do_not_modify_catalog(checkout, mutate):
    save(checkout, instruction())
    before = prefs.catalog_path(checkout).read_bytes()
    value = instruction()
    mutate(value)
    with pytest.raises(ValueError):
        prefs.prepare(checkout, {'upsert': [value]})
    assert prefs.catalog_path(checkout).read_bytes() == before


def test_commit_revalidates_structure_and_expected_revision(checkout):
    proposal = prefs.prepare(checkout, {'upsert': [instruction()]})
    proposal['changes']['upsert'][0]['scope']['include'][0]['path'] = '../escape'
    with pytest.raises(ValueError):
        prefs.commit(checkout, proposal)
    assert prefs.list_records(checkout)['records'] == []
    with pytest.raises(ValueError):
        prefs.commit(checkout, {'repository': repository(checkout), 'revision': False, 'changes': {}})
    with pytest.raises(ValueError):
        prefs.remove(checkout, ['unknown'], 0)
    malformed = prefs.prepare(checkout, {'upsert': [instruction()]})
    malformed['reused'] = None
    with pytest.raises(ValueError, match='metadata'):
        prefs.commit(checkout, malformed)
    assert prefs.list_records(checkout)['records'] == []


def test_atomic_replacement_across_owners(checkout):
    owner = {'type': 'repo', 'repository': repository(checkout)}
    _, saved = save(checkout, instruction(), instruction(owner=owner, prompt='Use independent data.'))
    proposed = deepcopy(saved['records'])
    for item in proposed:
        item['prompt'] += ' Preserve conditions.'
    prepared = prefs.prepare(checkout, {'upsert': proposed})
    assert prepared['context']['records'] == saved['records']
    committed = prefs.commit(checkout, prepared)
    assert committed['revision'] == 2 and len(committed['records']) == 2
    assert all(v['prompt'].endswith('Preserve conditions.') for v in committed['records'])


def test_concurrent_admissions_require_fresh_context(checkout, tmp_path):
    paths = []
    for index, prompt in enumerate(('Use fixtures.', 'Use independent data.')):
        path = tmp_path / f'proposal-{index}.json'
        path.write_text(json.dumps(prefs.prepare(checkout, {'upsert': [instruction(prompt=prompt)]})))
        paths.append(path)
    from config_request_admission import admitted
    request_ids = {path: admitted({'domain':'preferences', 'action':'commit',
                                  'proposal':json.loads(path.read_text())}, checkout=checkout) for path in paths}
    def invoke(path):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/preferences.py'), '--checkout', str(checkout),
                               'commit', '--file', str(path), '--request-id', request_ids[path]], text=True, capture_output=True, timeout=15)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(invoke, paths))
    assert sorted(v.returncode for v in outcomes) == [0, 2]
    assert 'changed' in json.loads(next(v for v in outcomes if v.returncode == 2).stdout)['reason']
    fresh = prefs.prepare(checkout, {'upsert': [instruction(prompt='Use fresh context.')]})
    assert len(fresh['context']['records']) == 1 and fresh['revision'] == 1
    assert len(prefs.commit(checkout, fresh)['records']) == 2


def test_failed_atomic_replace_preserves_previous_catalog(checkout, monkeypatch):
    save(checkout, instruction())
    target = prefs.catalog_path(checkout)
    before = target.read_bytes()
    proposal = prefs.prepare(checkout, {'upsert': [instruction(prompt='Use separate data.')]})
    def fail(source, destination):
        assert Path(destination) == target and target.read_bytes() == before
        assert len(json.loads(Path(source).read_text())['records']) == 2
        raise OSError('injected replacement failure')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='injected'):
        prefs.commit(checkout, proposal)
    assert target.read_bytes() == before and not list(target.parent.glob('.state-*'))


def test_storage_protection_and_corrupt_catalog(checkout, monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(checkout / 'private'))
    with pytest.raises(ValueError, match='outside'):
        prefs.prepare(checkout, {'upsert': [instruction()]})
    monkeypatch.setenv('XDG_CONFIG_HOME', 'relative')
    with pytest.raises(ValueError, match='absolute'):
        prefs.list_records(checkout)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'private'))
    path = prefs.catalog_path(checkout)
    path.parent.mkdir(parents=True)
    path.write_text('{"version":99,"revision":0,"records":[]}')
    with pytest.raises(ValueError, match='unsupported'):
        prefs.list_records(checkout)
    path.write_text('broken')
    with pytest.raises(ValueError):
        prefs.prepare(checkout, {'upsert': [instruction()]})
    assert path.read_text() == 'broken'


def test_cannot_modify_another_repository(checkout, tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    _, saved = save(other, instruction(owner={'type': 'repo', 'repository': repository(other)}))
    existing = saved['records'][0]
    for change in ({'remove': [existing['id']]}, {'upsert': [{**existing, 'owner': {'type': 'user'}}]}):
        with pytest.raises(ValueError, match='another repository'):
            prefs.prepare(checkout, change)
    with pytest.raises(ValueError, match='current repository'):
        prefs.prepare(checkout, {'upsert': [instruction(owner=existing['owner'])]})
    with pytest.raises(ValueError, match='Unknown preference'):
        prefs.show(checkout, existing['id'])


def test_cli_prepare_commit_resolve_and_render(checkout, tmp_path):
    def source(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return path
    prepared = json.loads(cli(checkout, 'prepare', '--file', source('change.json', {'upsert': [instruction()]})))
    assert prepared['changes']['upsert'][0]['id'].startswith('pref-')
    cli(checkout, 'commit', '--file', source('prepared.json', prepared))
    selection = json.loads(cli(checkout, 'resolve', '--mode', 'review', '--paths', source('paths.json', ['tests/new.py'])))
    assert selection['records'][0]['paths'] == ['tests/new.py']
    assert 'Instruction' in cli(checkout, 'render', '--file', source('result.json', selection))
    failure = json.loads(cli(checkout, 'commit', '--file', source('stale.json', prepared), ok=False))
    assert failure['status'] == 'blocked' and '--request-id' in failure['reason']
    malformed = json.loads(cli(checkout, 'render', '--file', source('invalid.json', {'records': [None]}), ok=False))
    assert malformed['status'] == 'blocked' and 'Preference requires' in malformed['reason']
