"""Offline layout admission, native ownership and crash/reuse boundaries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import shutil

import pytest

from test_config_requests import service, preview, admit, operation, relation, REPO, CI
import config
import config_requests as requests
import config_upgrades as upgrades
from shared.private_state import write

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def layout(tmp_path, monkeypatch):
    path = tmp_path/'layout.json'
    write(path, {'revision': 1})
    monkeypatch.setattr(upgrades, 'LAYOUT', path)
    return path


def bump(layout, revision=2):
    write(layout, {'revision': revision})


def reserve(service, layout):
    bump(layout)
    store, context = service
    result = store.upgrade_start(context)
    store.upgrade_bind(result['id'], result['claim_token'], 'fixture-native-agent')
    return result['id'], result['claim_token']


def prepare(store, request_id, token, ops=(), *, dispositions=None, unresolved=()):
    proposal = {'operations': list(ops)}
    result = store.prepare(request_id, proposal, claim_token=token)
    lineage = []
    for old in result['candidates']:
        choice, targets = (dispositions or {}).get(old['id'], ('preserved', []))
        lineage.append({'request': old['request'], 'decision': old['id'],
                        'disposition': choice, 'targets': targets,
                        'evidence': 'Original source ask; synthetic native interpretation.'})
    proposal.update(lineage=lineage, assessment={
        'fingerprint': result['fingerprint'], 'summary': 'Synthetic native interpretation of every active ask.',
        'reviewed': [c['request'] + '/' + c['id'] for c in result['candidates']],
        'relations': [], 'unresolved': list(unresolved)})
    return store.prepare(request_id, proposal, claim_token=token), proposal


def child(layout, expression, context):
    code = ('import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
            'import config_upgrades as u; u.LAYOUT=Path(sys.argv[2]); '
            'from config_requests import Requests; s=Requests(); c=json.loads(sys.argv[3]); '
            + expression)
    return [sys.executable, '-c', code, str(ROOT/'runtime'), str(layout), json.dumps(context)]


def test_baseline_installation_and_unchanged_reads_have_no_side_effects(service, layout):
    store, context = service
    for _ in range(2):
        assert store.upgrade_check(context) == {'status': 'ready', 'layout_revision': 1}
        assert store.upgrade_start(context)['status'] == 'ready'
        config.effective(REPO)
    assert not store.root.exists()
    assert not (store.root.parent/'configuration-history.lock').exists()


def test_native_reservation_binding_and_stale_owner_rejection(service, layout):
    store, context = service
    bump(layout)
    started = store.upgrade_start(context)
    request_id, token = started['id'], started['claim_token']
    assert started['delegation'] == 'reserved'
    joined = store.upgrade_start(context)
    assert joined['id'] == request_id and joined['status'] == 'waiting'
    assert 'claim_token' not in joined and 'handoff' not in joined
    with pytest.raises(ValueError, match='Bind'):
        prepare(store, request_id, token)
    store.upgrade_bind(request_id, token, 'agent-old')
    with pytest.raises(ValueError, match='cannot act'):
        store.upgrade_recover(request_id, token, {'binding': 'agent-old', 'state': 'timeout', 'reference': 'observation'})
    recovered = store.upgrade_recover(request_id, token, {
        'binding': 'agent-old', 'state': 'terminated', 'reference': 'fixture-host-terminal'})
    for action in [lambda: store.prepare(request_id, {'operations': []}, claim_token=token),
                   lambda: store.clarify(request_id, 'answer', claim_token=token),
                   lambda: store.cancel(request_id, 'cancel', claim_token=token),
                   lambda: store.apply(request_id, claim_token=token),
                   lambda: store.upgrade_bind(request_id, token, 'agent-stale')]:
        with pytest.raises(ValueError, match='claim token'):
            action()
    token = recovered['claim_token']
    store.upgrade_bind(request_id, token, 'agent-new')
    prepare(store, request_id, token)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert store.upgrade_check(context)['status'] == 'ready'


def test_unbound_interruption_needs_native_no_launch_evidence(service, layout):
    store, context = service
    bump(layout)
    result = store.upgrade_start(context)
    ownership = requests.Requests().show(result['id'], 'ownership')
    recovered = store.upgrade_recover(result['id'], ownership['claim_token'], {
        'binding': None, 'state': 'not_started', 'reference': 'fixture-native-no-launch'})
    assert recovered['claim_token'] != result['claim_token']
    assert requests.Requests().resume(result['id'])['upgrade']['action'] == 'reserved'


def test_subprocess_races_share_one_owner_across_repositories(service, layout):
    store, context = service
    bump(layout)
    commands = [child(layout, 'print(json.dumps(s.upgrade_start(c)))',
                      {**context, 'repository': f'github.com/owner/repo-{i % 3}'}) for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda cmd: json.loads(subprocess.check_output(cmd, text=True, timeout=20)), commands))
    assert len({r['id'] for r in results}) == 1
    assert sum(r.get('delegation') == 'reserved' for r in results) == 1
    assert len(store._records()) == 1


def test_fresh_session_reuses_admission_and_completed_revision(service, layout):
    store, context = service
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token)
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    result = json.loads(subprocess.check_output(child(layout,
        f'print(json.dumps(s.resume({request_id!r})))', context), text=True, timeout=20))
    assert result['admission'] == 'reusable'
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    store.apply(request_id, claim_token=token)
    result = json.loads(subprocess.check_output(child(layout,
        'print(json.dumps(s.upgrade_start(c)))', context), text=True, timeout=20))
    assert result['status'] == 'ready' and len(store._records()) == 1
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    # User scope is reusable, but a new repository requires its own admission.
    other = store.upgrade_check({**context, 'repository': 'github.com/other/project'})
    assert other['scopes'] == ['repo:github.com/other/project']


def test_renamed_split_and_removed_settings_use_original_intent(service, layout, monkeypatch):
    store, context = service
    original_value = {'extensions': {'old_limit': 41, 'removed': True, 'unrecorded': 'keep'}}
    # Unrecorded settings remain solely in the live store.
    write(config.path(REPO), {'extensions': {'unrecorded': 'keep'}})
    original, ops, result = preview(service, 'Use 41 for both limits; retire the old toggle when redundant.',
                                    [{'domain': 'workflow', 'action': 'set', 'value': original_value}])
    admit(store, original, ops, result)
    store.apply(original)
    historical = store._path(original).read_bytes()
    request_id, token = reserve(service, layout)
    validate = config.validate
    def new_layout(value, **kwargs):
        if set(value.get('extensions', {})) & {'old_limit', 'removed'}:
            raise ValueError('Obsolete layout')
        return validate(value, **kwargs)
    monkeypatch.setattr(config, 'validate', new_layout)
    ops = [{'domain': 'workflow', 'action': 'set', 'value': {'extensions': {
        'connection_limit': 41, 'request_limit': 41, 'unrecorded': 'keep'}}}]
    result = store.prepare(request_id, {'operations': ops}, claim_token=token)
    targets = [d['id'] for d in result['decisions'] if d['after']['present']]
    dispositions = {c['id']: ('translated', targets) if c['path'][-1] == 'old_limit' else ('retired', [])
                    for c in result['candidates']}
    prepare(store, request_id, token, ops, dispositions=dispositions)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert store._path(original).read_bytes() == historical
    assert json.loads(config.path(REPO).read_text()) == ops[0]['value']
    assert 'unrecorded' not in store._path(request_id).read_text()
    active = store.candidates({'id': '', 'context': context})
    assert all(c['request'] != original for c in active)
    assert any(c.get('origins') for c in active)


def test_partial_supersession_masks_removal_and_scoped_exception(service, layout):
    store, context = service
    first = operation(scope='user')
    first['value']['poll_interval_seconds'] = 3
    old, ops, result = preview(service, operations=[first])
    admit(store, old, ops, result); store.apply(old)
    second, ops, result = preview(service, 'Use repository timeout 52.', [operation(52)])
    user_timeout = next(c for c in result['candidates'] if c['path'][-1] == 'timeout_seconds')
    admit(store, second, ops, result, relations=[relation(user_timeout, result, 'exception')])
    store.apply(second)
    third, ops, result = preview(service, 'Remove the user timeout; retain user polling.', [
        {'domain': 'skill', 'action': 'set', 'scope': 'user', 'skill': CI, 'value': {'poll_interval_seconds': 3}}])
    old_timeout = next(c for c in result['candidates'] if c['request'] == old and c['path'][-1] == 'timeout_seconds')
    repo_timeout = next(c for c in result['candidates'] if c['request'] == second)
    admit(store, third, ops, result, relations=[relation(old_timeout, result), relation(repo_timeout, result, 'preserve')])
    store.apply(third)
    request_id, token = reserve(service, layout)
    sources = store.show(request_id, 'sources')['sources']
    original = next(s for s in sources if s['request'] == old)
    assert len(original['active']) == 1 and original['active'][0]['path'][-1] == 'poll_interval_seconds'
    assert len(original['masks']) == 1
    assert any(c['request'] == third and c['after'] == {'present': False}
               for s in sources for c in s['active'])
    prepare(store, request_id, token)
    store.apply(request_id, claim_token=token)


def test_unexplained_current_values_require_clarification(service, layout):
    store, context = service
    old, ops, result = preview(service)
    admit(store, old, ops, result); store.apply(old)
    write(config.path(REPO), {'skill_defaults': {CI: {'options': {'timeout_seconds': 99}}}})
    request_id, token = reserve(service, layout)
    with pytest.raises(ValueError, match='clarification required'):
        prepare(store, request_id, token)
    prepare(store, request_id, token, unresolved=['Was 99 seconds an intentional later choice?'])
    assert store.resume(request_id)['status'] == 'clarification'
    store.clarify(request_id, 'Keep 99 seconds; it was a later manual choice.', claim_token=token)
    prepare(store, request_id, token)
    store.apply(request_id, claim_token=token)
    assert json.loads(config.path(REPO).read_text())['skill_defaults'][CI]['options']['timeout_seconds'] == 99


@pytest.mark.parametrize('change', ['values', 'instructions', 'schema', 'behavior', 'contract'])
def test_relevant_inputs_invalidate_admission_without_erasing_it(service, layout, monkeypatch, change):
    store, context = service
    if change in {'schema', 'behavior', 'contract'}:
        # Use the real discovery and role/schema readers against an isolated
        # installation, never mutate this checkout or inject fingerprint inputs.
        import skill_config
        plugin = layout.parent/'plugin'
        shutil.copytree(ROOT/'codex', plugin/'codex')
        shutil.copytree(ROOT/'docs', plugin/'docs')
        (plugin/'runtime').mkdir()
        for name in ('config_adapters.py', 'config_upgrades.py'):
            shutil.copy2(ROOT/'runtime'/name, plugin/'runtime'/name)
        monkeypatch.setattr(requests.adapters, '__file__', str(plugin/'runtime/config_adapters.py'))
        monkeypatch.setattr(upgrades, '__file__', str(plugin/'runtime/config_upgrades.py'))
        monkeypatch.setattr(skill_config, 'DIRECTORY', plugin/'codex/skills')
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token)
    if change == 'values':
        write(config.path(REPO), {'extensions': {'later': True}})
    elif change == 'instructions':
        (Path(context['checkout'])/'AGENTS.md').write_text('Changed governing instruction.')
    else:
        target = {'schema': plugin/'codex/skills/dk-ci/config.schema.json',
                  'behavior': plugin/'codex/skills/dk-config/prompts/configuration-agent.md',
                  'contract': plugin/'docs/configuration-upgrades.md'}[change]
        if change == 'schema':
            value = json.loads(target.read_text())
            value['description'] = 'Changed target schema guidance.'
            write(target, value)
        else:
            target.write_text(target.read_text() + '\nChanged governing admission requirement.\n')
    assert store.resume(request_id)['admission'] == 'stale'
    with pytest.raises(ValueError, match='changed'):
        store.apply(request_id, claim_token=token)
    assert store._read(request_id)['assessment'] is not None


def test_partial_writes_and_missing_receipt_preserve_siblings_and_barrier(service, layout, monkeypatch):
    store, context = service
    request_id, token = reserve(service, layout)
    ops = [operation(), {'domain': 'completion', 'action': 'set', 'scope': 'user', 'value': 'Finish requested work.'}]
    prepare(store, request_id, token, ops)
    atomic = requests.atomic_text
    writes = []
    def interrupted(path, raw):
        atomic(path, raw); writes.append(path)
        if len(writes) == 2:
            raise OSError('Interrupted after second write before receipt')
    monkeypatch.setattr(requests, 'atomic_text', interrupted)
    with pytest.raises(OSError):
        store.apply(request_id, claim_token=token)
    progress = deepcopy(store._read(request_id)['progress'])
    assert progress[0]['receipt'] and progress[1]['intent'] and not progress[1].get('receipt')
    with pytest.raises(ValueError, match='Configuration upgrade'):
        config.effective(REPO)
    monkeypatch.setattr(requests, 'atomic_text', lambda *_: pytest.fail('Repeated write'))
    assert requests.Requests().apply(request_id, claim_token=token)['status'] == 'applied'
    assert store._read(request_id)['progress'][0] == progress[0]
    assert config.effective(REPO)['settings']['skill_defaults'][CI]['options']['timeout_seconds'] == 41


def test_prepared_writers_and_readers_cannot_cross_upgrade_barrier(service, layout):
    store, context = service
    old, ops, result = preview(service)
    admit(store, old, ops, result)
    request_id, token = reserve(service, layout)
    for action in [lambda: store.apply(old), lambda: store.prepare(old, {'operations': ops}),
                   lambda: store.start('Other change.', context), lambda: config.effective(REPO)]:
        with pytest.raises(ValueError, match='[Bb]arrier|Configuration upgrade'):
            action()
    prepare(store, request_id, token)
    store.apply(request_id, claim_token=token)
    with pytest.raises(ValueError, match='layout changed'):
        store.apply(old)


def test_corruption_newer_layout_and_invalid_bootstrap_block(service, layout):
    store, context = service
    write(config.path(REPO), {'skill_defaults': {'removed:skill': {'options': {'old': 4}}}})
    request_id, token = reserve(service, layout)
    # Bootstrap can resolve its valid role without validating unrelated old keys.
    assert store.resume(request_id)['upgrade']['binding'] == 'fixture-native-agent'
    store.cancel(request_id, 'Fixture cancellation', claim_token=token)
    write(config.path(REPO), {'skill_defaults': {'dk-workflows:dk-config': {'behavior': {
        'configuration-agent': {'override': ''}}}}})
    with pytest.raises(ValueError):
        store.upgrade_start(context)
    assert len(store._records()) == 1
    config.path(REPO).write_text('{broken')
    with pytest.raises(ValueError):
        store.upgrade_check(context)
    config.path(REPO).unlink()
    record = store._read(request_id); record['layout_revision'] = 99
    write(store._path(request_id), record)
    assert store.upgrade_check(context)['status'] == 'blocked'


def test_unsupported_legacy_values_cannot_be_silently_preserved(service, layout):
    store, context = service
    write(config.path(REPO), {'unknown_legacy': {'old': True}})
    request_id, token = reserve(service, layout)
    with pytest.raises(ValueError):
        prepare(store, request_id, token)
    assert store._read(request_id)['status'] == 'open'


def test_live_reader_waits_until_all_store_receipts_complete(service, layout, monkeypatch):
    import threading
    import select
    store, context = service
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token, [operation(), {
        'domain': 'completion', 'action': 'set', 'scope': 'user', 'value': 'All stores complete.'}])
    written, release = threading.Event(), threading.Event()
    atomic = requests.atomic_text
    def pause(path, raw):
        atomic(path, raw)
        if not written.is_set():
            written.set()
            assert release.wait(10)
    monkeypatch.setattr(requests, 'atomic_text', pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        applying = pool.submit(store.apply, request_id, claim_token=token)
        # Observe entry into the actual reader lock before allowing the writer
        # to finish. A process launch alone does not establish an overlap.
        instrumentation = ('from contextlib import contextmanager\n'
                           'original_reading = u.reading\n'
                           '@contextmanager\n'
                           'def observed_reading(store):\n'
                           ' print("reader-attempt", flush=True)\n'
                           ' with original_reading(store):\n'
                           '  print("reader-entered", flush=True)\n'
                           '  yield\n'
                           'u.reading = observed_reading\n')
        expression = ('exec(' + repr(instrumentation) + '); import settings; print(json.dumps(settings.load_sections(c["repository"], '
                      'checkout=c["checkout"], sections=["workflow","completion"])))')
        reader = None
        try:
            assert written.wait(10)
            reader = subprocess.Popen(child(layout, expression, context), stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            assert select.select([reader.stdout], [], [], 10)[0], 'Reader did not attempt its lock'
            import os
            initial = os.read(reader.stdout.fileno(), 4096).decode()
            assert initial == 'reader-attempt\n'
            assert not select.select([reader.stdout], [], [], 0.2)[0], 'Reader crossed the in-progress write'
            release.set()
            assert applying.result(timeout=10)['status'] == 'applied'
            output, error = reader.communicate(timeout=10)
            assert reader.returncode == 0, error
            assert output.startswith('reader-entered\n')
            result = json.loads(output.splitlines()[-1])
            assert result['workflow']['settings']['skill_defaults'][CI]['options']['timeout_seconds'] == 41
            assert result['completion']['value']['prompt'] == 'All stores complete.'
        finally:
            release.set()
            if reader is not None and reader.poll() is None:
                reader.kill(); reader.communicate(timeout=10)


def test_partial_upgrade_refresh_keeps_receipts_and_original_lineage(service, layout, monkeypatch):
    store, context = service
    request_id, token = reserve(service, layout)
    ops = [operation(), {'domain': 'completion', 'action': 'set', 'scope': 'user', 'value': 'Finish.'}]
    _, proposal = prepare(store, request_id, token, ops)
    original = requests.atomic_text
    count = 0
    def interrupt(path, raw):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('Interrupted second store')
        original(path, raw)
    monkeypatch.setattr(requests, 'atomic_text', interrupt)
    with pytest.raises(OSError):
        store.apply(request_id, claim_token=token)
    receipt = deepcopy(store._read(request_id)['progress'][0])
    (Path(context['checkout'])/'AGENTS.md').write_text('New governing instruction.')
    assert store.resume(request_id)['admission'] == 'stale'
    preview = store.prepare(request_id, {'operations': 'retained'}, claim_token=token)
    proposal['operations'] = 'retained'
    proposal['assessment']['fingerprint'] = preview['fingerprint']
    store.prepare(request_id, proposal, claim_token=token)
    monkeypatch.setattr(requests, 'atomic_text', original)
    store.apply(request_id, claim_token=token)
    assert store._read(request_id)['progress'][0] == receipt


def test_incompatible_upstream_requires_its_own_checkout(service, layout):
    store, context = service
    context['upstream'] = 'github.com/upstream/project'
    write(config.path(context['upstream']), {'obsolete_upstream': True})
    bump(layout)
    with pytest.raises(ValueError, match='upstream settings require their own checkout'):
        store.upgrade_start(context)
    assert not store.root.exists()


def test_legacy_records_default_to_baseline_and_never_replay(service, layout):
    store, context = service
    old, ops, result = preview(service)
    admit(store, old, ops, result); store.apply(old)
    legacy = store._read(old); legacy.pop('layout_revision')
    write(store._path(old), legacy)
    original = store._path(old).read_bytes()
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token)
    store.apply(request_id, claim_token=token)
    write(config.path(REPO), {'extensions': {'later': True}})
    assert store.apply(old)['status'] == 'applied'
    assert json.loads(config.path(REPO).read_text()) == {'extensions': {'later': True}}
    assert store._path(old).read_bytes() == original


def test_actual_consumer_retains_shared_lock_through_values_read(service, layout, monkeypatch):
    import fcntl
    import threading
    store, context = service
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token)
    store.apply(request_id, claim_token=token)
    write(config.path(REPO), {'extensions': {'old': True}})
    entered, release = threading.Event(), threading.Event()
    original = config.skill_config._scope_record
    def pause(path):
        entered.set()
        assert release.wait(10)
        return original(path)
    monkeypatch.setattr(config.skill_config, '_scope_record', pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(config.effective, REPO, expanded=False)
        try:
            assert entered.wait(10)
            with (store.root.parent/'configuration-history.lock').open('r') as lock:
                # Deterministic competing-writer probe during an actual paused
                # consumer, independent of child scheduling or final values.
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            release.set()
        assert reading.result(timeout=10)['settings']['extensions']['old'] is True


def test_partial_translated_source_refreshes_without_reasking_intent(service, layout, monkeypatch):
    store, context = service
    old, ops, result = preview(service, 'Use the same limit after a layout rename.', [{
        'domain': 'workflow', 'action': 'set', 'value': {'extensions': {'old_limit': 41}}}])
    admit(store, old, ops, result); store.apply(old)
    request_id, token = reserve(service, layout)
    ops = [{'domain': 'workflow', 'action': 'set', 'value': {'extensions': {'new_limit': 41}}},
           {'domain': 'completion', 'action': 'set', 'scope': 'user', 'value': 'Finish.'}]
    result = store.prepare(request_id, {'operations': ops}, claim_token=token)
    target = next(d['id'] for d in result['decisions'] if d['path'] == ['extensions', 'new_limit'])
    _, proposal = prepare(store, request_id, token, ops, dispositions={
        c['id']: ('translated', [target]) for c in result['candidates']})
    atomic = requests.atomic_text
    def interrupt(path, raw):
        if path != config.path(REPO):
            raise OSError('Interrupted after completed translation')
        atomic(path, raw)
    monkeypatch.setattr(requests, 'atomic_text', interrupt)
    with pytest.raises(OSError):
        store.apply(request_id, claim_token=token)
    receipt = deepcopy(store._read(request_id)['progress'][0])
    (Path(context['checkout'])/'AGENTS.md').write_text('New applicable instruction.')
    result = store.prepare(request_id, {'operations': 'retained'}, claim_token=token)
    proposal['operations'] = 'retained'
    proposal['assessment']['fingerprint'] = result['fingerprint']
    store.prepare(request_id, proposal, claim_token=token)
    monkeypatch.setattr(requests, 'atomic_text', atomic)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert store._read(request_id)['progress'][0] == receipt
    assert store._read(request_id)['clarifications'] == []


def test_instruction_source_readers_obey_partial_upgrade_barrier(service, layout, monkeypatch):
    import instruction_sources
    store, context = service
    (Path(context['checkout'])/'AGENTS.md').write_text('Applicable source.')
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token, [{
        'domain': 'instruction-sources', 'action': 'set', 'scope': 'repo',
        'value': {'mode': 'all', 'include': [], 'exclude': []}},
        {'domain': 'completion', 'action': 'set', 'scope': 'user', 'value': 'Finish.'}])
    atomic = requests.atomic_text
    def interrupt(path, raw):
        if path != instruction_sources.policy_path(REPO):
            raise OSError('Interrupted after policy write')
        atomic(path, raw)
    monkeypatch.setattr(requests, 'atomic_text', interrupt)
    with pytest.raises(OSError):
        store.apply(request_id, claim_token=token)
    for read in [lambda: instruction_sources.load(REPO),
                 lambda: instruction_sources.inspect(context['checkout'], REPO),
                 lambda: instruction_sources.read_source(context['checkout'], 'AGENTS.md', identity=REPO),
                 lambda: instruction_sources.binding(context['checkout'], REPO)]:
        with pytest.raises(ValueError, match='Configuration upgrade'):
            read()
    monkeypatch.setattr(requests, 'atomic_text', atomic)
    store.apply(request_id, claim_token=token)
    assert instruction_sources.inspect(context['checkout'], REPO)['status'] == 'ready'


def test_later_revision_references_existing_clarification_without_copying_it(service, layout):
    store, context = service
    old, ops, result = preview(service)
    admit(store, old, ops, result); store.apply(old)
    write(config.path(REPO), {'skill_defaults': {CI: {'options': {'timeout_seconds': 99}}}})
    first, token = reserve(service, layout)
    store.clarify(first, 'Keep the manually chosen 99 seconds.', claim_token=token)
    prepare(store, first, token)
    store.apply(first, claim_token=token)
    bump(layout, 3)
    second = store.upgrade_start(context)
    store.upgrade_bind(second['id'], second['claim_token'], 'next-native-agent')
    references = store.show(second['id'], 'sources')['sources'][0]['clarification_references']
    assert {'request': first, 'section': 'ask'} in references
    prepare(store, second['id'], second['claim_token'])
    for source_id, field in [(old, 'ask'), (first, 'clarifications')]:
        path = store._path(source_id)
        retained = path.read_bytes()
        source = store._read(source_id)
        source[field] = 'Changed original ask.' if field == 'ask' else [{'excerpt': 'Changed earlier answer.'}]
        write(path, source)
        assert store.resume(second['id'])['admission'] == 'stale'
        path.write_bytes(retained)
        assert store.resume(second['id'])['admission'] == 'reusable'
    store.apply(second['id'], claim_token=second['claim_token'])
    assert store._read(second['id'])['clarifications'] == []
    assert 'Keep the manually chosen' not in store._path(second['id']).read_text()


def test_translated_ancestors_and_answers_remain_freshness_inputs(service, layout):
    store, context = service
    original, ops, result = preview(service)
    store.clarify(original, 'The original timeout is intentional.')
    result = store.prepare(original, {'operations': ops})
    admit(store, original, ops, result); store.apply(original)
    first, token = reserve(service, layout)
    ops = [operation(52)]
    result = store.prepare(first, {'operations': ops}, claim_token=token)
    prepare(store, first, token, ops, dispositions={c['id']: ('translated', [result['decisions'][0]['id']])
                                                  for c in result['candidates']})
    store.apply(first, claim_token=token)
    bump(layout, 3)
    second = store.upgrade_start(context)
    store.upgrade_bind(second['id'], second['claim_token'], 'agent-third-layout')
    sources = store.show(second['id'], 'sources')['sources']
    ancestor = next(s for s in sources if s['request'] == original)
    assert ancestor['historical'] and not ancestor['active']
    prepare(store, second['id'], second['claim_token'])
    for field, value in [('ask', 'Changed original ask.'), ('clarifications', [{'excerpt': 'Changed original answer.'}])]:
        path = store._path(original)
        retained = path.read_bytes()
        changed = store._read(original); changed[field] = value
        write(path, changed)
        assert requests.Requests().resume(second['id'])['admission'] == 'stale'
        path.write_bytes(retained)
        assert requests.Requests().resume(second['id'])['admission'] == 'reusable'
    store.apply(second['id'], claim_token=second['claim_token'])


def test_layout_changes_during_application_report_compatible_recovery(service, layout, monkeypatch):
    store, context = service
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token, [operation()])
    atomic = requests.atomic_text
    def interrupt(path, raw):
        atomic(path, raw)
        raise OSError('Interrupted before receipt')
    monkeypatch.setattr(requests, 'atomic_text', interrupt)
    with pytest.raises(OSError):
        store.apply(request_id, claim_token=token)
    bump(layout, 3)
    with pytest.raises(ValueError, match='restore a compatible installation declaring layout 2'):
        store.apply(request_id, claim_token=token)
    with pytest.raises(ValueError, match='[Rr]eceipt|[Uu]ncertain|[Aa]ppl'):
        store.cancel(request_id, 'Cannot discard writes', claim_token=token)
    bump(layout, 2)
    monkeypatch.setattr(requests, 'atomic_text', lambda *_: pytest.fail('Repeated receipted write'))
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    bump(layout, 3)
    assert store.upgrade_check(context)['status'] == 'upgrade_required'


def test_explicit_other_checkout_survives_nested_readers(service, layout, monkeypatch):
    from importlib.util import module_from_spec, spec_from_file_location
    import skill_config
    from shared import repository_scope, pr_size
    store, context = service
    roots = [Path(context['checkout']), layout.parent/'other-checkout']
    for index, checkout in enumerate(roots):
        checkout.mkdir(exist_ok=True)
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        subprocess.run(['git', '-C', str(checkout), 'remote', 'add', 'origin',
                        f'https://github.com/owner/repo-{index}.git'], check=True)
    monkeypatch.setattr(repository_scope, 'upstream', lambda *args: None)
    monkeypatch.chdir(roots[0])
    bump(layout)
    spec = spec_from_file_location('upgrade_pr_config', ROOT/'scripts/pr_config.py')
    cli = module_from_spec(spec)
    spec.loader.exec_module(cli)
    for read in [lambda: cli.main(['--cwd', str(roots[1]), 'pr-size', 'list']),
                 lambda: skill_config.for_context(CI, cwd=roots[1]),
                 lambda: pr_size.effective(roots[1])]:
        with pytest.raises(ValueError) as caught:
            read()
        argv = caught.value.action['argv']
        assert argv[argv.index('--checkout')+1] == str(roots[1])
        assert argv[argv.index('--repository')+1].removeprefix('remote:') == 'github.com/owner/repo-1'
    # A known mismatched checkout cannot create a reservation even when supplied
    # directly instead of through the guarded CLI.
    other = {'repository': 'github.com/owner/repo-1', 'checkout': str(roots[0])}
    assert store.upgrade_start(other)['status'] == 'blocked'
    assert not store.root.exists()
    other['checkout'] = str(roots[1])
    result = store.upgrade_start(other)
    store.upgrade_bind(result['id'], result['claim_token'], 'other-checkout-agent')
    prepare(store, result['id'], result['claim_token'], [
        {'domain': 'pr-size', 'scope': 'repo', 'action': 'set', 'value': {'added_lines_threshold': 123}}])
    store.apply(result['id'], claim_token=result['claim_token'])
    with upgrades.internal():
        assert pr_size.load(['remote:github.com/owner/repo-1'])['settings']['added_lines_threshold']['value'] == 123
        assert not pr_size.path('remote:github.com/owner/repo-0').exists()


def test_obsolete_instruction_policy_validates_replacement_not_live_input(service, layout, monkeypatch):
    import instruction_sources
    store, context = service
    write(instruction_sources.policy_path(REPO), instruction_sources.envelope(REPO, {
        'mode': 'human', 'include': [], 'exclude': []}))
    validate = instruction_sources.validate
    def target(value):
        if value['mode'] == 'human':
            raise ValueError('Obsolete instruction policy mode')
        return validate(value)
    monkeypatch.setattr(instruction_sources, 'validate', target)
    request_id, token = reserve(service, layout)
    store.clarify(request_id, 'Use all source categories in the target layout.', claim_token=token)
    prepare(store, request_id, token, [{'domain': 'instruction-sources', 'scope': 'repo', 'action': 'set',
                                      'value': {'mode': 'all', 'include': [], 'exclude': []}}])
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert instruction_sources.load(REPO)['policy']['mode'] == 'all'


def checkout_remote(path, repository):
    path.mkdir(exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(path)], check=True, timeout=15)
    subprocess.run(['git', '-C', str(path), 'remote', 'add', 'origin',
                    'https://' + repository + '.git'], check=True, timeout=15)


def test_pr_selector_cannot_exempt_an_unrelated_checkout(service, layout, monkeypatch):
    store, context = service
    checkout_remote(Path(context['checkout']), 'github.com/unrelated/project')
    monkeypatch.setattr(requests.adapters, 'identities', lambda *args: ['remote:' + REPO])
    bump(layout)
    result = store.upgrade_start({**context, 'pr': 'https://github.com/owner/repo/pull/1'})
    assert result['status'] == 'blocked' and 'owning checkout' in result['reason']
    assert not store.root.exists()


def test_removed_historical_checkout_does_not_redefine_source_values(service, layout, monkeypatch):
    from shared import repository_scope, pr_size
    store, original_context = service
    checkout_remote(Path(original_context['checkout']), REPO)
    monkeypatch.setattr(repository_scope, 'upstream', lambda *args: None)
    op = {'domain': 'pr-size', 'scope': 'repo', 'action': 'set', 'value': {'added_lines_threshold': 321}}
    original, ops, result = preview(service, 'Use 321 added lines.', [op])
    admit(store, original, ops, result); store.apply(original)
    original_bytes = store._path(original).read_bytes()
    shutil.rmtree(original_context['checkout'])
    replacement = layout.parent/'replacement-checkout'
    checkout_remote(replacement, REPO)
    context = {**original_context, 'checkout': str(replacement)}
    request_id, token = reserve((store, context), layout)
    prepare(store, request_id, token)
    assert store.resume(request_id)['admission'] == 'reusable'
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert store._read(request_id)['clarifications'] == []
    assert store._path(original).read_bytes() == original_bytes
    assert json.loads(pr_size.path('remote:' + REPO).read_text())['pr_size']['added_lines_threshold'] == 321


def test_excluded_source_edits_reuse_admission_but_selected_edits_invalidate(service, layout):
    import instruction_sources
    store, context = service
    checkout = Path(context['checkout'])
    (checkout/'AGENTS.md').write_text('Selected rule.')
    excluded = checkout/'CLAUDE.md'; excluded.write_text('Excluded rule.')
    policy_path = instruction_sources.policy_path(REPO)
    write(policy_path, instruction_sources.envelope(REPO, {'mode': 'all', 'include': [], 'exclude': ['CLAUDE.md']}))
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token)
    excluded.write_text('Changed excluded rule.')
    assert requests.Requests().resume(request_id)['admission'] == 'reusable'
    (checkout/'AGENTS.md').write_text('Changed selected rule.')
    assert store.resume(request_id)['admission'] == 'stale'
    (checkout/'AGENTS.md').write_text('Selected rule.')
    assert store.resume(request_id)['admission'] == 'reusable'
    write(policy_path, instruction_sources.envelope(REPO, {'mode': 'all', 'include': [], 'exclude': []}))
    assert store.resume(request_id)['admission'] == 'stale'


def test_prepared_recovery_reuses_analysis_but_requires_native_rebinding(service, layout):
    store, context = service
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token, [operation()])
    assessment = store._read(request_id)['assessment']
    recovered = store.upgrade_recover(request_id, token, {
        'binding': 'fixture-native-agent', 'state': 'completed', 'reference': 'fixture-native-terminal'})
    current = recovered['claim_token']
    with pytest.raises(ValueError, match='Bind'):
        store.apply(request_id, claim_token=current)
    assert not config.path(REPO).exists()
    assert store._read(request_id)['assessment'] == assessment
    store.upgrade_bind(request_id, current, 'recovery-native-agent')
    with pytest.raises(ValueError, match='claim token'):
        store.apply(request_id, claim_token=token)
    assert store.apply(request_id, claim_token=current)['status'] == 'applied'
    assert store._read(request_id)['assessment'] == assessment


def test_fork_receipts_do_not_cover_untouched_head_ci_settings(service, layout, monkeypatch):
    store, context = service
    base = 'github.com/upstream/repo'
    checkout_remote(Path(context['checkout']), REPO)
    monkeypatch.setattr(requests.adapters, 'identities', lambda cwd, pr=None:
                        ['remote:' + REPO, 'remote:' + base] if pr else ['remote:' + REPO])
    context = {**context, 'pr': 'https://' + base + '/pull/1'}
    request_id, token = reserve((store, context), layout)
    prepare(store, request_id, token)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert store.upgrade_check(context)['status'] == 'ready'
    plain = {k: v for k, v in context.items() if k != 'pr'}
    assert store.upgrade_check(plain)['scopes'] == ['ci:' + REPO]
    # A base-repository context must first upgrade the PR's head-domain settings
    # from their owning checkout instead of mixing stores and governing rules.
    wrong = {**context, 'repository': base}
    assert store.upgrade_check(wrong)['status'] == 'blocked'


def test_origin_loss_cannot_readmit_or_recover_under_a_new_store_namespace(service, layout, monkeypatch):
    from shared import repository_scope
    store, context = service
    checkout_remote(Path(context['checkout']), REPO)
    monkeypatch.setattr(repository_scope, 'upstream', lambda *args: None)
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token)
    assessment = store._read(request_id)['assessment']
    subprocess.run(['git', '-C', context['checkout'], 'remote', 'remove', 'origin'], check=True, timeout=15)
    for act in [lambda: prepare(store, request_id, token),
                lambda: store.apply(request_id, claim_token=token),
                lambda: store.upgrade_recover(request_id, token, {
                    'binding': 'fixture-native-agent', 'state': 'completed', 'reference': 'fixture-terminal'})]:
        with pytest.raises(ValueError, match='Checkout identity changed'):
            act()
    assert store._read(request_id)['assessment'] == assessment
    subprocess.run(['git', '-C', context['checkout'], 'remote', 'add', 'origin',
                    'https://' + REPO + '.git'], check=True, timeout=15)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'


def test_incompatible_inherited_pr_domain_needs_its_own_checkout(service, layout, monkeypatch):
    from shared import pr_size
    store, context = service
    base = 'github.com/upstream/repo'
    checkout_remote(Path(context['checkout']), REPO)
    monkeypatch.setattr(requests.adapters, 'identities', lambda *args: ['remote:' + REPO, 'remote:' + base])
    write(pr_size.path('remote:' + base), {'unsupported_layout': True})
    bump(layout)
    with pytest.raises(ValueError, match='Incompatible upstream settings require their own checkout'):
        store.upgrade_start({**context, 'pr': 'https://' + base + '/pull/1'})
    assert not store.root.exists()


def test_differing_ci_owner_survives_historical_checkout_removal(service, layout, monkeypatch):
    store, context = service
    base = 'github.com/upstream/repo'
    checkout_remote(Path(context['checkout']), REPO)
    monkeypatch.setattr(requests.adapters, 'identities', lambda *args: ['remote:' + REPO, 'remote:' + base])
    context = {**context, 'pr': 'https://' + base + '/pull/1'}
    op = {'domain': 'ci-exceptions', 'scope': 'repo', 'action': 'add', 'value': {
        'kind': 'pipeline', 'workflow': '.github/workflows/test.yml', 'job': None,
        'test': None, 'signature': None, 'reason': 'Explicit existing exception.'}}
    old, ops, result = preview((store, context), 'Retain this base-repository CI exception.', [op])
    admit(store, old, ops, result); store.apply(old)
    # Simulate immutable pre-release history: no layout revision or owner field.
    legacy = store._read(old); legacy.pop('layout_revision', None)
    assert all('repository' not in d for d in legacy['decisions'])
    write(store._path(old), legacy)
    retained = store._path(old).read_bytes()
    shutil.rmtree(context['checkout'])
    replacement = layout.parent/'replacement-fork'
    checkout_remote(replacement, REPO)
    context['checkout'] = str(replacement)
    request_id, token = reserve((store, context), layout)
    prepare(store, request_id, token)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert store._path(old).read_bytes() == retained
    assert not store._read(request_id)['clarifications']


def test_path_owner_uses_existing_receipt_without_duplicate_metadata(service, layout):
    from shared import pr_size
    store, context = service
    op = {'domain': 'pr-size', 'scope': 'repo', 'action': 'set', 'value': {'added_lines_threshold': 321}}
    old, ops, result = preview(service, 'Use 321 added lines here.', [op])
    admit(store, old, ops, result); store.apply(old)
    record = store._read(old)
    assert all('repository' not in d for d in record['decisions'])
    record.pop('layout_revision', None)
    write(store._path(old), record)
    retained = store._path(old).read_bytes()
    request_id, token = reserve(service, layout)
    source = store.show(request_id, 'sources')['sources'][0]['active'][0]
    assert source['repository'] == 'path:' + context['checkout']
    prepare(store, request_id, token)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert not store._read(request_id)['clarifications']
    assert store._path(old).read_bytes() == retained
    assert json.loads(pr_size.path('path:' + context['checkout']).read_text())['pr_size']['added_lines_threshold'] == 321
    replacement = layout.parent/'another-local-checkout'; replacement.mkdir()
    check = store.upgrade_check({**context, 'checkout': str(replacement)})
    assert set(check['scopes']) == {'domain:path:' + str(replacement), 'ci:path:' + str(replacement)}
    # These values are workflow-only: a valid completed read needs neither the
    # unrelated current directory's domain store nor another interpretation.
    config.effective(REPO)


def test_inferred_non_pr_parent_is_validated_before_reservation(service, layout, monkeypatch):
    from shared import pr_size
    store, context = service
    parent = 'github.com/upstream/repo'
    checkout_remote(Path(context['checkout']), REPO)
    subprocess.run(['git', '-C', context['checkout'], 'remote', 'add', 'upstream',
                    'https://' + parent + '.git'], check=True, timeout=15)
    write(pr_size.path('remote:' + parent), {'schema_version': 1, 'identity': 'remote:' + parent,
                                           'pr_size': {'added_lines_threshold': 321}})
    validate = pr_size.validate
    def target(value):
        if value.get('added_lines_threshold') == 321:
            raise ValueError('This old value is unsupported in the target layout')
        return validate(value)
    monkeypatch.setattr(pr_size, 'validate', target)
    bump(layout)
    with pytest.raises(ValueError, match='Incompatible upstream settings require their own checkout'):
        store.upgrade_start(context)
    assert not store.root.exists()


def test_obsolete_bootstrap_policy_preserves_selection_at_entry_and_resume(service, layout, monkeypatch):
    import instruction_sources
    store, context = service
    checkout = Path(context['checkout'])
    selected = checkout/'CONTRIBUTING.md'; selected.write_text('Human governing source.')
    excluded = checkout/'CLAUDE.md'; excluded.write_text('Excluded automation source.')
    write(instruction_sources.policy_path(REPO), instruction_sources.envelope(REPO, {
        'mode': 'human', 'include': [], 'exclude': []}))
    validate = instruction_sources.validate
    def target(value):
        if value['mode'] == 'human':
            raise ValueError('Obsolete target mode')
        return validate(value)
    monkeypatch.setattr(instruction_sources, 'validate', target)
    request_id, token = reserve(service, layout)
    with upgrades.internal():
        saved = instruction_sources.bootstrap_load(REPO)
        sources = instruction_sources.discover(checkout, instruction_sources.effective_policy(saved))
    assert {s['path'] for s in sources if s['selected']} == {'CONTRIBUTING.md'}
    store.clarify(request_id, 'Keep only CONTRIBUTING.md in the target source policy.', claim_token=token)
    prepare(store, request_id, token, [{'domain': 'instruction-sources', 'scope': 'repo', 'action': 'set',
                                      'value': {'mode': 'custom', 'include': ['CONTRIBUTING.md'], 'exclude': []}}])
    excluded.write_text('Changed excluded automation source.')
    assert requests.Requests().resume(request_id)['admission'] == 'reusable'
    selected.write_text('Changed human governing source.')
    assert store.resume(request_id)['admission'] == 'stale'
    selected.write_text('Human governing source.')
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'


def test_missing_original_store_receipt_blocks_without_guessing_owner(service, layout):
    store, context = service
    old, ops, result = preview(service, operations=[{
        'domain': 'pr-size', 'scope': 'repo', 'action': 'set', 'value': {'added_lines_threshold': 321}}])
    admit(store, old, ops, result); store.apply(old)
    record = store._read(old); record['progress'][0]['receipt'].pop('path')
    write(store._path(old), record)
    bump(layout)
    with pytest.raises(ValueError, match='Restore original readback receipt: ' + old):
        store.upgrade_start(context)
    assert len(store._records()) == 1


@pytest.mark.parametrize(('domain', 'value'), [
    ('rules', {'rules': [{'text': 'Keep focused changes.'}]}),
    ('drafting', {'commit_message': 'team:message'}),
    ('completion', 'Keep the original completion instructions.'),
    ('commit-policy', {'prompt': 'Keep logical commits.'}),
])
@pytest.mark.parametrize('relocated', [False, True])
def test_domain_receipts_support_original_layout_and_relocated_data_root(service, layout, domain, value, relocated):
    store, context = service
    op = {'domain': domain, 'scope': 'repo', 'action': 'set', 'value': value}
    old, ops, result = preview(service, 'Keep these repository settings.', [op])
    admit(store, old, ops, result); store.apply(old)
    record = store._read(old); record.pop('layout_revision', None)
    if relocated:
        receipt = record['progress'][0]['receipt']
        receipt['path'] = str(Path('/previous-machine/private-data').joinpath(*Path(receipt['path']).parts[-2:]))
    write(store._path(old), record)
    retained = store._path(old).read_bytes()
    # Ordinary admission must discover this valid source too, not report corruption.
    another, _, result = preview(service)
    assert {c['request'] for c in result['candidates']} == {old}
    assert all(c['repository'] == 'path:' + context['checkout'] for c in result['candidates'])
    store.cancel(another, 'Read-only source discovery verified.')
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    assert store._path(old).read_bytes() == retained
    assert not store._read(request_id)['clarifications']


@pytest.mark.parametrize('consumer', ['behavior', 'skill'])
@pytest.mark.parametrize('matching_origin', [False, True])
def test_consumed_repository_domain_needs_its_own_receipt(service, layout, monkeypatch, consumer, matching_origin):
    import behavior
    import skill_config
    import settings
    store, context = service
    prompt = 'Apply the original remote repository completion instructions.'
    target = settings.domain_path('completion', 'remote:' + REPO)
    target.parent.mkdir(parents=True, exist_ok=True); target.write_text(prompt)
    monkeypatch.chdir(context['checkout'])
    def consume():
        if consumer == 'behavior':
            return behavior.resolve('dk-workflows:dk-pr-fix', repository=REPO)['effective']
        return skill_config.effective('dk-workflows:dk-pr-fix', REPO)['options']['completion_instructions']
    assert prompt in consume()  # Baseline installation requires no upgrade state.
    assert not store.root.exists()
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token)
    assert store.apply(request_id, claim_token=token)['status'] == 'applied'
    owner = layout.parent/'remote-owner'
    checkout_remote(owner, REPO)
    import shared.repository_scope
    monkeypatch.setattr(shared.repository_scope, 'upstream', lambda *_: None)
    monkeypatch.chdir(owner if matching_origin else context['checkout'])
    with pytest.raises(ValueError, match='Configuration upgrade:'):
        consume()
    config.effective(REPO)  # Pure workflow reads do not consume the remote domain.
    assert skill_config.effective('dk-workflows:dk-pr-fix', REPO, domain=False)['options']['completion_instructions'] is None
    owned_context = {**context, 'checkout': str(owner)}
    second = store.upgrade_start(owned_context)
    assert 'domain:' + REPO in store._read(second['id'])['upgrade']['scopes']
    store.upgrade_bind(second['id'], second['claim_token'], 'fixture-domain-owner')
    prepare(store, second['id'], second['claim_token'])
    assert store.apply(second['id'], claim_token=second['claim_token'])['status'] == 'applied'
    assert prompt in consume()


@pytest.mark.parametrize('consumer', ['ci', 'ci-sections', 'commit-policy', 'pr-size', 'rules', 'drafting', 'completion'])
def test_explicit_store_readers_and_nested_sections_require_selected_receipts(service, layout, monkeypatch, consumer):
    import commit_policy
    import settings
    from shared import ci_exceptions, pr_size
    import shared.repository_scope
    store, context = service
    remote = 'remote:' + REPO
    entry = ci_exceptions.entry('pipeline', '.github/workflows/test.yml', None, None, None, 'Existing explicit exception.')
    write(ci_exceptions.config_path(remote, 'repo'), {'schema_version': 1, 'identity': remote, 'entries': [entry]})
    write(pr_size.path(remote), {'schema_version': 1, 'identity': remote, 'pr_size': {'added_lines_threshold': 321}})
    write(commit_policy.path(remote), {'prompt': 'Keep logical commits.'})
    write(settings.domain_path('rules', remote), {'rules': [{'text': 'Keep focused changes.'}]})
    write(settings.domain_path('drafting', remote), {'commit_message': 'team:message'})
    completion = settings.domain_path('completion', remote)
    completion.parent.mkdir(parents=True, exist_ok=True); completion.write_text('Keep original completion guidance.')
    monkeypatch.setattr(shared.repository_scope, 'upstream', lambda *_: None)
    # The nested reader must inherit the supplied checkout, not the process cwd.
    monkeypatch.chdir(layout.parent if consumer == 'ci-sections' else context['checkout'])
    def consume():
        if consumer == 'ci':
            return ci_exceptions.load(remote)
        if consumer == 'ci-sections':
            return settings.load_sections(REPO, checkout=context['checkout'], sections=('ci-exceptions',))
        if consumer == 'commit-policy':
            return commit_policy.load([remote])
        if consumer == 'pr-size':
            return pr_size.load([remote])
        return settings.domain_load(consumer, [remote])
    baseline = consume()
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token); store.apply(request_id, claim_token=token)
    seen = []
    original = requests.Requests._upgrade_check
    def checked(self, current, **options):
        seen.append(current['checkout'])
        return original(self, current, **options)
    with monkeypatch.context() as patch:
        patch.setattr(requests.Requests, '_upgrade_check', checked)
        with pytest.raises(ValueError, match='Configuration upgrade:'):
            consume()
    assert seen and set(seen) == {context['checkout']}
    owner = layout.parent/'selected-store-owner'; checkout_remote(owner, REPO)
    second = store.upgrade_start({**context, 'checkout': str(owner)})
    store.upgrade_bind(second['id'], second['claim_token'], 'fixture-selected-store-owner')
    prepare(store, second['id'], second['claim_token'])
    store.apply(second['id'], claim_token=second['claim_token'])
    assert consume() == baseline


@pytest.mark.parametrize('copied_context', [False, True])
def test_read_admission_reuse_ends_with_outer_lock(service, layout, monkeypatch, copied_context):
    from contextvars import copy_context
    store, context = service
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token); store.apply(request_id, claim_token=token)
    checks = []
    original = requests.Requests._upgrade_check
    def checked(self, current, **options):
        checks.append(current)
        return original(self, current, **options)
    monkeypatch.setattr(requests.Requests, '_upgrade_check', checked)
    @upgrades.configuration_read(workflow_only=True)
    def outer(repository, checkout):
        config.effective(repository)
        config.effective(repository)
        return copy_context()
    copied = outer(REPO, context['checkout'])
    assert len(checks) == 1
    other = layout.parent/'other-read-owner'; other.mkdir()
    pending = store.upgrade_start({'checkout': str(other), 'repository': 'github.com/other/project'})
    before = len(checks)
    with pytest.raises(ValueError, match='Configuration upgrade: waiting'):
        if copied_context:
            copied.run(config.effective, REPO)
        else:
            outer(REPO, context['checkout'])
    assert len(checks) == before + 1
    store.cancel(pending['id'], 'Finish the synthetic reserved upgrade.', claim_token=pending['claim_token'])
    copied.run(config.effective, REPO)



def test_skills_without_legacy_domains_need_only_workflow_receipts(service, layout, monkeypatch):
    import skill_config
    import behavior
    store, context = service
    request_id, token = reserve(service, layout)
    prepare(store, request_id, token); store.apply(request_id, claim_token=token)
    monkeypatch.chdir(context['checkout'])
    # CI skill options and behavior read workflow settings, not the separate
    # CI exception store or any legacy domain. Do not request another upgrade.
    skill_config.effective(CI, REPO)
    behavior.resolve(CI, repository=REPO)
    assert len(store._records()) == 1
