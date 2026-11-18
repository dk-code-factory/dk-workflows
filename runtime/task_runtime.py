"""Task admission, evidence acceptance and retained workflow allowances."""
from copy import deepcopy
from pathlib import Path
import base64
import hashlib
import re
import time
import uuid
import task_policy
from task_capture import _CaptureContext
from task_primitives import CONTRACT, digest, has_output_content, read, task_key, write
from task_storage import TaskStorage
from shared.recovery_diagnostics import allowance, annotate, condition, evidence_code, from_exception

REVIEW_CYCLES = 5

def _pointer(parts):
    return ''.join('/' + str(part).replace('~', '~0').replace('/', '~1') for part in parts)


def _component_changes(old, new, parts=()):
    """Compare captured values, exposing identities rather than instruction bodies."""
    if digest(old) == digest(new):
        return

    def summary(value, path):
        # Content/Git hashes are already safe, useful identities. All other
        # values (including assigned instructions and option strings) are hashed.
        if ((path and path[0] in {'skill', 'instructions', 'inputs'} and path[-1] == 'sha256'
             or path[:2] == ('inputs', 'git'))
                and isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value)):
            return {'hash': value}
        return {'sha256': digest(value)}

    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(old.keys() | new.keys()):
            path = (*parts, key)
            if key not in old:
                yield {'path': _pointer(path), 'change': 'added', 'new': summary(new[key], path)}
            elif key not in new:
                yield {'path': _pointer(path), 'change': 'removed', 'old': summary(old[key], path)}
            else:
                yield from _component_changes(old[key], new[key], path)
    else:
        yield {'path': _pointer(parts), 'change': 'changed', 'old': summary(old, parts), 'new': summary(new, parts)}


class Store(TaskStorage):
    def findings_evidence(self, entry, saved):
        """Derive effective findings and their sources from immutable reports."""
        from checklists import supplemental
        result = deepcopy(saved['normalized'])
        findings = list(result.get('findings', []))
        sources = [saved['path']]
        gate = entry.get('checklist', {})
        if gate.get('fingerprint') == saved['fingerprint'] and gate.get('result') == entry['result']['hash']:
            added, paths = supplemental(self, entry)
            seen = {digest(finding) for finding in findings}
            for finding in added:
                if digest(finding) not in seen:
                    findings.append(finding)
                    seen.add(digest(finding))
            sources.extend(paths)
        result['findings'] = findings
        return result, sources


    def describe(self, captured, entry, *, explain=False):
        saved, result_problem = self._load_saved(entry)
        changed = []
        old = entry.get('components', {})
        for key, value in captured['components'].items():
            if value != old.get(key):
                changed.append(key)
        from native_review import assess, required
        native_problem = None
        if saved and saved['status'] == 'complete' and required(captured['components']):
            native = saved.get('native_execution', {})
            try:
                evidence = base64.b64decode(native['evidence'], validate=True) if native.get('evidence') is not None else None
                native_problem = assess(captured['components'], {'id': saved['claim'],
                    'fingerprint': saved['fingerprint'], 'provenance': saved['provenance']},
                    base64.b64decode(saved['original'], validate=True), evidence)
            except (ValueError, KeyError, TypeError, AttributeError):
                native_problem = {'code': 'malformed_provenance', 'reason': 'Retained native observations are damaged'}
        coverage_complete = bool(not native_problem and not entry.get('invalidated') and not entry.get('claim')
                                 and saved and saved['status'] == 'complete' and saved['fingerprint'] == captured['fingerprint'])
        analysis_complete = coverage_complete
        from checklists import contract as checklist_contract, summary
        checklist = summary(self, entry, captured)
        if 'managed' in entry:
            expected = checklist_contract(captured['components'], entry['managed']['definition'])
            if checklist is None or entry['checklist']['contract'] != expected:
                checklist = {**(checklist or {}), 'complete': False, 'accepted': False,
                             'outcome': 'refresh_required', 'required': expected['required'],
                             'max_checks': expected['max_checks']}
        if checklist is not None:
            coverage_complete = coverage_complete and checklist['accepted']
        reusable = bool(coverage_complete and captured['cacheable'] and saved.get('cacheable') is True)
        described = {'key': captured['key'], 'task': captured['components']['task']['id'],
                'reusable': reusable, 'coverage_complete': coverage_complete, 'changed': changed,
                'unfinished': entry.get('claim'), 'attempts': len(entry.get('attempts', [])),
                'result': saved['path'] if saved else None, 'completion': saved['status'] if saved else None,
                'findings': self.findings_evidence(entry, saved)[0]['findings'] if reusable else None,
                'invalidated': entry.get('invalidated'),
                'reason': ('reusable' if reusable else native_problem['code'] if native_problem else 'unfinished' if entry.get('claim') else
                           'invalidated' if entry.get('invalidated') else
                           'changed_inputs' if changed else saved['status'] if saved and saved['status'] != 'complete' else 'missing_or_incomplete_result')}
        if checklist is not None:
            described.update(analysis_complete=analysis_complete, checklist=checklist,
                             checklist_complete=bool(analysis_complete and checklist['complete']))
            if analysis_complete and not checklist['accepted']:
                described['reason'] = 'checklist_' + (checklist['outcome'] or 'pending')
        if explain:
            blockers = []
            if not captured['cacheable']:
                blockers.append('noncacheable_task')
            if entry.get('claim'):
                blockers.append('unfinished_claim')
            if entry.get('invalidated'):
                blockers.append('explicit_invalidation')
            if 'components' not in entry:
                blockers.append('no_previous_capture')
            elif digest(old) != captured['fingerprint']:
                blockers.append('changed_inputs')
            if native_problem:
                blockers.append(native_problem['code'])
            if checklist is not None and not checklist['accepted']:
                blockers.append('checklist_not_accepted')
            if result_problem:
                blockers.append(result_problem)
            elif saved:
                if saved['status'] != 'complete':
                    blockers.append(saved['status'] + '_result')
                if saved.get('cacheable') is not True:
                    blockers.append('noncacheable_result')
                if saved['fingerprint'] != captured['fingerprint']:
                    blockers.append('result_fingerprint_mismatch')
            described['explanation'] = {
                'blockers': blockers,
                'changes': list(_component_changes(old, captured['components'])),
                'previous_capture': {'manifest': str(self.manifest),
                                     'pointer': _pointer(('entries', captured['key'], 'components'))}
                                    if 'components' in entry else None,
                'current_fingerprint': captured['fingerprint'],
                'previous_capture_fingerprint': digest(old) if 'components' in entry else None,
                'result_fingerprint': saved['fingerprint'] if saved else None,
            }
        reference = str(self.manifest) + '#' + _pointer(('entries', captured['key']))
        if entry.get('claim'):
            return annotate(described, 'active_claim', 'The original task claim remains active.', reference + '/claim')
        if entry.get('invalidated') or 'components' in entry and changed:
            return annotate(described, 'stale_inputs', 'Current governing inputs differ from retained coverage.', reference)
        if native_problem:
            code = ('execution_contract_missing' if native_problem['code'] == 'missing_contract' else
                    'execution_evidence_missing' if native_problem['code'] == 'unavailable_provenance' else 'execution_evidence_invalid')
            return annotate(described, code, native_problem['reason'], reference + '/result')
        if entry.get('result') and result_problem:
            code = evidence_code(result_problem)
            return annotate(described, code, 'Original result evidence is unavailable: ' + result_problem, reference + '/result')
        return described


    def status(self, specs, *, compact=False, run=None, explain=False, tolerate_missing=False):
        context = _CaptureContext()
        captures, unavailable = [], []
        for spec in specs:
            try:
                captures.append(task_policy.capture(spec, _context=context))
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                if not tolerate_missing:
                    raise
                unavailable.append({
                    'key': task_key(self.subject, spec['task']['id'], spec['skill']['identity'], options=spec.get('options')),
                    'reusable': False, 'coverage_complete': False,
                    'reason': 'refresh_required', 'detail': str(exc),
                    'diagnostic': from_exception(exc, self.manifest)})
        if any(c['subject'] != self.subject for c in captures):
            raise ValueError('Specs refer to another PR')
        with self.locked() as manifest:
            if run is not None and run not in manifest['runs']:
                raise ValueError('Unknown run id: ' + run)
            selected = manifest['runs'] if run is None else {run: manifest['runs'][run]}
            runs = {name: {**state, 'review_budget': self.review_budget(state)}
                    for name, state in selected.items()}
            if compact:
                runs = {
                    name: {
                        **{key: value for key, value in state.items() if key != 'progress'},
                        'cycle': state['cycles'][-1] if state['cycles'] else None,
                        'progress_count': len(state['progress']),
                        'progress_ref': {
                            'manifest': str(self.manifest),
                            'pointer': '/runs/' + name.replace('~', '~0').replace('/', '~1') + '/progress',
                        },
                    }
                    for name, state in runs.items()
                }
            return {'contract': CONTRACT, 'subject': self.subject, 'manifest': str(self.manifest),
                    'tasks': [self.describe(c, manifest['entries'].get(c['key'], {}), explain=explain) for c in captures] + unavailable,
                    'runs': runs,
                    'hook_phases': [{'key': key, 'snapshot': digest(value),
                                     'occurrence': value['occurrence']}
                                    for key, value in manifest.get('hook_phases', {}).items()
                                    if run is None or value['occurrence']['run'] == run],
                    'unfinished': [e['claim'] for e in manifest['entries'].values() if e.get('claim')]}


    def reconcile_hooks(self, phase, expected, reason):
        """Record a deliberate phase-list change after prior effects are reconciled."""
        self._writable()
        if (not isinstance(phase, dict) or set(phase) != {'occurrence', 'hooks'}
                or not isinstance(phase['occurrence'], dict) or not isinstance(phase['hooks'], list)
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError('Hook reconciliation requires a phase snapshot and original reason')
        key = digest(phase['occurrence'])
        with self.locked() as manifest:
            previous = manifest.get('hook_phases', {}).get(key)
            if previous is None or digest(previous) != expected:
                raise ValueError('Hook phase snapshot changed; inspect current progress')
            for entry in manifest['entries'].values():
                active = entry.get('claim')
                occurrence = entry.get('components', {}).get('options', {}).get('cycle_hook', {})
                if active and all(occurrence.get(field) == value for field, value in phase['occurrence'].items()):
                    raise ValueError('Reconcile and close active hook claims before changing the list')
            manifest['hook_phases'][key] = deepcopy(phase)
            write(self.manifest, manifest)
        return {'reconciled': True, 'snapshot': digest(phase)}


    @staticmethod
    def review_budget(state):
        """Legacy runs have five cycles; only retained authorizations add more."""
        limit, requests = state.get('limits', {}).get('review_cycles', REVIEW_CYCLES), set()
        allowances = state.get('review_allowances', [])
        if not isinstance(allowances, list):
            raise condition('evidence_damaged', 'Damaged review allowance history; do not reset retained budgets')
        for allowance in allowances:
            if (not isinstance(allowance, dict)
                    or any(not isinstance(allowance.get(key), str) or not allowance[key].strip()
                           for key in ('request_id', 'authorization', 'source'))
                    or allowance['request_id'] in requests
                    or type(allowance.get('cycles')) is not int
                    or not 1 <= allowance['cycles'] <= REVIEW_CYCLES
                    or allowance.get('previous_limit') != limit
                    or allowance.get('cycle_limit') != limit + allowance['cycles']):
                raise condition('evidence_damaged', 'Damaged review allowance history; do not reset retained budgets')
            requests.add(allowance['request_id'])
            limit = allowance['cycle_limit']
        used = len(state['cycles'])
        return {'used': used, 'limit': limit, 'remaining': max(0, limit - used)}


    def continue_run(self, *, run, request_id, authorization, source, cycles=REVIEW_CYCLES):
        """Record a renewed user allowance without changing any existing work.

        The coordinating agent must supply an explicit user request, not infer
        authorization from a resume or repository content. This is bookkeeping,
        not authentication of the supplied evidence. Replays retain the original
        grant, including after later cycles have consumed it.
        """
        self._writable()
        if any(not isinstance(value, str) or not value.strip()
               for value in (run, request_id, authorization, source)):
            raise ValueError('Run, stable request id, exact user authorization and source are required')
        if type(cycles) is not int or not 1 <= cycles <= REVIEW_CYCLES:
            raise ValueError('Continuation requires between one and five additional review cycles')
        with self.locked() as manifest:
            if run not in manifest['runs']:
                raise ValueError('Unknown run id: ' + run)
            state = manifest['runs'][run]
            budget = self.review_budget(state)
            intent = {'request_id': request_id, 'authorization': authorization,
                      'source': source, 'cycles': cycles}
            for allowance in state.get('review_allowances', []):
                if allowance['request_id'] == request_id:
                    if any(allowance[key] != value for key, value in intent.items()):
                        raise ValueError('Continuation request id already has a different authorization')
                    return {'action': 'reuse', 'allowance': deepcopy(allowance),
                            'review_budget': budget, 'manifest': str(self.manifest)}
            if budget['remaining'] or budget['used'] != budget['limit']:
                raise ValueError('Continuation requires an exhausted review allowance')
            allowance = {**intent, 'previous_limit': budget['limit'],
                         'cycle_limit': budget['limit'] + cycles, 'authorized_at': time.time()}
            state.setdefault('review_allowances', []).append(allowance)
            write(self.manifest, manifest)
            return {'action': 'continued', 'allowance': deepcopy(allowance),
                    'review_budget': self.review_budget(state), 'manifest': str(self.manifest)}


    @staticmethod
    def budget(manifest, *, run, cycle, retry, ci_repair, ci_rerun, head, task, limits=None):
        ceilings = {'review_cycles': 5, 'retries_per_task': 1,
                    'ci_repairs_per_cycle': 5, 'ci_reruns_per_head': 3}
        if limits is not None and (not isinstance(limits, dict) or set(limits) - ceilings.keys()
                or any(type(value) is not int or not (1 if key == 'review_cycles' else 0) <= value <= ceilings[key]
                       for key, value in limits.items())):
            raise ValueError('Invalid process limits')
        limits = {**ceilings, **(limits or {})}
        if not run:
            if any((cycle, retry, ci_repair, ci_rerun)):
                raise ValueError('Budgeted execution requires a retained run id')
            return None
        state = manifest['runs'].setdefault(run, {'cycles': [], 'retries': {}, 'ci_repairs': {},
                                                 'ci_reruns': {}, 'review_tasks': {}, 'progress': []})
        # A later spec cannot restore a more generous limit or reset consumed work.
        limits = {key: min(value, state.get('limits', {}).get(key, value)) for key, value in limits.items()}
        if state.get('review_allowances') and limits['review_cycles'] != state.get('limits', {}).get('review_cycles', REVIEW_CYCLES):
            raise ValueError('Retain the initial review limit after explicit continuation allowances')
        state['limits'] = limits
        if cycle and cycle not in state['cycles']:
            if ci_repair or ci_rerun:
                raise ValueError('CI work must retain an existing cycle or use cycle zero')
            if retry:
                raise ValueError('Retry requires an existing cycle')
            budget = Store.review_budget(state)
            if not budget['remaining']:
                label = 'Five' if budget['limit'] == REVIEW_CYCLES else str(budget['limit'])
                raise allowance(label + ' review-and-fix cycle limit reached; '
                                'explicit user continuation is required', run=run, kind='review cycle')
            state['cycles'].append(cycle)
        if retry:
            if not cycle:
                raise ValueError('Same-cycle retry requires its cycle id')
            key = cycle + ':' + task
            if state['retries'].get(key, 0) >= limits['retries_per_task']:
                if limits['retries_per_task'] == 0:
                    raise allowance('Same-cycle retries are disabled', run=run, kind='same-cycle retry')
                raise allowance('Same-cycle retry already consumed', run=run, kind='same-cycle retry')
            state['retries'][key] = state['retries'].get(key, 0) + 1
        if ci_repair:
            key = cycle or '0'
            if state['ci_repairs'].get(key, 0) >= limits['ci_repairs_per_cycle']:
                count = 'Five' if limits['ci_repairs_per_cycle'] == 5 else str(limits['ci_repairs_per_cycle'])
                raise allowance(count + ' CI repair attempts per cycle reached', run=run, kind='CI repair')
            state['ci_repairs'][key] = state['ci_repairs'].get(key, 0) + 1
        if ci_rerun:
            if not head:
                raise ValueError('CI rerun requires exact head')
            if state['ci_reruns'].get(head, 0) >= limits['ci_reruns_per_head']:
                count = 'Three' if limits['ci_reruns_per_head'] == 3 else str(limits['ci_reruns_per_head'])
                raise allowance(count + ' CI reruns per head reached', run=run, kind='CI rerun')
            state['ci_reruns'][head] = state['ci_reruns'].get(head, 0) + 1
        return state


    def _start_captured(self, manifest, captured, *, recover=None, run=None, cycle=None,
                        retry=False, ci_repair=False, ci_rerun=False, head=None, checklist=None):
        """Stage one start in the caller's locked manifest; never persist here."""
        options = captured['components'].get('options', {})
        phase = captured.get('hook_phase')
        if phase is not None:
            if cycle or retry or ci_repair or ci_rerun:
                raise ValueError('Hook claims do not use reviewer or CI budget flags')
            if (not isinstance(phase, dict) or set(phase) != {'occurrence', 'hooks'}
                    or not isinstance(phase['occurrence'], dict) or not isinstance(phase['hooks'], list)
                    or phase['occurrence'].get('run') != run):
                raise ValueError('Hook phase requires its original run and list snapshot')
            phase_key = digest(phase['occurrence'])
            snapshots = manifest.setdefault('hook_phases', {})
            if phase_key in snapshots and snapshots[phase_key] != phase:
                raise ValueError('Hook list changed; reconcile the original phase before continuing')
            snapshots[phase_key] = phase
        entry = manifest['entries'].setdefault(captured['key'], {'key': captured['key'], 'attempts': []})
        previous = self.describe(captured, entry)
        if 'alternatives_reassessment' in options:
            from fix_alternatives import guard_reassessment
            guard_reassessment(captured, entry, options['alternatives_reassessment'])
        if entry.get('claim'):
            if recover != entry['claim']['id']:
                return annotate({'action': 'recover_required', **previous}, 'active_claim',
                    'The original task claim remains active.',
                    str(self.manifest) + '#' + _pointer(('entries', captured['key'], 'claim')))
            if run != entry['claim']['run'] or (
                    captured['fingerprint'] == entry['claim']['fingerprint']
                    and cycle != entry['claim']['cycle']):
                raise ValueError('Recovery must retain the original run and unchanged-input cycle budgets')
            entry['attempts'][-1]['status'] = 'interrupted'
            entry['attempts'][-1]['recovery'] = 'explicit recovery by coordinating agent'
            del entry['claim']
        elif recover:
            raise ValueError('Recovery id is not the active claim')
        if checklist is not None:
            from checklists import attach, reply, reserve
            attach(self, entry, captured, checklist)
            previous = self.describe(captured, entry)
            if previous['analysis_complete'] and not previous['checklist']['accepted']:
                gate = entry['checklist']
                if previous['checklist']['outcome'] == 'stale':
                    raise ValueError('Checklist evidence is damaged; retain results and reconcile its response references')
                reserve(gate)
                return {**previous, **reply(self, entry)}
        if previous['reusable']:
            if checklist is not None and entry['checklist']['outcome'] == 'best_effort':
                return {**previous, **reply(self, entry), 'action': 'reuse'}
            return {'action': 'reuse', **previous}
        reassessment = options.get('alternatives_reassessment')
        if (entry.get('components', {}).get('options', {}).get('alternatives_reassessment')
                and not reassessment):
            raise ValueError('Retained reassessment requires its original authorization binding')
        if reassessment:
            retained_run = next((attempt['run'] for attempt in reversed(entry['attempts']) if attempt.get('run')), None)
            if retained_run and run != retained_run:
                raise ValueError('Reassessment must retain the original run and its review/CI budget history')
            if retained_run and not cycle:
                raise ValueError('Reassessment in a retained run requires an explicit budgeted review cycle')
            if ci_repair or ci_rerun:
                raise ValueError('Reassessment requires review-cycle allowance, not a CI repair/rerun reservation')
        state = self.budget(manifest, run=run, cycle=cycle, retry=retry, ci_repair=ci_repair,
                            ci_rerun=ci_rerun, head=head, task=captured['key'],
                            limits=options.get('process_limits'))
        if cycle and not (ci_repair or ci_rerun):
            tasks = state['review_tasks'].setdefault(cycle, {})
            previous_fingerprint = tasks.get(captured['key'])
            if previous_fingerprint and previous_fingerprint != captured['fingerprint']:
                raise ValueError('Changed review inputs require a new cycle; retain the existing budget')
            if previous_fingerprint and not (retry or recover):
                raise ValueError('Repeated same-cycle execution requires explicit retry or claim recovery')
            if retry and not previous_fingerprint:
                raise ValueError('Same-cycle retry requires a recorded original task attempt')
            tasks[captured['key']] = captured['fingerprint']
        claim = {'id': uuid.uuid4().hex, 'key': captured['key'], 'fingerprint': captured['fingerprint'],
                 'cacheable': captured['cacheable'], 'started': time.time(), 'run': run, 'cycle': cycle,
                 'provenance': captured['provenance']}
        entry.update(claim=claim, components=captured['components'])
        entry['attempts'].append({**claim, 'status': 'started'})
        if reassessment:
            entry['attempts'][-1]['reassessment_request'] = reassessment['request']['id']
        return {'action': 'invoke', 'claim': claim, 'manifest': str(self.manifest),
                'changed': previous['changed'],
                **({'diagnostic': previous['diagnostic']} if 'diagnostic' in previous
                   and previous['diagnostic']['code'] != 'active_claim' else {})}


    def start(self, spec, *, recover=None, run=None, cycle=None, retry=False,
              ci_repair=False, ci_rerun=False, head=None):
        self._writable()
        captured = task_policy.capture(spec)
        if captured['subject'] != self.subject:
            raise ValueError('Spec refers to another PR')
        with self.locked() as manifest:
            result = self._start_captured(manifest, captured, recover=recover, run=run,
                                          cycle=cycle, retry=retry, ci_repair=ci_repair,
                                          ci_rerun=ci_rerun, head=head)
            if result['action'] == 'invoke':
                write(self.manifest, manifest)
            return result


    def native_binding(self, spec, claim_id):
        """Expose expected binding only for an unchanged active invocation."""
        from native_review import binding
        captured = task_policy.capture(spec)
        if captured['subject'] != self.subject:
            raise ValueError('Spec refers to another PR')
        with self.locked() as manifest:
            claim = manifest['entries'].get(captured['key'], {}).get('claim')
            if not claim or claim['id'] != claim_id:
                raise ValueError('Claim is absent or owned by another attempt')
            if claim['fingerprint'] != captured['fingerprint']:
                raise condition('stale_inputs', 'Inputs changed during execution; retain claim and explicitly recover',
                                str(self.manifest) + '#' + _pointer(('entries', captured['key'], 'claim')))
            return {'binding': binding(captured['components'], claim),
                    'requirements': captured['components']['native_review']}


    def start_batch(self, specs, *, recover=None, run=None, cycle=None, retry=False,
                    ci_repair=False, ci_rerun=False, head=None, retained=None, checklists=None, admission=None):
        """Claim a set of tasks atomically, sharing only command-local Git work.

        All captures and checks must succeed before one manifest replacement.
        An unfinished task without explicit recovery returns recover_required
        while eligible siblings can proceed. Validation and budget errors commit
        no batch changes.
        If the response is lost, status exposes every committed claim; callers
        must reconcile those claims rather than silently retry an invocation.
        """
        self._writable()
        context = _CaptureContext()
        captures = [task_policy.capture(spec, _context=context) for spec in specs]
        if not captures:
            raise ValueError('A batch requires at least one task spec')
        if any(item['subject'] != self.subject for item in captures):
            raise ValueError('Specs refer to another PR')
        keys = {item['key'] for item in captures}
        if len(keys) != len(captures):
            raise ValueError('Duplicate task keys in batch')
        if recover is None:
            recover = {}
        if (not isinstance(recover, dict)
                or any(not isinstance(key, str) or not isinstance(value, str) or not value
                       for key, value in recover.items())):
            raise ValueError('Batch recovery requires a task-key-to-claim-id map')
        if recover.keys() - keys:
            raise ValueError('Recovery key is not a task in this batch')
        retained = {} if retained is None else retained
        if (not isinstance(retained, dict) or retained.keys() - keys
                or any(not isinstance(value, dict) for value in retained.values())):
            raise ValueError('Retained definitions must belong to this batch')
        digest(retained)
        checklists = {} if checklists is None else checklists
        if checklists.keys() - retained.keys():
            raise ValueError('Checklists require a retained managed definition')
        with self.locked() as manifest:
            if admission is not None:
                admission(manifest['entries'], captures, retained)
            before = digest(manifest)
            # Explicit recovery mistakes reject the whole batch before staging
            # sibling starts. A missing recovery request is a normal outcome.
            for key, claim_id in recover.items():
                active = manifest['entries'].get(key, {}).get('claim')
                if not active or active['id'] != claim_id:
                    raise ValueError('Recovery id is not the active claim')
            tasks = []
            retained_changed = False
            for captured in captures:
                result = self._start_captured(
                    manifest, captured, recover=recover.get(captured['key']), run=run,
                    cycle=cycle, retry=retry, ci_repair=ci_repair, ci_rerun=ci_rerun, head=head,
                    checklist=checklists.get(captured['key']))
                tasks.append({**result, 'key': captured['key'],
                              'task': captured['components']['task']['id']})
                if result['action'] in {'invoke', 'reuse', 'recheck', 'failed'} and captured['key'] in retained:
                    entry = manifest['entries'][captured['key']]
                    if entry.get('managed') != retained[captured['key']]:
                        entry['managed'] = deepcopy(retained[captured['key']])
                        retained_changed = True
            if retained_changed or digest(manifest) != before:
                write(self.manifest, manifest)
            return {'tasks': tasks, 'manifest': str(self.manifest),
                    'unfinished': [entry['claim'] for entry in manifest['entries'].values()
                                   if entry.get('claim')]}


    def record(self, spec, claim_id, original, normalized, status, *, progress=None, native_evidence=None, checklist=None):
        self._writable()
        captured = task_policy.capture(spec)
        if captured['subject'] != self.subject:
            raise ValueError('Spec refers to another PR')
        if (status not in {'complete', 'failed', 'incomplete'} or not isinstance(normalized, dict)
                or type(normalized.get('complete')) is not bool
                or not isinstance(normalized.get('findings', []), list)
                or (status == 'complete') != normalized['complete']):
            raise ValueError('Explicit completion status and normalized complete/findings are required')
        if status == 'complete' and not has_output_content(original):
            raise ValueError('A complete task requires nonempty original output')
        with self.locked() as manifest:
            entry = manifest['entries'].get(captured['key'], {})
            claim = entry.get('claim')
            submission = digest({'original': base64.b64encode(original).decode(), 'report': normalized,
                                 'status': status, 'progress': progress,
                                 'native': base64.b64encode(native_evidence).decode() if native_evidence is not None else None})
            if (not claim and entry.get('checklist', {}).get('initial_submission') == submission
                    and (checklist is None or entry['checklist']['contract'] == checklist)):
                saved, problem = self._load_saved(entry)
                if (not problem and saved['claim'] == claim_id and not entry.get('invalidated')
                        and saved['fingerprint'] == captured['fingerprint']):
                    from checklists import reply
                    return reply(self, entry)
            if not claim or claim['id'] != claim_id:
                raise ValueError('Claim is absent or owned by another attempt')
            if claim['fingerprint'] != captured['fingerprint']:
                raise condition('stale_inputs', 'Inputs changed during execution; retain claim and explicitly recover',
                                str(self.manifest) + '#' + _pointer(('entries', captured['key'], 'claim')))
            if checklist is not None:
                from checklists import attach
                attach(self, entry, captured, checklist)
            if 'checklist' in entry and 'checklist' in normalized:
                from checklists import validate_report
                validate_report(entry['checklist'], normalized)
            from native_review import assess, required
            native = None
            if required(captured['components']):
                diagnostic = assess(captured['components'], claim, original, native_evidence)
                native = {'evidence': base64.b64encode(native_evidence).decode() if native_evidence is not None else None,
                          'diagnostic': diagnostic, 'requested_status': status,
                          'requested_normalized': deepcopy(normalized)}
                if status == 'complete' and diagnostic:
                    status = 'incomplete'
                    normalized = {**normalized, 'complete': False, 'execution_diagnostic': diagnostic}
            envelope = {'contract': CONTRACT, 'key': captured['key'], 'fingerprint': captured['fingerprint'],
                        'cacheable': claim['cacheable'], 'provenance': claim['provenance'], 'claim': claim_id, 'status': status,
                        'original': base64.b64encode(original).decode(),
                        'original_sha256': hashlib.sha256(original).hexdigest(), 'normalized': normalized}
            if native is not None:
                envelope['native_execution'] = native
            fingerprint = digest(envelope)
            filename = fingerprint + '.json'
            path = self.root/'results'/filename
            if path.exists():
                if read(path) != envelope:
                    raise ValueError('Immutable result collision')
            else:
                write(path, envelope)
            entry['result'] = {'file': filename, 'hash': fingerprint, 'fingerprint': captured['fingerprint']}
            entry['attempts'][-1].update(status=status, result=filename, finished=time.time())
            entry.pop('claim')
            entry.pop('invalidated', None)
            if progress is not None:
                if not claim['run']:
                    raise ValueError('Progress requires a retained run id')
                manifest['runs'][claim['run']]['progress'].append({'claim': claim_id, 'value': progress})
            followup = None
            if 'checklist' in entry:
                from checklists import attach, reply, reserve
                attach(self, entry, captured, entry['checklist']['contract'])
                if status == 'complete':
                    entry['checklist']['initial_submission'] = submission
                    if 'checklist' in normalized:
                        entry['checklist']['outcome'] = 'complete'
                    else:
                        reserve(entry['checklist'])
                    followup = reply(self, entry)
            write(self.manifest, manifest)
            if followup is not None:
                return followup
            result = {'status': status, 'result': str(path), 'reusable': status == 'complete' and captured['cacheable'],
                      **({'execution_diagnostic': native['diagnostic']} if native and native['diagnostic'] else {})}
            if native and native['diagnostic']:
                problem = native['diagnostic']
                code = ('execution_contract_missing' if problem['code'] == 'missing_contract' else
                        'execution_evidence_missing' if problem['code'] == 'unavailable_provenance' else 'execution_evidence_invalid')
                result = annotate(result, code, problem['reason'], path)
            return result


    def check(self, spec, claim_id, round_id, original, report):
        """Accept one reserved follow-up without replacing the primary report."""
        self._writable()
        from checklists import submit
        captured = task_policy.capture(spec)
        if captured['subject'] != self.subject:
            raise ValueError('Spec refers to another PR')
        if not has_output_content(original):
            raise ValueError('Checklist checks require nonempty original output')
        with self.locked() as manifest:
            entry = manifest['entries'].get(captured['key'], {})
            described = self.describe(captured, entry)
            gate = entry.get('checklist')
            saved, problem = self._load_saved(entry)
            if (not gate or problem or saved['claim'] != claim_id or not described.get('analysis_complete')
                    or gate['fingerprint'] != captured['fingerprint'] or gate['result'] != entry['result']['hash']):
                raise ValueError('Checklist requires unchanged complete primary evidence and its original claim')
            result = submit(self, entry, round_id, original, report)
            write(self.manifest, manifest)
            return result


    def original_checks(self, keys, *, expected, claim_id=None, round_id=None, original=None, report=None):
        """Finish original checklists, never start or replace primary analysis.

        Callers decide whether historical completion applies. The original
        fingerprint stays unchanged and supplies no current-input coverage.
        """
        self._writable()
        from checklists import reply, reserve, submit
        if (not keys or len(set(keys)) != len(keys) or not isinstance(expected, dict) or set(expected) != set(keys)
                or report is not None and (len(keys) != 1 or not has_output_content(original))):
            raise ValueError('Original completion requires unique keys and nonempty response output')
        with self.locked() as manifest:
            before = digest(manifest)
            originals = []
            for key in keys:
                entry = manifest['entries'].get(key, {})
                if digest(entry) != expected[key]:
                    raise ValueError('Original completion changed; prepare its current recovery assignment')
                saved, problem = self._load_saved(entry)
                if (problem or saved['status'] != 'complete' or saved['cacheable'] is not True
                        or digest(entry.get('components')) != saved['fingerprint']):
                    raise ValueError('Original completion requires intact complete primary evidence')
                captured = {'key': key, 'components': entry['components'],
                            'fingerprint': saved['fingerprint'], 'cacheable': saved['cacheable']}
                # Check the original contract and native observations, rather
                # than generating today's contract for yesterday's analysis.
                described = self.describe(captured, {k: v for k, v in entry.items() if k != 'managed'})
                gate = entry.get('checklist')
                if (not described.get('analysis_complete') or not gate
                        or described['checklist']['outcome'] == 'stale'
                        or gate['fingerprint'] != saved['fingerprint']
                        or gate['result'] != entry['result']['hash']
                        or report is not None and saved['claim'] != claim_id):
                    raise ValueError('Original completion requires its native evidence, checklist and original claim')
                originals.append(entry)
            results = []
            for entry in originals:
                if report is None:
                    reserve(entry['checklist'])
                    results.append(reply(self, entry))
                else:
                    results.append(submit(self, entry, round_id, original, report))
            if digest(manifest) != before:
                write(self.manifest, manifest)
            return {'tasks': results, 'manifest': str(self.manifest),
                    'unfinished': [entry['claim'] for entry in manifest['entries'].values() if entry.get('claim')]}


    def invalidate(self, keys, reason, *, recover=None):
        self._writable()
        if not reason:
            raise ValueError('An invalidation reason is required')
        with self.locked() as manifest:
            for key in keys:
                if key not in manifest['entries']:
                    raise ValueError('Unknown task key: ' + key)
                active = manifest['entries'][key].get('claim')
                if active and recover != active['id']:
                    raise ValueError('Active claim requires its explicit recovery id for invalidation')
                if recover and (not active or recover != active['id']):
                    raise ValueError('Recovery id is not the active claim')
            for key in keys:
                entry = manifest['entries'][key]
                if entry.pop('claim', None):
                    entry['attempts'][-1].update(status='interrupted', recovery=reason, finished=time.time())
                manifest['entries'][key]['invalidated'] = {'reason': reason, 'at': time.time()}
            write(self.manifest, manifest)
        return {'invalidated': keys}
