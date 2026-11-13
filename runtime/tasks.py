"""Managed bookkeeping for native tasks; no model execution or evidence synthesis."""
import base64
from copy import deepcopy
from pathlib import Path

from config import make_completion_spec, make_finalizer_spec, make_hook_spec
from checklists import contract as checklist_contract
from native_review import binding, required
from state import Store, digest, make_spec
import skill_config


class Tasks:
    def __init__(self, repository, pr, *, checkout=None):
        self.store = Store(repository, pr, checkout=checkout)
        self.checkout = Path(checkout).absolute() if checkout is not None else Path.cwd()

    def _retained_policy(self, descriptor):
        import instruction_sources
        definition = descriptor['definition']
        checkout = definition.get('git', {}).get('checkout', definition.get('instruction_policy', {}).get('checkout', self.checkout))
        if ('instruction_policy' not in definition
                and instruction_sources.binding(checkout, self.store.subject['repository']) is not None):
            raise ValueError('Legacy repository guidance requires a rebuilt assignment and handoff')

    def spec(self, descriptor):
        if (not isinstance(descriptor, dict) or set(descriptor) - {
                'kind', 'definition', 'run', 'cycle', 'phase', 'index', 'process', 'finalizer', 'upstream',
                'skill', 'selection', 'options', 'stage', 'dependencies'}):
            raise ValueError('Task requires a definition and optional occurrence selector')
        if not isinstance(descriptor.get('definition'), dict):
            raise ValueError('Task requires an explicit definition')
        kind = descriptor.get('kind', 'task')
        definition = {key: value for key, value in descriptor['definition'].items()
                      if key not in {'checklist', 'required'}}
        if 'git' not in definition and 'instruction_policy' not in definition:
            import instruction_sources
            policy = instruction_sources.binding(self.checkout, self.store.subject['repository'])
            if policy is not None:
                definition['instruction_policy'] = policy
        common = {**self.store.subject, 'definition': definition}
        if kind == 'task':
            if set(descriptor) - {'kind', 'definition'}:
                raise ValueError('Ordinary task identity excludes run and occurrence selectors')
            return make_spec(**self.store.subject, **definition)
        if kind == 'architecture':
            from architecture import stage_spec
            return stage_spec(self, descriptor, definition)
        if kind == 'skill':
            return skill_config.make_spec(**common, skill=descriptor['skill'], kind=descriptor['selection'],
                                          index=descriptor['index'], options=descriptor.get('options'),
                                          upstream=descriptor.get('upstream'), run=descriptor.get('run'))
        common.update(run=descriptor['run'], upstream=descriptor.get('upstream'))
        if kind == 'hook':
            return make_hook_spec(**common, cycle=descriptor['cycle'], phase=descriptor['phase'],
                                  index=descriptor['index'], process=descriptor.get('process', 'pr-fix'))
        if kind == 'finalizer':
            return make_finalizer_spec(**common, finalizer=descriptor.get('finalizer'),
                                       index=descriptor.get('index'), process=descriptor.get('process', 'pr-fix'))
        if kind == 'completion':
            return make_completion_spec(**common, options=descriptor.get('options'))
        raise ValueError('Task kind must be task, skill, hook, finalizer or completion')

    @staticmethod
    def compact(task):
        task = dict(task)
        if 'findings' in task:
            findings = task.pop('findings')
            task['finding_count'] = len(findings) if findings is not None else None
        return task

    def prepare(self, request, *, compact=False):
        if type(compact) is not bool:
            raise ValueError('Compact presentation must be boolean')
        allowed = {'tasks', 'run', 'cycle', 'recover', 'retry', 'ci_repair', 'ci_rerun', 'head'}
        if (not isinstance(request, dict) or set(request) - allowed
                or not isinstance(request.get('tasks'), list) or not request['tasks']):
            raise ValueError('Prepare requires a nonempty tasks list and execution options')
        for flag in ('retry', 'ci_repair', 'ci_rerun'):
            if flag in request and type(request[flag]) is not bool:
                raise ValueError(flag + ' must be boolean')
        descriptors = deepcopy(request['tasks'])
        entries = self._entries() if any(isinstance(item, dict) and 'key' in item for item in descriptors) else {}
        for index, item in enumerate(descriptors):
            if not isinstance(item, dict):
                raise ValueError('Each task must be a definition descriptor')
            if 'key' in item:
                if set(item) - {'key', 'git'}:
                    raise ValueError('Saved tasks accept only explicit Git pin updates; redefine other changed inputs')
                saved = entries.get(item['key'], {}).get('managed')
                if saved is None:
                    raise ValueError('Task has no retained definition')
                saved = deepcopy(saved)
                self._retained_policy(saved)
                if 'git' in item:
                    if (not isinstance(item['git'], dict) or set(item['git']) - {'target', 'base', 'head'}
                            or 'git' not in saved['definition']):
                        raise ValueError('Git updates require existing pinned Git inputs')
                    saved['definition']['git'].update(item['git'])
                item = descriptors[index] = saved
            definition = item.get('definition')
            if not isinstance(definition, dict):
                raise ValueError('Task requires an explicit definition')
            # Keep source references usable when the next session has another cwd.
            for field in ('resources', 'instructions', 'inputs'):
                if field in definition:
                    if not isinstance(definition[field], dict):
                        raise ValueError(field + ' must be a label-to-resource-path map')
                    definition[field] = {label: str(Path(path).absolute())
                                         for label, path in definition[field].items()}
            if isinstance(definition.get('git'), dict) and 'checkout' in definition['git']:
                definition['git']['checkout'] = str(Path(definition['git']['checkout']).absolute())
            if item.get('kind', 'task') in {'hook', 'finalizer', 'completion'} or (
                    item.get('kind') == 'skill' and item.get('selection') == 'finalizers'):
                item.setdefault('run', request.get('run'))
                if item['kind'] == 'hook':
                    item.setdefault('cycle', request.get('cycle'))
                    if item['cycle'] != request.get('cycle'):
                        raise ValueError('Hook occurrences must retain the request cycle')
                if item['run'] != request.get('run'):
                    raise ValueError('Occurrences must retain the request run')
        kinds = {item.get('kind', 'task') for item in descriptors}
        if len(kinds) != 1 or ('hook' in kinds and len(descriptors) != 1):
            raise ValueError('Prepare one task kind at a time; ordered hooks run individually')
        selections = {item.get('selection') for item in descriptors} if kinds == {'skill'} else set()
        if len(selections) > 1:
            raise ValueError('Prepare one skill-owned list kind at a time')
        occurrence = bool(kinds - {'task', 'skill', 'architecture'} or selections == {'finalizers'})
        if occurrence and any(request.get(key) for key in ('retry', 'ci_repair', 'ci_rerun')):
            raise ValueError('Occurrence tasks do not reserve reviewer or CI retry budgets')
        from architecture import evaluation
        with evaluation(self):
            specs = [self.spec(item) for item in descriptors]
        for spec, item in zip(specs, descriptors):
            if 'instruction_policy' in spec:
                item['definition']['instruction_policy'] = deepcopy(spec['instruction_policy'])
        for spec in specs:
            if required(spec) and 'native_review' not in spec:
                raise ValueError('Reviewer tasks require their native execution contract before preparation')
        # Key computation needs only identity, not a second expensive capture.
        retained = {digest({**self.store.subject, 'task': spec['task']['id'],
                            'skill': spec['skill']['identity']}): item
                    for spec, item in zip(specs, descriptors)}
        checklists = {key: checklist_contract(spec, item['definition'])
                      for key, spec, item in zip(retained, specs, descriptors)}
        assignments = {key: spec for key, spec in zip(retained, specs)}
        options = {key: value for key, value in request.items() if key != 'tasks'}
        if occurrence:
            options.pop('cycle', None)
        with evaluation(self):
            result = self.store.start_batch(specs, retained=retained, checklists=checklists, **options)
        # Read committed bindings, including after a lost-response recovery. They
        # describe expectations only; finish still captures live inputs afresh.
        with self.store.locked() as manifest:
            for task in result['tasks']:
                if not compact or task['action'] == 'invoke':
                    task['assignment'] = assignments[task['key']]
                else:
                    task.pop('assignment', None)
                task['checklist'] = checklists[task['key']]
                if task['action'] == 'invoke' and retained[task['key']].get('kind') == 'architecture':
                    from architecture import scoring_handoff
                    scores = scoring_handoff(assignments[task['key']])
                    if scores is not None:
                        task['architecture_scores'] = scores
                if task['action'] == 'invoke':
                    entry = manifest['entries'][task['key']]
                    if required(entry['components']):
                        task['native'] = {
                            'binding': binding(entry['components'], task['claim']),
                            'requirements': entry['components'].get('native_review')}
        result['tasks'] = [self.compact(task) for task in result['tasks']]
        return result

    def _entries(self):
        with self.store.locked() as manifest:
            return deepcopy(manifest['entries'])

    def saved_spec(self, key):
        entry = self._entries().get(key, {})
        if 'managed' not in entry:
            raise ValueError('Task has no retained definition')
        self._retained_policy(entry['managed'])
        return self.spec(entry['managed'])

    def resume(self, *, keys=(), run=None, explain=False):
        entries = self._entries()
        if set(keys) - entries.keys():
            raise ValueError('Unknown task key')
        specs, blocked, legacy, contracts = [], [], [], {}
        from architecture import evaluation
        with evaluation(self):
            for key, entry in entries.items():
                if keys and key not in keys:
                    continue
                descriptor = entry.get('managed')
                if descriptor is None:
                    legacy.append({'key': key, 'task': entry.get('components', {}).get('task', {}).get('id'),
                                   'reason': 'definition_required'})
                    continue
                occurrence = (descriptor.get('kind', 'task') not in {'task', 'skill', 'architecture'} or
                              descriptor.get('kind') == 'skill' and descriptor.get('selection') == 'finalizers')
                if (run is not None and occurrence
                        and descriptor.get('run') != run):
                    continue
                try:
                    self._retained_policy(descriptor)
                    spec = self.spec(descriptor)
                    contracts[key] = checklist_contract(spec, descriptor['definition'])
                    specs.append(spec)
                except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                    blocked.append({'key': key, 'reusable': False, 'coverage_complete': False,
                                    'reason': 'refresh_required', 'detail': str(exc)})
        with evaluation(self):
            result = self.store.status(specs, compact=True, run=run, explain=explain, tolerate_missing=True)
        result['tasks'] = [self.compact(task) for task in result['tasks']] + blocked
        pins = {digest({**self.store.subject, 'task': spec['task']['id'], 'skill': spec['skill']['identity']}): spec['git']
                for spec in specs if 'git' in spec}
        for task in result['tasks']:
            key = task['key']
            if key in contracts and entries[key].get('checklist', {}).get('contract') != contracts[key]:
                task.update(reusable=False, coverage_complete=False, checklist_complete=False,
                            reason='checklist_refresh_required')
            if task['key'] in pins:
                task['git'] = pins[task['key']]
        result['unmanaged'] = legacy
        return result

    def progress(self, run, *, after=0, limit=20):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Progress requires a nonnegative offset and limit from one to 100')
        with self.store.locked() as manifest:
            if run not in manifest['runs']:
                raise ValueError('Unknown run id: ' + run)
            records = manifest['runs'][run]['progress']
            end = min(after + limit, len(records))
            return {'run': run, 'records': deepcopy(records[after:end]),
                    'next': end if end < len(records) else None, 'total': len(records)}

    def finish(self, key, claim, original, report, *, status=None, progress=None, native_evidence=None,
               round_id=None, compact=False):
        if type(compact) is not bool:
            raise ValueError('Compact presentation must be boolean')
        entry = self._entries().get(key, {})
        if 'managed' not in entry:
            raise ValueError('Task has no retained definition; prepare it before finishing')
        if not isinstance(report, dict) or type(report.get('complete')) is not bool:
            raise ValueError('Report requires explicit complete boolean')
        spec = self.spec(entry['managed'])
        checklist = checklist_contract(spec, entry['managed']['definition'])
        if round_id is None:
            from architecture import validate_report
            validate_report(spec, report)
        if round_id is not None:
            if status is not None or progress is not None or native_evidence is not None:
                raise ValueError('Checklist follow-ups retain primary status, progress and native evidence')
            if entry.get('checklist', {}).get('contract') != checklist:
                raise ValueError('Checklist changed; prepare retained analysis for completion-only checking')
            result = self.store.check(spec, claim, round_id, original, report)
        else:
            status = status or ('complete' if report['complete'] else 'incomplete')
            result = self.store.record(spec, claim, original, report, status,
                                       progress=progress, native_evidence=native_evidence,
                                       checklist=checklist)
        if result.get('action') == 'recheck' and not compact:
            result['assignment'] = spec
        return result

    def result(self, key, section='findings'):
        if section not in {'findings', 'normalized', 'original', 'checklist'}:
            raise ValueError('Result section must be findings, normalized, original or checklist')
        with self.store.locked() as manifest:
            entry = manifest['entries'].get(key)
            if entry is None:
                raise ValueError('Unknown task key')
            saved, problem = self.store._load_saved(entry)
            if problem:
                raise ValueError('Result unavailable: ' + problem)
            if section == 'checklist':
                from checklists import load_response
                gate = deepcopy(entry.get('checklist'))
                responses = [r['response'] for r in gate['rounds'] if 'response' in r] if gate else []
                if responses:
                    gate['latest_report'] = load_response(self.store, responses[-1])['report']
                elif gate and gate['outcome'] == 'complete':
                    gate['latest_report'] = saved['normalized']
                return {'key': key, 'checklist': gate}
            normalized, sources = self.store.findings_evidence(entry, saved) if section != 'original' else (None, [])
        if section == 'original':
            value = base64.b64decode(saved['original'], validate=True).decode('utf-8', errors='replace')
        else:
            value = normalized if section == 'normalized' else normalized['findings']
        return {'key': key, 'status': saved['status'], 'result': saved['path'], section: value,
                **({'sources': sources} if sources else {})}
