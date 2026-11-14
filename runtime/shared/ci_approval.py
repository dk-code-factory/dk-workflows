"""Incoming-review evidence and fork workflow approval, independent of CI success."""
from pathlib import Path
import subprocess

import config
import skill_config
from state import capture, digest, read, resource
from shared import publication
from shared.pr_ownership import classify


class ApprovalFailure(ValueError):
    def __init__(self, error):
        super().__init__('Fork workflow approval failed: ' + (error.stderr or str(error)))
        self.stderr, self.stdout = error.stderr, error.stdout


class Approval:
    def __init__(self, handler):
        self.handler = handler
        self.request = handler.request

    def context(self):
        h = self.handler
        pull = h.get('/pulls/' + h.number)
        # Exclude CI, review decisions and mergeability: these are not prerequisites.
        fields = ('html_url', 'state', 'draft', 'title', 'body', 'user', 'labels', 'milestone')
        result = {key: pull.get(key) for key in fields}
        for key in ('head', 'base'):
            result[key] = {name: pull[key][name] for name in ('sha', 'ref', 'repo')}
        result['target_tip'] = h.branch(pull['base']['ref'])
        result['discussion'] = {
            name: h.pages(route) for name, route in (
                ('comments', '/issues/' + h.number + '/comments'),
                ('reviews', '/pulls/' + h.number + '/reviews'),
                ('inline', '/pulls/' + h.number + '/comments'))}
        result['discussion']['threads'] = self.thread_states(pull['head']['sha'])
        return result

    def thread_states(self, head):
        h = self.handler
        _, owner, repo = h.root.split('/')
        cursor, seen, values = None, set(), []
        query = '''query($owner:String!,$repo:String!,$number:Int!,$after:String){
          repository(owner:$owner,name:$repo){pullRequest(number:$number){url headRefOid
            reviewThreads(first:100,after:$after){nodes{id isResolved isOutdated}
              pageInfo{hasNextPage endCursor}}}}}'''
        while True:
            data = h.graphql(query, {'owner': owner, 'repo': repo, 'number': int(h.number), 'after': cursor})
            pull = data['repository']['pullRequest']
            if pull['url'] != self.request['pr'] or pull['headRefOid'] != head:
                raise ValueError('PR changed while collecting governing thread state')
            connection = pull['reviewThreads']
            values.extend(connection['nodes'])
            page = connection['pageInfo']
            if page['hasNextPage'] is False:
                break
            cursor = page['endCursor']
            if page['hasNextPage'] is not True or not cursor or cursor in seen:
                raise ValueError('Incomplete review thread pagination')
            seen.add(cursor)
        if (len({v['id'] for v in values}) != len(values)
                or any(type(v.get('isResolved')) is not bool or type(v.get('isOutdated')) is not bool for v in values)):
            raise ValueError('Incomplete or duplicated governing thread state')
        return sorted(values, key=lambda v: v['id'])

    def evidence(self):
        # Retained documents, legacy specs and immutable reports are untrusted
        # JSON shapes. Keep malformed references on the same fail-closed path
        # as missing/stale evidence, including the bridge's native recovery.
        try:
            return self._evidence()
        except (AttributeError, KeyError, TypeError, IndexError) as exc:
            raise ValueError('Malformed retained incoming-review evidence: ' + str(exc)) from exc

    def _evidence(self):
        request, h = self.request, self.handler
        value = read(Path(request['evidence']))
        if (not isinstance(value, dict) or value.get('workflow') != 'dk-workflows:dk-pr-review'
                or value.get('pr') != request['pr'] or value.get('head') != request['head']
                or value.get('base') != request['base'] or value.get('analysis_only') is not False):
            raise ValueError('Current incoming-review evidence and mutation permission are required')
        if (any(not isinstance(value.get(key), str) or not value[key] for key in ('run', 'cycle', 'checkout'))
                or any(not isinstance(value.get(key, []), list) for key in ('discovery', 'hooks', 'publications'))):
            raise ValueError('Retained review run/cycle, checkout and task lists are required')
        verification = value.get('verification')
        if verification is not None and not (
                isinstance(verification, str) and verification
                or isinstance(verification, dict) and set(verification) == {'key'}
                and isinstance(verification['key'], str) and verification['key']):
            raise ValueError('Verification requires a managed task key or legacy spec path')
        repository = h.host + '/' + h.root.removeprefix('repos/')
        if value.get('repository') != repository:
            raise ValueError('Review evidence belongs to another repository')
        settings = config.effective(repository, value.get('upstream'))
        if value.get('configuration', 'legacy') not in {'legacy', 'skill-owned'}:
            raise ValueError('Unknown review configuration mode')
        owned = value.get('configuration') == 'skill-owned'
        permissions = settings['settings']['processes']['pr-review']['permissions']
        if owned:
            selected_settings = {
                name: skill_config.effective('dk-workflows:' + name, repository, value.get('upstream'),
                                             options=value.get(field, {}))
                for name, field in [('dk-pr-review', 'options'), ('dk-pr-reviewers-run', 'reviewer_options')]}
            fingerprints = {name: digest(selected['options']) for name, selected in selected_settings.items()}
            if fingerprints != value.get('skill_settings_fingerprints'):
                raise ValueError('Skill-owned review configuration changed')
            permissions = selected_settings['dk-pr-review']['options']['permissions']
        if value.get('settings') != settings or not permissions['approve_fork_ci']:
            raise ValueError('CI approval policy or governing configuration changed or is disabled')
        instructions = value.get('instructions')
        if not isinstance(instructions, dict) or not instructions:
            raise ValueError('Original governing instructions are required')
        if {key: resource(path) for key, path in instructions.items()} != value.get('instruction_fingerprints'):
            raise ValueError('Original governing instructions changed')
        context = self.context()
        if context != value.get('context'):
            raise ValueError('Reviewed head, target or governing discussion changed')
        if (context['html_url'] != request['pr'] or context['head']['sha'] != request['head']
                or context['target_tip'] != request['base']
                or context['state'] != 'open' or context['draft'] is not False):
            raise ValueError('CI approval requires an open non-draft PR at the reviewed head and target')
        actor = h.api(request['pr'], 'user')
        if classify(h.host, context['user'], actor, settings['settings']['accounts']['personal'])['ownership'] != 'others':
            raise ValueError('CI approval requires another author\'s PR')
        from tasks import Tasks
        managed = Tasks(repository, request['pr'], checkout=value['checkout'])
        store = managed.store
        results, specs = {}, {}
        approval_key, approval_claim, approval_fingerprint = None, None, None
        if 'approval_task' in value:
            active = value['approval_task']
            if (not isinstance(active, dict) or set(active) not in ({'spec', 'claim'}, {'key', 'claim'})
                    or not isinstance(active['claim'], str)):
                raise ValueError('Malformed current approval task binding')
            current = managed.saved_spec(active['key']) if 'key' in active else read(Path(active['spec']))
            if not isinstance(current, dict):
                raise ValueError('Current approval task spec must be an object')
            selection = current.get('skill_configuration', {})
            if (not owned or current['skill']['identity'] != 'dk-workflows:dk-ci-approve'
                    or selection.get('skill') != 'dk-workflows:dk-pr-review'
                    or selection.get('kind') != 'finalizers' or selection.get('options') != value.get('options', {})
                    or selection.get('upstream') != value.get('upstream')):
                raise ValueError('Current approval task must be the selected incoming-review finalizer')
            captured = capture(current)
            if captured['subject'] != store.subject:
                raise ValueError('Current approval task belongs to another PR')
            approval_key, approval_claim = captured['key'], active['claim']
            approval_fingerprint = captured['fingerprint']

        def completed(reference, *, source_required=True, cycle=value['cycle']):
            if isinstance(reference, dict) and set(reference) == {'key'} and isinstance(reference['key'], str):
                spec = managed.saved_spec(reference['key'])
            elif isinstance(reference, str):
                spec = read(Path(reference))
            else:
                raise ValueError('Review evidence requires a managed task key or legacy spec path')
            if not isinstance(spec, dict):
                raise ValueError('Review task spec must be an object')
            if any(spec.get('instructions', {}).get(key) != path for key, path in instructions.items()):
                raise ValueError('Review tasks must capture the original governing instructions')
            current = capture(spec)
            if current['subject'] != store.subject:
                raise ValueError('Review task belongs to another PR')
            source = spec.get('git', {})
            if source_required and (source.get('head') != request['head'] or source.get('target') != request['base']):
                raise ValueError('Review task source pins changed')
            with store.locked() as manifest:
                entry = manifest['entries'].get(current['key'], {})
                if any(e.get('claim') and not (
                        key == approval_key and e['claim']['id'] == approval_claim
                        and e['claim']['fingerprint'] == approval_fingerprint
                        and e['claim'].get('run') == value['run']) for key, e in manifest['entries'].items()):
                    raise ValueError('Unfinished review tasks block CI approval')
                status = store.describe(current, entry)
                saved = store.saved(entry)
                attempt = entry.get('attempts', [{}])[-1]
                current_completion = bool(
                    status['coverage_complete']
                    and spec.get('governing_inputs_complete') is True and spec['skill'].get('resources')
                    and attempt.get('run') == value.get('run') and attempt.get('cycle') == cycle)
                if not status['reusable'] and not current_completion:
                    raise ValueError('Incomplete or stale review evidence: ' + status['reason'])
            normalized, sources = store.findings_evidence(entry, saved)
            results[current['key']] = {**saved, 'candidate_paths': sources}
            specs[current['key']] = spec
            return current['key'], normalized

        discovery = value.get('discovery', [])
        if owned:
            selected = [skill_config.configured_call(call, repository, value.get('upstream'))
                        for group in skill_config.groups(selected_settings['dk-pr-reviewers-run']['options']['reviewers'])
                        for call in group]
        else:
            selected = [config.select(settings['settings'], 'pr-review', 'reviewer', index)
                        for index, _ in enumerate(config.reviewers(settings['settings'], 'pr-review'))]
        if not discovery or not selected:
            raise ValueError('Complete selected discovery coverage is required')
        candidates, discovery_specs = [], []
        for path in discovery:
            key, result = completed(path)
            if key in [item[0] for item in discovery_specs]:
                raise ValueError('Current selected reviewer coverage is incomplete or duplicated')
            discovery_specs.append((key, specs[key]))
            candidates.extend((key, index) for index, _ in enumerate(result.get('findings', [])))
        remaining = list(discovery_specs)
        for index, call in enumerate(selected):
            expected = call.get('skill', 'dk-workflows:dk-pr-reviewers-run' if owned else config.invocation.normalize(
                settings['settings']['processes']['pr-review']['coordinator']).get('skill'))
            matches = [(key, spec) for key, spec in remaining if config.invocation.matches(expected, spec['skill']['identity'])
                       and (not owned or spec.get('skill_configuration', {}).get('index') == index)]
            if len(matches) != 1:
                raise ValueError('Current selected reviewer coverage is incomplete or duplicated')
            key, spec = matches[0]
            if owned:
                selection = spec.get('skill_configuration', {})
                if (selection.get('skill') != 'dk-workflows:dk-pr-reviewers-run' or selection.get('kind') != 'reviewers'
                        or selection.get('options') != value.get('reviewer_options', {})
                        or selection.get('upstream') != value.get('upstream')):
                    raise ValueError('Reviewer evidence must retain the selected skill-owned configuration')
            selected_call = (call if owned else config.invocation.configured(
                call, settings['settings']['skill_defaults'], identity=spec['skill']['identity']))
            actual = spec['options'].get('invocation', {'skill': call.get('skill')})
            if actual != selected_call:
                raise ValueError('Reviewer evidence must retain the selected invocation controls')
            remaining.remove(matches[0])
        if remaining:
            raise ValueError('Current selected reviewer coverage is incomplete or duplicated')
        verification = value.get('verification')
        if candidates and not verification:
            raise ValueError('Candidate findings require independent verification and dispositions')
        if verification:
            key, result = completed(verification)
            if owned:
                selection = specs[key].get('skill_configuration', {})
                if (selection.get('skill') != 'dk-workflows:dk-pr-review' or selection.get('kind') != 'tasks'
                        or selection.get('options') != value.get('options', {})
                        or selection.get('upstream') != value.get('upstream')):
                    raise ValueError('Verification must retain its selected skill-owned occurrence')
                verifier, _ = skill_config.select('dk-workflows:dk-pr-review', 'tasks', selection['index'],
                                                  repository, value.get('upstream'), options=value.get('options', {}))
            else:
                verifier = config.select(settings['settings'], 'pr-review', 'task', task='verify_findings')
            expected = verifier.get('skill', 'dk-workflows:dk-pr-review' if owned else config.invocation.normalize(
                settings['settings']['processes']['pr-review']['coordinator']).get('skill'))
            if (not config.invocation.matches(expected, specs[key]['skill']['identity'])
                    or not (specs[key]['task']['id'] == 'finding-verification'
                            or owned and specs[key]['task']['id'].startswith('finding-verification:'))):
                raise ValueError('Expected the incoming-review finding verification task')
            if not owned:
                verifier = config.invocation.configured(verifier, settings['settings']['skill_defaults'],
                                                        identity=specs[key]['skill']['identity'])
            if specs[key]['options'].get('invocation') != verifier:
                raise ValueError('Verification evidence must retain the selected invocation controls')
            original_inputs = set(specs[key].get('inputs', {}).values())
            if any(path not in original_inputs for task, _ in candidates for path in results[task]['candidate_paths']):
                raise ValueError('Verification must capture the original current candidate reports')
            dispositions = result.get('dispositions', [])
            covered = []
            for disposition in dispositions:
                covered.append((disposition['task'], disposition['index']))
                if (disposition.get('status') not in {'rejected', 'resolved'}
                        or not isinstance(disposition.get('evidence'), str)
                        or not disposition['evidence'].strip()):
                    raise ValueError('Unresolved actionable or uncertain findings block CI approval')
            if sorted(covered) != sorted(candidates):
                raise ValueError('Every candidate requires an evidenced verification disposition')
            if result.get('findings'):
                raise ValueError('Unresolved verification findings block CI approval')
        occurrences = []
        for path in value.get('hooks', []):
            key, result = completed(path, source_required=False, cycle=None)
            occurrence = specs[key]['options'].get('cycle_hook', {})
            if (occurrence.get('run') != value.get('run') or occurrence.get('cycle') != value.get('cycle')
                    or result.get('findings')):
                raise ValueError('Hook evidence does not cover the current review cycle')
            occurrences.append((occurrence.get('phase'), occurrence.get('index')))
        required_hooks = [] if owned else [(phase, index) for phase, hooks in settings['settings']['processes']['pr-review']['hooks'].items()
                          for index, _ in enumerate(hooks)]
        if sorted(occurrences) != sorted(required_hooks):
            raise ValueError('Current review cycle hooks are incomplete')
        for path in value.get('publications', []):
            operation = publication.load(path)
            if (operation['status'] != 'verified' or operation['request']['target'] != request['pr']
                    or operation['request']['approved'].get('head') != request['head']):
                raise ValueError('Review publications require reconciled receipts at the reviewed head')
        # Also detect uncertain writes omitted from the evidence document.
        from shared.workflow_storage import operations_root
        from shared.review_request_recovery import Recovery
        from shared.github_publications import Handler
        recovery = Recovery(lambda approved: Handler(approved, api=h.api, metadata=h.metadata,
                                                     run=h.run, endpoint=h.endpoint))
        scope = operations_root(repository=request.get('checkout')) / publication.digest(request['pr'])
        for path in scope.glob('*/*.json'):
            operation = publication.load(path)
            if recovery.blocks(operation, {**request, 'kind': 'ci-approve'}):
                # The current operation is pending while checking its own receipt.
                if operation['request']['approved'].get('_payload_hash') != request.get('_payload_hash'):
                    raise ValueError('Uncertain publication requires read-only reconciliation')
        return {'evidence': digest(value), 'results': {key: result['original_sha256'] for key, result in results.items()},
                'actor': actor, 'context': context}

    @staticmethod
    def blocked(run):
        return run.get('conclusion') == 'action_required' or run.get('status') == 'action_required'

    def association(self, run, *, context=None):
        r, h = self.request, self.handler
        if run.get('head_sha') != r['head'] or run.get('event') != 'pull_request':
            return False
        pulls = run.get('pull_requests')
        if not isinstance(pulls, list) or len(pulls) != 1:
            raise ValueError('Workflow PR association is absent or ambiguous')
        pull = pulls[0]
        if pull.get('number') != int(h.number):
            return False
        context = self.context() if context is None else context
        # The live target binds review coverage; historical PR base metadata
        # independently binds the run's source association.
        if (context['head']['sha'] != r['head'] or context['target_tip'] != r['base']
                or pull.get('head', {}).get('sha') != r['head']
                or pull.get('base', {}).get('sha') != context['base']['sha']
                or pull.get('base', {}).get('ref') != context['base']['ref']
                or pull.get('base', {}).get('repo', {}).get('id') != context['base']['repo']['id']
                or pull.get('head', {}).get('repo', {}).get('id') != context['head']['repo']['id']
                or (run.get('repository') or {}).get('id') != context['base']['repo']['id']
                or context['head']['repo']['id'] == context['base']['repo']['id']):
            raise ValueError('Workflow fork/head/target association is ambiguous or changed')
        return True

    def gate(self, run):
        if not self.blocked(run) or not self.association(run):
            raise ValueError('Workflow is not awaiting fork workflow approval for this PR')
        h = self.handler
        route = '/actions/runs/' + str(run['id'])
        jobs = h.get(route + '/attempts/' + str(run['run_attempt']) + '/jobs?per_page=1')
        if jobs.get('total_count') != 0 or jobs.get('jobs') != [] or h.get(route + '/pending_deployments') != []:
            raise ValueError('Workflow gate is ambiguous or is a deployment approval')

    def discover(self):
        def listing_count(pages):
            if not isinstance(pages, list) or not pages:
                raise ValueError('Incomplete workflow run pagination')
            counts = [page.get('total_count') if isinstance(page, dict) else None for page in pages]
            if (any(type(count) is not int or count < 0 for count in counts)
                    or len(set(counts)) != 1):
                raise ValueError('Incomplete or changed workflow run count')
            return counts[0]

        h = self.handler
        route = h.root + '/actions/runs?per_page=100'
        pages = h.run(['gh', 'api', '--hostname', h.host,
                       route + '&head_sha=' + self.request['head'] + '&event=pull_request',
                       '--paginate', '--slurp'])
        count = listing_count(pages)
        if count >= 1000:
            # Filtered searches are capped at 1,000 results. Retain the complete
            # unfiltered path for unusually large per-head histories.
            pages = h.run(['gh', 'api', '--hostname', h.host, route, '--paginate', '--slurp'])
            count = listing_count(pages)
        runs = []
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get('workflow_runs'), list):
                raise ValueError('Incomplete workflow run page')
            runs.extend(page['workflow_runs'])
        if len(runs) != count:
            raise ValueError('Incomplete workflow run pagination')
        ids = [run['id'] for run in runs]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicated workflow runs across pagination')
        eligible = []
        for run in runs:
            if self.blocked(run) and self.association(run):
                self.gate(run)
                eligible.append(run)
        return eligible

    def current_run(self, *, context=None):
        run = self.handler.get('/actions/runs/' + str(self.request['run_id']))
        if (run.get('id') != self.request['run_id'] or run.get('run_attempt') != self.request['attempt']
                or not self.association(run, context=context)):
            raise ValueError('Workflow run identity or attempt changed')
        return run

    def prepare(self):
        from shared.github_publications import positive, sha
        sha(self.request.get('head'))
        sha(self.request.get('base'))
        positive(self.request.get('run_id'), 'run_id')
        positive(self.request.get('attempt'), 'attempt')
        evidence = self.evidence()
        run = self.current_run()
        self.gate(run)
        return {'evidence': evidence, 'run': run}

    def check(self, expected, verified):
        if self.evidence() != expected['evidence']:
            raise ValueError('Current review readiness changed')
        run = self.current_run()
        if not verified:
            self.gate(run)
            if run != expected['run']:
                raise ValueError('Workflow run changed before approval')

    def send(self):
        try:
            return self.handler.post('/actions/runs/' + str(self.request['run_id']) + '/approve', {})
        except subprocess.CalledProcessError as exc:
            raise ApprovalFailure(exc) from exc

    def observe(self, expected):
        # Reconcile the bound mutation independently of present PR readiness;
        # check() reports subsequent drift after the receipt is saved.
        run = self.current_run(context=expected['evidence']['context'])
        # No actor-specific fork-approval receipt API exists. Require execution of
        # this exact attempt; waiting/deployment or another attempt is ambiguous.
        if (self.blocked(run) or run.get('status') not in {'queued', 'in_progress', 'completed'}
                or (run['status'] == 'completed' and run.get('conclusion') not in {
                    'success', 'failure', 'neutral', 'cancelled', 'skipped', 'timed_out'})):
            raise ValueError('Fork workflow approval is not observable; do not resend')
        if run['status'] == 'completed':
            jobs = self.handler.get('/actions/runs/' + str(run['id']) + '/attempts/'
                                    + str(run['run_attempt']) + '/jobs?per_page=1')
            if type(jobs.get('total_count')) is not int or jobs['total_count'] < 1 or not jobs.get('jobs'):
                raise ValueError('Completed jobless run may be expired; approval remains ambiguous')
        return {'pr': self.request['pr'], 'head': self.request['head'], 'base': self.request['base'],
                'run_id': run['id'], 'attempt': run['run_attempt'], 'status': run['status'],
                'execution_permitted': True}
