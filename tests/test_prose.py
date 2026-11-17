"""Offline prose protocol fixtures; native language judgment is not simulated."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import config
import skill_config
from prose import execution_options, handoff, wrapped
from state import read, write
from tasks import Tasks
from checklist_reports import accept, finish
from test_state import case, git, REPO, PR, ROOT

FIX = 'dk-workflows:dk-pr-fix'
PLAIN = 'dk-workflows:dk-plain-english'
STE = 'dk-workflows:dk-asd-ste100'


def descriptor(case, provider=PLAIN, plan=None):
    spec, _ = case
    return {'kind': 'skill', 'skill': FIX, 'selection': 'finalizers', 'index': 0,
            'run': 'retained', 'options': {'finalizers': plan or [provider], 'alternatives': {'enabled': False}},
            'definition': {'skill_identity': provider, 'resources': spec['skill']['resources'],
                           'assigned_instructions': 'Edit candidate prose under the shared prose contract.',
                           **{k: deepcopy(spec[k]) for k in ('inputs', 'instructions', 'git', 'options', 'governing_inputs_complete')}}}


def commit_text(case, text):
    checkout = case[0]['git']['checkout']
    Path(checkout, 'code').write_text(text)
    git(checkout, 'commit', '-qam', 'edit')
    return git(checkout, 'rev-parse', 'HEAD')


def complete(tasks, selected, *, output=None, extra=None):
    task = tasks.prepare(selected['request'])['tasks'][0]
    refs = execution_options(task['assignment'], is_wrapped=wrapped(task['assignment']))['prose_cleanup']
    value = {'complete': True, 'findings': [], 'changed_locations': [],
             'validation': ['Checked protected syntax and original requirements.'],
             'unresolved_ambiguities': [], 'reference_verification_limits': [],
             'prose_cleanup': {**refs, 'output': output or refs['head']}, **(extra or {})}
    finish(tasks, task['key'], task['claim']['id'], b'Original native prose output.', value)
    return task


def test_default_order_alternative_custom_disabled_and_incoming_unchanged():
    defaults = skill_config.defaults(FIX)['finalizers']
    assert defaults == [{'skill': name, 'options': {}, 'required': True} for name in (
        PLAIN, 'dk-workflows:dk-commit-split', 'dk-workflows:dk-pr-decisions',
        'dk-workflows:dk-pr-description', 'dk-workflows:dk-pr-feedback-reply',
        'dk-workflows:dk-ci', 'dk-workflows:dk-pr-publish')]
    incoming = skill_config.defaults('dk-workflows:dk-pr-review')
    for replacement in ([], [STE], [{'skill': 'team:prose', 'instructions': 'Keep terminology.',
                                    'options': {'dialect': 'local'}, 'agent': {'model': 'chosen', 'reasoning_effort': 'high'}}]):
        write(config.path(REPO), {'skill_defaults': {FIX: {'options': {'finalizers': replacement}}}})
        assert skill_config.effective(FIX, REPO)['options']['finalizers'] == replacement
        assert skill_config.effective('dk-workflows:dk-pr-review', REPO)['options'] == incoming
    assert PLAIN not in [call.get('skill') for _, call in skill_config.calls('dk-workflows:dk-pr-review', REPO)]


def test_fresh_process_reuses_accepted_output_after_own_edits_and_history_cleanup(case, tmp_path):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case)
    selected = handoff(tasks, desc)
    assert selected['mode'] == 'full' and '+review me' in selected['patch']
    output = commit_text(case, 'Clear technical prose.\n')
    task = complete(tasks, selected, output=output)
    desc['definition']['git']['head'] = output
    # Tree-preserving commit cleanup must not repeat prose analysis.
    git(case[0]['git']['checkout'], 'commit', '--amend', '-qm', 'reworded')
    desc['definition']['git']['head'] = git(case[0]['git']['checkout'], 'rev-parse', 'HEAD')
    source = tmp_path/'descriptor.json'
    write(source, desc)
    before = tasks.store.manifest.read_bytes()
    result = json.loads(subprocess.check_output([
        sys.executable, str(ROOT/'scripts/prose.py'), '--repository', REPO,
        '--pr', PR, '--descriptor', str(source)], text=True))
    resumed = json.loads(subprocess.check_output([
        sys.executable, str(ROOT/'scripts/prose.py'), '--repository', REPO, '--pr', PR,
        '--key', task['key'], '--head', desc['definition']['git']['head']], text=True))
    assert resumed == result
    assert result['action'] == 'reuse' and result['patch'] == ''
    assert result['key'] == task['key'] and result['output'] == output
    assert Tasks(REPO, PR).prepare(result['request'])['tasks'][0]['action'] == 'reuse'
    assert tasks.store.manifest.read_bytes() == before


def test_later_text_selects_only_delta_and_preserves_completed_siblings(case):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case, plan=[PLAIN, 'team:sibling'])
    output = commit_text(case, 'Accepted prose.\n')
    first = complete(tasks, handoff(tasks, desc), output=output)
    sibling = deepcopy(desc)
    sibling['index'] = 1
    sibling['definition']['skill_identity'] = 'team:sibling'
    sibling_task = tasks.prepare({'run': 'retained', 'tasks': [sibling]})['tasks'][0]
    finish(tasks, sibling_task['key'], sibling_task['claim']['id'], b'Completed sibling.', {'complete': True})
    original = read(tasks.store.manifest)['entries'][sibling_task['key']]
    head = commit_text(case, 'Accepted prose.\nLater wording.\n')
    desc['definition']['git']['head'] = head
    selected = handoff(tasks, desc)
    assert selected['mode'] == 'delta' and selected['baseline'] == output
    assert '+Later wording.' in selected['patch'] and '+Accepted prose.' not in selected['patch']
    assert selected['request']['tasks'][0]['definition']['inputs']['prose_previous'] == tasks.result(first['key'])['result']
    complete(tasks, selected)
    assert read(tasks.store.manifest)['entries'][sibling_task['key']] == original
    assert handoff(Tasks(REPO, PR), desc)['action'] == 'reuse'
    assert tasks.store.status([])['runs']['retained']['cycles'] == []


def test_interruption_and_pending_checklist_resume_original_work(case):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case)
    selected = handoff(tasks, desc)
    task = tasks.prepare(selected['request'])['tasks'][0]
    output = commit_text(case, 'Edited before interruption.\n')
    desc['definition']['git']['head'] = output
    recovering = handoff(Tasks(REPO, PR), desc)
    assert recovering['action'] == 'recover_required'
    assert recovering['request']['tasks'][0] == selected['request']['tasks'][0]
    recovered = tasks.prepare({**recovering['request'], 'recover': {task['key']: task['claim']['id']}})['tasks'][0]
    assert recovered['key'] == task['key'] and recovered['action'] == 'invoke'
    refs = recovered['assignment']['options']['execution']['prose_cleanup']
    checked = tasks.finish(task['key'], recovered['claim']['id'], b'Recovered original edits.',
                           {'complete': True, 'prose_cleanup': {**refs, 'output': output},
                            'changed_locations': ['code:1'], 'validation': ['Recovered edits match source meaning.'],
                            'unresolved_ambiguities': [], 'reference_verification_limits': []})
    assert handoff(Tasks(REPO, PR), desc)['action'] == 'recheck'
    accept(tasks, checked)
    assert handoff(Tasks(REPO, PR), desc)['action'] == 'reuse'


@pytest.mark.parametrize('change', ['instruction', 'reference', 'provider', 'base', 'native_controls'])
def test_governing_changes_require_full_pass(case, change):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case)
    complete(tasks, handoff(tasks, desc))
    if change == 'instruction':
        Path(desc['definition']['instructions']['user']).write_text('Changed requirements.')
    elif change == 'reference':
        Path(desc['definition']['resources']['entry']).write_text('New dictionary or provider guidance.')
    elif change == 'provider':
        desc = descriptor(case, provider=STE)
    elif change == 'base':
        desc['definition']['git']['base'] = desc['definition']['git']['head']
    else:
        desc['options']['finalizers'] = [{'skill': PLAIN, 'agent': {'model': 'chosen'}}]
    selected = handoff(Tasks(REPO, PR), desc)
    assert selected['mode'] == 'full' and selected['previous'] is None


@pytest.mark.parametrize('extra', [
    {'validation': ['No eligible prose: candidate changes contain only executable code.']},
    {'unresolved_ambiguities': [{'location': 'code:1', 'reason': 'Actor is unspecified; left unchanged.'}]},
    {'reference_verification_limits': ['Official dictionary unavailable; rule and vocabulary verification incomplete.']},
])
def test_noop_ambiguity_and_unavailable_references_can_complete_with_explicit_evidence(case, extra):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case, provider=STE)
    task = complete(tasks, handoff(tasks, desc), extra=extra)
    normalized = tasks.result(task['key'], 'normalized')['normalized']
    assert all(normalized[key] == value for key, value in extra.items())
    assert handoff(Tasks(REPO, PR), desc)['action'] == 'reuse'


def test_invalid_output_references_do_not_consume_claim(case):
    tasks = Tasks(REPO, PR)
    selected = handoff(tasks, descriptor(case))
    task = tasks.prepare(selected['request'])['tasks'][0]
    refs = execution_options(task['assignment'], is_wrapped=wrapped(task['assignment']))['prose_cleanup']
    before = tasks.store.manifest.read_bytes()
    for value in ({}, {'baseline': refs['head'], 'head': refs['head'], 'output': refs['head']},
                  {**refs, 'output': refs['baseline']}):
        report = {'complete': True, 'changed_locations': [], 'validation': ['Fixture validation.'],
                  'unresolved_ambiguities': [], 'reference_verification_limits': [], 'prose_cleanup': value}
        with pytest.raises(ValueError, match='prose report|current committed tree|input references'):
            tasks.finish(task['key'], task['claim']['id'], b'Invalid evidence.', report)
        assert tasks.store.manifest.read_bytes() == before


def test_native_prompt_contracts_cover_scope_protection_and_reference_limits():
    for name in ('dk-plain-english', 'dk-asd-ste100'):
        text = (ROOT/'codex/skills'/name/'SKILL.md').read_text()
        for required in ('docstrings, comments and documentation', 'machine directives',
                         'Leave ambiguous wording unchanged', 'no eligible prose', 'explicit completion'):
            assert required.lower() in text.lower()
    ste = (ROOT/'codex/skills/dk-asd-ste100/SKILL.md').read_text()
    assert 'Do not claim full ASD-STE100 compliance' in ste and 'continue the best-effort editing pass' in ste


def test_missing_result_blocks_reuse_without_clearing_saved_work(case):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case)
    task = complete(tasks, handoff(tasks, desc))
    Path(tasks.result(task['key'])['result']).unlink()
    before = tasks.store.manifest.read_bytes()
    with pytest.raises(ValueError, match='result unavailable'):
        handoff(Tasks(REPO, PR), desc)
    assert tasks.store.manifest.read_bytes() == before


def test_newly_available_reference_refreshes_full_pass(case, tmp_path):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case, provider=STE)
    complete(tasks, handoff(tasks, desc), extra={'reference_verification_limits': ['Dictionary unavailable.']})
    dictionary = tmp_path/'dictionary.txt'
    dictionary.write_text('Offline authoritative-reference fixture, not a copied dictionary.')
    desc['definition']['resources']['dictionary'] = str(dictionary)
    selected = handoff(tasks, desc)
    assert selected['mode'] == 'full' and selected['previous'] is None


def test_custom_provider_uses_same_delta_contract(case):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case, provider='team:prose')
    complete(tasks, handoff(tasks, desc))
    desc['definition']['git']['head'] = commit_text(case, 'review me\nLater prose.\n')
    assert handoff(Tasks(REPO, PR), desc)['mode'] == 'delta'


def test_changing_provider_preserves_unfinished_original_occurrence(case):
    tasks = Tasks(REPO, PR)
    selected = handoff(tasks, descriptor(case))
    task = tasks.prepare(selected['request'])['tasks'][0]
    before = tasks.store.manifest.read_bytes()
    changed = handoff(tasks, descriptor(case, provider=STE))
    assert changed['action'] == 'recover_required' and changed['key'] == task['key']
    assert changed['request']['tasks'][0]['definition']['skill_identity'] == PLAIN
    assert tasks.store.manifest.read_bytes() == before


def test_adopted_repository_guidance_refreshes_full_pass_without_reset(case):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case)
    guidance = Path(case[0]['git']['checkout'])/'AGENTS.md'
    guidance.write_text('Initial repository guidance.\n')
    task = complete(tasks, handoff(tasks, desc))
    old = deepcopy(read(tasks.store.manifest)['entries'][task['key']])
    guidance.write_text('Changed repository guidance. Preserve technical terms.\n')
    selected = handoff(Tasks(REPO, PR), desc)
    assert selected['action'] == 'prepare' and selected['mode'] == 'full'
    assert selected['previous'] is None
    assert read(tasks.store.manifest)['entries'][task['key']] == old
    complete(tasks, selected)
    assert handoff(Tasks(REPO, PR), desc)['action'] == 'reuse'


def test_binary_assets_are_summarized_without_hiding_text_candidates(case):
    import random
    checkout = Path(case[0]['git']['checkout'])
    (checkout/'asset.bin').write_bytes(b'\0' + random.Random(7).randbytes(128 * 1024))
    (checkout/'notes.md').write_text('Changed documentation prose.\n')
    git(checkout, 'add', '.')
    git(checkout, 'commit', '-qm', 'text and binary candidates')
    desc = descriptor(case)
    desc['definition']['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    selected = handoff(Tasks(REPO, PR), desc)
    assert 'Binary files ' in selected['patch'] and 'asset.bin' in selected['patch']
    assert '+Changed documentation prose.' in selected['patch']
    assert 'GIT binary patch' not in selected['patch'] and len(selected['patch']) < 2000


def test_current_cache_opt_out_repeats_analysis_instead_of_reusing_saved_setting(case):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case)
    first = complete(tasks, handoff(tasks, desc))
    desc['definition']['cacheable'] = False
    selected = handoff(tasks, desc)
    assert selected['action'] == 'prepare' and selected['mode'] == 'full'
    assert selected['request']['tasks'][0]['definition']['cacheable'] is False
    prepared = tasks.prepare(selected['request'])['tasks'][0]
    assert prepared['action'] == 'invoke' and prepared['key'] == first['key']


@pytest.mark.parametrize('control', ['items', 'max_checks', 'required'])
def test_current_completion_controls_refresh_only_checking(case, control):
    tasks = Tasks(REPO, PR)
    desc = descriptor(case)
    first = complete(tasks, handoff(tasks, desc))
    before = deepcopy(read(tasks.store.manifest)['entries'][first['key']])
    if control == 'items':
        desc['definition']['checklist'] = {'items': [{'id': 'terminology', 'text': 'Verify technical terminology.'}]}
    elif control == 'max_checks':
        desc['definition']['checklist'] = {'max_checks': 2}
    else:
        desc['definition']['required'] = False
    selected = handoff(tasks, desc)
    assert selected['action'] == 'recheck'
    check = tasks.prepare(selected['request'])['tasks'][0]
    assert check['action'] == 'recheck' and check['key'] == first['key']
    current = read(tasks.store.manifest)['entries'][first['key']]
    assert current['result'] == before['result'] and current['attempts'] == before['attempts']
    if control == 'items':
        assert any(item['id'] == 'terminology' for item in check['checklist']['items'])
    elif control == 'max_checks':
        assert check['checklist']['max_checks'] == 2
    else:
        assert check['checklist']['required'] is False
    accept(tasks, check)
    assert handoff(tasks, desc)['action'] == 'reuse'


def test_ordinary_normalized_options_preserve_reuse_and_require_output_references(case):
    tasks = Tasks(REPO, PR)
    desc = {'definition': descriptor(case)['definition']}
    desc['definition']['responsibility'] = 'prose-standalone'
    desc['definition']['options'] = {'execution': {'source': 'normalized standalone context'}}
    selected = handoff(tasks, desc)
    task = tasks.prepare(selected['request'])['tasks'][0]
    before = tasks.store.manifest.read_bytes()
    with pytest.raises(ValueError, match='prose report'):
        tasks.finish(task['key'], task['claim']['id'], b'No references.', {'complete': True})
    assert tasks.store.manifest.read_bytes() == before
    refs = task['assignment']['options']['prose_cleanup']
    output = commit_text(case, 'Accepted standalone prose.\n')
    finish(tasks, task['key'], task['claim']['id'], b'Standalone result.',
           {'complete': True, 'findings': [], 'changed_locations': ['code:1'],
            'validation': ['Source meaning and syntax verified.'], 'unresolved_ambiguities': [],
            'reference_verification_limits': [], 'prose_cleanup': {**refs, 'output': output}})
    desc['definition']['git']['head'] = output
    reused = handoff(Tasks(REPO, PR), desc)
    assert reused['action'] == 'reuse'
    assert tasks.prepare(reused['request'])['tasks'][0]['action'] == 'reuse'
    desc['definition']['git']['head'] = commit_text(case, 'Accepted standalone prose.\nLater text.\n')
    delta = handoff(Tasks(REPO, PR), desc)
    assert delta['mode'] == 'delta' and delta['baseline'] == output
    assert '+Later text.' in delta['patch']
