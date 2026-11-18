"""Read current native obligations; never claim, execute or publish work."""
from copy import deepcopy
from pathlib import Path

import alternatives
import checklists
import skill_config
from task_runtime import Store
from task_policy import capture
from task_primitives import digest, task_key
from tasks import Tasks
from shared import publication
from shared.workflow_storage import operations_root
from shared.recovery_diagnostics import from_exception, publication as publication_diagnostic


def reference(store, key, field='managed'):
    return {'manifest': str(store.manifest), 'pointer': '/entries/' + key + '/' + field}


def _publications(tasks, supplied):
    paths = set((operations_root(repository=tasks.checkout) / publication.digest(
        tasks.store.subject['pr'])).glob('*/*.json'))
    paths.update(Path(path) for path in supplied)
    items = []
    for path in sorted(paths):
        try:
            value = publication.load(path)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            items.append({'action': 'restore_evidence', 'operation': str(path),
                          'reason': 'publication_evidence_unavailable', 'detail': str(exc)[:512],
                          'diagnostic': from_exception(exc, path)})
            continue
        if value['status'] in {'prepared', 'pending'}:
            items.append({'action': 'reconcile_publication' if value['status'] == 'pending'
                          else 'inspect_publication', 'operation': str(path),
                          'status': value['status'], 'kind': value['request']['kind'],
                          **({'diagnostic': publication_diagnostic(value, path)}
                             if value['status'] == 'pending' else {})})
    return items


def _descriptor(tasks, request, location, selected, entries):
    kind, index = location.split('/')
    retained = [(key, value['managed']) for key, value in entries.items()
                if value.get('managed', {}).get('kind') == 'skill'
                and value['managed']['skill'] == request['skill']
                and value['managed']['selection'] == kind
                and value['managed']['index'] == int(index)
                and (kind != 'finalizers' or value['managed'].get('run') == request['run'])]
    if len(retained) > 1 and location not in request.get('definitions', {}):
        raise ValueError('Multiple retained definitions require an explicit current definition')
    key, old = retained[0] if len(retained) == 1 else (None, None)
    definition = deepcopy(request.get('definitions', {}).get(location,
                                                               old['definition'] if old else None))
    if definition is None:
        return None, key
    if 'git' in definition:
        if 'git' not in request:
            raise ValueError('Supply current Git pins before assessing pinned coverage')
        definition['git'].update(request['git'])
    expected = selected.get('skill', request['skill'])
    if definition.get('skill_identity') != expected:
        raise ValueError('The current invocation needs a definition for its selected skill')
    descriptor = {'kind': 'skill', 'skill': request['skill'], 'selection': kind,
                  'index': int(index), 'options': request.get('options', {}),
                  'definition': definition}
    for field in ('upstream', 'workflow'):
        if field in request:
            descriptor[field] = request[field]
    if kind == 'finalizers':
        descriptor['run'] = request['run']
    return descriptor, key


def _status(tasks, descriptor, entries):
    spec = tasks.spec(deepcopy(descriptor), replay=True)
    captured = capture(spec)
    key = captured['key']
    entry = entries.get(key, {})
    status = tasks.store.describe(captured, entry)
    expected = checklists.contract(spec, descriptor['definition'])
    if status.get('analysis_complete', status['coverage_complete']) and (
            entry.get('checklist', {}).get('contract') != expected):
        status.update(coverage_complete=False, reusable=False, analysis_complete=True,
                      checklist_complete=False, reason='checklist_refresh_required')
    return spec, captured, entry, status


def _budget(tasks, request, spec, captured, manifest, location):
    execution = request.get('execution', {}).get(location, {})
    review = (location.startswith('reviewers/') or spec['skill']['identity'] ==
              'dk-workflows:dk-pr-reviewers-run')
    cycle = execution.get('cycle', request.get('cycle') if review else None)
    if review and not cycle:
        return {'action': 'blocked_prerequisite', 'reason': 'review_cycle_required'}
    flags = {key: execution.get(key, False) for key in ('retry', 'ci_repair', 'ci_rerun')}
    staged = deepcopy(manifest)
    try:
        state = Store.budget(staged, run=request['run'], cycle=cycle,
                             head=request.get('git', {}).get('head'), task=captured['key'],
                             limits=spec.get('options', {}).get('process_limits'), **flags)
    except ValueError as exc:
        state = staged['runs'].get(request['run'], {})
        limits = state.get('limits', {})
        existing_cycle = not cycle or cycle in state.get('cycles', [])
        exhausted = bool(
            cycle and not existing_cycle and not any(flags.values()) and not Store.review_budget(state)['remaining']
            or existing_cycle and cycle and flags['retry'] and state.get('retries', {}).get(str(cycle) + ':' + captured['key'], 0)
            >= limits.get('retries_per_task', 1)
            or existing_cycle and flags['ci_repair'] and state.get('ci_repairs', {}).get(cycle or '0', 0)
            >= limits.get('ci_repairs_per_cycle', 5)
            or existing_cycle and flags['ci_rerun'] and request.get('git', {}).get('head')
            and state.get('ci_reruns', {}).get(request['git']['head'], 0)
            >= limits.get('ci_reruns_per_head', 3))
        return {'action': 'exhausted_allowance' if exhausted else 'blocked_prerequisite',
                'reason': 'budget_rejected', 'detail': str(exc)[:512],
                'diagnostic': from_exception(exc, tasks.store.manifest)}
    if cycle and not (flags['ci_repair'] or flags['ci_rerun']):
        original = state.get('review_tasks', {}).get(cycle, {}).get(captured['key'])
        if original and original != captured['fingerprint']:
            return {'action': 'blocked_prerequisite', 'reason': 'changed_inputs_require_new_cycle'}
        if original and not flags['retry']:
            return {'action': 'blocked_prerequisite', 'reason': 'explicit_retry_required'}
        if flags['retry'] and not original:
            return {'action': 'blocked_prerequisite', 'reason': 'original_attempt_required'}
    return None


def _row(tasks, request, location, call, entries, manifest):
    row = {'location': location, 'invocation': call, 'required': call.get('required', True)}
    try:
        descriptor, original = _descriptor(tasks, request, location, call, entries)
        if original:
            row['assignment'] = reference(tasks.store, original)
        if descriptor is None:
            return {**row, 'action': 'definition_required', 'reason': 'current_definition_required'}
        spec, captured, entry, status = _status(tasks, descriptor, entries)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        return {**row, 'action': 'definition_required', 'reason': 'definition_unavailable',
                'detail': str(exc)[:512], 'diagnostic': from_exception(exc, tasks.store.manifest)}
    key = captured['key']
    row.update(key=key, fingerprint=captured['fingerprint'], reason=status['reason'])
    if 'diagnostic' in status:
        row['diagnostic'] = status['diagnostic']
    if entry:
        row['assignment'] = reference(tasks.store, key)
    if status['result']:
        row['result'] = status['result']
    if entry.get('claim'):
        return {**row, 'action': 'reconcile_claim', 'claim': reference(tasks.store, key, 'claim')}
    _, problem = tasks.store._load_saved(entry)
    if entry.get('result') and problem:
        return {**row, 'action': 'restore_evidence', 'reason': problem,
                'result_reference': reference(tasks.store, key, 'result')}
    if (status.get('checklist', {}).get('outcome') == 'stale'
            and entry.get('checklist', {}).get('fingerprint') == captured['fingerprint']):
        return {**row, 'action': 'restore_evidence', 'reason': 'damaged_checklist_evidence',
                'checklist': reference(tasks.store, key, 'checklist')}
    if status['reusable']:
        return {**row, 'action': 'consume_result'}
    if status.get('analysis_complete'):
        if not status.get('coverage_complete'):
            gate = entry.get('checklist', {})
            if gate.get('outcome') == 'failed' and gate.get('contract') == checklists.contract(
                    spec, descriptor['definition']):
                return {**row, 'action': 'exhausted_allowance', 'reason': 'completion_checks_exhausted',
                        'checklist': reference(tasks.store, key, 'checklist')}
            return {**row, 'action': 'check_completion',
                    'checklist': reference(tasks.store, key, 'checklist')}
        # Noncacheable work cannot acquire reusable status from this view.
    if (not row['required'] and status['completion'] in {'failed', 'incomplete'}
            and not status['changed'] and not status['invalidated']):
        return {**row, 'action': 'optional_incomplete'}
    rejected = _budget(tasks, request, spec, captured, manifest, location)
    return {**row, **(rejected or {'action': 'invoke_native'})}


def _dependency(tasks, key, entries, request):
    entry = entries.get(key, {})
    descriptor = deepcopy(entry.get('managed'))
    if descriptor is None:
        return None
    if 'git' in descriptor['definition']:
        if 'git' not in request:
            return None
        descriptor['definition']['git'].update(request['git'])
    try:
        _, _, _, status = _status(tasks, descriptor, entries)
        return status if status['key'] == key and status['reusable'] else None
    except (ValueError, KeyError, TypeError, OSError, RuntimeError):
        return None


def _applicability(tasks, row, evidence, entries, request):
    key = evidence['key']
    status = _dependency(tasks, key, entries, request)
    if status is None:
        return {'action': 'blocked_prerequisite', 'reason': 'applicability_evidence_unavailable'}
    saved, problem = tasks.store._load_saved(entries[key])
    record = saved['normalized'] if not problem else None
    try:
        for part in evidence['pointer'].split('/')[1:]:
            part = part.replace('~1', '/').replace('~0', '~')
            record = record[int(part)] if isinstance(record, list) else record[part]
        if (not isinstance(record, dict) or type(record.get('applicable')) is not bool
                or record.get('invocation') != row['invocation']
                or not isinstance(record.get('reason'), str) or not record['reason'].strip()
                or not isinstance(record.get('evidence'), list) or not record['evidence']
                or any(not isinstance(ref, str) or not ref.strip() for ref in record['evidence'])):
            raise ValueError('Applicability needs the exact invocation, boolean, reason and evidence')
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return {'action': 'blocked_prerequisite', 'reason': 'invalid_applicability_evidence',
                'detail': str(exc)[:512]}
    return {'action': 'not_applicable', 'reason': 'sourced_applicability',
            'applicability': {'result': status['result'], 'pointer': evidence['pointer']}} if not record['applicable'] else {}


def _validate(request, after, limit):
    fields = {'skill', 'options', 'upstream', 'workflow', 'run', 'cycle', 'git', 'definitions',
              'dependencies', 'applicability', 'execution', 'publications'}
    if (not isinstance(request, dict) or set(request) - fields
            or not isinstance(request.get('run'), str) or not request['run'].strip()
            or not isinstance(request.get('skill'), str)):
        raise ValueError('Next actions require an owning skill, retained run and supported inspection inputs')
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Page requires nonnegative after and limit from one to 100')
    if 'cycle' in request and (not isinstance(request['cycle'], str) or not request['cycle'].strip()):
        raise ValueError('An intended cycle must be a nonempty string')
    for field in ('definitions', 'dependencies', 'applicability', 'execution'):
        if not isinstance(request.get(field, {}), dict):
            raise ValueError(field + ' must map current locations to inspection inputs')
    if 'git' in request and (not isinstance(request['git'], dict) or set(request['git']) - {'head', 'base', 'target'}
            or not {'head', 'target'} <= request['git'].keys()
            or any(not isinstance(v, str) or len(v) != 40 or set(v) - set('0123456789abcdef')
                   for v in request['git'].values())):
        raise ValueError('Current Git pins require full head/target SHAs and optional base')
    if (not isinstance(request.get('publications', []), list)
            or any(not isinstance(v, str) or not v for v in request.get('publications', []))):
        raise ValueError('Publications must reference original journal paths')


def inspect(repository, pr, request, *, checkout=None, after=0, limit=20):
    """A bounded advisory projection. Existing execution boundaries still govern."""
    _validate(request, after, limit)
    tasks = Tasks(repository, pr, checkout=checkout, read_only=True)
    with tasks.store.locked() as manifest:
        entries = manifest['entries']
    kinds = ['reviewers'] if request['skill'] == 'dk-workflows:dk-pr-reviewers-run' else ['tasks', 'finalizers']
    groups = []
    for kind in kinds:
        plan = skill_config.plan(request['skill'], kind, repository, request.get('upstream'),
                                 options=request.get('options'), workflow=request.get('workflow'))
        index = 0
        for group in plan['groups']:
            current = []
            for value in group:
                current.append((kind + '/' + str(index), skill_config.configured_call(
                    value, repository, request.get('upstream'))))
                index += 1
            groups.append(current)
    locations = {name for group in groups for name, _ in group}
    for field in ('definitions', 'dependencies', 'applicability', 'execution'):
        if request.get(field, {}).keys() - locations:
            raise ValueError(field + ' references an occurrence outside the current selection')
    dependencies = request.get('dependencies', {})
    implicit, prior = {}, []
    for group in groups:
        for name, _ in group:
            implicit[name] = list(prior)
            refs = dependencies.get(name, [])
            if (not isinstance(refs, list) or any(not isinstance(ref, str) or
                    ref not in locations and not (ref.startswith('key:') and ref[4:] in entries) for ref in refs)):
                raise ValueError('Dependencies must reference current locations or retained key:KEY values')
        prior.extend(name for name, _ in group)
    active, seen = set(), set()
    def visit(name):
        if name in active:
            raise ValueError('Cyclic current prerequisites')
        if name in seen:
            return
        active.add(name)
        for ref in implicit[name] + dependencies.get(name, []):
            if ref in locations:
                visit(ref)
        active.remove(name)
        seen.add(name)
    for name in locations:
        visit(name)
    for evidence in request.get('applicability', {}).values():
        if (not isinstance(evidence, dict) or set(evidence) != {'key', 'pointer'}
                or not isinstance(evidence['key'], str) or not isinstance(evidence['pointer'], str)
                or not evidence['pointer'].startswith('/')):
            raise ValueError('Applicability requires an original task key and normalized-result JSON pointer')
    for execution in request.get('execution', {}).values():
        if (not isinstance(execution, dict) or set(execution) - {'cycle', 'retry', 'ci_repair', 'ci_rerun'}
                or any(type(execution[k]) is not bool for k in execution if k != 'cycle')
                or 'cycle' in execution and (not isinstance(execution['cycle'], str) or not execution['cycle'].strip())):
            raise ValueError('Execution inspection accepts cycle and boolean retry/CI flags')
    recoveries = _publications(tasks, request.get('publications', []))
    design_references = []
    for key, entry in entries.items():
        if entry.get('claim'):
            recoveries.append({'action': 'reconcile_claim', 'key': key,
                               'claim': reference(tasks.store, key, 'claim')})
    with alternatives.evaluation(tasks):
        gate = None
        if request['skill'] in {'dk-workflows:dk-pr-fix', 'dk-workflows:dk-pr-review'}:
            from assessment_alternatives import gate as alternatives_gate
            try:
                value = alternatives_gate(tasks, request['skill'], options=request.get('options'),
                                          upstream=request.get('upstream'))
                gate = {key: value[key] for key in ('outcome', 'release_reviewers', 'stage',
                                                  'design_context', 'current_alignment_required') if key in value}
                if value.get('next_task'):
                    gate['next_task'] = {key: value['next_task'][key] for key in
                                         ('key', 'kind', 'skill', 'stage', 'decision') if key in value['next_task']}
                for label, key in value.get('references', {}).items():
                    design_references.append({'action': 'consume_result', 'design_context': True,
                                              'label': label, 'key': key})
                for source in value.get('governing_evidence', []):
                    design_references.append({'action': 'consume_result', 'design_context': True, 'result': source})
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                gate = {'outcome': 'recovery_required', 'release_reviewers': False, 'detail': str(exc)[:512]}
        rows = {name: _row(tasks, request, name, call, entries, manifest)
                for group in groups for name, call in group}
        for name, evidence in request.get('applicability', {}).items():
            # Claims and damaged originals always retain their own recovery path.
            if rows[name]['action'] not in {'reconcile_claim', 'restore_evidence'}:
                rows[name].update(_applicability(tasks, rows[name], evidence, entries, request))
        resolved = set()
        def resolve(name):
            if name in resolved:
                return
            row = rows[name]
            unmet = []
            for ref in implicit[name] + dependencies.get(name, []):
                if ref in rows:
                    resolve(ref)
                    accepted = rows[ref]['action'] == 'consume_result'
                    if ref in implicit[name] and ref not in dependencies.get(name, []):
                        accepted |= rows[ref]['action'] in {'not_applicable', 'optional_incomplete'}
                else:
                    accepted = _dependency(tasks, ref[4:], entries, request) is not None
                if not accepted:
                    unmet.append(ref)
            if row['action'] not in {'reconcile_claim', 'restore_evidence', 'optional_incomplete',
                                      'not_applicable'}:
                if unmet:
                    row.update(retained_action=row['action'], action='blocked_prerequisite',
                               reason='unmet_prerequisites', prerequisites=sorted(set(unmet))[:20],
                               prerequisite_count=len(set(unmet)))
                elif row['action'] != 'consume_result' and recoveries and any(r['action'] != 'reconcile_claim' for r in recoveries):
                    row.update(action='blocked_prerequisite', reason='publication_recovery_required')
                elif gate and not gate['release_reviewers'] and row['action'] != 'consume_result':
                    row.update(action='blocked_prerequisite', reason='alternatives_prerequisite')
            resolved.add(name)
        for name in rows:
            resolve(name)
    # Keep configured prompts/options out of routine status. The selector and
    # digest locate the live invocation; preparation supplies its full handoff.
    for row in rows.values():
        call = row['invocation']
        row['invocation_fingerprint'] = digest(call)
        row['invocation'] = {key: call[key] for key in ('skill', 'agent', 'required') if key in call}
    items = recoveries + design_references + list(rows.values())
    end = min(len(items), after + limit)
    state = manifest['runs'].get(request['run'])
    return {'contract': 1, 'subject': tasks.store.subject, 'run': request['run'],
            'manifest': str(tasks.store.manifest), 'readiness_established': False,
            'selected_complete': all(row['action'] in {'consume_result', 'not_applicable', 'optional_incomplete'}
                                     for row in rows.values()) and not recoveries
                                 and (gate is None or gate['release_reviewers']),
            'alternatives': gate, 'review_budget': Store.review_budget(state) if state else None,
            'items': items[after:end], 'total': len(items), 'next_after': end if end < len(items) else None}
