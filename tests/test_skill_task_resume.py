"""Run-scoped resume must discover shared tasks without leaking run occurrences."""
import json
import subprocess

import pytest

from checklist_reports import finish
from tasks import Tasks
from test_batch_state import cli, definition
from test_state import case, REPO, PR


@pytest.mark.parametrize('selection', ['tasks', 'reviewers'])
def test_run_resume_includes_shared_skill_tasks_and_only_its_own_finalizers(case, selection):
    spec, store = case
    fields = definition(spec)
    fields['skill_identity'] = 'dk-workflows:dk-pr-fix'
    fields['responsibility'] = 'bounded-inspection'
    owner = 'dk-workflows:dk-pr-fix' if selection == 'tasks' else 'dk-workflows:dk-pr-reviewers-run'
    options = {selection: [{'instructions': 'Inspect the bounded fixture.', 'required': True}]}
    shared = {'kind': 'skill', 'skill': owner, 'selection': selection,
              'index': 0, 'options': options, 'definition': fields}
    if selection == 'reviewers':
        # Reviewer occurrences require actual host execution; retain its unresolved
        # claim here to verify discovery without fabricating completion evidence.
        from native_review_observations import CONTRACT
        fields['native_review'] = CONTRACT
        fields['skill_identity'] = owner
    finalizer = {'kind': 'skill', 'skill': 'dk-workflows:dk-pr-fix', 'selection': 'finalizers',
                 'index': 0, 'options': {'finalizers': [{'instructions': 'Bounded finalizer.'}]},
                 'definition': {**definition(spec), 'skill_identity': 'dk-workflows:dk-pr-fix'}}
    tasks = Tasks(REPO, PR)
    prepared = tasks.prepare({'run': 'one', 'tasks': [shared]})['tasks'][0]
    if selection == 'tasks':
        finish(tasks, prepared['key'], prepared['claim']['id'], b'Original shared result', {'complete': True})
    finalizer_keys = {}
    for run in ('one', 'two'):
        item = tasks.prepare({'run': run, 'tasks': [finalizer]})['tasks'][0]
        finish(tasks, item['key'], item['claim']['id'], b'Original finalizer', {'complete': True})
        finalizer_keys[run] = item['key']
    before = store.manifest.read_bytes()
    for run in ('one', 'two'):
        resumed = json.loads(subprocess.check_output(cli('resume', '--run', run), text=True, timeout=60))
        assert {item['key'] for item in resumed['tasks']} == {prepared['key'], finalizer_keys[run]}
        shared_status = next(item for item in resumed['tasks'] if item['key'] == prepared['key'])
        assert shared_status['reusable'] == (selection == 'tasks')
        if selection == 'reviewers':
            assert any(claim['id'] == prepared['claim']['id'] for claim in resumed['unfinished'])
    assert store.manifest.read_bytes() == before
    if selection == 'tasks':
        repeated = tasks.prepare({'run': 'two', 'tasks': [shared]})['tasks'][0]
        assert repeated['action'] == 'reuse'
        assert tasks.result(prepared['key'], 'original')['original'] == 'Original shared result'
