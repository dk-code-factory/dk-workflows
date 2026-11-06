"""Reusable preference inputs and one read facade avoid repeated model setup."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

import commit_policy
import config
import preferences
import settings
from shared import ci_exceptions, pr_partition_policy, pr_size
from shared.private_state import write
from test_preference_handoffs import workflow

ROOT = Path(__file__).resolve().parents[1]
REPO = 'github.com/owner/repo'
PR = 'https://' + REPO + '/pull/7'
CHAIN = ['remote:github.com/fork/repo', 'remote:' + REPO]


def decision(preference_id):
    return {'id': preference_id, 'applicable': True, 'paths': ['code.py'],
            'reason': 'Task specifies a settled return value.'}


def test_handoff_reuses_only_identical_condition_context_and_scoped_records(workflow):
    checkout, preference_id, store = workflow
    context = {'settled_return_value': 2}
    pending = preferences.handoff(checkout, 'review', ['code.py'], context)
    assert not pending['complete'] and len(pending['pending']) == 1
    completed = preferences.handoff(checkout, 'review', ['code.py'], context, decisions=[decision(preference_id)])
    assert completed['complete']
    previous = completed['handoff']
    assert preferences.handoff(checkout, 'review', ['code.py'], context, previous=previous)['handoff'] == previous
    assert not preferences.handoff(checkout, 'review', ['code.py'], {'settled_return_value': 3}, previous=previous)['complete']
    assert not preferences.handoff(checkout, 'implementation', ['code.py'], context, previous=previous)['complete']
    record = preferences.show(checkout, preference_id)['record']
    record['prompt'] = 'Check a different invariant.'
    preferences.commit(checkout, preferences.prepare(checkout, {'upsert': [record]}))
    assert not preferences.handoff(checkout, 'review', ['code.py'], context, previous=previous)['complete']


def test_unrelated_preference_does_not_rewrite_handoff_in_new_process(workflow, tmp_path):
    checkout, preference_id, store = workflow
    request = tmp_path/'handoff-request.json'
    write(request, {'mode': 'review', 'paths': ['code.py'], 'condition_context': {'return_value': 2},
                    'decisions': [decision(preference_id)]})
    output = tmp_path/'handoff.json'
    command = [sys.executable, str(ROOT/'scripts/preferences.py'), '--checkout', str(checkout),
               'handoff', '--file', str(request), '--output', str(output)]
    completed = json.loads(subprocess.check_output(command, text=True))
    assert completed['complete'] and not completed['unchanged']
    original = (output.read_bytes(), output.stat().st_mtime_ns)
    unrelated = {'owner': {'type': 'user'}, 'prompt': 'Read documentation carefully.',
                 'scope': {'include': [{'type': 'directory', 'path': 'docs'}], 'exclude': [], 'condition': None}}
    preferences.commit(checkout, preferences.prepare(checkout, {'upsert': [unrelated]}))
    body = json.loads(request.read_text())
    body.pop('decisions')
    write(request, body)
    repeated = json.loads(subprocess.check_output(command, text=True))
    assert repeated == {'complete': True, 'path': str(output), 'unchanged': True, 'pending': []}
    assert (output.read_bytes(), output.stat().st_mtime_ns) == original
    body['condition_context'] = {'return_value': 3}
    write(request, body)
    pending = json.loads(subprocess.check_output(command, text=True))
    assert not pending['complete'] and pending['path'] is None
    assert (output.read_bytes(), output.stat().st_mtime_ns) == original


@pytest.mark.parametrize('change', [
    {'applicable': 'yes'}, {'paths': ['outside.py']}, {'reason': ''},
    {'applicable': False, 'paths': ['code.py']}, {'applicable': True, 'paths': []},
])
def test_handoff_rejects_unscoped_or_ambiguous_decisions(workflow, change):
    checkout, preference_id, store = workflow
    with pytest.raises(ValueError):
        preferences.handoff(checkout, 'review', ['code.py'], {}, decisions=[{**decision(preference_id), **change}])


def test_settings_facade_preserves_domain_precedence_and_reads_identity_once(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, 'identities', lambda *args: calls.append(args) or CHAIN)
    write(config.path(), {'skills': {'cleanup': 'team:cleanup'}})
    write(config.path(REPO), {'incoming_review': {'post': False}})
    write(settings.domain_path('rules', 'user'), {'rules': [{'text': 'Global rule'}]})
    write(settings.domain_path('rules', CHAIN[1]), {'rules': [{'text': 'Upstream rule'}]})
    write(settings.domain_path('drafting', 'user'), {'commit_message': 'team:commit', 'issue': 'team:issue'})
    write(settings.domain_path('drafting', CHAIN[0]), {'issue': 'repo:issue'})
    write(commit_policy.path(CHAIN[1]), {'prompt': 'Keep logical commits.'})
    loaded = settings.load_sections(REPO, checkout='.', pr=PR,
                                    sections=['workflow', 'rules', 'drafting', 'completion', 'commit-policy', 'pr-size', 'ci-exceptions', 'pr-partition'])
    assert len(calls) == 1
    assert loaded['workflow'] == config.effective(REPO)
    assert loaded['rules']['source'] == CHAIN[1]
    assert loaded['drafting']['value'] == {'commit_message': 'team:commit', 'issue': 'repo:issue'}
    assert loaded['commit-policy']['source'] == CHAIN[1]
    assert loaded['pr-size'] == pr_size.load(CHAIN)
    assert loaded['ci-exceptions'] == ci_exceptions.load('remote:' + REPO)
    assert loaded['pr-partition'] == pr_partition_policy.load(PR)
    assert loaded['completion']['source'] == 'default'


def test_workflow_only_facade_has_no_identity_discovery_or_snapshot(monkeypatch):
    def unexpected(*args):
        raise AssertionError('Workflow reads do not need PR identity discovery')
    monkeypatch.setattr(settings, 'identities', unexpected)
    assert settings.load_sections(REPO) == {'workflow': config.effective(REPO)}
    assert not config.path().parent.exists()
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPO, 'show', '--section', 'workflow']
    assert json.loads(subprocess.check_output(command, text=True)) == settings.load_sections(REPO)
    assert not config.path().parent.exists()


def test_malformed_selected_setting_is_not_hidden_by_facade(monkeypatch):
    monkeypatch.setattr(settings, 'identities', lambda *args: CHAIN)
    source = settings.domain_path('completion', 'user')
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('')
    with pytest.raises(ValueError, match='Malformed completion'):
        settings.load_sections(REPO, sections=['completion'])
