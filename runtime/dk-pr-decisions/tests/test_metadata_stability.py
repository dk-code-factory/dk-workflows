"""Fresh-contract regressions for identity, durable evidence and competing writers."""
import copy
import importlib.util
import json
import multiprocessing
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import pytest

from preparation_fixtures import Package, analysis, descriptor, prep, response, retain
from test_preparation import module
from shared import review_coverage, review_sessions
from shared.command_results import classification
from shared.engineering_identity import engineering_id
from shared.private_state import read, write

api = module('metadata_decisions', 'dk-pr-decisions/scripts/decisions.py')
state_api = module('metadata_run', 'dk-pr-iter/scripts/run_state.py')
runner = module('metadata_runner', 'dk-pr-review/scripts/run_reviewers.py')
completion = module('metadata_completion', 'dk-pr-iter/scripts/completion.py')


def state(url='https://github.com/o/r/pull/1'):
    return {'version': 2, 'pr': {'url': url, 'number': 1, 'headRefOid': 'a' * 40,
            'title': 'Fixture', 'body': 'Scope'}, 'decisions': [], 'manual_decisions': [],
            'comments': {}, 'threads': {}, 'summaries': {}, 'publication_history': []}


def competing_writer(path, ready, proceed, queue):
    current = read(Path(path), {})
    current['decisions'].append({'text': 'Stale choice', 'references': []})
    ready.set()
    assert proceed.wait(10)
    try:
        api.save_state(current, Path(path))
        queue.put({'error': 'stale writer committed'})
    except api.MetadataConflict as exc:
        queue.put({'code': exc.code, 'proposal': exc.proposal_path, 'action': exc.action})


def test_stale_process_preserves_choices_intents_and_history(tmp_path):
    path = tmp_path / 'decisions.json'
    initial = state()
    api.save_state(initial, path)
    ctx = multiprocessing.get_context('fork')
    ready, proceed, queue = ctx.Event(), ctx.Event(), ctx.Queue()
    process = ctx.Process(target=competing_writer, args=(str(path), ready, proceed, queue))
    process.start()
    try:
        assert ready.wait(10)
        current = read(path, {})
        current['decisions'] = [{'text': 'Winning choice', 'references': []}]
        current['publication_intent'] = {'id': 'intent', 'operation_id': 'owner', 'status': 'pending'}
        current['publication_history'] = [dict(current['publication_intent'])]
        api.save_state(current, path)
        committed = path.read_bytes()
        proceed.set()
        result = queue.get(timeout=10)
        process.join(10)
        assert process.exitcode == 0
        assert result['code'] == 'metadata_conflict'
        assert result['action']['kind'] == 'command'
        assert result['action']['argv'][-2:] == ['publish', '--reconcile']
        assert result['proposal'] in result['action']['artifacts']
        assert path.read_bytes() == committed
        proposal = read(Path(result['proposal']), {})
        assert proposal['proposal']['decisions'][0]['text'] == 'Stale choice'
        assert proposal['current_revision'] == 2 and proposal['expected_revision'] == 1
        assert proposal['next_action']
        with pytest.raises(api.MetadataConflict):
            api.save_state(read(path, {}), path)
        assert path.read_bytes() == committed
    finally:
        proceed.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join()


@pytest.mark.parametrize('applied', [False, True])
def test_publication_interruption_requires_explicit_reconciliation_without_replay(tmp_path, applied):
    path = tmp_path / 'state.json'
    current = state()
    current['decisions'] = [{'text': 'Preserve API', 'references': []}]
    api.save_state(current, path)
    live = {**current['pr']}
    mutations = []
    def github(args):
        if args[0] == 'api':
            mutations.append(args)
            if applied:
                live['body'] = args[-1][5:]
            raise KeyboardInterrupt
        return dict(live)
    with patch.object(api, 'gh_json', side_effect=github):
        with pytest.raises(KeyboardInterrupt):
            api.publish(current, current['decisions'], persist=lambda: api.save_state(current, path))
        pending = read(path, {})
        assert pending['publication_intent']['status'] == 'pending'
        assert pending['publication_history'][0]['status'] == 'pending'
        with pytest.raises(api.MetadataConflict):
            api.save_state(pending, path)
        recovered = read(path, {})
        result = api.publish(recovered, recovered['decisions'], reconcile=True,
                             persist=lambda: api.save_state(recovered, path))
    assert len(mutations) == 1
    assert not result['updated']
    saved = read(path, {})
    assert saved['publication_intent']['status'] == ('verified' if applied else 'not_applied')
    assert saved['publication_history'][0] == saved['publication_intent']
    assert saved['revision'] > pending['revision']


def test_legacy_decisions_import_leaves_pending_original_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    path = api.cache_path('https://github.com/o/r/pull/1')
    legacy = tmp_path / 'dk-pr-decisions/github.com/o/r/pr-1.json'
    current = state()
    current['publication_intent'] = {'id': 'uncertain', 'status': 'pending',
                                     'body': 'Scope with the pending choice', 'snapshot': dict(current['pr'])}
    legacy.parent.mkdir(parents=True, exist_ok=True)
    write(legacy, current)
    before = legacy.read_bytes()
    loaded, target = api.load_cache(current['pr']['url'])
    assert target == path and loaded['revision'] == 1
    assert legacy.read_bytes() == before
    with pytest.raises(api.MetadataConflict):
        api.save_state(loaded, target)
    assert loaded['publication_intent']['id'] == 'uncertain'


def test_conflict_envelope_is_a_workflow_block(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'state.json'
    current = state()
    api.save_state(current, path)
    stale = copy.deepcopy(current)
    api.save_state(current, path)
    monkeypatch.setattr(api, 'cli', lambda: api.save_state(stale, path))
    assert api.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result['schema_version'] == 2 and result['status'] == 'error'
    assert result['error']['code'] == 'metadata_conflict'
    assert Path(result['error']['proposal_path']).exists()
    assert classification('decisions', 1, result)[0:2] == ('blocked', 'metadata_conflict')


def test_full_workflow_operational_refresh_resume_and_completion(tmp_path, monkeypatch):
    package = Package(tmp_path)
    names = ['example', 'second']
    package.manifest['mode'] = 'iteration'
    package.manifest['evidence']['reviewers'].append(str(package.write('second.json', descriptor('second', package.repo / 'transform.py'))))
    history = {'preparation_history_version': 1, 'governing': {'questions': []}, 'operational': {'cycles': 0}}
    package.manifest['evidence']['run_history'] = [str(package.write('history.json', history))]
    package.write('manifest.json', package.manifest)
    first = package.prepare(analysis(names))
    prepared = prep.read(first['artifact'])
    record, record_path = {}, tmp_path / 'run.json'
    state_api.start_run(record)
    state_api.attach_preparation(record, first['artifact'], package.context['url'], package.head)
    state_api.next_cycle(record, package.head)
    state_api.classify(record, 'feedback', package.head, 'valid', 'source', 'Confirmed')
    state_api.assess(record, 'feedback', package.head, first['preparation_id'], 'within_scope', 'source', 'Local repair')
    binding = state_api.review_session(record, package.head, record_path)
    write(record_path, record)
    monkeypatch.setattr(runner, 'describe_reviewer', lambda name: prepared['reviewers'][name])
    monkeypatch.setattr(runner, 'skill_path', lambda name: package.repo / 'transform.py')
    session = review_sessions.Session(package.context, prepared, names, path=binding['session_file'])
    with session.execution(), patch.object(runner, 'run_skill_reviewer', side_effect=[{'findings': []}, KeyboardInterrupt]):
        with pytest.raises(KeyboardInterrupt):
            runner.review(package.context, names, '', package.decisions, '', prepared, session=session)
    old = read(session.path, {})
    checkpoint = Path(old['reviews'][names[0]]['checkpoint']['path'])
    checkpoint_bytes = checkpoint.read_bytes()
    check = {'satisfied': True, 'rationale': 'Inspected supported transform and evidence',
             'evidence': [{'path': prepared['sources']['production/0']['snapshot'], 'sha256': prepared['sources']['production/0']['sha256']}]}
    verification = package.write('verification.json', {'verification_version': 2,
        'preparation_id': first['preparation_id'], 'engineering_id': first['engineering_id'],
        'pr_url': package.context['url'], 'head_sha': package.head,
        'checks': {key: check for key in ('behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity')}})
    history['operational']['cycles'] = 1
    package.write('history.json', history)
    with patch.object(prep, 'analyze') as model:
        fresh = prep.prepare(package.manifest_path, package.versions, previous_preparation=first['artifact'])
    model.assert_not_called()
    assert first['preparation_id'] != fresh['preparation_id']
    assert first['engineering_id'] == fresh['engineering_id']
    prepared = prep.read(fresh['artifact'])
    state_api.attach_preparation(record, fresh['artifact'], package.context['url'], package.head)
    assert state_api.fix_plan(record, 'feedback', package.head)['action'] == 'fix'
    assert record['assessments'][0]['preparation_id'] == first['preparation_id']
    resumed = review_sessions.Session(package.context, prepared, names, path=session.path, resume=True)
    with resumed.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as calls:
        result = runner.review(package.context, names, '', package.decisions, '', prepared, session=resumed)
        item = resumed.finish(result)
    assert calls.call_count == 1 and result['reused_reviewers'] == names[:1]
    assert checkpoint.read_bytes() == checkpoint_bytes
    assert read(session.path, {})['review_history'][:len(old['review_history'])] == old['review_history']
    state_api.register_review(record, {'review_artifact': item}, package.head)
    write(record_path, record)
    assert review_coverage.load(item)['version'] == 3
    assert read(session.path, {})['session_version'] == 2
    assert prep.verify_completion(verification, prepared)['preparation_id'] == first['preparation_id']
    assert completion.completion_gate(package.head, package.head, package.head, True, False, 'success',
        stage='review', preparation=fresh['artifact'], run_record=record_path,
        pr_url=package.context['url'], body='', verification=verification) == 'clean'
    assert len(read(record_path, {})['cycles']) == 1
    # A completed immutable result and the earlier verification survive another counter refresh.
    history['operational']['cycles'] = 2
    history['operational']['publication_receipts'] = [{'kind': 'ci-rerun', 'head_sha': package.head,
        'outcome': 'verified', 'artifact': str(tmp_path / 'retained-operation.json')}]
    package.write('history.json', history)
    final = prep.prepare(package.manifest_path, package.versions, previous_preparation=fresh['artifact'])
    assert final['engineering_id'] == first['engineering_id']
    assert final['refresh']['model_call_count'] == 0
    state_api.attach_preparation(record, final['artifact'], package.context['url'], package.head)
    write(record_path, record)
    assert state_api.verify_review(record_path, prep.read(final['artifact']))['preparation_id'] == fresh['preparation_id']
    assert completion.completion_gate(package.head, package.head, package.head, True, False, 'success',
        stage='review', preparation=final['artifact'], run_record=record_path,
        pr_url=package.context['url'], body='', verification=verification) == 'clean'
    assert len(record['cycles']) == 1
    before_stage = copy.deepcopy(record)
    assert state_api.next_stage(record, package.head, record_path)['stage'] == 'engineering_verification'
    assert state_api.next_stage(record, package.head, record_path, verification=verification)['stage'] == 'cleanup'
    assert record == before_stage


def test_durable_captures_survive_export_and_checkout_removal_live_rules_still_block(tmp_path):
    package = Package(tmp_path)
    # This fixture's descriptor is a live instruction; retain it separately from the disposable checkout.
    instruction = package.write('instruction.json', {'procedure': 'Review the supported transform'})
    package.write('reviewer.json', descriptor('example', instruction))
    package.manifest['source_modes'] = {name + '/0': 'capture' for name in prep.EVIDENCE
                                       if name not in {'reviewers', 'rules', 'user_instructions'}}
    package.write('manifest.json', package.manifest)
    result = package.prepare()
    prepared = prep.read(result['artifact'])
    live_paths = {s['path'] for s in prepared['sources'].values() if s['mode'] == 'live'}
    for key, source in prepared['sources'].items():
        if source['mode'] == 'capture' and source['path'] not in live_paths:
            Path(source['path']).unlink(missing_ok=True)
    shutil.rmtree(package.repo)
    assert prep.gate(result['artifact'], pr_url=package.context['url'], head_sha=package.head) == prepared
    session = review_sessions.Session(package.context, prepared, ['example'])
    assert session.path.is_relative_to(Path(prepared['workflow_dir']))
    refreshed = prep.prepare(prepared['recovery_manifest']['path'], package.versions, previous_preparation=result['artifact'])
    assert refreshed['refresh']['model_call_count'] == 0
    assert refreshed['engineering_id'] == result['engineering_id']
    review_sessions.Session(prep.context_for(prep.read(refreshed['artifact'])), prep.read(refreshed['artifact']),
                            ['example'], path=session.path, resume=True)
    instruction.unlink()
    with pytest.raises((ValueError, OSError)):
        prep.gate(result['artifact'], pr_url=package.context['url'], head_sha=package.head)


@pytest.mark.parametrize('category', ['issues', 'choices', 'user_instructions', 'rules', 'questions', 'feedback', 'run_history'])
def test_each_governing_source_invalidates_interrupted_reuse(tmp_path, category):
    package = Package(tmp_path)
    source = package.write('governing.json', {'verified_absence': True, 'reason': 'No initial obligation'})
    package.manifest['evidence'][category] = [str(source)]
    package.write('manifest.json', package.manifest)
    first = package.prepare()
    old = prep.read(first['artifact'])
    session = review_sessions.Session(package.context, old, ['example'])
    session.record('example', 'completed', {'findings': []})
    before = session.path.read_bytes()
    package.write('governing.json', {'verified_absence': True, 'reason': 'Changed governing obligation'})
    fresh = prep.prepare(package.manifest_path, package.versions, previous_preparation=first['artifact'],
                         analyzer=lambda *_: retain(old, unmapped=['source:' + category + '/0'] if category in {'feedback', 'run_history'} else []))
    assert fresh['engineering_id'] != first['engineering_id']
    with pytest.raises(ValueError, match='changed evidence'):
        review_sessions.Session(package.context, prep.read(fresh['artifact']), ['example'], path=session.path, resume=True)
    assert session.path.read_bytes() == before


def test_unknown_fields_govern_reports_and_verified_lineage_do_not(tmp_path):
    package = Package(tmp_path)
    prepared = prep.read(package.prepare()['artifact'])
    expected = engineering_id(prepared)
    for key in ('refresh', 'parent'):
        operational = copy.deepcopy(prepared)
        operational[key] = {'bookkeeping': 'changed'}
        assert engineering_id(operational) == expected
    unknown = copy.deepcopy(prepared)
    unknown['new_constraint'] = 'Must govern'
    assert engineering_id(unknown) != expected
    unknown = copy.deepcopy(prepared)
    unknown['sources']['rules/0']['new_constraint'] = 'Must govern'
    assert engineering_id(unknown) != expected


def test_legacy_active_runs_are_untouched_and_terminal_history_survives(tmp_path):
    record = {'iteration_run': {'id': 'legacy', 'status': 'active'}, 'cycles': [{'number': 5, 'head': 'a' * 40}],
              'events': [{'kind': 'published'}], 'local_decisions': [{'text': 'Keep API', 'source': 'user'}]}
    before = copy.deepcopy(record)
    with pytest.raises(ValueError, match='retained runtime'):
        state_api.start_run(record, resume=True)
    assert record == before
    record['iteration_run']['status'] = 'completed'
    state_api.start_run(record)
    assert record['iteration_run']['contract_version'] == 2
    assert record['run_history'][0]['cycles'] == before['cycles']
    assert record['local_decisions'] == before['local_decisions']
    assert record['events'] == before['events']


def test_invalid_retained_binding_blocks_before_consuming_another_cycle(tmp_path):
    from shared import bindings
    from shared.runtime_bundles import retained_binding
    provider = tmp_path / 'provider.py'
    provider.write_text('print("review fixture")\n')
    selected = {'command': [sys.executable, str(provider), '{handoff}']}
    name = bindings.key(selected)
    retained = retained_binding({'binding': selected, 'identity': name, 'executor': 'command'})
    (tmp_path / 'evidence').mkdir()
    package = Package(tmp_path / 'evidence')
    package.manifest['mode'] = 'iteration'
    procedure = Path(retained['argv'][1])
    value = descriptor(name, procedure)
    value['execution'] = retained
    package.write('reviewer.json', value)
    package.write('manifest.json', package.manifest)
    result = package.prepare(analysis([name]))
    record = {}
    state_api.start_run(record)
    state_api.attach_preparation(record, result['artifact'], package.context['url'], package.head)
    state_api.next_cycle(record, package.head)
    before = copy.deepcopy(record)
    procedure.chmod(0o600)
    procedure.write_text('print("tampered")\n')
    with pytest.raises((ValueError, RuntimeError)):
        state_api.next_cycle(record, package.head)
    assert record == before and len(record['cycles']) == 1


def test_conflict_during_refresh_retains_the_requested_choice(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'state.json'
    current = state()
    api.save_state(current, path)
    stale = copy.deepcopy(current)
    api.save_state(current, path)
    request = {'text': 'Keep the selected API', 'references': []}
    monkeypatch.setattr(sys, 'argv', ['decisions.py', '--pr', current['pr']['url'], 'add',
                        '--json', json.dumps(request), '--user-instruction', 'Keep the API'])
    monkeypatch.setattr(api, 'scan', lambda *_: api.save_state(stale, path))
    assert api.main() == 1
    result = json.loads(capsys.readouterr().out)
    proposal = read(Path(result['error']['proposal_path']), {})
    assert json.loads(proposal['request']['json']) == request
    assert proposal['request']['user_instruction'] == 'Keep the API'


def test_unknown_prior_evidence_cannot_hide_as_lineage_bookkeeping(tmp_path):
    package = Package(tmp_path)
    first = package.prepare()
    parent = prep.read(first['artifact'])
    package.manifest['evidence']['prior_preparation'] = [str(package.write('prior.json', {
        'verified_absence': True, 'reason': 'No prior run', 'unresolved_constraint': 'Preserve compatibility'}))]
    package.write('manifest.json', package.manifest)
    fresh = prep.prepare(package.manifest_path, package.versions, previous_preparation=first['artifact'],
                         analyzer=lambda *_: retain(parent, unmapped=['source:prior_preparation/0']))
    assert fresh['engineering_id'] != first['engineering_id']


def test_artifact_storage_and_verified_lineage_do_not_change_engineering_identity(tmp_path):
    package = Package(tmp_path)
    first = package.prepare()
    second = package.prepare()
    assert second['preparation_id'] != first['preparation_id']
    assert second['engineering_id'] == first['engineering_id']
    package.manifest['evidence']['prior_preparation'] = [first['artifact']]
    package.write('manifest.json', package.manifest)
    grown = prep.prepare(package.manifest_path, package.versions, previous_preparation=first['artifact'])
    assert grown['refresh']['model_call_count'] == 0
    assert grown['engineering_id'] == first['engineering_id']
