"""Incoming-review evidence and fork workflow approval, independent of CI success."""
from pathlib import Path
import subprocess

import config
from state import Store, capture, digest, read, resource
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
        request, h = self.request, self.handler
        value = read(Path(request['evidence']))
        if (not isinstance(value, dict) or value.get('workflow') != 'dk-workflows:dk-pr-review'
                or value.get('pr') != request['pr'] or value.get('head') != request['head']
                or value.get('base') != request['base'] or value.get('analysis_only') is not False):
            raise ValueError('Current incoming-review evidence and mutation permission are required')
        if (any(not isinstance(value.get(key), str) or not value[key] for key in ('run', 'cycle', 'checkout'))
                or any(not isinstance(value.get(key, []), list) for key in ('discovery', 'hooks', 'publications'))):
            raise ValueError('Retained review run/cycle, checkout and task lists are required')
        repository = h.host + '/' + h.root.removeprefix('repos/')
        if value.get('repository') != repository:
            raise ValueError('Review evidence belongs to another repository')
        settings = config.effective(repository, value.get('upstream'))
        if value.get('settings') != settings or not settings['settings']['incoming_review']['approve_ci']:
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
                or context['base']['sha'] != request['base'] or context['target_tip'] != request['base']
                or context['state'] != 'open' or context['draft'] is not False):
            raise ValueError('CI approval requires an open non-draft PR at the reviewed head and target')
        actor = h.api(request['pr'], 'user')
        if classify(h.host, context['user'], actor, settings['settings']['identities'])['ownership'] != 'others':
            raise ValueError('CI approval requires another author\'s PR')
        store = Store(repository, request['pr'], checkout=value['checkout'])
        results, specs = {}, {}

        def completed(path, *, source_required=True):
            spec = read(Path(path))
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
                if any(e.get('claim') for e in manifest['entries'].values()):
                    raise ValueError('Unfinished review tasks block CI approval')
                status = store.describe(current, entry)
                saved = store.saved(entry)
                attempt = entry.get('attempts', [{}])[-1]
                current_completion = bool(
                    saved and saved['status'] == 'complete'
                    and saved['fingerprint'] == current['fingerprint']
                    and not entry.get('invalidated') and not entry.get('claim')
                    and spec.get('governing_inputs_complete') is True and spec['skill'].get('resources')
                    and attempt.get('run') == value.get('run') and attempt.get('cycle') == value.get('cycle'))
                if not status['reusable'] and not current_completion:
                    raise ValueError('Incomplete or stale review evidence: ' + status['reason'])
            results[current['key']] = saved
            specs[current['key']] = spec
            return current['key'], saved['normalized']

        discovery = value.get('discovery', [])
        selected = settings['settings']['reviewers']
        if not discovery or not selected:
            raise ValueError('Complete selected discovery coverage is required')
        candidates, identities = [], []
        for path in discovery:
            key, result = completed(path)
            identities.append(specs[key]['skill']['identity'])
            candidates.extend((key, index) for index, _ in enumerate(result.get('findings', [])))
        if sorted(identities) != sorted(r['skill'] for r in selected):
            raise ValueError('Current selected reviewer coverage is incomplete or duplicated')
        verification = value.get('verification')
        if candidates and not verification:
            raise ValueError('Candidate findings require independent verification and dispositions')
        if verification:
            key, result = completed(verification)
            if (specs[key]['skill']['identity'] != 'dk-workflows:dk-pr-review'
                    or specs[key]['task']['id'] != 'finding-verification'):
                raise ValueError('Expected the incoming-review finding verification task')
            original_inputs = set(specs[key].get('inputs', {}).values())
            if any(results[task]['path'] not in original_inputs for task, _ in candidates):
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
            key, result = completed(path, source_required=False)
            occurrence = specs[key]['options'].get('cycle_hook', {})
            if (occurrence.get('run') != value.get('run') or occurrence.get('cycle') != value.get('cycle')
                    or result.get('findings')):
                raise ValueError('Hook evidence does not cover the current review cycle')
            occurrences.append((occurrence.get('phase'), occurrence.get('id')))
        required_hooks = [(phase, hook['id']) for phase, hooks in settings['settings']['cycle_hooks'].items()
                          for hook in hooks]
        if sorted(occurrences) != sorted(required_hooks):
            raise ValueError('Current review cycle hooks are incomplete')
        for path in value.get('publications', []):
            operation = publication.load(path)
            if (operation['status'] != 'verified' or operation['request']['target'] != request['pr']
                    or operation['request']['approved'].get('head') != request['head']):
                raise ValueError('Review publications require reconciled receipts at the reviewed head')
        # Also detect uncertain writes omitted from the evidence document.
        from shared.workflow_storage import operations_root
        scope = operations_root(repository=request.get('checkout')) / publication.digest(request['pr'])
        for path in scope.glob('*/*.json'):
            if publication.load(path)['status'] == 'pending':
                # The current operation is pending while checking its own receipt.
                operation = publication.load(path)
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
        if (context['head']['sha'] != r['head'] or context['base']['sha'] != r['base']
                or context['target_tip'] != r['base']
                or pull.get('head', {}).get('sha') != r['head']
                or pull.get('base', {}).get('sha') != r['base']
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
        h = self.handler
        # Unfiltered listing avoids GitHub's 1,000-result limit on searches.
        pages = h.run(['gh', 'api', '--hostname', h.host, h.root + '/actions/runs?per_page=100',
                       '--paginate', '--slurp'])
        if not isinstance(pages, list) or not pages:
            raise ValueError('Incomplete workflow run pagination')
        runs = []
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get('workflow_runs'), list):
                raise ValueError('Incomplete workflow run page')
            runs.extend(page['workflow_runs'])
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
