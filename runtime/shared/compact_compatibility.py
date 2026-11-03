"""Offline diagnostics for retained paired review inputs; never adoption authority."""
import copy
import hashlib
import json
from pathlib import Path

from shared import architecture, bindings, review_execution
from shared.preparation_views import reviewer_view

ROOT = Path(__file__).resolve().parents[2]
COHORT = [('codex-ad', 'code-review-ad:code-review-ad', 'codex'),
          ('claude-toolkit', 'pr-review-toolkit:review-pr', 'claude'),
          ('claude-ad', 'code-review-ad:code-review-ad', 'claude')]
AUTHORITY = {'authority': 'diagnostic-only', 'adoption_authorized': False,
             'publication_authorized': False, 'live_acceptance': 'pending'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_path(value):
    path = Path(value)
    require(path.is_absolute() and not path.resolve().is_relative_to(ROOT),
            'captures and reports must use absolute private paths outside the repository')
    return path


def read(reference, *, json_value=True):
    require(isinstance(reference, dict), 'hashed evidence reference required')
    path = private_path(reference['path'])
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
        read(ref, json_value=False)
        path = Path(ref['path'])
        sources[str(path.relative_to(root)) if path.is_relative_to(root) else str(path)] = ref['sha256']
    return {'execution': value, 'sources': sources}


def source_pins(prepared):
    pins = {}
    for name, value in prepared['sources'].items():
        ref = {'path': value.get('snapshot') or value['path'], 'sha256': value['sha256']}
        read(ref, json_value=False)
        # These transport records legitimately change when preparation is refreshed
        # for a different contract. Provider texts are compared separately.
        if name not in {'manifest', 'context'} and not name.startswith(('reviewers/', 'reviewer-execution/')):
            pins[name] = value['sha256']
    return pins


def packets(handoff, recipient):
    references = dict(handoff.get('nested_architecture_packets', {}))
    if handoff.get('architecture_packet'):
        references['reviewer'] = handoff['architecture_packet']
    result = {}
    for role, reference in references.items():
        private_path(reference['path'])
        packet = architecture.load(reference)
        require(packet['agent'] == (recipient if role == 'reviewer' else role),
                'architecture packet recipient mismatch: ' + role)
        require(packet['head'] == handoff['head_sha'] and packet['baseline'] == handoff['base_sha'],
                'architecture packet governing revisions mismatch')
        result[role] = packet
    return result


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
                session = next(e['thread_id'] for e in events if e.get('type') == 'thread.started')
                require(evidence.get('type') == 'item.completed' and item.get('type') == 'collab_tool_call'
                        and item.get('tool') == 'spawn_agent'
                        and item.get('sender_thread_id') == session and record['pointer'] == '/item/prompt',
                        'delivery must cite the direct specialist launch prompt')
            else:
                session = next(e['session_id'] for e in events if e.get('type') == 'result')
                parts = record['pointer'].split('/')
                require(len(parts) == 6 and parts[1:3] == ['message', 'content']
                        and parts[3].isdigit() and parts[4:] == ['input', 'prompt'], 'invalid native prompt pointer')
                block = pointer(evidence, '/message/content/' + parts[3])
                require(evidence.get('type') == 'assistant' and evidence.get('session_id') == session
                        and evidence.get('parent_tool_use_id') is None and block.get('type') == 'tool_use'
                        and block.get('name') in ('Task', 'Agent'),
                        'delivery must cite the direct specialist launch prompt')
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
        require(received == delivery_payload(handoff, role), 'incomplete or changed guidance delivery: ' + role)
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
        require(handoff['preparation'] == reviewer_view(prepared, reviewer), 'compact guidance projection changed')
        require(handoff['decisions'] == active_decisions(decisions), 'compact active Decisions changed')
    else:
        prepared, decisions = handoff['preparation'], handoff['decisions']
    require(prepared['reviewers'][reviewer] == descriptor, 'preparation descriptor differs from execution')
    require(handoff['reviewer_addendum'] == prepared['analysis']['reviewer_addenda'][reviewer],
            'assigned reviewer addendum changed')
    for key in ('base_sha', 'head_sha', 'pr_url'):
        require(prepared['identity'][key] == handoff[key], 'preparation governing revision mismatch: ' + key)
    patch = private_path(handoff['patch']).read_bytes()
    patch_hash = hashlib.sha256(patch).hexdigest()
    sources = source_pins(prepared)
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
                     'decisions': decisions, 'architecture': packet_values},
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
            require(full['pins'] == compact['pins'], 'paired governing evidence or requested scope changed')
            pins = {k: full['pins'][k] for k in ('base_sha', 'head_sha', 'pr_url', 'scope', 'rules', 'patch', 'decisions', 'sources')}
            pins['shared_analysis'] = {k: v for k, v in full['pins']['analysis'].items() if k != 'reviewer_addenda'}
            require(cohort is None or cohort == pins, 'cohort governing evidence or requested scope changed')
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
