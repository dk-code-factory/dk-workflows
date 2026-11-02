#!/usr/bin/env python3
"""Offline checks of manually normalized workflow observations; no gate authority."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ('waiting', 'communication', 'ci', 'handoffs', 'output', 'reuse')
ENTRYPOINTS = ('iteration', 'review', 'decisions', 'feedback', 'reply')
# Every event also has id, at_ms and evidence (source ID plus one-based line).
SCHEMAS = {
    'timeout': ('waiting', {'stage': str}),
    'query': ('waiting', {'category': str, 'cause': str}),
    'host_due': ('communication', {'stage': str, 'deadline_ms': int}),
    'update': ('communication', {'stage': str, 'text': str, 'cause': str}),
    'notification': ('communication', {'stage': str, 'signal': str}),
    'transition': ('waiting', {'stage': str, 'cause': str}),
    'message': ('communication', {'agent': str, 'type': str, 'result': str}),
    'ci_start': ('ci', {'head': str, 'waiter': str, 'interval_ms': int}),
    'ci_observe': ('ci', {'head': str, 'waiter': str}),
    'ci_end': ('ci', {'head': str, 'waiter': str}),
    'spawn': ('handoffs', {'agent': str, 'role': str, 'stage': str, 'fork_turns': str, 'handoff': dict}),
    'reuse_worker': ('handoffs', {'agent': str, 'role': str, 'stage': str}),
    'output': ('output', {'mode': str, 'helper': str, 'text': str, 'artifact': str, 'need': str, 'need_event': str}),
    'preparation': ('reuse', {'preparation_id': str, 'governing': dict}),
    'refresh': ('reuse', {'previous': str, 'current': str, 'model_calls': int, 'reused': bool}),
    'checkpoint': ('reuse', {'preparation': str, 'head': str, 'complete': bool}),
    'interrupt': ('reuse', {}),
    'resume': ('reuse', {'checkpoint': str, 'preparation': str, 'head': str, 'action': str}),
}
OPTIONAL_FIELDS = {'ci_start': {'deadline_ms': int}}
# Required identifiers and references, distinct from potentially empty prose.
IDENTITY_FIELDS = {'id', 'stage', 'cause', 'agent', 'head', 'waiter',
                   'artifact', 'preparation_id', 'previous', 'current',
                   'preparation', 'checkpoint'}
ROLES = {'reviewer': {'review'}, 'architecture-reviewer': {'review'},
         'fixer': {'feedback', 'fix'}, 'reply': {'feedback', 'review', 'completion'},
         'preparation': {'preparation'}, 'decisions': {'decisions'}, 'completion': {'completion'}}
PINS = {'runtime', 'head', 'base', 'decisions', 'preparation', 'preparation_id',
        'engineering_id', 'architecture', 'invocation', 'cli_reference', 'waiting_policy', 'contract'}
CI_INTERVAL_MS = 30000  # ci_wait.py's existing default; overrides remain unknown here.
GOVERNING = {'tree', 'base', 'scope', 'decisions', 'rules', 'reviewers', 'runtime'}


class InvalidTrace(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidTrace(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(value):
    return isinstance(value, str) and bool(value.strip())


def identity_collection(value):
    if isinstance(value, str):
        return identity(value)
    if isinstance(value, list):
        return all(identity(item) for item in value)
    return isinstance(value, dict) and all(identity(k) and identity(v) for k, v in value.items())


def load_trace(path):
    """Validate shape and retained source bytes; never authenticate host semantics."""
    path = Path(path)
    try:
        trace = json.loads(path.read_text())
    except RecursionError as exc:
        raise InvalidTrace('excessive JSON nesting') from exc
    require(isinstance(trace, dict), 'trace must be an object')
    require(set(trace) == {'schema_version', 'provenance', 'entrypoint', 'coverage', 'sources', 'events'},
            'unexpected or missing trace fields')
    require(type(trace['schema_version']) is int and trace['schema_version'] == 1, 'unsupported schema')
    require(trace['provenance'] in ('synthetic', 'manual-host'), 'invalid provenance')
    require(trace['entrypoint'] in ENTRYPOINTS, 'invalid entrypoint')
    require(isinstance(trace['coverage'], dict) and set(trace['coverage']) == set(FAMILIES), 'invalid coverage')
    require(all(v in ('complete', 'unavailable') for v in trace['coverage'].values()), 'invalid coverage status')
    require(isinstance(trace['sources'], dict) and isinstance(trace['events'], list), 'invalid sources/events')
    sources = {}
    for key, source in trace['sources'].items():
        require(identity(key), 'invalid source ID')
        require(isinstance(source, dict) and set(source) == {'path', 'sha256'}, f'invalid source: {key}')
        require(isinstance(source['path'], str) and bool(source['path']), f'invalid source path: {key}')
        sha = source['sha256']
        require(isinstance(sha, str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha),
                f'invalid source hash: {key}')
        file = path.parent / source['path']
        try:
            data = file.read_bytes()
        except OSError:
            sources[key] = None
        else:
            require(digest(data) == sha, f'source hash mismatch: {key}')
            sources[key] = data.decode('utf-8').splitlines()
    previous = -1
    ids = set()
    for event in trace['events']:
        require(isinstance(event, dict) and event.get('kind') in SCHEMAS, 'unknown event kind')
        kind = event['kind']
        fields = {'id': str, 'kind': str, 'at_ms': int, 'evidence': dict, **SCHEMAS[kind][1]}
        fields.update({k: t for k, t in OPTIONAL_FIELDS.get(kind, {}).items() if k in event})
        require(set(event) == set(fields), f'unexpected or missing {kind} fields')
        require(all(type(event[k]) is t for k, t in fields.items()), f'invalid {kind} field type')
        require(all(identity(event[k]) for k in IDENTITY_FIELDS & event.keys()), f'invalid {kind} identity')
        require(event['id'] not in ids, 'duplicate event ID')
        ids.add(event['id'])
        require(event['at_ms'] >= 0 and event['at_ms'] >= previous, 'events must have nonnegative monotonic timestamps')
        previous = event['at_ms']
        ref = event['evidence']
        require(set(ref) == {'source', 'line'} and ref['source'] in sources and type(ref['line']) is int
                and ref['line'] > 0, f'invalid evidence locator: {event["id"]}')
        lines = sources[ref['source']]
        require(lines is None or ref['line'] <= len(lines), f'evidence line out of range: {event["id"]}')
    return trace, sources


def evaluate(path):
    trace, sources = load_trace(path)
    events = trace['events']
    by_id = {e['id']: e for e in events}
    issues = {family: [] for family in FAMILIES}
    unknown = {family: trace['coverage'][family] == 'unavailable' for family in FAMILIES}

    def problem(family, event, code):
        issues[family].append({'event': event['id'], 'code': code})

    def source_ok(source):
        require(source in sources, f'unknown artifact source: {source}')
        return sources[source] is not None

    def cause(event):
        ref = event['cause']
        require(ref in by_id and by_id[ref]['at_ms'] <= event['at_ms']
                and events.index(by_id[ref]) < events.index(event), f'invalid cause: {event["id"]}')
        if not source_ok(by_id[ref]['evidence']['source']):
            unknown[SCHEMAS[event['kind']][0]] = True
        return by_id[ref]

    workers, active_ci, last_ci, preparations, checkpoints = {}, {}, {}, {}, {}
    pending_text, completions, help_reads = {}, set(), set()
    interrupted = False
    for event in events:
        kind = event['kind']
        family = SCHEMAS[kind][0]
        if not source_ok(event['evidence']['source']):
            unknown[family] = True
        if kind == 'query':
            require(event['category'] in ('agent_status', 'file', 'git', 'github', 'provider', 'ci'), 'invalid query category')
            trigger = cause(event)
            if trigger['kind'] in ('timeout', 'host_due', 'update'):
                problem('waiting', event, 'timeout_or_commentary_query')
            else:
                require(trigger['kind'] == 'notification',
                        f'unsupported query cause: {event["id"]}; cite an earlier observed notification')
            if event['category'] == 'ci' and active_ci:
                problem('ci', event, 'extra_ci_query')
        elif kind == 'transition':
            if cause(event)['kind'] != 'notification':
                problem(family, event, 'transition_without_notification')
        elif kind == 'host_due':
            require(event['deadline_ms'] >= event['at_ms'], 'host deadline precedes requirement')
            reports = [e for e in events if e['kind'] == 'update' and e['cause'] == event['id']]
            if not reports or reports[0]['at_ms'] > event['deadline_ms']:
                problem(family, event, 'missed_host_update')
        elif kind == 'notification':
            pending_text.pop(event['stage'], None)
            require(event['signal'] in ('result', 'failure', 'decision', 'completion'), 'invalid notification signal')
            if event['signal'] in ('failure', 'decision'):
                # The normalized next communication/action must report the material change.
                following = [e for e in events[events.index(event) + 1:] if e['kind'] not in ('notification',)]
                if (not following or following[0]['kind'] != 'update' or following[0]['cause'] != event['id']
                        or not following[0]['text'].strip()):
                    problem(family, event, 'material_report_delayed')
        elif kind == 'update':
            trigger = cause(event)
            if trigger['kind'] == 'host_due':
                text = event['text']
                if event['stage'] != trigger['stage'] or len(text) > 160 or '\n' in text or not text.strip():
                    problem(family, event, 'unbounded_pending_update')
                if event['stage'] in pending_text and pending_text[event['stage']] != text:
                    problem(family, event, 'unstable_pending_update')
                pending_text[event['stage']] = text
            elif trigger['kind'] != 'notification':
                problem(family, event, 'update_without_observation')
        elif kind == 'message':
            require(event['type'] in ('automatic_completion', 'completion', 'heartbeat', 'material'), 'invalid message type')
            if event['type'] == 'heartbeat':
                problem(family, event, 'routine_heartbeat')
            if event['type'] in ('automatic_completion', 'completion'):
                require(identity(event['result']), 'invalid completion result identity')
                key = (event['agent'], event['result'])
                if key in completions:
                    problem(family, event, 'duplicate_completion')
                completions.add(key)
        elif kind == 'ci_start':
            require(event['interval_ms'] > 0, 'invalid CI interval')
            require('deadline_ms' not in event or event['deadline_ms'] >= event['at_ms'], 'invalid CI deadline')
            if event['interval_ms'] != CI_INTERVAL_MS:
                unknown[family] = True
            if event['head'] in active_ci or event['waiter'] in (e['waiter'] for e in active_ci.values()):
                problem(family, event, 'duplicate_ci_waiter')
            active_ci[event['head']] = event
        elif kind in ('ci_observe', 'ci_end'):
            start = active_ci.get(event['head'])
            if not start or start['waiter'] != event['waiter']:
                problem(family, event, 'ci_without_waiter')
            elif kind == 'ci_observe':
                key = (event['head'], event['waiter'])
                if key in last_ci and event['at_ms'] - last_ci[key] < start['interval_ms']:
                    deadline = start.get('deadline_ms')
                    following = next((e for e in events[events.index(event) + 1:]
                                      if e['kind'].startswith('ci_') and e['head'] == event['head']
                                      and e['waiter'] == event['waiter']), None)
                    terminal = (deadline is not None and last_ci[key] < deadline <= event['at_ms']
                                and following is not None and following['kind'] == 'ci_end')
                    if not terminal:
                        problem(family, event, 'ci_cadence_accelerated')
                last_ci[key] = event['at_ms']
            else:
                del active_ci[event['head']]
                last_ci.pop((event['head'], event['waiter']), None)
        elif kind == 'spawn':
            require(event['role'] in ROLES, 'unknown worker role')
            handoff = event['handoff']
            require(set(handoff) == {'task', 'permitted_actions', 'pins', 'inline_context'}, 'invalid handoff fields')
            require(all(isinstance(handoff[k], str) for k in ('task', 'permitted_actions', 'inline_context'))
                    and isinstance(handoff['pins'], dict), 'invalid handoff values')
            pins = handoff['pins']
            if event['agent'] in workers or event['fork_turns'] != 'none' or event['stage'] not in ROLES[event['role']]:
                problem(family, event, 'worker_context_or_role')
            if not identity(handoff['task']) or not identity(handoff['permitted_actions']) or handoff['inline_context'] or set(pins) != PINS:
                problem(family, event, 'handoff_not_narrow_or_pinned')
            for key, value in pins.items():
                require(identity(value), f'invalid handoff pin: {key}')
                if key not in ('head', 'base', 'preparation_id', 'engineering_id') and not source_ok(value):
                    unknown[family] = True
            if event['role'] == 'reply' and any(w['role'] == 'reply' for w in workers.values()):
                problem(family, event, 'duplicate_reply_worker')
            workers[event['agent']] = event
        elif kind == 'reuse_worker':
            worker = workers.get(event['agent'])
            if (not worker or event['role'] not in ('reply', 'fixer') or worker['role'] != event['role']
                    or event['stage'] not in ROLES[event['role']]):
                problem(family, event, 'forbidden_worker_reuse')
        elif kind == 'output':
            require(event['mode'] in ('receipt', 'page', 'help', 'full_conversation', 'full_diff', 'full_log'), 'invalid output mode')
            if event['mode'] == 'help':
                require(identity(event['helper']), 'invalid help helper identity')
            if not source_ok(event['artifact']):
                problem(family, event, 'missing_complete_output')
            if event['mode'] in ('receipt', 'page') and len(event['text'].encode()) > 4096:
                problem(family, event, 'unbounded_output')
            needs_reason = event['mode'].startswith('full_') or event['mode'] == 'help' and event['helper'] in help_reads
            if needs_reason:
                need = by_id.get(event['need_event'])
                if (not event['need'].strip() or not need or need['kind'] != 'notification'
                        or events.index(need) >= events.index(event)):
                    problem(family, event, 'unjustified_full_or_repeated_output')
                elif not source_ok(need['evidence']['source']):
                    unknown[family] = True
            if event['mode'] == 'help':
                help_reads.add(event['helper'])
        elif kind == 'preparation':
            governing = event['governing']
            require(set(governing) == GOVERNING, 'invalid governing evidence')
            require(all(identity(governing[k]) for k in ('tree', 'base', 'scope', 'runtime'))
                    and all(identity_collection(governing[k]) for k in ('decisions', 'rules'))
                    and isinstance(governing['reviewers'], list)
                    and all(identity(v) for v in governing['reviewers']), 'invalid governing identities')
            require(event['preparation_id'] not in preparations, 'duplicate preparation ID')
            preparations[event['preparation_id']] = event
        elif kind == 'refresh':
            require(event['previous'] in preparations and event['current'] in preparations and event['model_calls'] >= 0,
                    'invalid preparation refresh')
            same = preparations[event['previous']]['governing'] == preparations[event['current']]['governing']
            if event['reused'] and not all(source_ok(preparations[key]['evidence']['source'])
                                           for key in (event['previous'], event['current'])):
                problem(family, event, 'missing_preparation_evidence_reused')
            if event['reused'] and not same:
                problem(family, event, 'changed_governing_evidence_reused')
            if same and (not event['reused'] or event['model_calls'] != 0):
                problem(family, event, 'operational_refresh_repeated_analysis')
        elif kind == 'checkpoint':
            require(event['preparation'] in preparations, 'unknown checkpoint preparation')
            checkpoints[event['id']] = event
        elif kind == 'interrupt':
            interrupted = True
        elif kind == 'resume':
            require(event['action'] in ('reuse', 'rerun', 'block'), 'invalid resume action')
            require(event['checkpoint'] in checkpoints and event['preparation'] in preparations, 'unknown resume evidence')
            checkpoint = checkpoints[event['checkpoint']]
            same = (checkpoint['head'] == event['head'] and preparations[checkpoint['preparation']]['governing']
                    == preparations[event['preparation']]['governing'])
            available = all(source_ok(e['evidence']['source']) for e in
                            (checkpoint, preparations[checkpoint['preparation']], preparations[event['preparation']]))
            if not interrupted or event['action'] == 'reuse' and (not checkpoint['complete'] or not same or not available):
                problem(family, event, 'unsafe_checkpoint_resume')
    # A lone event cannot establish coverage of an entire policy family.
    kinds = {kind: [e for e in events if e['kind'] == kind] for kind in SCHEMAS}
    coverage = {
        'waiting': len(kinds['timeout']) >= 3 and bool(kinds['transition']),
        'communication': len(kinds['host_due']) >= 3 and
            {'failure', 'decision'} <= {e['signal'] for e in kinds['notification']},
        'ci': bool(kinds['ci_start']) and len(kinds['ci_observe']) >= 3 and bool(kinds['ci_end']),
        'handoffs': {'reviewer', 'reply', 'fixer'} <= {e['role'] for e in kinds['spawn']} and
            {'reply', 'fixer'} <= {e['role'] for e in kinds['reuse_worker']},
        'output': {'receipt', 'page', 'help'} <= {e['mode'] for e in kinds['output']},
        'reuse': bool(kinds['refresh']) and bool(kinds['resume']) and bool(kinds['interrupt']),
    }
    checks = {f: {'status': 'failed' if issues[f] else 'unknown' if unknown[f] or not coverage[f] else 'passed',
                  'findings': issues[f]} for f in FAMILIES}
    status = 'failed' if any(issues.values()) else 'unknown' if any(c['status'] == 'unknown' for c in checks.values()) else 'passed'
    return {'schema_version': 1, 'status': status, 'provenance': trace['provenance'],
            'entrypoint': trace['entrypoint'], 'trace_sha256': digest(Path(path).read_bytes()),
            'checks': checks, 'authority': 'diagnostic-only', 'agent_behavior_verified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--report', type=Path, required=True, help='New private report path outside repository')
    args = parser.parse_args(argv)
    try:
        report_path = args.report.absolute()
        if not args.report.is_absolute() or report_path.resolve().is_relative_to(ROOT):
            raise InvalidTrace('report must be an absolute private path outside the repository')
        try:
            report = evaluate(args.trace)
        except (InvalidTrace, ValueError, OSError, TypeError, KeyError, RecursionError) as exc:
            report = {'schema_version': 1, 'status': 'invalid', 'error': str(exc), 'authority': 'diagnostic-only'}
        fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        print(json.dumps({'status': report['status'], 'report': str(report_path), 'authority': 'diagnostic-only'}))
        return {'passed': 0, 'failed': 1, 'invalid': 2, 'unknown': 3}[report['status']]
    except (InvalidTrace, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
