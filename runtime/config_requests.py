"""Private configuration request admission and crash-safe application, without agents."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import config_adapters as adapters
import config
import skill_config
from shared.private_state import atomic_text, write, sync_directory
from shared.repository_scope import checkout_roots, repository
from shared.storage_paths import location

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def read_text(path):
    return path.read_text(encoding='utf-8') if path.exists() else None


def text(value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('Nonempty request text required')
    return value


def context_value(context):
    if not isinstance(context, dict) or set(context) - {'checkout', 'repository', 'upstream', 'pr'}:
        raise ValueError('Unsupported configuration context')
    result = deepcopy(context)
    result['checkout'] = str(Path(result.get('checkout', '.')).resolve())
    if 'repository' not in result:
        result['repository'] = skill_config.repository_key(result['checkout'], result.get('pr'))
    if not isinstance(result['repository'], str) or not result['repository'].strip():
        raise ValueError('Repository identity required')
    if not result['repository'].startswith('path:'):
        result['repository'] = result['repository'].lower()
    config.path(result['repository'])
    if result.get('upstream'):
        result['upstream'] = result['upstream'].lower()
        config.path(result['upstream'])
    if result.get('pr'):
        adapters.pr_partition_policy.identity(result['pr'])
    return result


def scope_identity(value):
    return value.removeprefix('remote:') if isinstance(value, str) else value


def logical(raw, op):
    if raw is None:
        return {}
    domain = op['domain']
    if domain == 'completion':
        return {'prompt': raw}
    try:
        value = json.loads(raw)
    except ValueError:
        return {'$raw': raw}
    if domain == 'preferences':
        return {item['id']: item for item in value['records']}
    if domain == 'ci-exceptions':
        return {item['id']: item for item in value['entries']}
    if domain == 'pr-partition':
        return value['history'][-1]['policy'] if value['history'] else adapters.pr_partition_policy.DEFAULT
    if domain == 'pr-size':
        return value['pr_size']
    return value if isinstance(value, dict) else {'$value': value}


def differences(before, after, path=()):
    """Only changed leaves; lists are settings values, never expanded transcripts."""
    changes = []
    for key in sorted(before.keys() | after.keys()):
        left, right = before.get(key), after.get(key)
        if key in before and key in after and isinstance(left, dict) and isinstance(right, dict):
            changes.extend(differences(left, right, (*path, key)))
        elif key not in before or key not in after or left != right:
            # Expand new/deleted maps to keep supersession at setting granularity.
            if isinstance(left, dict) or isinstance(right, dict):
                if (left is None or isinstance(left, dict)) and (right is None or isinstance(right, dict)) and (left or right):
                    changes.extend(differences(left or {}, right or {}, (*path, key)))
                    continue
            changes.append({'path': [*path, key], 'before': {'present': key in before, **({'value': left} if key in before else {})},
                            'after': {'present': key in after, **({'value': right} if key in after else {})}})
    return changes


class Requests:
    def __init__(self):
        self.root = location('tasks')/'configuration-history'

    def _private(self, checkout=None):
        probe = self.root.parent
        while not probe.exists():
            probe = probe.parent
        roots = [ROOT, *checkout_roots(probe)]
        if checkout:
            roots += checkout_roots(checkout)
            roots.append(Path(checkout).resolve())
        if any(self.root.resolve().is_relative_to(root) for root in roots):
            raise ValueError('Configuration history must stay outside plugin and working repositories')

    def _git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        return subprocess.check_output(['git', '-C', str(self.root), '-c', 'core.hooksPath=/dev/null',
            '-c', 'commit.gpgsign=false', '-c', 'user.name=dk-workflows configuration',
            '-c', 'user.email=configuration@localhost', *args], env=env, stderr=subprocess.PIPE, text=True).strip()

    @contextmanager
    def locked(self):
        self._private()
        self.root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root.parent/'configuration-history.lock').open('a') as stream:
            os.chmod(stream.name, 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            self.root.mkdir(exist_ok=True, mode=0o700)
            os.chmod(self.root, 0o700)
            if not (self.root/'.git').exists():
                self._git('init', '--quiet')
            if self._git('remote'):
                raise ValueError('Private configuration history must have no remote')
            yield

    def _path(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'cfg-[0-9a-f]{32}', request_id):
            raise ValueError('Invalid configuration request ID')
        return self.root/(request_id + '.json')

    def _read(self, request_id):
        return json.loads(self._path(request_id).read_text())

    def _save(self, request, event):
        path = self._path(request['id'])
        if read_text(path) != adapters.encode(request):
            write(path, request)
        self._git('add', '--', path.name)
        if self._git('diff', '--cached', '--name-only'):
            self._git('commit', '--quiet', '-m', request['id'] + ': ' + event)

    def _committed(self, request):
        # An interrupted Git receipt must be durable before any further store write.
        self._save(request, 'recover retained request facts')

    def start(self, ask, context):
        context = context_value(context)
        self._private(context['checkout'])
        with self.locked():
            request = {'id': 'cfg-' + uuid.uuid4().hex, 'ask': text(ask), 'context': context,
                       'status': 'open', 'clarifications': []}
            self._save(request, 'start')
            return self._status(request)

    def _records(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('cfg-*.json'))]

    def candidates(self, request):
        context = request['context']
        records = self._records()
        superseded = {(r['request'], r['decision']) for entry in records if entry['status'] == 'applied'
                      for r in entry.get('assessment', {}).get('relations', []) if r['choice'] in {'replace', 'narrow'}}
        result = []
        for entry in records:
            if entry['id'] == request['id'] or entry['status'] != 'applied':
                continue
            # User decisions can affect every repo. Repo decisions apply to that
            # repo and explicit upstream scopes; PR decisions only to that PR.
            for decision in entry.get('decisions', []):
                op = entry['operations'][decision['operation']]
                scope = decision.get('scope', op['scope'])
                repository = decision.get('repository', entry['context']['repository'])
                applies = (scope == 'user' or
                           scope == 'pr' and entry['context'].get('pr') == context.get('pr') or
                           scope == 'repo' and scope_identity(repository) in {scope_identity(context['repository']), scope_identity(context.get('upstream'))})
                if applies and (entry['id'], decision['id']) not in superseded:
                    result.append({'request': entry['id'], **decision, 'domain': op['domain'],
                                   'scope': scope, 'repository': repository, 'summary': entry['assessment']['summary']})
        return result

    def _inputs(self, request, operations):
        context = request['context']
        paths = adapters.input_paths(context, operations)
        targets = {adapters.destination(context, op)[0].resolve() for op in operations}
        inputs = {str(path.resolve()): digest(read_text(path)) for path in sorted(paths) if path.resolve() not in targets}
        inputs['@checkout_identity'] = repository(context['checkout'])
        return fingerprint(inputs)

    @contextmanager
    def _stores(self, context, operations):
        with ExitStack() as stack:
            stack.enter_context(adapters.resolved(context, operations))
            roots = [ROOT, *checkout_roots(context['checkout'])]
            for path in sorted(adapters.lock_paths(context, operations)):
                if any(path.resolve().is_relative_to(root) for root in roots):
                    raise ValueError('Configuration stores must stay outside plugin and working repositories')
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                stream = stack.enter_context(path.open('a'))
                os.chmod(path, 0o600)
                fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def _proposal(self, request, operations):
        destinations = [adapters.destination(request['context'], op)[0] for op in operations]
        if len(set(destinations)) != len(destinations):
            raise ValueError('Combine operations on the same store into one typed operation')
        recovering = request['status'] == 'applying'
        if recovering and (len(operations) != len(request['operations']) or not all(
                adapters.matches(saved, op) for saved, op in zip(request['operations'], operations))):
            raise ValueError('Recovery must retain the exact original operations and receipts')
        progress, decisions = [], []
        for index, (op, path) in enumerate(zip(operations, destinations)):
            before = read_text(path)
            if recovering:
                original = request['progress'][index]
                observed = digest(before)
                if original.get('receipt') or original.get('intent') and observed == original['after']:
                    if observed != original['after']:
                        raise ValueError('Completed operation changed unexpectedly; recovery blocked')
                    adapters.validate_observed(request['context'], op, before)
                elif observed != original['before']:
                    raise ValueError('Unexpected value after interruption; recovery blocked')
                elif digest(adapters.project(request['context'], op, before)) != original['after']:
                    raise ValueError('Recovery projection changed; reconcile original intent')
                progress.append(deepcopy(original))
                decisions.extend(deepcopy(d) for d in request['decisions'] if d['operation'] == index)
                continue
            after = adapters.project(request['context'], op, before)
            left, right = logical(before, op), logical(after, op)
            if op['domain'] == 'preferences':
                changes = [{'path': [key],
                            'before': {'present': key in left, **({'value': left[key]} if key in left else {})},
                            'after': {'present': key in right, **({'value': right[key]} if key in right else {})}}
                           for key in sorted(left.keys() | right.keys()) if left.get(key) != right.get(key)]
            else:
                changes = differences(left, right)
            progress.append({'before': digest(before), 'after': digest(after)})
            for change in changes:
                decision = {'id': 'd-' + fingerprint([index, change['path']])[:16], 'operation': index, **change}
                if op['domain'] == 'preferences':
                    record = (change['after'] if change['after']['present'] else change['before'])['value']
                    decision['scope'] = record['owner']['type']
                    if record['owner']['type'] == 'repo':
                        decision['repository'] = record['owner']['repository'].removeprefix('remote:')
                decisions.append(decision)
        inputs = self._inputs(request, operations)
        candidates = self.candidates(request)
        binding = fingerprint({'operations': operations, 'progress': progress, 'inputs': inputs,
                               'candidates': candidates, 'clarifications': request['clarifications']})
        return progress, decisions, inputs, candidates, binding

    def prepare(self, request_id, proposal):
        if not isinstance(proposal, dict) or set(proposal) - {'operations', 'assessment'}:
            raise ValueError('Prepare requires typed operations and optional assessment')
        operations = proposal.get('operations')
        if operations != 'retained' and (not isinstance(operations, list) or not operations):
            raise ValueError('Nonempty operation list or retained selector required')
        with self.locked():
            request = self._read(request_id)
            if operations == 'retained':
                if not request.get('operations'):
                    raise ValueError('No retained operations to prepare')
                operations = [adapters.expand(request['context'], op, progress)
                              for op, progress in zip(request['operations'], request['progress'])]
            operations = [adapters.normalize(op) for op in operations]
            if request['status'] in {'applied', 'cancelled'}:
                raise ValueError('Applied or cancelled requests cannot be prepared again')
            self._private(request['context']['checkout'])
            targets = {adapters.destination(request['context'], op)[0] for op in operations}
            for pending in self._records():
                if pending['id'] != request_id and pending['status'] == 'applying' and any(
                        adapters.destination(pending['context'], op)[0] in targets or
                        op['scope'] in {'user', 'catalog'} or
                        op['scope'] == 'repo' and scope_identity(pending['context']['repository']) in {
                            scope_identity(request['context']['repository']), scope_identity(request['context'].get('upstream'))} or
                        op['scope'] == 'pr' and pending['context'].get('pr') == request['context'].get('pr')
                        for op in pending['operations']):
                    raise ValueError('Reconcile uncertain configuration request first: ' + pending['id'])
            with self._stores(request['context'], operations):
                progress, decisions, inputs, candidates, binding = self._proposal(request, operations)
                assessment = proposal.get('assessment')
                if assessment is None:
                    # Preview is read-only. Full candidate asks/diffs are fetched explicitly.
                    return {'id': request_id, 'status': 'assessment_required', 'fingerprint': binding,
                            'decisions': decisions, 'candidates': [{k: v for k, v in c.items() if k not in {'before', 'after'}} for c in candidates],
                            'legacy_intent': 'unknown where no matching request decision exists'}
                self._assessment(assessment, binding, candidates, decisions, operations, request['context'])
                request.update(status=('applying' if request['status'] == 'applying' else
                                       'prepared' if not assessment['unresolved'] else 'clarification'),
                               operations=[adapters.compact(op, read_text(adapters.destination(request['context'], op)[0]))
                                           if request['status'] != 'applying' else deepcopy(request['operations'][index])
                                           for index, op in enumerate(operations)], progress=progress, decisions=decisions,
                               inputs=inputs, candidate_fingerprint=fingerprint(candidates),
                               assessment=deepcopy(assessment))
                self._save(request, 'prepare admission')
                return self._status(request)

    def _assessment(self, assessment, binding, candidates, decisions, operations, context):
        if (not isinstance(assessment, dict) or set(assessment) != {'fingerprint', 'summary', 'reviewed', 'relations', 'unresolved'}
                or assessment['fingerprint'] != binding):
            raise ValueError('Admission must bind the fresh preparation fingerprint')
        text(assessment['summary'])
        expected = sorted(item['request'] + '/' + item['id'] for item in candidates)
        if not isinstance(assessment['reviewed'], list) or sorted(assessment['reviewed']) != expected:
            raise ValueError('Assess every relevant earlier decision before saving')
        if not isinstance(assessment['unresolved'], list) or any(not isinstance(item, str) or not item.strip() for item in assessment['unresolved']):
            raise ValueError('Unresolved conflicts must be explicit questions')
        if not isinstance(assessment['relations'], list):
            raise ValueError('Relations must be a list')
        ids = {item['id'] for item in decisions}
        seen = set()
        for relation in assessment['relations']:
            if (not isinstance(relation, dict) or set(relation) != {'request', 'decision', 'choice', 'evidence', 'new_decision'}
                    or relation['request'] + '/' + relation['decision'] not in expected
                    or relation['new_decision'] not in ids
                    or relation['choice'] not in {'replace', 'narrow', 'exception', 'preserve'}):
                raise ValueError('Relation must identify an earlier and new decision with a user choice')
            key = relation['request'], relation['decision']
            if key in seen:
                raise ValueError('Each earlier decision needs one resolution')
            seen.add(key)
            text(relation['evidence'])
            old = next(c for c in candidates if (c['request'], c['id']) == key)
            new = next(d for d in decisions if d['id'] == relation['new_decision'])
            op = operations[new['operation']]
            same_scope = (old['scope'] == new.get('scope', op['scope']) and
                          (old['scope'] == 'user' or scope_identity(old['repository']) == scope_identity(new.get('repository', context['repository']))))
            same_preference = old['domain'] == op['domain'] == 'preferences' and old['path'] == new['path']
            if relation['choice'] in {'replace', 'narrow'} and not (same_scope or same_preference):
                raise ValueError('A narrower scope is an exception, not global supersession')
        # Deterministic overlap is a floor; semantic contradictions belong to the agent.
        for candidate in candidates:
            for decision in decisions:
                op = operations[decision['operation']]
                same_domain = (candidate['domain'] == op['domain'] or
                               {candidate['domain'], op['domain']} <= {'workflow', 'skill'})
                if not same_domain or candidate['path'] != decision['path'] or candidate['after'] == decision['after']:
                    continue
                relation = next((r for r in assessment['relations'] if
                                 (r['request'], r['decision']) == (candidate['request'], candidate['id'])), None)
                if relation is None and not assessment['unresolved']:
                    raise ValueError('Changed earlier setting requires an explicit conflict resolution')
                if relation:
                    same_scope = (candidate['scope'] == decision.get('scope', op['scope']) and
                                  (candidate['scope'] == 'user' or scope_identity(candidate['repository']) == scope_identity(decision.get('repository', context['repository']))))
                    if relation['choice'] == 'preserve' and same_scope:
                        raise ValueError('Preserving an earlier setting requires removing the conflicting operation')

    def clarify(self, request_id, excerpt, question=None):
        with self.locked():
            request = self._read(request_id)
            if request['status'] in {'applied', 'cancelled'}:
                raise ValueError('Cannot clarify a completed request')
            entry = {'excerpt': text(excerpt)}
            if question is not None:
                entry['question'] = text(question)
            request['clarifications'].append(entry)
            if request['status'] == 'applying':
                request['assessment'] = None
                self._save(request, 'recovery clarification; retain original intent')
                return self._status(request)
            request['status'] = 'open'
            # Old prepared evidence stays in Git, but is no longer admitted.
            for field in ('operations', 'progress', 'decisions', 'inputs', 'assessment', 'candidate_fingerprint'):
                request.pop(field, None)
            self._save(request, 'clarification')
            return self._status(request)

    def cancel(self, request_id, reason):
        with self.locked():
            request = self._read(request_id)
            if request['status'] in {'applying', 'applied'}:
                raise ValueError('Reconcile uncertain writes first; applied changes need a new request')
            if request['status'] != 'cancelled':
                request.update(status='cancelled', outcome=text(reason))
                self._save(request, 'cancel')
            return self._status(request)

    def _fresh(self, request):
        if not request.get('assessment') or request['assessment']['unresolved']:
            raise ValueError('Request has missing or unresolved admission')
        if fingerprint(self.candidates(request)) != request['candidate_fingerprint']:
            raise ValueError('Earlier decisions changed; prepare and assess again')
        if self._inputs(request, request['operations']) != request['inputs']:
            raise ValueError('Configuration, admission context or governing inputs changed; fresh preparation required')
        for index, op in enumerate(request['operations']):
            progress = request['progress'][index]
            observed = digest(read_text(adapters.destination(request['context'], op)[0]))
            expected = {progress['after']} if progress.get('receipt') else (
                {progress['before'], progress['after']} if progress.get('intent') else {progress['before']})
            if observed not in expected:
                raise ValueError('Configuration changed unexpectedly; fresh preparation or recovery required')

    def apply(self, request_id, operation=None, context=None):
        with self.locked():
            request = self._read(request_id)
            if context is not None:
                # Legacy commands must bind to their current repository and scope.
                supplied = context_value(context)
                if any(request['context'].get(k) != v for k, v in supplied.items() if k in context or k == 'checkout'):
                    raise ValueError('Request context does not match this command')
            if operation is not None:
                normalized = adapters.normalize(operation)
                if len(request.get('operations', [])) != 1 or not adapters.matches(request['operations'][0], normalized):
                    raise ValueError('Request is not bound to this exact single operation; use config_requests apply for a batch')
            if request['status'] not in {'prepared', 'applying', 'applied'}:
                raise ValueError('Request has missing or unresolved admission')
            if request['status'] == 'applied':
                if self._git('status', '--porcelain', '--', self._path(request_id).name):
                    self._committed(request)
                return self._status(request)  # Original receipt, never replay an old write.
            self._private(request['context']['checkout'])
            with self._stores(request['context'], request['operations']):
                self._fresh(request)
                self._committed(request)
                # Validate the complete batch before any write. Completed siblings
                # retain receipts and are never replayed.
                desired = {}
                for index, op in enumerate(request['operations']):
                    progress = request['progress'][index]
                    path = adapters.destination(request['context'], op)[0]
                    raw = read_text(path)
                    observed = digest(raw)
                    if progress.get('receipt'):
                        if observed != progress['after']:
                            raise ValueError('Completed operation changed unexpectedly; recovery blocked')
                        continue
                    if progress.get('intent') and observed == progress['after']:
                        continue
                    if observed != progress['before']:
                        raise ValueError('Unexpected value after interruption; recovery blocked')
                    after = adapters.project(request['context'], op, raw)
                    if digest(after) != progress['after']:
                        raise ValueError('Projection changed; repeat admission')
                    desired[index] = after
                for index, op in enumerate(request['operations']):
                    progress = request['progress'][index]
                    if progress.get('receipt'):
                        continue
                    path = adapters.destination(request['context'], op)[0]
                    if not progress.get('intent'):
                        progress['intent'] = True
                        request['status'] = 'applying'
                        self._save(request, 'write intent ' + str(index))
                    if index in desired and digest(read_text(path)) != progress['after']:
                        if desired[index] is None:
                            path.unlink(missing_ok=True)
                            sync_directory(path.parent)
                        else:
                            atomic_text(path, desired[index])
                    if digest(read_text(path)) != progress['after']:
                        raise ValueError('Configuration readback mismatch; recovery blocked')
                    progress['receipt'] = {'path': str(path), 'sha256': progress['after']}
                    self._save(request, 'readback receipt ' + str(index))
                request['status'] = 'applied'
                self._save(request, 'complete')
                return self._status(request)

    def _status(self, request):
        result = {'id': request['id'], 'status': request['status'],
                  'reference': str(self._path(request['id']))}
        if request['status'] not in {'applied', 'cancelled'}:
            result['handoff'] = {'request_id': request['id'], 'responsibility': 'configuration-only',
                                 'contract': str(ROOT/'docs/configuration-agent.md'),
                                 'evidence_sections': ['ask', 'context', 'operations', 'assessment']}
        if 'operations' in request:
            result['operations'] = [{'index': i, 'domain': op['domain'], 'scope': op['scope'],
                                     'status': 'applied' if request['progress'][i].get('receipt') else
                                               'reconcile' if request['progress'][i].get('intent') else 'pending'}
                                    for i, op in enumerate(request['operations'])]
            result['questions'] = (request.get('assessment') or {}).get('unresolved', [])
        return result

    def resume(self, request_id):
        request = self._read(request_id)
        result = self._status(request)
        if request['status'] in {'prepared', 'applying'}:
            try:
                self._fresh(request)
                result['admission'] = 'reusable'
            except (ValueError, OSError) as exc:
                result.update(admission='stale', reason=str(exc))
        return result

    def show(self, request_id, section='status'):
        request = self._read(request_id)
        if section == 'status':
            return self.resume(request_id)
        if section == 'ask':
            return {key: request[key] for key in ('id', 'ask', 'clarifications')}
        if section not in {'decisions', 'operations', 'assessment', 'progress', 'context'}:
            raise ValueError('Select a request evidence section')
        return {'id': request_id, section: request.get(section)}

    def history(self, *, repository=None, domain=None, status=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('History limit must be 1..100')
        records = [r for r in self._records() if
                   (repository is None or r['context']['repository'] == repository or
                    any(op['scope'] in {'user', 'catalog'} for op in r.get('operations', []))) and
                   (domain is None or any(op['domain'] == domain for op in r.get('operations', []))) and
                   (status is None or r['status'] == status)]
        if records:
            names = dict.fromkeys(name for name in self._git('log', '--format=', '--name-only').splitlines() if name)
            order = {name: index for index, name in enumerate(names)}
            records.sort(key=lambda r: order.get(r['id'] + '.json', len(order)))
        return {'requests': [self._status(r) for r in records[:limit]], 'more': len(records) > limit}


def apply_bound(request_id, operation, context):
    if not request_id:
        raise ValueError('Configuration writes require --request-id from the configuration agent')
    return Requests().apply(request_id, operation, context)
