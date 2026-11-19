"""Layout admission and native upgrade reservations in existing request history.

No session launcher or clock-based lease. All semantic work belongs to the
already-running session's native configuration agent.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import fcntl
from functools import wraps
import inspect
import json
from pathlib import Path
import uuid

BASELINE = 1
LAYOUT = Path(__file__).with_name('config_layout.json')
INTERNAL = ContextVar('configuration_internal', default=False)
READ_CONTEXT = ContextVar('configuration_read_context', default=None)
READ_ADMISSIONS = ContextVar('configuration_read_admissions', default=None)


@contextmanager
def using_context(context):
    """Carry the caller's checkout through readers that accept only identities."""
    from config_requests import context_value
    token = READ_CONTEXT.set(context_value(context))
    try:
        yield
    finally:
        READ_CONTEXT.reset(token)


def revision():
    value = json.loads(LAYOUT.read_text())
    if set(value) != {'revision'} or type(value['revision']) is not int or value['revision'] < BASELINE:
        raise ValueError('Invalid declared configuration layout')
    return value['revision']


@contextmanager
def internal():
    token = INTERNAL.set(True)
    try:
        yield
    finally:
        INTERNAL.reset(token)


@contextmanager
def reading(store):
    """Read-only short lock; an unused installation creates no state."""
    lock = store.root.parent/'configuration-history.lock'
    if not lock.exists():
        if store.root.exists():
            raise ValueError('Configuration history lock missing; restore private history')
        yield
        return
    with lock.open('r') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        yield


@contextmanager
def read_admissions():
    """Reuse readiness only while the outer shared read lock remains held."""
    current = READ_ADMISSIONS.get()
    if current is not None and current['active']:
        yield current['checks']
        return
    current = {'active': True, 'checks': set()}
    token = READ_ADMISSIONS.set(current)
    try:
        yield current['checks']
    finally:
        current['active'] = False
        current['checks'].clear()
        READ_ADMISSIONS.reset(token)


def configuration_read(function=None, *, workflow_only=False, domain_repository=False,
                       domain_chain=False, ci_identity=False):
    """Protect the complete read, including nested reads, from partial writes."""
    if function is None:
        return lambda target: configuration_read(target, workflow_only=workflow_only,
            domain_repository=domain_repository, domain_chain=domain_chain, ci_identity=ci_identity)
    signature = inspect.signature(function)

    @wraps(function)
    def guarded(*args, **kwargs):
        if INTERNAL.get():
            return function(*args, **kwargs)
        from config_requests import Requests, context_value
        store = Requests()
        if revision() == BASELINE and not store.root.exists():
            return function(*args, **kwargs)
        values = signature.bind(*args, **kwargs).arguments
        supplied = READ_CONTEXT.get() or {}
        repo = values.get('repository') or supplied.get('repository')
        checkout = values.get('checkout') or values.get('cwd') or supplied.get('checkout', '.')
        pr = values.get('pr') or supplied.get('pr')
        chain = values.get('chain') or []
        if repo is None and chain:
            repo = chain[0].removeprefix('remote:')
        if repo is None and values.get('identity') not in {None, 'user'}:
            repo = values['identity'].removeprefix('remote:')
        context = context_value({k: v for k, v in {
            'repository': repo, 'checkout': str(checkout), 'pr': pr,
            'upstream': values.get('upstream') or supplied.get('upstream') or (chain[1].removeprefix('remote:') if len(chain) > 1 else None),
        }.items() if v is not None})
        repository_domain = domain_repository(values) if callable(domain_repository) else domain_repository
        domain = context['repository'] if repository_domain and values.get('domain', True) else None
        if domain_chain:
            domain = chain[0] if chain else None
        if values.get('scope') == 'user':
            domain = None
        selected_ci = values.get('identity') if ci_identity and values.get('scope') != 'user' else None
        with reading(store), using_context(context), read_admissions() as admitted:
            key = (str(store.root), revision(), tuple(sorted(context.items())), workflow_only, domain, selected_ci)
            if key in admitted:
                return function(*args, **kwargs)
            with internal():
                result = store._upgrade_check(context, workflow_only=workflow_only,
                                              domain_identity=domain, ci_identity=selected_ci)
            if result['status'] != 'ready':
                from shared.recovery_diagnostics import condition
                from shared.diagnostics import command, task
                action = (task(result['reason'], result.get('reference', LAYOUT)) if result['status'] == 'blocked' else
                          command(['python3', Path(__file__).resolve().parents[1]/'scripts/config_requests.py',
                              'upgrade-start', '--checkout', context['checkout'], '--repository', context['repository'],
                              *(['--upstream', context['upstream']] if context.get('upstream') else []),
                              *(['--pr', context['pr']] if context.get('pr') else [])], result.get('reference', LAYOUT)))
                raise condition('admission_required', 'Configuration upgrade: ' + result['status'] +
                                '; ' + result.get('reason', 'run upgrade-start before workflow execution'),
                                action=action)
            admitted.add(key)
            return function(*args, **kwargs)
    return guarded


def ci_owner(context):
    from config_requests import scope_identity
    import config_adapters as adapters
    return scope_identity(adapters.owner(context, {'domain': 'ci-exceptions', 'scope': 'repo'}))


def scopes(context, *, workflow_only=False):
    import config_adapters as adapters
    from config_requests import scope_identity
    result = ['user', 'repo:' + scope_identity(context['repository'])]
    if workflow_only:
        return result
    with adapters.resolved(context, [{'domain': 'pr-size'}]):
        result.extend(['domain:' + scope_identity(adapters.chain(context)[0]), 'ci:' + ci_owner(context)])
    return [*result, *(['pr:' + context['pr']] if context.get('pr') else [])]


def context_error(context):
    """PR URLs select PR state; they never establish local checkout ownership."""
    from shared.repository_scope import repository
    from config_requests import scope_identity
    import config_adapters as adapters
    if not Path(context['checkout']).is_dir():
        return 'Restore the owning checkout before upgrading: ' + context['checkout']
    actual = repository(context['checkout'])
    expected = scope_identity(context['repository'])
    if actual.startswith('remote:') and scope_identity(actual) != expected:
        return 'Use the owning checkout for repository ' + expected
    if context.get('pr'):
        primary = scope_identity(adapters.identities(context['checkout'], context['pr'])[0])
        if primary != expected:
            return 'Use the PR head repository checkout and --repository ' + primary + ' for its domain settings'
    return None


def dependency_paths(context, operations=()):
    """One freshness boundary; inventory placeholders never reset source choice."""
    import config_adapters as adapters
    items = inventory(context)
    inputs = [op for op, _ in items if op['domain'] != 'instruction-sources']
    inputs.extend(op for op in operations if op['domain'] == 'instruction-sources')
    return ({path for _, path in items} | {path for _, path, _ in upstream_paths(context)}
            | adapters.input_paths(context, inputs, bootstrap=True) | {LAYOUT, Path(__file__)})


def policy_path(domain, identity):
    """Resolve an existing store namespace, independently of an old checkout."""
    from shared import ci_exceptions, pr_size
    import settings
    if not identity.startswith(('remote:', 'path:')) and identity != 'user':
        identity = 'remote:' + identity
    if domain == 'pr-size':
        return pr_size.path(identity)
    if domain == 'ci-exceptions':
        return ci_exceptions.config_path(identity, 'user' if identity == 'user' else 'repo')
    return settings.domain_path(domain, identity)


def receipt_owner(record, decision, identities):
    """Original readback references already identify baseline store ownership.

    Match namespace/filename rather than the machine-specific data-root prefix.
    Never read an arbitrary path from history or resolve a removed checkout.
    """
    import re
    from config_requests import scope_identity
    op = record['operations'][decision['operation']]
    try:
        receipt = record['progress'][decision['operation']]['receipt']
        original = Path(receipt['path'])
        if op['domain'] == 'completion':
            valid = original.parent.name == 'repositories' and re.fullmatch('[0-9a-f]{24}[.]txt', original.name)
        else:
            valid = (re.fullmatch('[0-9a-f]{24}', original.parent.name)
                     and original.name == policy_path(op['domain'], 'user').name)
        if not original.is_absolute() or not valid:
            raise ValueError('Invalid namespace')
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError('Restore original readback receipt: ' + record['id'] + '/' + decision['id']) from exc
    for identity in dict.fromkeys(scope_identity(value) for value in identities if value):
        current = policy_path(op['domain'], identity)
        if (original.parent.name, original.name) == (current.parent.name, current.name):
            return identity
    return None


def source_path(record, decision):
    import config_adapters as adapters
    import settings
    op = record['operations'][decision['operation']]
    if op['domain'] in {*settings.DOMAINS, 'pr-size', 'ci-exceptions'}:
        return policy_path(op['domain'], 'user' if op['scope'] == 'user' else decision['repository'])
    return adapters.destination(record['context'], op)[0]


def inventory(context):
    """Discover original stores, never copy their contents into history."""
    import config_adapters as adapters
    import settings
    result = []
    for scope in ('user', 'repo'):
        for domain in ('workflow', *settings.DOMAINS, 'pr-size', 'ci-exceptions'):
            result.append({'domain': domain, 'scope': scope, 'action': 'reset'})
    result.append({'domain': 'instruction-sources', 'scope': 'repo', 'action': 'reset'})
    result.append({'domain': 'preferences', 'scope': 'catalog', 'action': 'commit'})
    if context.get('pr'):
        result.append({'domain': 'pr-partition', 'scope': 'pr', 'action': 'disable'})
    with adapters.resolved(context, result):
        return [(op, adapters.destination(context, op)[0]) for op in result]


def upstream_paths(context):
    import config
    import config_adapters as adapters
    import settings
    from shared import ci_exceptions, pr_size
    result = []
    upstream = context.get('upstream')
    if upstream:
        identity = upstream if upstream.startswith(('remote:', 'path:')) else 'remote:' + upstream
        result.extend([('workflow', config.path(upstream), identity),
                       *[(domain, settings.domain_path(domain, identity), identity) for domain in settings.DOMAINS],
                       ('pr-size', pr_size.path(identity), identity),
                       ('ci-exceptions', ci_exceptions.config_path(identity, 'repo'), identity)])
    for identity in adapters.chain(context)[1:]:
        result.extend([*[(domain, settings.domain_path(domain, identity), identity) for domain in settings.DOMAINS],
                       ('pr-size', pr_size.path(identity), identity)])
    return list(dict.fromkeys(result))


def validate_upstream(context):
    import config
    import config_adapters as adapters
    from shared import ci_exceptions, pr_size
    for domain, path, identity in upstream_paths(context):
        if not path.exists():
            continue
        try:
            raw = path.read_text()
            value = json.loads(raw) if domain != 'completion' else raw
            if domain == 'workflow':
                config.validate(value)
            elif domain == 'pr-size':
                pr_size.load([identity if identity.startswith(('remote:', 'path:')) else 'remote:' + identity], 'repo')
            elif domain == 'ci-exceptions':
                ci_exceptions.validate(value, identity if identity.startswith('remote:') else 'remote:' + identity)
            else:
                adapters.project(context, {'domain': domain, 'scope': 'user', 'action': 'set', 'value': value}, raw)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('Incompatible upstream settings require their own checkout and upgrade: ' + str(path)) from exc


def bootstrap(context):
    """Resolve just the configuration role; unrelated old options may be invalid."""
    import behavior
    import invocation
    import skill_config
    skill, role = 'dk-workflows:dk-config', 'configuration-agent'
    fields = {'override': None, 'append': None}
    sources = {key: 'default' for key in fields}
    controls = {}
    instructions = []
    for repo in [None, *([context['upstream']] if context.get('upstream') else []), context['repository']]:
        path = skill_config.scope_path(repo)
        if not path.exists():
            continue
        saved = json.loads(path.read_text())
        if not isinstance(saved, dict):
            raise ValueError('Corrupt configuration bootstrap: ' + str(path))
        if not isinstance(saved.get('skill_defaults', {}), dict):
            raise ValueError('Ambiguous configuration-agent bootstrap: ' + str(path))
        entry = saved.get('skill_defaults', {}).get(skill, {})
        # Native controls/instructions must also remain unambiguous.
        invocation.validate_settings(entry, skill=skill)
        controls.update(entry.get('agent', {}))
        if entry.get('instructions'):
            instructions = [entry['instructions']]
        layer = behavior.validate(skill, entry.get('behavior', {})).get(role, {})
        fields.update(layer)
        sources.update({key: str(path) for key in layer})
    default_path = behavior.roles(skill)[role]
    prompt = fields['override'] if fields['override'] is not None else default_path.read_text()
    if fields['append'] is not None:
        prompt += '\n\n' + fields['append']
    return {'skill': skill, 'role': role, 'effective': prompt, 'default_path': str(default_path),
            'sources': sources, 'overridden': fields['override'] is not None,
            'agent': controls, 'additional_instructions': instructions}


class Upgrades:
    """Mixed into Requests so admission and writes keep their original protocol."""

    def _owner(self, request, claim_token):
        if 'upgrade' in request and (not claim_token or request['upgrade']['claim'] != claim_token):
            raise ValueError('Current upgrade claim token required; reconcile the native owner')

    def _upgrade_context(self, request):
        from config_requests import fingerprint
        from shared.repository_scope import repository
        problem = context_error(request['context'])
        if problem:
            raise ValueError(problem)
        if request['upgrade'].get('checkout_identity') != fingerprint(repository(request['context']['checkout'])):
            raise ValueError('Checkout identity changed; restore the reserved checkout identity before recovery, '
                             'or cancel an unwritten request and start with the current context')

    def _barrier(self, request_id=None):
        active = [r for r in self._records() if 'upgrade' in r and r['status'] not in {'applied', 'cancelled'}]
        if len(active) > 1:
            raise ValueError('Conflicting upgrade owners; restore configuration history')
        if active and active[0]['id'] != request_id:
            raise ValueError('Configuration upgrade barrier: resume ' + active[0]['id'])

    def _upgrade_check(self, context, *, workflow_only=False, domain_identity=None, ci_identity=None):
        from config_requests import fingerprint, digest, read_text
        target = revision()
        records = self._records()
        for record in records:
            declared = record.get('layout_revision', BASELINE)
            if type(declared) is not int or declared > target or declared < BASELINE:
                return {'status': 'blocked', 'reason': 'Unsupported saved configuration layout',
                        'reference': str(self._path(record['id']))}
        active = [r for r in records if 'upgrade' in r and r['status'] not in {'applied', 'cancelled'}]
        if len(active) > 1:
            return {'status': 'blocked', 'reason': 'Conflicting upgrade owners; restore private history'}
        if active:
            return {'status': 'waiting', 'id': active[0]['id'], 'reference': str(self._path(active[0]['id'])),
                    'reason': 'Reconcile or wait for the reserved native configuration agent'}
        if target == BASELINE:
            return {'status': 'ready', 'layout_revision': target}
        revisions = {scope: BASELINE for scope in scopes(context, workflow_only=workflow_only)}
        from config_requests import scope_identity
        consumers = {kind: scope_identity(identity) for kind, identity in
                     [('domain', domain_identity), ('ci', ci_identity)] if identity not in {None, 'user'}}
        revisions.update({kind + ':' + identity: BASELINE for kind, identity in consumers.items()})
        for record in records:
            if record['status'] == 'applied' and 'upgrade' in record:
                for scope in record['upgrade']['scopes']:
                    if scope in revisions:
                        revisions[scope] = max(revisions[scope], record['layout_revision'])
        required = [scope for scope, rev in revisions.items() if rev < target]
        if not required:
            return {'status': 'ready', 'layout_revision': target}
        for kind, identity in consumers.items():
            if kind + ':' + identity in required:
                import config_adapters as adapters
                owner = ci_owner(context) if kind == 'ci' else scope_identity(adapters.chain(context)[0])
                if owner != identity:
                    return {'status': 'blocked', 'reason': 'Use the owning checkout to upgrade ' + kind +
                            ' settings for ' + identity, 'reference': context['checkout']}
        from shared.repository_scope import repository
        checkout_identity = fingerprint(repository(context['checkout']))
        problem = context_error(context)
        if problem:
            return {'status': 'blocked', 'reason': problem, 'reference': context['checkout']}
        pending = next((r for r in records if r['status'] == 'applying'), None)
        if pending:
            return {'status': 'blocked', 'reason': 'Reconcile uncertain configuration writes before upgrading',
                    'id': pending['id'], 'reference': str(self._path(pending['id']))}
        # Baseline installation is intentionally a no-op. Future declarations
        # admit even legacy stores with no saved asks through native assessment.
        items = inventory(context)
        validate_upstream(context)
        for op, path in items:
            raw = read_text(path)
            if raw is not None and op['domain'] != 'completion':
                json.loads(raw)  # Corruption requires restoration, never guessed intent.
        paths = dependency_paths(context)
        candidates = self.candidates({'id': '', 'context': context})
        binding = fingerprint({'target': target, 'scopes': required, 'sources': candidates,
                               'inputs': {str(p): digest(read_text(p)) for p in sorted(paths)}})
        return {'status': 'upgrade_required', 'layout_revision': target, 'scopes': required,
                'fingerprint': binding, 'checkout_identity': checkout_identity}

    def upgrade_check(self, context):
        from config_requests import context_value
        context = context_value(context)
        with reading(self), internal():
            return self._upgrade_check(context)

    def upgrade_start(self, context):
        from config_requests import context_value
        context = context_value(context)
        # Avoid creating history (or even its lock) for the baseline no-op.
        result = self.upgrade_check(context)
        if result['status'] != 'upgrade_required':
            return result
        self._private(context['checkout'])
        with self.locked():
            result = self._upgrade_check(context)
            if result['status'] != 'upgrade_required':
                return result
            assignment = bootstrap(context)  # Invalid bootstrap never reserves work.
            token = uuid.uuid4().hex
            request = {'id': 'cfg-' + uuid.uuid4().hex, 'context': context, 'status': 'open',
                       'clarifications': [], 'layout_revision': result['layout_revision'],
                       'upgrade': {'scopes': result['scopes'], 'claim': token, 'binding': None,
                                   'checkout_identity': result['checkout_identity'],
                                   }}
            self._save(request, 'reserve layout upgrade')
            return {**self._status(request), 'claim_token': token, 'delegation': 'reserved',
                    'handoff': self._upgrade_handoff(request, assignment)}

    def _upgrade_handoff(self, request, assignment=None):
        return {'request_id': request['id'], 'responsibility': 'configuration-only',
                'contract': str(Path(__file__).resolve().parents[1]/'docs/configuration-upgrades.md'),
                'behavior': assignment or bootstrap(request['context']),
                'evidence_sections': ['sources', 'context', 'operations', 'assessment', 'progress']}

    def upgrade_bind(self, request_id, claim_token, binding):
        from config_requests import text
        with self.locked():
            request = self._read(request_id)
            self._owner(request, claim_token)
            if 'upgrade' not in request or request['status'] in {'applied', 'cancelled'}:
                raise ValueError('Active upgrade required')
            previous = request['upgrade']['binding']
            if previous is not None and previous != binding:
                raise ValueError('Native agent already bound; reconcile before recovery')
            request['upgrade']['binding'] = text(binding)
            self._save(request, 'bind native upgrade agent')
            return self._status(request)

    def upgrade_recover(self, request_id, claim_token, evidence):
        """Caller reconciles native tools first; time never proves quiescence."""
        from config_requests import text
        with self.locked():
            request = self._read(request_id)
            self._owner(request, claim_token)
            self._upgrade_context(request)
            old = request['upgrade']['binding']
            if (request['status'] in {'applied', 'cancelled'} or not isinstance(evidence, dict)
                    or set(evidence) != {'binding', 'state', 'reference'} or evidence['binding'] != old
                    or evidence['state'] not in ({'terminated', 'completed'} if old else {'not_started'})):
                raise ValueError('Native reconciliation must establish the previous owner cannot act')
            text(evidence['reference'])
            request['upgrade'].update(claim=uuid.uuid4().hex, binding=None,
                                      reconciliation=deepcopy(evidence))
            self._save(request, 'recover quiescent native owner')
            return {**self._status(request), 'claim_token': request['upgrade']['claim'],
                    'delegation': 'reserved', 'handoff': self._upgrade_handoff(request)}

    def _upgrade_source_closure(self, request):
        """Follow decision origins; retain references, never copies of intent."""
        candidates = self.candidates(request)
        records = {r['id']: r for r in self._records()}
        pending = [(c['request'], c['id']) for c in candidates]
        keys, answers = set(), set()
        while pending:
            key = pending.pop()
            if key in keys:
                continue
            request_id, decision_id = key
            record = records.get(request_id)
            decision = next((d for d in (record or {}).get('decisions', []) if d['id'] == decision_id), None)
            if not record or record['status'] != 'applied' or not decision:
                raise ValueError('Restore referenced original configuration decision: ' + '/'.join(key))
            keys.add(key)
            pending.extend((o['request'], o['decision']) for o in decision.get('origins', []))
            pending.extend((o['request'], o['decision']) for o in record.get('upgrade', {}).get('lineage', [])
                           if decision_id in o['targets'])
            for previous in records.values():
                if previous['status'] == 'applied' and previous.get('clarifications') and (
                        previous['id'] == request_id or any(
                            (o['request'], o['decision']) == key
                            for o in previous.get('upgrade', {}).get('lineage', []))):
                    answers.add(previous['id'])
        return candidates, records, keys, answers

    def upgrade_sources(self, request):
        """Active leaves plus masked historical origins, all by reference."""
        candidates, records, keys, answers = self._upgrade_source_closure(request)
        active = {(c['request'], c['id']) for c in candidates}
        result = []
        for request_id in sorted({key[0] for key in keys}):
            record = records[request_id]
            relevant = {decision for source, decision in keys if source == request_id}
            result.append({'request': request_id, 'reference': str(self._path(request_id)),
                           'active': [c for c in candidates if c['request'] == request_id],
                           'historical': [d['id'] for d in record.get('decisions', [])
                                          if d['id'] in relevant and (request_id, d['id']) not in active],
                           'masks': [d['id'] for d in record.get('decisions', []) if d['id'] not in relevant],
                           'ask_reference': record.get('upgrade', {}).get('lineage', request_id),
                           'clarification_references': [{'request': answer, 'section': 'ask'} for answer in sorted(answers)
                               if answer == request_id or any(o['request'] == request_id for o in
                                   records[answer].get('upgrade', {}).get('lineage', []))]})
        return {'sources': result, 'stores': [{'domain': op['domain'], 'scope': op['scope'], 'path': str(path)}
                                            for op, path in inventory(request['context'])]}

    def _upgrade_revision_error(self, request):
        if request['status'] == 'applying':
            return ('Upgrade target changed; restore a compatible installation declaring layout '
                    + str(request['layout_revision']) + '; reconcile original writes and receipts before starting a newer upgrade')
        return 'Upgrade target changed; cancel the unwritten request before restarting'

    def _upgrade_admission(self, request, proposal, candidates, decisions, operations):
        from config_requests import text, read_text, logical, scope_identity, digest
        if 'upgrade' not in request:
            return
        if request['upgrade']['binding'] is None:
            raise ValueError('Bind the reserved native configuration agent before assessment')
        if request['layout_revision'] != revision():
            raise ValueError(self._upgrade_revision_error(request))
        if not proposal['assessment'].get('unresolved'):
            self._upgrade_validate(request, operations)
        lineage = proposal.get('lineage')
        if not isinstance(lineage, list):
            raise ValueError('Upgrade requires lineage for every active decision')
        expected = {(c['request'], c['id']) for c in candidates}
        if len(lineage) != len(expected) or {(l['request'], l['decision']) for l in lineage} != expected:
            raise ValueError('Upgrade lineage must cover every active decision exactly once')
        ids = {d['id'] for d in decisions}
        import config_adapters as adapters
        records = {r['id']: r for r in self._records()}
        for item in lineage:
            if (set(item) != {'request', 'decision', 'targets', 'disposition', 'evidence'}
                    or item['disposition'] not in {'translated', 'retired', 'preserved'}
                    or not isinstance(item['targets'], list) or set(item['targets']) - ids
                    or (item['disposition'] == 'translated') != bool(item['targets'])):
                raise ValueError('Invalid upgrade translation lineage')
            text(item['evidence'])
            old = next(c for c in candidates if (c['request'], c['id']) == (item['request'], item['decision']))
            domain_owned = old['domain'] in {*adapters.settings.DOMAINS, 'pr-size', 'ci-exceptions'}
            owner = (scope_identity(adapters.owner(request['context'], {'domain': old['domain'], 'scope': 'repo'}))
                     if domain_owned else scope_identity(request['context']['repository']))
            if old['scope'] == 'repo' and scope_identity(old['repository']) != owner:
                if item['disposition'] != 'preserved':
                    raise ValueError('Incompatible upstream settings require their own checkout and upgrade')
            record = records[item['request']]
            op = record['operations'][old['operation']]
            original_path = source_path(record, old)
            raw = read_text(original_path)
            current = logical(raw, op)
            present = True
            for key in old['path']:
                if not isinstance(current, dict) or key not in current:
                    present, current = False, None
                    break
                current = current[key]
            observed = {'present': present, **({'value': current} if present else {})}
            # Original admitted translations explain their own readback values.
            # _proposal has already checked every original before/after hash;
            # recovery must keep its operations, source lineage and receipts.
            explained = request['status'] == 'applying' and any(
                progress.get('intent') and digest(raw) == progress['after']
                and adapters.destination(request['context'], operation)[0] == original_path
                for operation, progress in zip(request['operations'], request['progress']))
            clarified = request['clarifications'] or record.get('clarifications') or any(
                previous['status'] == 'applied' and previous.get('clarifications') and any(
                    (item['request'], item['decision']) == (old['request'], old['id'])
                    for item in previous.get('upgrade', {}).get('lineage', [])) for previous in records.values())
            if observed != old['after'] and not explained and not clarified and not proposal['assessment']['unresolved']:
                raise ValueError('Recorded ask conflicts with current values; clarification required')
        # Translation is not a new user preference; original asks stay immutable.
        if request['status'] == 'applying' and lineage != request['upgrade'].get('lineage'):
            raise ValueError('Partial recovery must preserve original translation lineage')
        request['upgrade']['lineage'] = deepcopy(lineage)

    def _upgrade_validate(self, request, operations):
        """Admit the resulting layout, without validating the obsolete whole input."""
        from config_requests import read_text, digest
        import config_adapters as adapters
        context = request['context']
        projected = {}
        with adapters.resolved(context, operations):
            for index, op in enumerate(operations):
                path = adapters.destination(context, op)[0]
                raw = read_text(path)
                progress = request.get('progress', [])[index] if request['status'] == 'applying' else {}
                projected[path] = (raw if progress.get('intent') and digest(raw) == progress['after']
                                   else adapters.project(context, op, raw))
        for op, path in inventory(context):
            adapters.validate_observed(context, op, projected.get(path, read_text(path)))
        validate_upstream(context)
