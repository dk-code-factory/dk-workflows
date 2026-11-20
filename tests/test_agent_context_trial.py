"""Offline checks for the agent-context reviewer trial kit (#256); no model or GitHub calls."""
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests/fixtures'))
import agent_context_trial as trial

SCHEMA = json.loads((ROOT/'schemas/agent-context.schema.json').read_text())


@pytest.fixture(scope='module')
def arms(tmp_path_factory):
    root = tmp_path_factory.mktemp('trial')
    return {arm: (root/arm, trial.init(root/arm, arm)) for arm in trial.ARMS}


def handoff(root):
    return json.loads((root/'handoff.json').read_text())


def test_arms_share_prepared_task_and_checklist(arms):
    baseline, candidate = handoff(arms['baseline'][0]), handoff(arms['candidate'][0])
    prepared = json.loads((arms['baseline'][0]/'control/prepared.json').read_text())
    assert baseline == {'assignment': prepared['assignment'], 'checklist': prepared['checklist']}
    assert candidate['checklist'] == baseline['checklist']
    assert candidate['context']['task']['instructions'] == baseline['assignment']['task']['instructions']
    assert arms['baseline'][1]['head'] == arms['candidate'][1]['head']


def test_candidate_document_is_valid_and_complete(arms):
    root = arms['candidate'][0]
    context = handoff(root)['context']
    assert not list(Draft202012Validator(SCHEMA).iter_errors(context))
    for address, item in context['governing'].items():
        assert trial.get_item(root, trial.TASK_ID, address) == {'item': item}
    for address in context['evidence']:
        assert 'item' in trial.get_item(root, trial.TASK_ID, address)
    assert arms['candidate'][1]['governing_bytes'] > 0


def test_get_item_outcomes(arms):
    root = arms['candidate'][0]
    assert trial.get_item(root, trial.TASK_ID, 'decision/pr/escape-names#/statement')['item'].startswith('User')
    assert trial.get_item(root, trial.TASK_ID, 'decision/pr/missing') == {'outcome': 'not_found'}
    assert trial.get_item(root, trial.TASK_ID, 'decision/pr/escape-names#/nope') == {'outcome': 'not_found'}
    assert trial.get_item(root, '/task/pr/other', 'evidence/pr/diff') == {'outcome': 'not_found'}


def test_arms_are_never_replaced(arms):
    with pytest.raises(ValueError):
        trial.init(arms['baseline'][0], 'baseline')


@pytest.mark.parametrize('findings,complete,passed', [
    ([{'path': 'greeting.py', 'title': 'Name is not HTML-escaped'},
      {'path': 'greeting.py', 'title': 'Mutable default argument shares history'}], True, True),
    ([{'path': 'greeting.py', 'title': 'Name is not HTML-escaped'}], True, False),
    ([{'path': 'greeting.py', 'title': 'Name is not HTML-escaped'},
      {'path': 'greeting.py', 'title': 'Mutable default argument shares history'}], False, False),
])
def test_check_requires_both_defects(tmp_path, findings, complete, passed):
    (tmp_path/'artifacts').mkdir()
    (tmp_path/'artifacts/report.json').write_text(json.dumps({'complete': complete, 'findings': findings}))
    assert trial.check(tmp_path)['passed'] is passed
