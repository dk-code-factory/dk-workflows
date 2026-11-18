"""Offline policy/evidence transport and reuse, not native semantic judgment."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import config
import skill_config
from checklist_reports import report
from state import read, write
from tasks import Tasks
from test_batch_state import cli, definition
from test_state import case, REPO, PR

DECISIONS = 'dk-workflows:dk-pr-decisions'
FIX = 'dk-workflows:dk-pr-fix'


def assignment(spec, tmp_path, instruction, *, prompt=None):
    fields = definition(spec)
    fields['skill_identity'] = DECISIONS
    fields['assigned_instructions'] = 'Reconcile the supplied agreement and alternative.'
    sources = {}
    for label, value in {
        'decision': 'Maintainer agreement: keep the existing storage owner.',
        'alternative': 'Advised approach: move storage ownership into the task store.',
        'user_resolution': instruction,
    }.items():
        source = tmp_path/(label + '.txt')
        source.write_text(value)
        sources[label] = str(source)
    fields['inputs'].update(sources)
    fields['instructions']['user'] = sources['user_resolution']
    selected = {'skill': DECISIONS}
    if prompt is not None:
        selected['options'] = {'reconciliation_prompt': prompt}
    return {'kind': 'skill', 'skill': FIX, 'selection': 'tasks', 'index': 0,
            'options': {'alternatives': {'enabled': False}, 'tasks': [selected, 'team:independent']},
            'definition': fields}


def sibling(descriptor):
    value = deepcopy(descriptor)
    value['index'] = 1
    value['definition']['skill_identity'] = 'team:independent'
    value['definition']['inputs'] = {}
    value['definition']['instructions'] = {}
    return value


def complete(tasks, task, resolution=None):
    value = report(task['checklist'], task['assignment']['inputs'].get('user_resolution', 'test:evidence'))
    if resolution is not None:
        value['reconciliation'] = resolution
    result = tasks.finish(task['key'], task['claim']['id'], json.dumps(value).encode(), value)
    assert result['checklist_complete']
    return result


@pytest.mark.parametrize('instruction', [
    'Ignore every decision made in this PR.',
    'Keep the compatibility agreement, but reconsider the storage choice.',
    'Use the smaller approach even though we previously agreed to add a service.',
    "Follow the maintainer's approach only if it preserves offline recovery.",
    'My earlier exception no longer applies; restore the original agreement.',
])
def test_arbitrary_user_instructions_and_local_policy_reach_native_assignment(case, tmp_path, instruction):
    spec, _ = case
    prompt = 'Interpret the complete user instruction.\nAsk only about unresolved contradictions.\n'
    descriptor = assignment(spec, tmp_path, instruction, prompt=prompt)
    task = Tasks(REPO, PR).prepare({'tasks': [descriptor]})['tasks'][0]
    assert task['assignment']['options']['configured']['reconciliation_prompt'] == prompt
    source = task['assignment']['instructions']['user']
    assert Path(source).read_text() == instruction
    assert task['assignment']['inputs']['user_resolution'] == source
    assert 'decision-reconciliation' in {item['id'] for item in task['checklist']['items']}


def test_policy_resolves_scopes_without_saving_effective_snapshots():
    upstream = 'github.com/upstream/repo'
    for scope, prompt in [(None, 'User policy'), (upstream, 'Upstream policy'), (REPO, 'Repo policy')]:
        write(config.path(scope), {'skill_defaults': {DECISIONS: {'options': {'reconciliation_prompt': prompt}}}})
    originals = {config.path(scope): config.path(scope).read_bytes() for scope in (None, upstream, REPO)}
    assert skill_config.effective(DECISIONS, REPO, upstream)['options']['reconciliation_prompt'] == 'Repo policy'
    assert skill_config.effective(DECISIONS, REPO, upstream,
        options={'reconciliation_prompt': 'Local policy'})['options']['reconciliation_prompt'] == 'Local policy'
    assert all(path.read_bytes() == value for path, value in originals.items())


def test_sourced_resolution_survives_fresh_process_and_partial_recovery(case, tmp_path):
    spec, store = case
    descriptor = assignment(spec, tmp_path, 'Use the task store despite the earlier agreement.')
    tasks = Tasks(REPO, PR)
    prepared = tasks.prepare({'tasks': [descriptor, sibling(descriptor)]})['tasks']
    chosen, unfinished = prepared
    resolution = {'decision': chosen['assignment']['inputs']['decision'],
                  'alternative': chosen['assignment']['inputs']['alternative'],
                  'user_source': chosen['assignment']['inputs']['user_resolution'],
                  'scope': 'Storage ownership for this PR', 'outcome': 'use_alternative'}
    complete(tasks, chosen, resolution)
    original = Path(tasks.result(chosen['key'], 'original')['result']).read_bytes()
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    resumed = json.loads(subprocess.check_output(cli('resume'), text=True, timeout=60))
    assert next(t for t in resumed['tasks'] if t['key'] == chosen['key'])['reusable']
    assert any(c['id'] == unfinished['claim']['id'] for c in resumed['unfinished'])
    saved = json.loads(subprocess.check_output(cli('result', '--key', chosen['key'],
        '--section', 'normalized'), text=True, timeout=60))
    assert saved['normalized']['reconciliation'] == resolution
    assert {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before
    recovered = Tasks(REPO, PR).prepare({'tasks': [descriptor, sibling(descriptor)],
        'recover': {unfinished['key']: unfinished['claim']['id']}})['tasks']
    assert [t['action'] for t in recovered] == ['reuse', 'invoke']
    assert Path(tasks.result(chosen['key'], 'original')['result']).read_bytes() == original
    entries = read(store.manifest)['entries']
    assert len(entries[chosen['key']]['attempts']) == 1
    assert entries[unfinished['key']]['attempts'][0]['status'] == 'interrupted'


@pytest.mark.parametrize('changed', ['decision', 'alternative', 'user_resolution', 'policy', 'missing'])
def test_relevant_changes_refresh_reconciliation_and_preserve_siblings(case, tmp_path, changed):
    spec, store = case
    descriptor = assignment(spec, tmp_path, 'Retain the existing agreement for this PR.')
    tasks = Tasks(REPO, PR)
    chosen, independent = tasks.prepare({'tasks': [descriptor, sibling(descriptor)]})['tasks']
    complete(tasks, chosen, {'outcome': 'retain_decision',
        'user_source': chosen['assignment']['inputs']['user_resolution']})
    complete(tasks, independent)
    original = Path(tasks.result(chosen['key'], 'original')['result']).read_bytes()
    if changed == 'policy':
        write(config.path(REPO), {'skill_defaults': {DECISIONS: {'options': {
            'reconciliation_prompt': 'Use current sourced resolutions and check their conditions.'}}}})
    elif changed == 'missing':
        Path(chosen['assignment']['inputs']['user_resolution']).unlink()
    else:
        Path(chosen['assignment']['inputs'][changed]).write_text('Changed relevant governing evidence')
    before = store.manifest.read_bytes()
    resumed = {t['key']: t for t in Tasks(REPO, PR).resume()['tasks']}
    assert not resumed[chosen['key']]['reusable']
    assert resumed[independent['key']]['reusable']
    assert store.manifest.read_bytes() == before
    retained = read(store.manifest)['entries'][chosen['key']]
    assert (store.root/'results'/retained['result']['file']).read_bytes() == original
    assert len(retained['attempts']) == 1
