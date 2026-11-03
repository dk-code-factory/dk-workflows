"""Synthetic offline compatibility controls never establish native adoption."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from shared import architecture, bindings, codex_profiles, compact_compatibility as compatibility, review_execution as execution
from shared.preparation_views import reviewer_view
from shared.preparation_graph import EVIDENCE, digest
from test_codex_profiles import profile_install
from test_review_execution import claude_rows, codex_rows, stream


def retain(path, value):
    path.write_text(json.dumps(value))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def captures(tmp_path, monkeypatch, request):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    drift = getattr(request, 'param', None)
    if drift:
        categories = set(compatibility.SOURCE_CATEGORIES)
        if drift == 'missing':
            categories.remove('feedback')
        else:
            categories.add('future_category')
        monkeypatch.setattr(compatibility, 'SOURCE_CATEGORIES', categories)
    repository = tmp_path / 'repository'
    repository.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-c', 'user.name=Offline fixture',
            '-c', 'user.email=fixture@example.test', *args], cwd=repository, text=True).strip()
    git('init', '-q')
    (repository / 'a.py').write_text('old\n')
    git('add', 'a.py')
    git('commit', '-qm', 'Synthetic base')
    base = git('rev-parse', 'HEAD')
    (repository / 'a.py').write_text('new\n')
    git('commit', '-qam', 'Synthetic head')
    head = git('rev-parse', 'HEAD')
    patch = tmp_path / 'patch.diff'
    patch.write_text(git('diff', base, head) + '\n')
    patch_ref = {'path': str(patch), 'sha256': hashlib.sha256(patch.read_bytes()).hexdigest()}
    decisions = {'decisions': [{'id': 'choice', 'status': 'active', 'text': 'Preserve exact scope.',
                               'conditions': 'Until upgraded.', 'rationale': 'Compatibility.', 'sources': ['user']}],
                 'local_decisions': [], 'discussion_decisions': [], 'live_decisions': [],
                 'verified': True, 'body_hash': 'synthetic-body'}
    manifest = {'version': 1, 'provenance': 'synthetic', 'pairs': []}
    shared_sources = {'patch': patch_ref,
        'metadata': retain(tmp_path / 'metadata.json', {'url': 'https://example.test/pull/1'}),
        'decisions': retain(tmp_path / 'source-decisions.json', decisions),
        'manifest': retain(tmp_path / 'source-manifest.json', {'mode': 'standalone'}),
        'context': retain(tmp_path / 'source-context.json', {'url': 'https://example.test/pull/1'})}
    for category in set(EVIDENCE) - {'reviewers'}:
        shared_sources[category + '/0'] = retain(tmp_path / (category + '.json'),
            {'verified_absence': True, 'reason': 'Synthetic offline fixture.'})
    for label, skill, host in compatibility.COHORT:
        directory = tmp_path / label
        directory.mkdir()
        source = directory / 'SKILL.md'
        source.write_text('Synthetic offline installed procedure: ' + label)
        source_ref = {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        # Synthetic procedure registry, never installed-provider compatibility.
        monkeypatch.setitem(execution._METHODS, (skill, host),
                            {'version': 'test', 'sources': {'SKILL.md': source_ref['sha256']}})
        pair = {'id': label, 'route': 'native' if host == 'codex' else 'run-claude-skill'}
        for mode in ('full', 'compact'):
            contract = 'git-diff-v1' if mode == 'full' else 'git-diff-v2'
            binding = {'skill': skill, 'host': host, 'handoff_contract': contract}
            reviewer = bindings.key(binding)
            descriptor = {'descriptor_version': 1, 'complete': True, 'missing': [], 'reviewer': reviewer,
                          'handoff_contract': contract, 'aspects': ['Spec'], 'effective_prompt': 'Read the pinned scope.',
                          'sources': [source_ref], 'execution': {'executor': host, 'root': str(directory),
                              'procedure': str(source), 'version': 'test', 'bundle_digest': 'synthetic',
                              'binding': binding}}
            prepared = {'preparation_version': 4, 'preparation_id': mode,
                        'identity': {'base_sha': base, 'head_sha': head, 'pr_url': 'https://example.test/pull/1',
                                     'body_hash': decisions['body_hash'], 'decisions_hash': digest(decisions)},
                        'sources': {**shared_sources,
                            'reviewers/0': retain(directory / (mode + '-source-descriptor.json'), descriptor),
                            'reviewer-source/0/0': source_ref}, 'reviewers': {reviewer: descriptor},
                        'analysis': {'boundaries': ['Preserve all shared guidance.'], 'questions': [],
                            'reviewer_addenda': {reviewer: 'Assigned addendum.'},
                            'worker_instructions': 'Read original history only when needed.'}}
            evidence = {category: [prepared['sources'][category + '/0']['path']]
                        for category in EVIDENCE}
            prepared['sources']['manifest'] = retain(directory / (mode + '-source-manifest.json'),
                {'mode': 'standalone', 'evidence': evidence})
            handoff = {'contract': contract, **prepared['identity'], 'scope': 'Exact requested scope.',
                       'rules': 'Exact rules.', 'patch': str(patch), 'checkout': str(repository),
                       'preparation': prepared, 'decisions': decisions, 'reviewer_addendum': 'Assigned addendum.',
                       'decision_instructions': 'Active choices govern; history is available on demand.'}
            if mode == 'compact':
                handoff.update(full_preparation=retain(directory / 'preparation.json', prepared),
                               full_decisions=retain(directory / 'decisions.json', decisions),
                               preparation_view_version=1, preparation=reviewer_view(prepared, reviewer),
                               decisions=compatibility.active_decisions(decisions))
            roles = execution.requirements(binding, handoff)
            # Execute conditional roles as well to avoid synthetic applicability claims.
            roles += [r for r in execution.conditional(binding)[1] if r not in roles]
            rows = codex_rows(roles) if host == 'codex' else claude_rows(roles)
            for row in rows:
                item = row.get('item', {})
                if item.get('tool') == 'spawn_agent':
                    role = item['prompt'].splitlines()[0].split('=', 1)[1]
                    item['prompt'] += '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(compatibility.delivery_payload(handoff, role))
                for block in row.get('message', {}).get('content', []):
                    if block.get('type') == 'tool_use':
                        role = block['input']['prompt'].splitlines()[0].split('=', 1)[1]
                        block['input']['prompt'] += '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(compatibility.delivery_payload(handoff, role))
            path = directory / (mode + '-handoff.json')
            handoff_ref = retain(path, handoff)
            result = {'findings': []}
            with execution.collect(binding, descriptor, path, repository) as collector:
                execution.record(host, stream(rows), '', 0, 0)
                proof_ref = execution.finish(collector, result)
            proof = compatibility.read(proof_ref)
            deliveries = []
            for index, row in enumerate(rows, 1):
                prompt_pointer = None
                if row.get('item', {}).get('tool') == 'spawn_agent':
                    prompt_pointer = '/item/prompt'
                    prompt = row['item']['prompt']
                elif row.get('type') == 'assistant':
                    prompt_pointer = '/message/content/0/input/prompt'
                    prompt = row['message']['content'][0]['input']['prompt']
                if prompt_pointer:
                    deliveries.append({'recipient': prompt.splitlines()[0].split('=', 1)[1], 'attempt': 0,
                        'kind': 'native-prompt', 'source': proof['attempts'][0], 'event': index, 'pointer': prompt_pointer})
            prompt_ref = retain(directory / (mode + '-parent-prompt.json'), {'prompt': 'Retained parent delivery record.'})
            deliveries.append({'recipient': 'reviewer', 'attempt': 0, 'kind': 'source-bound-observation',
                'source': prompt_ref, 'pointer': '/prompt', 'excerpt': 'Retained parent delivery record.',
                'received': compatibility.delivery_payload(handoff, 'reviewer')})
            pair[mode] = {'binding': binding, 'handoff': handoff_ref,
                         'descriptor': retain(directory / (mode + '-descriptor.json'), descriptor),
                         'result': retain(directory / (mode + '-result.json'), result),
                         'execution': proof_ref, 'deliveries': deliveries}
        manifest['pairs'].append(pair)
    path = tmp_path / 'manifest.json'
    retain(path, manifest)
    return path, manifest


def compare(captures):
    path, manifest = captures
    retain(path, manifest)
    return compatibility.compare(path)


def refresh(run, *, slots=(0,), findings=None):
    """Regenerate actual captures/proof after a negative-control edit."""
    handoff = compatibility.read(run['handoff'])
    descriptor = compatibility.read(run['descriptor'])
    previous = compatibility.read(run['execution'])
    native = compatibility.read(previous['attempts'][0])
    rows = [json.loads(line) for line in native['stdout'].splitlines()]
    for row in rows:
        item = row.get('item', {})
        if item.get('tool') == 'spawn_agent':
            role = item['prompt'].splitlines()[0].split('=', 1)[1]
            item['prompt'] = 'DK_REVIEW_PASS=' + role + '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(compatibility.delivery_payload(handoff, role))
        for block in row.get('message', {}).get('content', []):
            if block.get('type') == 'tool_use':
                role = block['input']['prompt'].splitlines()[0].split('=', 1)[1]
                block['input']['prompt'] = 'DK_REVIEW_PASS=' + role + '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(compatibility.delivery_payload(handoff, role))
    result = {'findings': [] if findings is None else findings}
    run['result'] = retain(Path(run['result']['path']), result)
    with execution.collect(run['binding'], descriptor, run['handoff']['path'], handoff['checkout']) as collector:
        for slot in slots:
            execution.record(native['host'], stream(rows), '', 0, slot)
        run['execution'] = execution.finish(collector, result)
    proof = compatibility.read(run['execution'])
    for record in run['deliveries']:
        if 'event' in record:
            record['source'] = proof['attempts'][0]
        else:
            record['received'] = compatibility.delivery_payload(handoff, record['recipient'])


def add_architecture(captures):
    directory = captures[0].parent
    first_handoff = compatibility.read(captures[1]['pairs'][0]['full']['handoff'])
    catalog = architecture.seal({'repository': 'path:' + str(directory / 'repository'),
        'baseline': first_handoff['base_sha'], 'rules': [{'id': 'rule', 'agents': ['all'], 'patterns': ['*.py']}],
        'conflicts': [], 'stale_rules': []})
    storage, _ = architecture.folder(directory / 'repository', catalog['repository'])
    catalog_path = storage / 'catalogs' / (catalog['fingerprint'] + '.json')
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(json.dumps(catalog))
    for pair in captures[1]['pairs']:
        for label in ('full', 'compact'):
            run = pair[label]
            handoff = compatibility.read(run['handoff'])
            prepared = (compatibility.read(handoff['full_preparation']) if label == 'compact'
                        else handoff['preparation'])
            root = Path(run['handoff']['path']).parent
            guidance = retain(root / (label + '-instructions.json'), {'instruction': 'Same governing scope.'})
            governing = architecture.governing_sources({'user_instructions/0': guidance['path']})
            scope_decisions = (compatibility.read(handoff['full_decisions']) if label == 'compact'
                               else handoff['decisions'])
            scope = {'metadata': {'url': handoff['pr_url']}, 'decisions': scope_decisions,
                     'governing': {key: ref['fingerprint'] for key, ref in governing.items()}}
            def packet(role):
                value = architecture.seal({'schema_version': 1, 'kind': 'architecture-packet',
                    'repository': catalog['repository'], 'catalog': catalog['fingerprint'],
                    'baseline': handoff['base_sha'], 'head': handoff['head_sha'], 'tree': 'tree', 'task': handoff['pr_url'],
                    'agent': role, 'paths': ['a.py'], 'rules': catalog['rules'],
                    'scope': architecture.digest(scope), 'scope_contents': scope,
                    'governing_sources': governing,
                    'deferred_obligations': {'conflicts': [], 'stale_rules': []}})
                path = root / (label + '-' + role.replace(':', '-') + '-packet.json')
                path.write_text(json.dumps(value))
                return {'path': str(path), 'fingerprint': value['fingerprint']}
            coordinator = packet('coordinator')
            prepared['architecture'] = {'packet': coordinator, 'catalog': catalog['fingerprint'],
                                        'governing': governing}
            prepared['sources']['architecture/0'] = retain(Path(coordinator['path']), architecture.load(coordinator))
            handoff['architecture_packet'] = packet(bindings.key(run['binding']))
            proof = compatibility.read(run['execution'])
            handoff['nested_architecture_packets'] = {role: packet(role) for role in proof['assessment']['completed']}
            if label == 'compact':
                handoff['full_preparation'] = retain(Path(handoff['full_preparation']['path']), prepared)
                handoff['preparation'] = reviewer_view(prepared, bindings.key(run['binding']))
            else:
                handoff['preparation'] = prepared
            run['handoff'] = retain(Path(run['handoff']['path']), handoff)
            refresh(run)


def replace_preparation(run, handoff, prepared):
    if handoff['contract'] == 'git-diff-v2':
        handoff['full_preparation'] = retain(Path(handoff['full_preparation']['path']), prepared)
        handoff['preparation'] = reviewer_view(prepared, bindings.key(run['binding']))
    else:
        handoff['preparation'] = prepared
    run['handoff'] = retain(Path(run['handoff']['path']), handoff)
    refresh(run)


def replace_native_attempts(run, attempts):
    handoff, descriptor = (compatibility.read(run[key]) for key in ('handoff', 'descriptor'))
    with execution.collect(run['binding'], descriptor, run['handoff']['path'], handoff['checkout']) as collector:
        for rows, returncode in attempts:
            execution.record(run['binding']['host'], stream(rows), '', returncode, 0)
        run['execution'] = execution.finish(collector, compatibility.read(run['result']))
    proof = compatibility.read(run['execution'])
    for record in run['deliveries']:
        if 'event' in record:
            record['source'] = proof['attempts'][0]


@pytest.mark.parametrize('pair_index', [0, 1])
@pytest.mark.parametrize('cite_retry', [False, True])
def test_delivery_is_bound_to_successful_transport_attempt(captures, pair_index, cite_retry):
    run = captures[1]['pairs'][pair_index]['compact']
    proof = compatibility.read(run['execution'])
    original = compatibility.read(proof['attempts'][0])
    discarded = [json.loads(line) for line in original['stdout'].splitlines()]
    retry = json.loads(json.dumps(discarded).replace('child-', 'retry-child-')
                       .replace('call-', 'retry-call-'))
    for row in retry:
        item = row.get('item', {})
        if item.get('tool') == 'spawn_agent' and not cite_retry:
            item['prompt'] = item['prompt'].splitlines()[0]
        for block in row.get('message', {}).get('content', []):
            if block.get('type') == 'tool_use' and not cite_retry:
                block['input']['prompt'] = block['input']['prompt'].splitlines()[0]
    replace_native_attempts(run, [(discarded, 1), (retry, 0)])
    execution.verify(run['execution'], run['binding'], compatibility.read(run['descriptor']), run['handoff']['path'])
    if cite_retry:
        proof = compatibility.read(run['execution'])
        for record in run['deliveries']:
            if 'event' in record:
                record['source'] = proof['attempts'][1]
    row = compare(captures)['pairs'][pair_index]
    assert row['status'] == ('observed' if cite_retry else 'incomplete'), row['issues']
    if not cite_retry:
        assert 'transport attempt' in row['issues'][0]


@pytest.mark.parametrize('change', ['status', 'empty-receiver', 'invalid-receiver', 'self-receiver', 'claude-error'])
def test_repaired_failed_launch_does_not_establish_delivery(captures, change):
    pair_index = 1 if change == 'claude-error' else 0
    run = captures[1]['pairs'][pair_index]['compact']
    proof = compatibility.read(run['execution'])
    original = compatibility.read(proof['attempts'][0])
    failed = [json.loads(line) for line in original['stdout'].splitlines()]
    repaired = json.loads(json.dumps(failed))
    for row in failed:
        item = row.get('item', {})
        if item.get('tool') == 'spawn_agent':
            if change == 'status':
                item['status'] = 'failed'
            else:
                item['receiver_thread_ids'] = {'empty-receiver': [], 'invalid-receiver': [17],
                                              'self-receiver': ['root']}[change]
        for block in row.get('message', {}).get('content', []):
            if block.get('type') == 'tool_result':
                block['is_error'] = True
    for row in repaired:
        item = row.get('item', {})
        if item.get('tool') == 'spawn_agent':
            item['prompt'] = item['prompt'].splitlines()[0]
        for block in row.get('message', {}).get('content', []):
            if block.get('type') == 'tool_use':
                block['input']['prompt'] = block['input']['prompt'].splitlines()[0]
    replace_native_attempts(run, [(failed, 1), (repaired, 0)])
    execution.verify(run['execution'], run['binding'], compatibility.read(run['descriptor']), run['handoff']['path'])
    row = compare(captures)['pairs'][pair_index]
    assert row['status'] == 'incomplete'
    assert 'launch' in row['issues'][0]


@pytest.mark.parametrize('target', ['native', 'observation', 'compact'])
def test_guidance_comparison_preserves_json_boolean_types(captures, target):
    run = captures[1]['pairs'][0]['compact']
    if target == 'compact':
        handoff = compatibility.read(run['handoff'])
        handoff['decisions']['verified'] = 1
        run['handoff'] = retain(Path(run['handoff']['path']), handoff)
        refresh(run)
    elif target == 'observation':
        run['deliveries'][-1]['received']['decisions']['verified'] = 1
    else:
        proof = compatibility.read(run['execution'])
        original = compatibility.read(proof['attempts'][0])
        rows = [json.loads(line) for line in original['stdout'].splitlines()]
        for event in rows:
            item = event.get('item', {})
            if item.get('tool') == 'spawn_agent':
                marker, envelope = item['prompt'].split('\nDK_REVIEW_DELIVERY_JSON=')
                value = json.loads(envelope)
                value['decisions']['verified'] = 1
                item['prompt'] = marker + '\nDK_REVIEW_DELIVERY_JSON=' + json.dumps(value)
        replace_native_attempts(run, [(rows, 0)])
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    expected = 'Decisions changed' if target == 'compact' else 'guidance delivery'
    assert expected in row['issues'][0]


def test_consistently_narrowed_architecture_packets_cannot_omit_changed_paths(captures):
    add_architecture(captures)
    for label in ('full', 'compact'):
        run = captures[1]['pairs'][0][label]
        handoff = compatibility.read(run['handoff'])
        prepared = compatibility.read(handoff['full_preparation']) if label == 'compact' else handoff['preparation']
        references = [prepared['architecture']['packet'], handoff['architecture_packet'],
                      *handoff['nested_architecture_packets'].values()]
        for reference in references:
            packet = architecture.load(reference)
            packet.pop('fingerprint')
            packet['paths'] = []
            packet['rules'] = packet['rules'] if packet['agent'] == 'coordinator' else []
            packet = architecture.seal(packet)
            Path(reference['path']).write_text(json.dumps(packet))
            reference['fingerprint'] = packet['fingerprint']
        prepared['sources']['architecture/0'] = retain(Path(references[0]['path']), architecture.load(references[0]))
        replace_preparation(run, handoff, prepared)
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert 'architecture changed paths' in row['issues'][0]


@pytest.mark.parametrize('change', ['source', 'identity'])
def test_delivered_decisions_are_bound_to_preparation_original(captures, change):
    for pair in captures[1]['pairs']:
        for label in ('full', 'compact'):
            run = pair[label]
            handoff = compatibility.read(run['handoff'])
            prepared = compatibility.read(handoff['full_preparation']) if label == 'compact' else handoff['preparation']
            decisions = compatibility.read(handoff['full_decisions']) if label == 'compact' else handoff['decisions']
            decisions['decisions'][0]['conditions'] = 'Changed governing condition.'
            if change == 'source':
                prepared['identity']['decisions_hash'] = digest(decisions)
            else:
                source = prepared['sources']['decisions']
                prepared['sources']['decisions'] = retain(Path(source['path']), decisions)
            handoff['decisions'] = compatibility.active_decisions(decisions) if label == 'compact' else decisions
            if label == 'compact':
                handoff['full_decisions'] = retain(Path(handoff['full_decisions']['path']), decisions)
            replace_preparation(run, handoff, prepared)
    report = compare(captures)
    assert report['status'] == 'incomplete'
    assert all('Decisions' in pair['issues'][0] for pair in report['pairs'])


@pytest.mark.parametrize('source', ['manifest', 'context', 'metadata', 'decisions', 'patch',
                                    'feedback/0', 'user_instructions/0', 'reviewers/0'])
def test_omitted_required_source_cannot_hide_unavailable_original(captures, source):
    for label in ('full', 'compact'):
        run = captures[1]['pairs'][0][label]
        handoff = compatibility.read(run['handoff'])
        prepared = (compatibility.read(handoff['full_preparation']) if label == 'compact'
                    else handoff['preparation'])
        original = prepared['sources'].pop(source)
        replace_preparation(run, handoff, prepared)
    Path(original['path']).unlink()
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert row['issues'] == ['preparation source index is incomplete']


@pytest.mark.parametrize('change', ['none', 'omit', 'replace'])
def test_every_manifest_source_in_multi_record_category_is_required(captures, change):
    original = retain(captures[0].parent / 'second-feedback.json',
                      {'verified_absence': True, 'reason': 'Additional synthetic source.'})
    for pair in captures[1]['pairs']:
        for label in ('full', 'compact'):
            run = pair[label]
            handoff = compatibility.read(run['handoff'])
            prepared = (compatibility.read(handoff['full_preparation']) if label == 'compact'
                        else handoff['preparation'])
            source = prepared['sources']['manifest']
            manifest = compatibility.read(source)
            manifest['evidence']['feedback'].append(original['path'])
            prepared['sources']['manifest'] = retain(Path(source['path']), manifest)
            if change != 'omit':
                prepared['sources']['feedback/1'] = (prepared['sources']['feedback/0']
                    if change == 'replace' else original)
            replace_preparation(run, handoff, prepared)
    if change != 'none':
        Path(original['path']).unlink()
    report = compare(captures)
    assert report['status'] == ('observed' if change == 'none' else 'incomplete')
    if change != 'none':
        assert all('source ' in row['issues'][0] and 'manifest' in row['issues'][0]
                   for row in report['pairs'])


def test_provider_specific_procedure_sources_do_not_change_cohort_scope(captures):
    hashes = set()
    for pair in captures[1]['pairs']:
        handoff = compatibility.read(pair['full']['handoff'])
        hashes.add(handoff['preparation']['sources']['reviewer-source/0/0']['sha256'])
    assert len(hashes) == 3
    assert compare(captures)['status'] == 'observed'


@pytest.mark.parametrize('captures', ['missing', 'extra'], indirect=True)
def test_fixture_source_inventory_exposes_analyzer_schema_drift(captures):
    report = compare(captures)
    assert report['status'] == 'incomplete'
    assert all('incomplete' in row['issues'][0] for row in report['pairs'])


@pytest.mark.parametrize('change', ['none', 'settings', 'external-path', 'unknown-extension', 'unproven'])
def test_profiled_pair_projects_only_proved_workspace_identity(captures, profile_install, change):
    pair = captures[1]['pairs'][0]
    workspaces = []
    profile_text = profile_install['profile'].read_text()
    for label in ('full', 'compact'):
        run = pair[label]
        handoff = compatibility.read(run['handoff'])
        prepared = (compatibility.read(handoff['full_preparation']) if label == 'compact'
                    else handoff['preparation'])
        checkout = captures[0].parent / (label + '-execution-checkout')
        subprocess.run(['git', 'clone', '--quiet', handoff['checkout'], str(checkout)],
                       check=True, capture_output=True)
        workspaces.append(str(checkout))
        handoff['checkout'] = str(checkout)
        if change == 'settings' and label == 'compact':
            profile_install['profile'].write_text('model = "changed-profile-model"\n')
        if change == 'external-path':
            instructions = captures[0].parent / (label + '-external-instructions.md')
            instructions.write_text('Identical instructions at distinct external paths.')
            profile_install['profile'].write_text(profile_text + '\nmodel_instructions_file = '
                                                 + json.dumps(str(instructions)) + '\n')
        snapshot = codex_profiles.resolve_profile('review', checkout)
        if change == 'unknown-extension':
            snapshot['future_extension'] = {'workspace': str(checkout)}
            snapshot['identity_evidence']['exact_sha256'] = codex_profiles.fingerprint(
                {key: value for key, value in snapshot.items() if key != 'identity_evidence'})
        if change == 'unproven':
            snapshot['identity_evidence']['exact_sha256'] = 'invalid-proof'
        previous = bindings.key(run['binding'])
        run['binding']['codex_profile'] = 'review'
        reviewer = bindings.key(run['binding'])
        descriptor = compatibility.read(run['descriptor'])
        descriptor['reviewer'] = reviewer
        descriptor['execution'].update(binding=run['binding'], codex_execution=snapshot)
        run['descriptor'] = retain(Path(run['descriptor']['path']), descriptor)
        prepared['reviewers'] = {reviewer: descriptor}
        prepared['analysis']['reviewer_addenda'][reviewer] = prepared['analysis']['reviewer_addenda'].pop(previous)
        source = prepared['sources']['reviewers/0']
        prepared['sources']['reviewers/0'] = retain(Path(source['path']), descriptor)
        replace_preparation(run, handoff, prepared)
    assert workspaces[0] != workspaces[1]
    row = compare(captures)['pairs'][0]
    assert [row['runs'][label]['execution']['codex_execution']['identity_evidence']['workspace']
            for label in ('full', 'compact')] == workspaces
    if change == 'none':
        assert row['status'] == 'observed', row['issues']
        assert row['findings'] == {'shared': [], 'full_only': [], 'compact_only': []}
    else:
        assert row['status'] == 'incomplete'
        assert row['issues'] == ['installed version, procedure, sources or execution changed']


@pytest.mark.parametrize('change', ['task', 'decisions', 'metadata'])
def test_consistently_stale_architecture_family_is_incomplete(captures, change):
    add_architecture(captures)
    for label in ('full', 'compact'):
        run = captures[1]['pairs'][0][label]
        handoff = compatibility.read(run['handoff'])
        prepared = (compatibility.read(handoff['full_preparation']) if label == 'compact'
                    else handoff['preparation'])
        references = [prepared['architecture']['packet'], handoff['architecture_packet'],
                      *handoff['nested_architecture_packets'].values()]
        for reference in references:
            packet = architecture.load(reference)
            packet.pop('fingerprint')
            if change == 'task':
                packet['task'] = 'https://example.test/pull/2'
            elif change == 'decisions':
                packet['scope_contents']['decisions']['decisions'][0]['conditions'] = 'An older condition.'
            else:
                packet['scope_contents']['metadata']['url'] = 'https://example.test/pull/2'
            packet['scope'] = architecture.digest(packet['scope_contents'])
            packet = architecture.seal(packet)
            retained = retain(Path(reference['path']), packet)
            reference['fingerprint'] = packet['fingerprint']
            if reference is prepared['architecture']['packet']:
                prepared['sources']['architecture/0'] = retained
        replace_preparation(run, handoff, prepared)
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert 'scope or Decisions changed' in row['issues'][0]


@pytest.mark.parametrize('action', ['changed', 'missing', 'inaccessible'])
def test_architecture_governing_originals_are_required(captures, monkeypatch, action):
    add_architecture(captures)
    handoff = compatibility.read(captures[1]['pairs'][0]['full']['handoff'])
    packet = architecture.load(handoff['architecture_packet'])
    path = Path(packet['governing_sources']['user_instructions/0']['path'])
    if action == 'changed':
        path.write_text('changed')
    elif action == 'missing':
        path.unlink()
    else:
        original = Path.read_bytes
        def denied(self):
            if self == path:
                raise PermissionError('governing original inaccessible')
            return original(self)
        monkeypatch.setattr(Path, 'read_bytes', denied)
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert row['issues']


def test_synthetic_pair_matrix_retains_no_adoption_authority(captures):
    report = compare(captures)
    assert report['status'] == 'observed', report
    assert report['provenance'] == 'synthetic'
    assert report['adoption_authorized'] is report['publication_authorized'] is False
    assert report['live_acceptance'] == 'pending'
    assert [p['id'] for p in report['pairs']] == ['codex-ad', 'claude-toolkit', 'claude-ad']
    assert report['pairs'][0]['runs']['compact']['completed'] == ['Performance', 'Spec', 'Standards', 'Toolkit']
    assert report['pairs'][0]['findings'] == {'shared': [], 'full_only': [], 'compact_only': []}


@pytest.mark.parametrize('action', ['changed', 'missing', 'inaccessible'])
def test_original_evidence_failures_are_explicitly_incomplete(captures, monkeypatch, action):
    run = captures[1]['pairs'][0]['compact']
    handoff = compatibility.read(run['handoff'])
    path = Path(handoff['full_preparation']['path'])
    if action == 'changed':
        path.write_text('changed')
    elif action == 'missing':
        path.unlink()
    else:
        original = Path.read_bytes
        def denied(self):
            if self == path:
                raise PermissionError('original inaccessible')
            return original(self)
        monkeypatch.setattr(Path, 'read_bytes', denied)
    report = compare(captures)
    assert report['status'] == 'incomplete'
    assert report['pairs'][0]['issues']


def test_native_completion_does_not_establish_guidance_delivery(captures):
    captures[1]['pairs'][0]['compact']['deliveries'] = []
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert row['runs']['compact']['completed']
    assert any('delivery unknown' in issue for issue in row['issues'])


def test_changed_nested_active_choice_is_rejected(captures):
    run = captures[1]['pairs'][0]['compact']
    observation = run['deliveries'][-1]
    observation['received']['decisions']['decisions'][0]['conditions'] = 'Changed condition.'
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert 'guidance delivery' in row['issues'][0]


def test_observation_requires_accessible_source_excerpt(captures):
    captures[1]['pairs'][1]['compact']['deliveries'][-1]['excerpt'] = 'Not present.'
    assert 'excerpt unavailable' in compare(captures)['pairs'][1]['issues'][0]


def test_native_role_evidence_cannot_be_replaced_by_findings(captures):
    captures[1]['pairs'][2]['compact']['execution'] = captures[1]['pairs'][2]['compact']['result']
    assert compare(captures)['pairs'][2]['status'] == 'incomplete'


@pytest.mark.parametrize('change', ['contract', 'scope', 'method', 'guidance', 'decisions'])
def test_regenerated_capture_hashes_do_not_hide_changed_governing_inputs(captures, change):
    run = captures[1]['pairs'][0]['compact']
    value = compatibility.read(run['handoff'])
    if change == 'contract':
        value['contract'] = 'git-diff-v3'
    elif change == 'scope':
        value['scope'] = 'Different requested scope.'
    elif change == 'method':
        descriptor = compatibility.read(run['descriptor'])
        descriptor['execution']['version'] = 'changed'
        run['descriptor'] = retain(Path(run['descriptor']['path']), descriptor)
    elif change == 'guidance':
        value['preparation']['analysis']['worker_instructions'] = 'Omitted shared constraint.'
    else:
        value['decisions']['decisions'][0]['text'] = 'Changed active choice.'
    run['handoff'] = retain(Path(run['handoff']['path']), value)
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert row['issues']


def test_cohort_omission_is_invalid(captures):
    captures[1]['pairs'].pop()
    with pytest.raises(ValueError, match='cohort required'):
        compare(captures)


def test_claude_route_drift_is_invalid(captures):
    captures[1]['pairs'][2]['route'] = 'native'
    with pytest.raises(ValueError, match='Claude Toolkit and AD routes must match'):
        compare(captures)


@pytest.mark.parametrize('pair_index,marker', [(0, 'thread.started'), (1, 'result')])
@pytest.mark.parametrize('session_value', [None, '', [], 17])
def test_partial_repaired_native_delivery_is_explicitly_incomplete(captures, pair_index, marker, session_value):
    run = captures[1]['pairs'][pair_index]['compact']
    handoff, descriptor = (compatibility.read(run[key]) for key in ('handoff', 'descriptor'))
    previous = compatibility.read(run['execution'])
    raw = compatibility.read(previous['attempts'][0])
    rows = [json.loads(line) for line in raw['stdout'].splitlines()]
    partial = json.loads(json.dumps(rows))
    if session_value is None:
        partial = [row for row in partial if row.get('type') != marker]
    else:
        for row in partial:
            if marker == 'thread.started':
                if row.get('type') == marker:
                    row['thread_id'] = session_value
                if row.get('item', {}).get('tool') == 'spawn_agent':
                    row['item']['sender_thread_id'] = session_value
            elif 'session_id' in row:
                row['session_id'] = session_value
    with execution.collect(run['binding'], descriptor, run['handoff']['path'], handoff['checkout']) as collector:
        execution.record(raw['host'], stream(partial), 'interrupted transport', 1, 0)
        execution.record(raw['host'], stream(rows), '', 0, 0)
        run['execution'] = execution.finish(collector, compatibility.read(run['result']))
    execution.verify(run['execution'], run['binding'], descriptor, run['handoff']['path'])
    proof = compatibility.read(run['execution'])
    for record in run['deliveries']:
        if 'event' in record:
            event = rows[record['event'] - 1]
            if session_value is None:
                record['event'] = partial.index(event) + 1
            record['source'] = proof['attempts'][0]
    row = compare(captures)['pairs'][pair_index]
    assert row['status'] == 'incomplete'
    assert row['issues'] == ['native delivery session marker unavailable']


def test_correction_needs_independent_delivery_observations(captures):
    run = captures[1]['pairs'][0]['compact']
    refresh(run, slots=(0, 1))
    row = compare(captures)['pairs'][0]
    assert row['runs']['compact']['response_slots'] == [0, 1]
    assert row['status'] == 'incomplete'
    assert any('response 1' in message for message in row['issues'])


def test_corrected_delivery_can_be_observed_without_replacing_initial_evidence(captures):
    run = captures[1]['pairs'][0]['compact']
    refresh(run, slots=(0, 1))
    proof = compatibility.read(run['execution'])
    corrected = json.loads(json.dumps(run['deliveries']))
    for record in corrected:
        record['attempt'] = 1
        if 'event' in record:
            record['source'] = proof['attempts'][1]
    run['deliveries'].extend(corrected)
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'observed', row['issues']
    assert {record['attempt'] for record in row['runs']['compact']['delivery']} == {0, 1}


def test_paired_scope_drift_survives_regenerated_native_proof(captures):
    run = captures[1]['pairs'][0]['compact']
    handoff = compatibility.read(run['handoff'])
    handoff['scope'] = 'Narrowed silently.'
    run['handoff'] = retain(Path(run['handoff']['path']), handoff)
    refresh(run)
    assert compare(captures)['pairs'][0]['issues'] == ['paired governing evidence or requested scope changed']


def test_separate_claude_methods_keep_identical_requested_scope(captures):
    for label in ('full', 'compact'):
        run = captures[1]['pairs'][2][label]
        handoff = compatibility.read(run['handoff'])
        handoff['scope'] = 'Different Claude AD scope.'
        run['handoff'] = retain(Path(run['handoff']['path']), handoff)
        refresh(run)
    assert compare(captures)['pairs'][2]['issues'] == ['cohort governing evidence or requested scope changed']


def test_retained_findings_are_compared_without_recall_claims(captures):
    finding = {'severity': 'Major', 'path': 'a.py', 'line': 1, 'message': 'A retained sample finding.', 'fix': 'Preserve it.'}
    refresh(captures[1]['pairs'][0]['compact'], findings=[finding])
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'observed'
    assert row['findings']['compact_only'] == [finding]
    assert 'recall' not in row


def test_architecture_recipient_and_revision_are_checked(captures):
    add_architecture(captures)
    handoff = compatibility.read(captures[1]['pairs'][0]['full']['handoff'])
    ref = handoff['nested_architecture_packets']['Spec']
    assert compatibility.packets(handoff, bindings.key(captures[1]['pairs'][0]['full']['binding']))['Spec'] == architecture.load(ref)
    handoff.pop('architecture_packet')
    handoff['nested_architecture_packets'] = {'Standards': ref}
    with pytest.raises(ValueError, match='recipient mismatch'):
        compatibility.packets(handoff, 'parent')
    handoff['nested_architecture_packets'] = {'Spec': ref}
    handoff['head_sha'] = 'moved'
    with pytest.raises(ValueError, match='scope or Decisions changed'):
        compatibility.packets(handoff, 'parent')


def test_fresh_architecture_captures_compare_governing_content(captures):
    add_architecture(captures)
    pair = captures[1]['pairs'][0]
    full, compact = (compatibility.read(pair[label]['handoff']) for label in ('full', 'compact'))
    assert full['architecture_packet']['fingerprint'] != compact['architecture_packet']['fingerprint']
    assert compare(captures)['status'] == 'observed'


@pytest.mark.parametrize('change,error', [('paths', 'scope expanded'), ('rules', 'rule coverage differs'),
                                       ('scope', 'preparation scope mismatch')])
def test_architecture_applicability_is_checked(captures, change, error):
    add_architecture(captures)
    run = captures[1]['pairs'][0]['compact']
    handoff = compatibility.read(run['handoff'])
    reference = handoff['nested_architecture_packets']['Spec']
    packet = architecture.load(reference)
    packet.pop('fingerprint')
    if change in ('paths', 'rules'):
        packet[change] = []
        if change == 'paths':
            packet['rules'] = []
    else:
        packet['scope_contents']['metadata']['url'] = 'https://example.test/pull/2'
        packet['scope'] = architecture.digest(packet['scope_contents'])
    packet = architecture.seal(packet)
    Path(reference['path']).write_text(json.dumps(packet))
    reference['fingerprint'] = packet['fingerprint']
    run['handoff'] = retain(Path(run['handoff']['path']), handoff)
    refresh(run)
    row = compare(captures)['pairs'][0]
    assert row['status'] == 'incomplete'
    assert error in row['issues'][0]


def test_cli_writes_private_non_overwriting_diagnostic_report(captures, tmp_path):
    # CLI sees the actual supported-method registry, so synthetic profiles remain
    # incomplete in a fresh process. This also proves it never invokes a provider.
    target = tmp_path / 'report.json'
    command = [sys.executable, str(ROOT / 'scripts/compare_review_handoffs.py'), str(captures[0]), '--report', str(target)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert json.loads(target.read_text())['status'] == 'incomplete'
    assert target.stat().st_mode & 0o777 == 0o600
    assert subprocess.run(command, capture_output=True).returncode == 2


def test_report_path_inside_repository_is_rejected():
    with pytest.raises(ValueError, match='private paths'):
        compatibility.private_path(ROOT / 'accidental-capture.json')


def test_external_checkout_captures_and_report_are_rejected(tmp_path):
    checkout = tmp_path / 'external'
    subprocess.run(['git', 'init', str(checkout)], check=True, capture_output=True)
    target = checkout / 'nested' / 'report.json'
    with pytest.raises(ValueError, match='private paths'):
        compatibility.private_path(target)
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/compare_review_handoffs.py'),
        str(tmp_path / 'missing-manifest.json'), '--report', str(target)], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'private paths' in result.stderr
    assert not target.exists()


def test_installed_procedure_source_can_be_a_local_checkout(captures):
    run = captures[1]['pairs'][0]['full']
    descriptor = compatibility.read(run['descriptor'])
    root = captures[0].parent / 'installed-plugin'
    subprocess.run(['git', 'init', str(root)], check=True, capture_output=True)
    source = root / 'SKILL.md'
    source.write_bytes(Path(descriptor['sources'][0]['path']).read_bytes())
    descriptor['execution']['root'] = str(root)
    descriptor['sources'][0]['path'] = str(source)
    assert compatibility.method(descriptor)['sources'] == {'SKILL.md': descriptor['sources'][0]['sha256']}
