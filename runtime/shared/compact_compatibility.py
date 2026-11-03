"""Offline diagnostics for retained paired review inputs; never adoption authority."""
import copy
import hashlib
import json
from pathlib import Path

from shared import architecture, bindings, review_execution
from shared.preparation_views import reviewer_view
from shared.preparation_graph import digest as preparation_digest

ROOT = Path(__file__).resolve().parents[2]
COHORT = [('codex-ad', 'code-review-ad:code-review-ad', 'codex'),
          ('claude-toolkit', 'pr-review-toolkit:review-pr', 'claude'),
          ('claude-ad', 'code-review-ad:code-review-ad', 'claude')]
AUTHORITY = {'authority': 'diagnostic-only', 'adoption_authorized': False,
             'publication_authorized': False, 'live_acceptance': 'pending'}
SOURCE_CATEGORIES = {'issues', 'feedback', 'choices', 'user_instructions', 'run_history',
                     'questions', 'prior_preparation', 'rules', 'production', 'tests', 'ci', 'reviewers'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def json_equal(left, right):
    """Preserve JSON scalar types when comparing retained guidance copies."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def private_path(value):
    path = Path(value)
    resolved = path.resolve()
    require(path.is_absolute() and not resolved.is_relative_to(ROOT)
            and not any((parent / '.git').exists() for parent in (resolved, *resolved.parents)),
            'captures and reports must use absolute private paths outside the repository')
    return path


def read(reference, *, json_value=True, installed_source=False):
    require(isinstance(reference, dict), 'hashed evidence reference required')
    path = Path(reference['path']) if installed_source else private_path(reference['path'])
    require(path.is_absolute(), 'absolute evidence path required')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == reference['sha256'], 'evidence changed: ' + str(path))
    return json.loads(raw) if json_value else raw


def active_decisions(value):
    require(isinstance(value.get('decisions'), list), 'unified Decisions required')
    result = {k: v for k, v in value.items()
              if k not in {'live_decisions', 'discussion_decisions', 'local_decisions'}}
    return dict(result, decisions=[d for d in value['decisions'] if d['status'] == 'active'])


def method(descriptor):
    """Contract selection changes preparation, but cannot change the installed method."""
    value = copy.deepcopy(descriptor['execution'])
    value.get('binding', {}).pop('handoff_contract', None)
    sources = {}
    root = Path(value['root'])
    for ref in descriptor['sources']:
        read(ref, json_value=False, installed_source=True)
        path = Path(ref['path'])
        sources[str(path.relative_to(root)) if path.is_relative_to(root) else str(path)] = ref['sha256']
    return {'execution': value, 'sources': sources}


def source_pins(prepared):
    sources = prepared['sources']
    require({'manifest', 'context', 'decisions', 'metadata', 'patch'} <= sources.keys()
            and SOURCE_CATEGORIES <= {key.split('/')[0] for key in sources if '/' in key},
            'preparation source index is incomplete')
    source = sources['manifest']
    manifest = read({'path': source.get('snapshot') or source['path'], 'sha256': source['sha256']})
    evidence = manifest.get('evidence')
    require(isinstance(evidence, dict) and set(evidence) == SOURCE_CATEGORIES,
            'preparation manifest evidence categories are incomplete')
    for category, values in evidence.items():
        require(isinstance(values, list) and bool(values)
                and all(isinstance(value, str) and bool(value) for value in values),
                'preparation manifest source paths are incomplete')
        expected = {category + '/' + str(index) for index in range(len(values))}
        require(expected == {key for key in sources if key.startswith(category + '/')},
                'preparation source index differs from manifest: ' + category)
        for index, value in enumerate(values):
            require(Path(sources[category + '/' + str(index)]['path']).resolve()
                    == (Path(source['path']).parent / value).resolve(),
                    'preparation source path differs from manifest: ' + category + '/' + str(index))
    pins = {}
    for name, value in sources.items():
        ref = {'path': value.get('snapshot') or value['path'], 'sha256': value['sha256']}
        read(ref, json_value=False, installed_source=name.startswith('reviewer-source/'))
        # These transport records legitimately change when preparation is refreshed
        # for a different contract. Provider texts are compared separately.
        if name not in {'manifest', 'context'} and not name.startswith(
                ('reviewers/', 'reviewer-execution/', 'reviewer-source/', 'architecture/')):
            pins[name] = value['sha256']
    return pins


def governing_originals(references):
    paths = {key: str(private_path(ref['path'])) for key, ref in references.items()}
    require(architecture.governing_sources(paths) == references,
            'architecture governing originals changed')


def packets(handoff, recipient):
    references = dict(handoff.get('nested_architecture_packets', {}))
    if handoff.get('architecture_packet'):
        references['reviewer'] = handoff['architecture_packet']
    result = {}
    prepared = handoff['preparation'].get('architecture')
    require(not references or prepared, 'architecture preparation unavailable')
    coordinator = None
    if prepared:
        private_path(prepared['packet']['path'])
        coordinator = architecture.validate_packet(prepared['packet'], handoff['checkout'],
            agent='coordinator', current_catalog=False, live=False)
        require(coordinator['catalog'] == prepared['catalog'], 'architecture preparation catalog mismatch')
        governing_originals(prepared.get('governing', {}))
        governing_originals(coordinator.get('governing_sources', {}))
        metadata = handoff['preparation']['sources']['metadata']
        context = {'checkout': handoff['checkout'], 'url': handoff['pr_url'],
                   'base_sha': handoff['base_sha'], 'head_sha': handoff['head_sha'],
                   'metadata': metadata.get('snapshot') or metadata['path']}
        decisions = read(handoff['full_decisions']) if 'full_decisions' in handoff else handoff['decisions']
        architecture.validate_preparation(prepared, context, decisions, live=False)
        require(coordinator['paths'] == architecture.changed_paths(
                    handoff['checkout'], handoff['base_sha'], handoff['head_sha']),
                'architecture changed paths differ from captured revisions')
    for role, reference in references.items():
        private_path(reference['path'])
        packet = architecture.load(reference)
        require(packet['agent'] == (recipient if role == 'reviewer' else role),
                'architecture packet recipient mismatch: ' + role)
        require(packet['head'] == handoff['head_sha'] and packet['baseline'] == handoff['base_sha'],
                'architecture packet governing revisions mismatch')
        architecture.validate_packet(reference, handoff['checkout'], agent=packet['agent'],
            paths=coordinator['paths'], current_catalog=False, live=False)
        governing_originals(packet.get('governing_sources', {}))
        require(all(packet[key] == coordinator[key] for key in
                    ('repository', 'catalog', 'baseline', 'head', 'tree', 'task', 'scope', 'scope_contents')),
                'architecture packet preparation scope mismatch')
        result[role] = packet
    return result


def packet_pins(packets):
    """Compare governing packet content independently of capture locations."""
    return {role: {**{key: value for key, value in packet.items()
                     if key not in {'fingerprint', 'governing_sources'}},
                   'governing_sources': {key: ref['fingerprint']
                       for key, ref in packet.get('governing_sources', {}).items()}}
            for role, packet in packets.items()}


def pointer(value, path):
    require(isinstance(path, str) and (not path or path.startswith('/')), 'invalid JSON pointer')
    for part in path.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def delivery_payload(handoff, role):
    """The fields whose complete delivery must be observed independently of completion."""
    result = {key: handoff[key] for key in ('scope', 'base_sha', 'head_sha', 'rules', 'pr_url',
              'reviewer_addendum', 'decision_instructions')}
    result['analysis'] = handoff['preparation']['analysis']
    result['decisions'] = active_decisions(handoff['decisions'])
    for key in ('full_preparation', 'full_decisions'):
        if key in handoff:
            result[key] = handoff[key]
    packet = (handoff.get('architecture_packet') if role == 'reviewer' else
              handoff.get('nested_architecture_packets', {}).get(role))
    if packet:
        result['architecture_packet'] = packet
    return result


def delivery(run, handoff, proof):
    coverage, issues = [], []
    attempts = [read(ref) for ref in proof['attempts']]
    slots = sorted({attempt['attempt'] for attempt in attempts})
    required = set(proof['assessment']['required']) | set(proof['assessment']['completed']) | {'reviewer'}
    seen = set()
    for record in run.get('deliveries', []):
        role, slot = record['recipient'], record['attempt']
        require(role in required and type(slot) is int and slot in slots,
                'delivery recipient or response slot is unknown')
        require((role, slot) not in seen, 'duplicate delivery observation')
        evidence = read(record['source'])
        # Raw native attempts retain JSONL stdout. Other sources can be retained
        # prompt captures. No reviewer result or completion status supplies delivery.
        if 'event' in record:
            matching = [a for a, ref in zip(attempts, proof['attempts']) if ref == record['source']]
            require(len(matching) == 1 and matching[0]['attempt'] == slot,
                    'native delivery event is not bound to this response slot')
            require(type(record['event']) is int and record['event'] > 0, 'invalid native event line')
            evidence = json.loads(evidence['stdout'].splitlines()[record['event'] - 1])
        selected = pointer(evidence, record['pointer'])
        kind = record['kind']
        if kind == 'native-prompt':
            require(role != 'reviewer' and 'event' in record and isinstance(selected, str),
                    'native prompt must cite a specialist execution event')
            host = matching[0]['host']
            events = [json.loads(line) for line in matching[0]['stdout'].splitlines() if line.strip()]
            if host == 'codex':
                item = evidence.get('item', {})
                session = next((e['thread_id'] for e in events if e.get('type') == 'thread.started'), None)
                require(isinstance(session, str) and bool(session), 'native delivery session marker unavailable')
                require(evidence.get('type') == 'item.completed' and item.get('type') == 'collab_tool_call'
                        and item.get('tool') == 'spawn_agent'
                        and item.get('sender_thread_id') == session and record['pointer'] == '/item/prompt',
                        'delivery must cite the direct specialist launch prompt')
                receivers = item.get('receiver_thread_ids')
                require(item.get('status') == 'completed' and isinstance(receivers, list)
                        and len(receivers) == 1 and isinstance(receivers[0], str)
                        and bool(receivers[0]) and receivers[0] != session,
                        'native delivery launch did not establish a specialist recipient')
            else:
                session = next((e['session_id'] for e in events if e.get('type') == 'result'), None)
                require(isinstance(session, str) and bool(session), 'native delivery session marker unavailable')
                parts = record['pointer'].split('/')
                require(len(parts) == 6 and parts[1:3] == ['message', 'content']
                        and parts[3].isdigit() and parts[4:] == ['input', 'prompt'], 'invalid native prompt pointer')
                block = pointer(evidence, '/message/content/' + parts[3])
                require(evidence.get('type') == 'assistant' and evidence.get('session_id') == session
                        and evidence.get('parent_tool_use_id') is None and block.get('type') == 'tool_use'
                        and block.get('name') in ('Task', 'Agent'),
                        'delivery must cite the direct specialist launch prompt')
                call = block.get('id')
                require(isinstance(call, str) and bool(call) and not block['input'].get('resume')
                        and any(event.get('type') == 'user' and event.get('session_id') == session
                            and event.get('parent_tool_use_id') is None
                            and any(part.get('type') == 'tool_result' and part.get('tool_use_id') == call
                                    and part.get('is_error', False) is False
                                    for part in event.get('message', {}).get('content', []))
                            for event in events),
                        'native delivery launch did not establish a specialist recipient')
            # An explicit JSON envelope avoids guessing what prose or a file path
            # conveyed. Existing traces without it can use source-bound observations.
            lines = [line.split('=', 1)[1] for line in selected.splitlines()
                     if line.startswith('DK_REVIEW_DELIVERY_JSON=')]
            require(len(lines) == 1, 'native prompt delivery envelope unavailable')
            received = json.loads(lines[0])
            require('DK_REVIEW_PASS=' + role in selected.splitlines(),
                    'native prompt recipient mismatch')
        else:
            require(kind == 'source-bound-observation', 'unknown delivery observation kind')
            require(isinstance(record['excerpt'], str) and record['excerpt'].strip()
                    and isinstance(selected, str) and record['excerpt'] in selected,
                    'delivery observation source excerpt unavailable')
            received = record['received']
        if 'event' in record:
            require(matching[0].get('returncode') == 0,
                    'native delivery transport attempt did not complete')
        require(json_equal(received, delivery_payload(handoff, role)), 'incomplete or changed guidance delivery: ' + role)
        seen.add((role, slot))
        coverage.append({'recipient': role, 'attempt': slot, 'kind': kind})
    for slot in slots:
        for role in sorted(required):
            if (role, slot) not in seen:
                issues.append('delivery unknown: response ' + str(slot) + ', ' + role)
    return coverage, issues


def inspect_run(run, binding, contract):
    handoff, descriptor, result = (read(run[key]) for key in ('handoff', 'descriptor', 'result'))
    require(isinstance(result.get('findings'), list) and result.get('status') not in
            ('incomplete', 'tokens_exhausted'), 'retained review result is incomplete')
    require(handoff['contract'] == contract, 'handoff contract changed')
    require(bindings.normalize(binding).get('handoff_contract', 'git-diff-v1') == contract,
            'binding contract differs from delivered contract')
    require(descriptor.get('handoff_contract', 'git-diff-v1') == contract, 'descriptor contract changed')
    reviewer = bindings.key(binding)
    require(descriptor.get('reviewer') == reviewer, 'descriptor reviewer mismatch')
    require(bindings.normalize(descriptor['execution']['binding']) == bindings.normalize(binding),
            'descriptor execution binding mismatch')
    installed = method(descriptor)
    if contract == 'git-diff-v2':
        prepared, decisions = (read(handoff[key]) for key in ('full_preparation', 'full_decisions'))
        require(handoff.get('preparation_view_version') == 1, 'unknown preparation view version')
        require(json_equal(handoff['preparation'], reviewer_view(prepared, reviewer)), 'compact guidance projection changed')
        require(json_equal(handoff['decisions'], active_decisions(decisions)), 'compact active Decisions changed')
    else:
        prepared, decisions = handoff['preparation'], handoff['decisions']
    require(json_equal(prepared['reviewers'][reviewer], descriptor), 'preparation descriptor differs from execution')
    require(json_equal(handoff['reviewer_addendum'], prepared['analysis']['reviewer_addenda'][reviewer]),
            'assigned reviewer addendum changed')
    for key in ('base_sha', 'head_sha', 'pr_url'):
        require(prepared['identity'][key] == handoff[key], 'preparation governing revision mismatch: ' + key)
    sources = source_pins(prepared)
    source = prepared['sources']['decisions']
    original_decisions = read({'path': source.get('snapshot') or source['path'], 'sha256': source['sha256']})
    require(json_equal(decisions, original_decisions), 'delivered Decisions differ from preparation original')
    require(prepared['identity']['decisions_hash'] == preparation_digest(decisions)
            and prepared['identity']['body_hash'] == decisions['body_hash'],
            'preparation Decisions identity differs from delivered Decisions')
    patch = private_path(handoff['patch']).read_bytes()
    patch_hash = hashlib.sha256(patch).hexdigest()
    require(sources['patch'] == patch_hash, 'patch differs from preparation')
    proof = read(run['execution'])
    # This owns required-role execution and conditional applicability. Never
    # substitute delivery observations or findings for its native evidence gate.
    review_execution.verify(run['execution'], binding, descriptor, run['handoff']['path'], result)
    coverage, issues = delivery(run, handoff, proof)
    packet_values = packets(handoff, reviewer)
    if prepared.get('architecture'):
        require(set(proof['assessment']['completed']) | {'reviewer'} <= packet_values.keys(),
                'applicable recipient architecture packet unavailable')
    return {'handoff': handoff, 'method': installed, 'proof': proof,
            'pins': {**{key: handoff[key] for key in ('base_sha', 'head_sha', 'pr_url', 'scope', 'rules')},
                     'patch': patch_hash, 'sources': sources, 'analysis': prepared['analysis'],
                     'decisions': decisions, 'architecture': packet_pins(packet_values)},
            'coverage': coverage, 'issues': issues, 'result': result,
            'matrix': {'reviewer': reviewer, 'contract': contract,
                       'execution': descriptor['execution'], 'sources': descriptor['sources'],
                       'preparation_id': prepared.get('preparation_id'),
                       'base_sha': handoff['base_sha'], 'head_sha': handoff['head_sha'], 'scope': handoff['scope'],
                       'handoff': run['handoff'], 'invocation': proof['invocation'],
                       'required': proof['assessment']['required'], 'completed': proof['assessment']['completed'],
                       'response_slots': sorted({read(r)['attempt'] for r in proof['attempts']})}}


def compare(path):
    manifest = json.loads(private_path(path).read_bytes())
    require(manifest.get('version') == 1, 'unknown compatibility manifest version')
    require(manifest.get('provenance') in ('synthetic', 'native-observation'), 'explicit provenance required')
    pairs = manifest['pairs']
    require([p['id'] for p in pairs] == [p[0] for p in COHORT],
            'ordered Codex AD, Claude Toolkit, Claude AD cohort required')
    report = {'version': 1, **AUTHORITY, 'provenance': manifest['provenance'], 'pairs': [], 'issues': []}
    cohort = None
    invocations = set()
    for pair, (_, skill, host) in zip(pairs, COHORT):
        row = {'id': pair['id'], 'route': pair['route'], 'status': 'incomplete', 'issues': [], 'runs': {}}
        report['pairs'].append(row)
        try:
            require(pair['route'] in ('native', 'run-claude-skill'), 'unknown executor route')
            require(host == 'claude' or pair['route'] == 'native', 'Codex AD must remain enabled natively')
            runs = []
            for label, contract in (('full', 'git-diff-v1'), ('compact', 'git-diff-v2')):
                run = pair[label]
                binding = run['binding']
                require(binding['skill'] == skill and binding['host'] == host, 'cohort provider mismatch')
                inspected = inspect_run(run, binding, contract)
                require(inspected['matrix']['execution']['executor'] == host, 'executor differs from cohort')
                invocation = inspected['proof']['invocation']
                require(invocation not in invocations, 'full, compact and separate methods need independent invocations')
                invocations.add(invocation)
                row['runs'][label] = dict(inspected['matrix'], delivery=inspected['coverage'])
                row['issues'].extend(inspected['issues'])
                runs.append(inspected)
            full, compact = runs
            require(full['method'] == compact['method'], 'installed version, procedure, sources or execution changed')
            require(json_equal(full['pins'], compact['pins']), 'paired governing evidence or requested scope changed')
            pins = {k: full['pins'][k] for k in ('base_sha', 'head_sha', 'pr_url', 'scope', 'rules', 'patch', 'decisions', 'sources')}
            pins['shared_analysis'] = {k: v for k, v in full['pins']['analysis'].items() if k != 'reviewer_addenda'}
            require(cohort is None or json_equal(cohort, pins), 'cohort governing evidence or requested scope changed')
            cohort = pins
            finding_sets = [{review_execution.digest(f): f for f in run['result']['findings']} for run in runs]
            left, right = finding_sets
            row['findings'] = {'shared': [left[k] for k in sorted(left.keys() & right.keys())],
                               'full_only': [left[k] for k in sorted(left.keys() - right.keys())],
                               'compact_only': [right[k] for k in sorted(right.keys() - left.keys())]}
            row['status'] = 'incomplete' if row['issues'] else 'observed'
        except (ValueError, OSError, TypeError, KeyError, IndexError, AttributeError) as exc:
            row['issues'].append(str(exc))
    require(pairs[1]['route'] == pairs[2]['route'], 'Claude Toolkit and AD routes must match')
    report['status'] = 'incomplete' if any(p['issues'] for p in report['pairs']) else 'observed'
    return report
