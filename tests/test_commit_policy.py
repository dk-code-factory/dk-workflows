"""Private policy configuration, native handoffs and tree-preserving cleanup."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import commit_policy as policy
import config
from state import Store, make_spec, write

module_spec = importlib.util.spec_from_file_location('policy_config', ROOT/'scripts/pr_config.py')
pr_config = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(pr_config)
REPO = 'github.com/owner/repo'
PR = 'https://' + REPO + '/pull/1'
CHAIN = ['remote:github.com/fork/repo', 'remote:' + REPO]
PROMPT = 'Squash all PR changes into one commit. Use a short subject without a body.\n'


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(pr_config, 'identities', lambda *_: CHAIN)
    return pr_config.main


@pytest.mark.parametrize('value', [{'prompt': PROMPT}, {'skill': 'my-plugin:commit-policy'}, {'skill': 'local-policy'}])
def test_policy_round_trip_reset_and_other_settings_unchanged(settings, tmp_path, value):
    rules = tmp_path/'rules.json'
    write(rules, {'rules': [{'text': 'Keep the original rule.'}]})
    settings(['rules', 'set', '--scope', 'user', '--file', str(rules)])
    write(policy.path('user').parent/'drafting.json', {'commit_message': 'team:message'})
    write(config.path(), {'reviewers': [{'skill': 'team:review'}]})
    preserved = {p: p.read_bytes() for p in (
        policy.path('user').parent/'user/rules.json',
        policy.path('user').parent/'drafting.json', config.path())}
    source = tmp_path/'policy.json'
    write(source, value)
    settings(['commit-policy', 'set', '--scope', 'user', '--file', str(source)])
    shown = settings(['commit-policy', 'show'])
    assert shown == {'value': value, 'source': 'user', 'file': str(policy.path('user'))}
    assert policy.path('user').stat().st_mode & 0o777 == 0o600
    assert settings(['commit-policy', 'show', '--scope', 'repo']) == {'value': None, 'source': 'default'}
    settings(['commit-policy', 'reset', '--scope', 'user'])
    assert settings(['commit-policy', 'show']) == {'value': None, 'source': 'default'}
    assert all(p.read_bytes() == content for p, content in preserved.items())


def test_whole_policy_inherits_repo_upstream_user_default(settings, tmp_path):
    source = tmp_path/'policy.json'
    write(source, {'prompt': PROMPT})
    settings(['commit-policy', 'set', '--scope', 'user', '--file', str(source)])
    write(policy.path(CHAIN[1]), {'skill': 'upstream:policy'})
    assert settings(['commit-policy', 'show'])['value'] == {'skill': 'upstream:policy'}
    assert settings(['commit-policy', 'show'])['source'] == CHAIN[1]
    write(source, {'prompt': 'Keep independent logical commits.'})
    settings(['commit-policy', 'set', '--scope', 'repo', '--file', str(source)])
    assert settings(['commit-policy', 'show'])['source'] == CHAIN[0]
    assert settings(['commit-policy', 'show', '--scope', 'user'])['value'] == {'prompt': PROMPT}
    settings(['commit-policy', 'reset', '--scope', 'repo'])
    assert settings(['commit-policy', 'show'])['source'] == CHAIN[1]
    policy.path(CHAIN[1]).unlink()
    assert settings(['commit-policy', 'show'])['source'] == 'user'
    settings(['commit-policy', 'reset', '--scope', 'user'])
    assert settings(['commit-policy', 'show'])['source'] == 'default'


@pytest.mark.parametrize('value', [None, [], {}, {'prompt': ''}, {'prompt': ' \n'}, {'prompt': 7},
    {'prompt': 'x\x00y'}, {'prompt': 'x', 'skill': 'team:policy'}, {'skill': ''},
    {'skill': ' team:policy'}, {'skill': '/skill/team:policy'}, {'skill': '../policy'},
    {'skill': 'team:policy:extra'}, {'skill': ['team:policy']}, {'prompt': 'x', 'extra': True}])
def test_invalid_writes_preserve_previous_policy_and_malformed_reads_block(settings, tmp_path, value):
    write(policy.path('user'), {'prompt': PROMPT})
    source = tmp_path/'bad.json'
    write(source, value)
    with pytest.raises(ValueError):
        settings(['commit-policy', 'set', '--scope', 'user', '--file', str(source)])
    assert settings(['commit-policy', 'show'])['value'] == {'prompt': PROMPT}
    write(policy.path(CHAIN[0]), value)
    with pytest.raises(ValueError):
        settings(['commit-policy', 'show'])
    settings(['commit-policy', 'reset', '--scope', 'repo'])
    assert settings(['commit-policy', 'show'])['source'] == 'user'


def test_effective_mutation_rejected(settings, tmp_path):
    source = tmp_path/'policy.json'
    write(source, {'prompt': PROMPT})
    for args in (['reset'], ['set', '--file', str(source)]):
        with pytest.raises(ValueError, match='Mutations require'):
            settings(['commit-policy', *args, '--scope', 'effective'])


def git(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args],
                                   stderr=subprocess.PIPE, timeout=15).decode().strip()


@pytest.fixture
def context(tmp_path):
    checkout = tmp_path/'checkout'
    checkout.mkdir()
    git(checkout, 'init', '-q')
    git(checkout, 'config', 'user.email', 'fixture@example.com')
    git(checkout, 'config', 'user.name', 'Fixture Author')
    (checkout/'code.py').write_text('VALUE = 1\n')
    git(checkout, 'add', '.')
    git(checkout, 'commit', '-qm', 'Initial')
    base = git(checkout, 'rev-parse', 'HEAD')
    (checkout/'code.py').write_text('VALUE = 2\n')
    git(checkout, 'commit', '-qam', 'Implement value')
    (checkout/'README').write_text('Use value two.\n')
    git(checkout, 'add', '.')
    git(checkout, 'commit', '-qm', 'Document value')
    resource = tmp_path/'provider.md'
    resource.write_text('Return grouping and message instructions only. No edits or publication.\n')
    requirements = tmp_path/'requirements.txt'
    requirements.write_text('Preserve authorship and required repository conventions.\n')
    constraints = tmp_path/'constraints.txt'
    constraints.write_text('Caller authorizes local fixture rewrite only.\n')
    consumed = tmp_path/'context.json'
    write(consumed, {'scope': 'Return two and document its use.'})
    return {'skill_identity': 'team:policy', 'resources': {'skill': str(resource)},
            'assigned_instructions': 'Define grouping and message instructions only; return policy_prompt and completion.',
            'instructions': {'requirements': str(requirements), 'user': str(constraints)},
            'inputs': {'context': str(consumed)}, 'options': {},
            'git': {'checkout': str(checkout), 'target': base, 'base': base,
                    'head': git(checkout, 'rev-parse', 'HEAD'), 'history': True, 'head_sensitive': True},
            'governing_inputs_complete': True}


def prepare(context):
    return policy.policy_spec(chain=CHAIN, repository=REPO, pr=PR, definition=context)


def report():
    return {'complete': True, 'findings': [], 'policy_prompt': PROMPT, 'blockers': []}


def finish(store, task, result=None):
    claim = store.start(task, run='run')['claim']
    result = result or {'complete': True, 'findings': []}
    store.record(task, claim['id'], json.dumps(result).encode(), result, 'complete')


def cleanup_task(context, handoff, tmp_path):
    selected = tmp_path/'handoff.json'
    write(selected, handoff)
    return make_spec(repository=REPO, pr=PR, skill_identity='dk-workflows:dk-commit-split',
        resources=context['resources'], responsibility='commit-cleanup',
        assigned_instructions='Apply supplied grouping and message policy within caller permissions.',
        instructions=handoff['spec']['instructions'], inputs={**context['inputs'], 'commit_policy': str(selected)},
        options={}, git=context['git'], governing_inputs_complete=True)


def test_selected_provider_handoff_and_unchanged_managed_reuse(context, tmp_path):
    write(policy.path('user'), {'skill': 'team:policy'})
    task = prepare(context)
    store = Store(REPO, PR)
    finish(store, task, report())
    supplied = policy.handoff(chain=CHAIN, spec=task, report=report())
    cleanup = cleanup_task(context, supplied, tmp_path)
    finish(store, cleanup)
    assert policy.verify(chain=CHAIN, repository=REPO, pr=PR, definition=context, supplied=supplied) == supplied
    assert store.start(prepare(context), run='run')['action'] == 'reuse'
    assert store.start(cleanup, run='run')['action'] == 'reuse'
    # Unconsumed settings do not invalidate provider or cleanup evidence.
    write(policy.path('user').parent/'drafting.json', {'issue_description': 'other:issue'})
    assert store.start(prepare(context), run='run')['action'] == 'reuse'
    assert store.start(cleanup, run='run')['action'] == 'reuse'


@pytest.mark.parametrize('changed', ['configuration', 'provider', 'requirements', 'constraints', 'context', 'history'])
def test_relevant_changes_invalidate_policy_and_cleanup(context, tmp_path, changed):
    write(policy.path('user'), {'skill': 'team:policy'})
    initial = prepare(context)
    supplied = policy.handoff(chain=CHAIN, spec=initial, report=report())
    cleanup = cleanup_task(context, supplied, tmp_path)
    store = Store(REPO, PR)
    finish(store, initial, report())
    finish(store, cleanup)
    current = deepcopy(context)
    if changed == 'configuration':
        write(policy.path(CHAIN[0]), {'skill': 'team:policy'})
    elif changed == 'history':
        checkout = current['git']['checkout']
        git(checkout, 'commit', '--amend', '-qm', 'Updated history only')
        current['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    else:
        source = {'provider': context['resources']['skill'], 'requirements': context['instructions']['requirements'],
                  'constraints': context['instructions']['user'], 'context': context['inputs']['context']}[changed]
        Path(source).write_text('Changed governing input.\n')
    with pytest.raises(ValueError, match='stale'):
        policy.verify(chain=CHAIN, repository=REPO, pr=PR, definition=current, supplied=supplied)
    refreshed = prepare(current)
    assert not store.status([refreshed])['tasks'][0]['reusable']
    new_handoff = policy.handoff(chain=CHAIN, spec=refreshed, report=report())
    refreshed_cleanup = cleanup_task(current, new_handoff, tmp_path)
    assert not store.status([refreshed_cleanup])['tasks'][0]['reusable']


@pytest.mark.parametrize('bad_report', [None, {}, {'complete': False, 'findings': [], 'policy_prompt': PROMPT},
    {'complete': True, 'findings': [], 'policy_prompt': ''},
    {'complete': True, 'findings': [], 'policy_prompt': ' \n'},
    {'complete': True, 'findings': [], 'policy_prompt': PROMPT, 'blockers': ['Unavailable context']},
    {'complete': True, 'policy_prompt': PROMPT}])
def test_incomplete_provider_blocks_handoff(context, bad_report):
    write(policy.path('user'), {'skill': 'team:policy'})
    with pytest.raises(ValueError, match='completed report'):
        policy.handoff(chain=CHAIN, spec=prepare(context), report=bad_report)


def test_missing_provider_wrong_identity_and_changed_execution_inputs_block(context):
    write(policy.path('user'), {'skill': 'team:policy'})
    initial = prepare(context)
    Path(context['resources']['skill']).write_text('Provider changed while running.\n')
    with pytest.raises(ValueError, match='changed during execution'):
        policy.handoff(chain=CHAIN, spec=initial, report=report())
    Path(context['resources']['skill']).unlink()
    with pytest.raises(OSError):
        prepare(context)
    context['skill_identity'] = 'wrong:provider'
    with pytest.raises(ValueError, match='selected policy skill'):
        prepare(context)


@pytest.mark.parametrize('change', ['history', 'head_sensitive', 'unpinned', 'resources', 'complete'])
def test_incomplete_capture_definitions_block(context, change):
    write(policy.path('user'), {'skill': 'team:policy'})
    if change in {'history', 'head_sensitive'}:
        context['git'][change] = False
    elif change == 'unpinned':
        context['git']['head'] = 'HEAD'
    elif change == 'resources':
        context['resources'] = {}
    else:
        context['governing_inputs_complete'] = False
    with pytest.raises(ValueError):
        prepare(context)


def test_failed_provider_never_supplies_reusable_policy_coverage(context):
    write(policy.path('user'), {'skill': 'team:policy'})
    task = prepare(context)
    store = Store(REPO, PR)
    claim = store.start(task, run='run')['claim']
    failed = {'complete': False, 'findings': [], 'blockers': ['Native provider failed.']}
    store.record(task, claim['id'], json.dumps(failed).encode(), failed, 'failed')
    assert not store.status([task])['tasks'][0]['reusable']
    with pytest.raises(ValueError, match='completed report'):
        policy.handoff(chain=CHAIN, spec=task, report=failed)


@pytest.mark.parametrize('malformed', [None, [], {}, {'task': []}, {'task': {}, 'skill': [], 'options': {}}])
def test_malformed_handoff_inputs_block(malformed, context):
    with pytest.raises(ValueError, match='Malformed'):
        policy.handoff(chain=CHAIN, spec=malformed)
    with pytest.raises(ValueError, match='Malformed'):
        policy.verify(chain=CHAIN, repository=REPO, pr=PR, definition=context, supplied=malformed)


def test_public_commands_support_direct_provider_and_prompt_handoffs(context, tmp_path, monkeypatch):
    # A strict offline gh fixture supplies only repository metadata; production
    # commands still run in separate processes, including the identity resolver.
    bin_dir = tmp_path/'bin'
    bin_dir.mkdir()
    executable = bin_dir/'gh'
    metadata = {'number': 1, 'url': PR, 'headRepository': {'nameWithOwner': 'owner/repo'}, 'isCrossRepository': False}
    executable.write_text('#!' + sys.executable + '\nimport json, sys\n'
                          'assert sys.argv[1:3] == ["pr", "view"]\n'
                          'print(' + repr(json.dumps(metadata)) + ')\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ['PATH'])
    prefix = [sys.executable, str(ROOT/'scripts/commit_policy.py'), '--cwd', context['git']['checkout'],
              '--repository', REPO, '--pr', PR]
    settings_cli = [sys.executable, str(ROOT/'scripts/pr_config.py'), '--cwd', context['git']['checkout'], '--pr', PR]
    def command(argv, success=True):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        assert result.returncode == (0 if success else 2), result.stdout + result.stderr
        return json.loads(result.stdout)
    source, definition = tmp_path/'source.json', tmp_path/'definition.json'
    spec_file, result_file, handoff_file = tmp_path/'spec.json', tmp_path/'report.json', tmp_path/'supplied.json'
    for value in ({'skill': 'team:policy'}, {'prompt': PROMPT}):
        write(source, value)
        command([*settings_cli, 'commit-policy', 'set', '--scope', 'user', '--file', str(source)])
        assert command([*settings_cli, 'commit-policy', 'show'])['value'] == value
        context['skill_identity'] = value.get('skill', 'dk-workflows:dk-commit-split')
        write(definition, context)
        write(spec_file, command([*prefix, 'spec', '--definition', str(definition)]))
        args = ['--report', str(result_file)] if 'skill' in value else []
        write(result_file, report())
        supplied = command([*prefix, 'handoff', '--spec', str(spec_file), *args])
        write(handoff_file, supplied)
        assert supplied['policy_prompt'] == PROMPT
        assert command([*prefix, 'verify', '--definition', str(definition), '--handoff', str(handoff_file)]) == supplied
        # Configuration changes after handoff block acceptance.
        write(source, {'prompt': 'Keep independent logical commits.'})
        command([*settings_cli, 'commit-policy', 'set', '--scope', 'user', '--file', str(source)])
        assert command([*prefix, 'verify', '--definition', str(definition), '--handoff', str(handoff_file)], False)['status'] == 'blocked'
    command([*settings_cli, 'commit-policy', 'reset', '--scope', 'user'])
    assert command([*settings_cli, 'commit-policy', 'show'])['source'] == 'default'


@pytest.mark.parametrize('value', [None, {'prompt': PROMPT}])
def test_direct_prompt_and_default_handoffs_need_no_provider(context, value):
    if value is not None:
        write(policy.path('user'), value)
    context['skill_identity'] = 'dk-workflows:dk-commit-split'
    task = prepare(context)
    supplied = policy.handoff(chain=CHAIN, spec=task)
    assert supplied['report'] is None
    assert supplied['policy_prompt'] == (PROMPT if value else None)
    assert policy.verify(chain=CHAIN, repository=REPO, pr=PR, definition=context, supplied=supplied) == supplied
    with pytest.raises(ValueError, match='do not consume'):
        policy.handoff(chain=CHAIN, spec=task, report=report())


def test_real_git_single_subject_commit_preserves_tree_content_reviews_and_final_ci_head(context, tmp_path):
    """An explicit offline coordinator applies one fixture policy, not an LLM interpreter."""
    write(policy.path('user'), {'prompt': PROMPT})
    context['skill_identity'] = 'dk-workflows:dk-commit-split'
    original = prepare(context)
    supplied = policy.handoff(chain=CHAIN, spec=original)
    assert supplied['policy_prompt'] == PROMPT
    checkout = context['git']['checkout']
    before = context['git']['head']
    tree = git(checkout, 'rev-parse', 'HEAD^{tree}')
    review_git = {**context['git'], 'history': False, 'head_sensitive': False}
    def review():
        return make_spec(repository=REPO, pr=PR, skill_identity='fixture:review', resources=context['resources'],
            responsibility='content-review', assigned_instructions='Review the complete content.',
            instructions=context['instructions'], inputs=context['inputs'], options={},
            git=review_git, governing_inputs_complete=True)
    store = Store(REPO, PR)
    finish(store, review())
    cleanup = cleanup_task(context, supplied, tmp_path)
    claim = store.start(cleanup, run='run')['claim']
    assert not git(checkout, 'status', '--porcelain')
    git(checkout, 'branch', 'policy-recovery', before)
    git(checkout, 'reset', '--soft', context['git']['base'])
    git(checkout, 'commit', '-qm', 'Return two and document usage')
    after = git(checkout, 'rev-parse', 'HEAD')
    assert after != before
    assert git(checkout, 'rev-parse', 'HEAD^{tree}') == tree
    assert git(checkout, 'rev-list', '--count', context['git']['base'] + '..HEAD') == '1'
    assert git(checkout, 'show', '-s', '--format=%B', 'HEAD') == 'Return two and document usage'
    assert git(checkout, 'show', '-s', '--format=%an <%ae>', before) == git(checkout, 'show', '-s', '--format=%an <%ae>', after)
    assert git(checkout, 'rev-parse', 'policy-recovery') == before
    subprocess.run([sys.executable, '-c', 'import runpy, sys; assert runpy.run_path(sys.argv[1])["VALUE"] == 2',
                    str(Path(checkout)/'code.py')], check=True, timeout=15)
    store.record(cleanup, claim['id'], b'Validated tree-preserving fixture rewrite.',
                 {'complete': True, 'findings': [], 'reviewed_head': before, 'final_head': after}, 'complete')
    review_git['head'] = after
    assert store.start(review(), run='run')['action'] == 'reuse'
    # Final CI is a separate, live exact-head observation after cleanup.
    events = [('cleanup', after)]
    ci = make_spec(repository=REPO, pr=PR, skill_identity='fixture:ci', resources=context['resources'],
        responsibility='final-ci', assigned_instructions='Observe resulting final head.', instructions={}, inputs={},
        options={'head': after}, git={**context['git'], 'head': after}, cacheable=False)
    claim = store.start(ci, run='run')['claim']
    ci_head = git(checkout, 'rev-parse', 'HEAD')
    events.append(('ci', ci_head))
    assert ci_head == after and ci_head != before
    store.record(ci, claim['id'], b'Exact-head offline CI fixture passed.',
                 {'complete': True, 'findings': [], 'head': ci_head}, 'complete')
    assert events == [('cleanup', after), ('ci', after)]
    assert not store.status([ci])['tasks'][0]['reusable']
