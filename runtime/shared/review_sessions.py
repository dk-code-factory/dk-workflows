"""Durable review sessions for standalone reviews and workflow callers."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import uuid
from urllib.parse import urlparse

from shared.private_state import root, read, mutate
from shared import review_coverage as coverage
from shared.engineering_identity import engineering_id


def directory(pr_url):
    url = urlparse(pr_url)
    parts = url.path.strip('/').split('/')
    if (url.scheme != 'https' or not url.hostname or len(parts) != 4
            or parts[2] != 'pull' or not parts[3].isdigit()):
        raise ValueError('expected a PR URL')
    return root('/'.join([url.hostname, *parts[:2]]).lower()) / 'review-sessions'


def outside(path, context):
    return coverage.outside(path, context)


def identity(context, prepared, reviewers, scope='', rules=''):
    field = 'engineering_id' if prepared.get('preparation_version', 1) >= 3 else 'preparation_id'
    return {'pr_url': context['url'], 'head': context['head_sha'],
        'base': prepared['identity']['base_sha'], field: engineering_id(prepared),
        'tree': coverage.git(context['checkout'], 'rev-parse', f"{context['head_sha']}^{{tree}}"),
        'scope': coverage.scope(prepared), 'scope_text': coverage.digest(scope),
        'rules_text': coverage.digest(rules), 'reviewers': list(reviewers)}


def validate_artifact_storage(path, context):
    """Protect immutable evidence before its adjacent mutation lock is opened."""
    raw = Path(path)
    coverage.storage_path(raw, context)
    path = outside(raw, context)
    coverage.storage_path(path, context)
    lock = path.with_name(path.name + '.lock')
    if (lock.resolve() == path.resolve()
            or (lock.exists() and path.exists() and lock.samefile(path))):
        raise ValueError('review artifact and its lock must be distinct files')
    if lock.exists() and lock.stat().st_size:
        raise ValueError('review artifact lock destination contains existing data')
    return path


def validate_storage(path, context):
    """Check session destinations before opening either lock for writing."""
    path = validate_artifact_storage(path, context)
    if path.name.endswith('.lock'):
        raise ValueError('review session filenames ending in .lock are reserved for locks')
    execution = path.with_name(path.name + '.execution.lock')
    outside(execution, context)
    for other in (path, path.with_name(path.name + '.lock')):
        if (execution.resolve() == other.resolve()
                or (execution.exists() and other.exists() and execution.samefile(other))):
            raise ValueError('review session and its locks must be distinct files')
    if execution.exists() and execution.stat().st_size:
        raise ValueError('review session lock destination contains existing data')
    for name in ('review-checkpoints', 'review-results'):
        outside(path.parent / name, context)
    return path


@contextmanager
def ownership(path):
    """Execution and recovery share one nonblocking ownership lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + '.execution.lock')
    if lock_path.exists() and lock_path.stat().st_size:
        raise ValueError('review session lock destination contains existing data')
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('review session is already executing') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def iteration_allocation(binding):
    result = {key: binding[key] for key in ('iteration_run_id', 'cycle', 'attempt')}
    if binding.get('architecture_stage'):
        result['architecture_stage'] = binding['architecture_stage']
    return result


def reserve(path, context, allocation, *, activation=None):
    """Persist evidence before delegation, even if the runner never initializes."""
    path = validate_storage(path, context)
    saved = {'reservation_version': 1, 'status': 'reserved',
             'iteration_allocation': allocation, 'launch_tracking_version': 1,
             'execution_history': [], 'generation': 0}
    if activation:
        saved['recovery_activation'] = activation
    with ownership(path):
        def initialize(old):
            if old != saved:
                raise ValueError('review session reservation already exists')
        mutate(path, saved, initialize)


def unlaunched(data):
    """Absence of optional telemetry or result prose is never launch evidence."""
    history = data.get('execution_history')
    if (data.get('launch_tracking_version') != 1 or not isinstance(history, list)
            or not isinstance(data.get('reviews', {}), dict)
            or not isinstance(data.get('review_history', []), list)
            or not isinstance(data.get('results', []), list)
            or data.get('status') not in {'reserved', 'active', 'incomplete'}
            or data.get('invocations', 0) != len(history)):
        raise ValueError('review launch state is uncertain; prelaunch recovery denied')
    for number, item in enumerate(history, 1):
        if (not isinstance(item, dict) or item.get('number') != number
                or item.get('status') != 'drained' or not isinstance(item.get('launch_intents'), list)):
            raise ValueError('review launch state is uncertain; prelaunch recovery denied')
        if item['launch_intents']:
            raise ValueError('reviewer launch intent exists; prelaunch recovery denied')
    if data.get('reviews') or data.get('results'):
        # Queued/running checkpoints may be written before dispatch. Only the
        # mandatory launch journal can establish whether dispatch was attempted.
        if not history:
            raise ValueError('review launch state is uncertain; prelaunch recovery denied')
    if any(not isinstance(item, dict) or item.get('status') not in {'running', 'failed'}
           for item in [*data.get('reviews', {}).values(), *data.get('review_history', [])]):
        raise ValueError('review checkpoints contradict zero launch; prelaunch recovery denied')
    for reference in data.get('results', []):
        artifact = coverage.load(reference)
        result = artifact['result']
        if (artifact.get('session_id') != data.get('id')
                or artifact.get('session_identity') != data.get('identity')
                or result.get('complete') is not False or result.get('guard_failure') is not True
                or any(result.get(key) for key in ('executed_reviewers', 'reused_reviewers',
                    'failed_reviewers', 'skipped_reviewers', 'architecture_review', 'findings'))):
            raise ValueError('review results contradict zero launch; prelaunch recovery denied')


def recoverable_roles(data):
    """All workers drained, successful evidence intact, failed transports typed."""
    from shared.role_failures import eligible
    if not isinstance(data, dict):
        raise ValueError('role recovery session is malformed')
    history = data.get('execution_history')
    identity = data.get('identity')
    if (not isinstance(identity, dict) or not isinstance(identity.get('reviewers'), list)
            or not isinstance(data.get('reviews'), dict)):
        raise ValueError('role recovery identity or checkpoint ownership is uncertain')
    if (data.get('session_version') != 3 or data.get('status') != 'incomplete'
            or data.get('launch_tracking_version') != 1 or not isinstance(history, list) or not history
            or data.get('invocations') != len(history)
            or any(not isinstance(item, dict) or item.get('number') != index or item.get('status') != 'drained'
                   or not isinstance(item.get('launch_intents'), list)
                   for index, item in enumerate(history, 1))):
        raise ValueError('role recovery launch intent requires drained execution; ownership is uncertain')
    roles = list(data['identity']['reviewers'])
    if not data.get('iteration_allocation', {}).get('architecture_stage'):
        roles.append('architecture-review')
    if set(data.get('reviews', {})) != set(roles) or not data.get('results'):
        raise ValueError('role recovery requires every role and its terminal result')
    artifact = coverage.load(data['results'][-1])
    if (artifact.get('session_id') != data.get('id') or artifact.get('session_identity') != data['identity']
            or artifact['result'].get('complete') is not False):
        raise ValueError('role recovery result identity changed')
    failures = {item['role']: item for item in artifact['result'].get('role_failures', [])}
    # Validate original deliveries before charging, including the successful
    # roles that recovery will never dispatch again.
    import hashlib
    import importlib.util
    validator_path = Path(__file__).resolve().parents[1] / 'dk-pr-review/scripts/validate_result.py'
    spec = importlib.util.spec_from_file_location('recovery_findings_validator', validator_path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    deliveries = {}
    for role in roles:
        ref = data.get('deliveries', {}).get(role)
        if not isinstance(ref, dict):
            raise ValueError('role recovery delivery missing')
        raw = Path(ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref.get('sha256'):
            raise ValueError('role recovery delivery changed')
        import json
        deliveries[role] = json.loads(raw)
        handoff = deliveries[role]
        delivered_checkout = handoff.get('context', {}).get('checkout') if role == 'architecture-review' else handoff.get('checkout')
        if (not delivered_checkout or coverage.git(delivered_checkout, 'rev-parse', 'HEAD') != data['identity']['head']
                or coverage.git(delivered_checkout, 'status', '--porcelain=v1')):
            raise ValueError('role recovery delivery checkout changed')
        if handoff.get('response_budget') != data['id'] + ':' + role:
            raise ValueError('role recovery response-slot ownership changed')
        from shared import architecture
        if role == 'architecture-review':
            architecture.validate_preparation(handoff['architecture'], handoff['context'], handoff['decisions'])
        else:
            for field in ('full_preparation', 'full_decisions'):
                if field in handoff:
                    full = handoff[field]
                    if hashlib.sha256(Path(full['path']).read_bytes()).hexdigest() != full['sha256']:
                        raise ValueError('role recovery full delivery changed')
    architecture_delivery = deliveries.get('architecture-review')
    failed = []
    for role in roles:
        item = data['reviews'][role]
        checkpoint = read(Path(item['checkpoint']['path']), {})
        if (coverage.digest(checkpoint) != item['checkpoint']['sha256']
                or checkpoint.get('checkpoint_version') != 2 or checkpoint.get('reviewer') != role
                or checkpoint.get('session_id') != data['id'] or checkpoint.get('session_identity') != data['identity']
                or checkpoint.get('status') != item.get('status')):
            raise ValueError('role recovery checkpoint changed')
        if item['status'] == 'completed':
            if not isinstance(checkpoint.get('result'), dict):
                raise ValueError('completed role result missing')
            if role == 'architecture-review':
                from shared import architecture
                architecture.verify_review(checkpoint['result'], architecture_delivery['architecture'],
                    architecture_delivery['context'], architecture_delivery['decisions'])
            else:
                handoff = deliveries[role]
                validator.validate(checkpoint['result'], validator.added_lines(Path(handoff['patch']).read_text()))
            continue
        if (item['status'] != 'failed' or not eligible(checkpoint.get('diagnostic'))
                or role not in failures or failures[role].get('diagnostic') != checkpoint['diagnostic']):
            raise ValueError('launch intent has no eligible structured infrastructure failure')
        if role not in history[-1]['launch_intents']:
            raise ValueError('failed role dispatch ownership is uncertain')
        failed.append(role)
    if not failed or set(failures) != set(failed):
        raise ValueError('role recovery requires only eligible failed roles')
    return failed


def validate_recovery_proof(data, payload):
    if payload.get('kind') == 'role_infrastructure':
        if (recoverable_roles(data) != payload.get('failed_roles')
                or data.get('identity') != payload['identity']):
            raise ValueError('role recovery evidence changed')
    else:
        unlaunched(data)


def activation_plan(data):
    """Validate pending evidence without treating it as activation authority."""
    activation = data.get('recovery_activation')
    if not isinstance(activation, dict) or coverage.digest(activation.get('payload')) != activation.get('sha256'):
        raise ValueError('recovery activation plan is missing or changed; reconcile recovery')
    return activation['payload']


def require_activation(data, path):
    if 'recovery_activation' not in data:
        if data.get('prelaunch_recovery'):
            raise ValueError('recovery activation is missing; reconcile recovery')
        return
    payload = activation_plan(data)
    reference = data['recovery_activation']
    receipt = read(Path(reference['path']), {})
    proof = read(Path(payload['proof']['path']), {})
    if (receipt != payload or coverage.digest(receipt) != reference['sha256']
            or coverage.digest(proof) != payload['proof']['sha256']
            or Path(payload['binding']['session_file']).resolve() != Path(path).resolve()
            or data.get('generation', 0) != payload['generation']
            or ('identity' in data and data['identity'] != payload['identity'])
            or payload['allocation'] != data.get('iteration_allocation')):
        raise ValueError('recovery activation is pending or changed; reconcile recovery')


def activate(path, context, activation):
    """The caller commits its charge before publishing this immutable receipt."""
    path = validate_storage(path, context)
    target = validate_artifact_storage(Path(activation['path']), context)
    with ownership(path):
        saved = read(path, {})
        if saved.get('recovery_activation') != activation:
            raise ValueError('recovery activation plan changed')
        payload = activation_plan(saved)
        proof = read(Path(payload['proof']['path']), {})
        if (coverage.digest(proof) != payload['proof']['sha256']
                or proof.get('iteration_allocation') != payload['prior_allocation']):
            raise ValueError('recovery proof changed')
        validate_recovery_proof(proof, payload)
        if (saved.get('iteration_allocation') != payload['allocation']
                or saved.get('generation', 0) != payload['generation']
                or ('identity' in saved and saved['identity'] != payload['identity'])
                or path != Path(payload['binding']['session_file']).resolve()):
            raise ValueError('recovery allocation changed')
        def retain(old):
            if old != payload:
                raise ValueError('recovery activation receipt changed')
        mutate(target, payload, retain)
        require_activation(saved, path)


def recover_unlaunched(path, context, current_identity, prior_binding, replacement_binding, *, after_dispatch=False):
    """Freeze or authorize a proved-unlaunched session while excluding workers."""
    path = validate_storage(path, context)
    with ownership(path):
        def recover(data):
            prior = iteration_allocation(prior_binding)
            if data.get('iteration_allocation') != prior:
                raise ValueError('review allocation evidence is missing or changed')
            failed_roles = recoverable_roles(data) if after_dispatch else []
            if after_dispatch and data.get('identity') != current_identity:
                raise ValueError('role recovery requires identical evidence and bindings')
            if not after_dispatch:
                unlaunched(data)
            if data.get('prelaunch_recovery'):
                raise ValueError('prelaunch recovery already recorded for this session')
            proof = dict(data)
            fingerprint = coverage.digest(proof)
            proof_path = validate_artifact_storage(path.parent / 'prelaunch-proofs' / (fingerprint + '.json'), context)
            def retain(old):
                if old != proof:
                    raise ValueError('prelaunch recovery proof changed')
            mutate(proof_path, proof, retain)
            same = data.get('identity') == current_identity
            data['prelaunch_recovery'] = {'path': str(proof_path), 'sha256': fingerprint}
            binding = {**replacement_binding, 'session_file': str(path) if same else replacement_binding['session_file']}
            allocation = iteration_allocation(binding)
            payload = {'activation_version': 1, 'proof': data['prelaunch_recovery'],
                'prior_binding': prior_binding, 'prior_allocation': prior, 'binding': binding,
                'allocation': allocation, 'identity': current_identity, 'resume': same,
                'generation': data.get('generation', 0) + 1 if same else 0}
            if after_dispatch:
                payload.update(kind='role_infrastructure', failed_roles=failed_roles)
            digest = coverage.digest(payload)
            receipt = coverage.storage_path(path.parent / 'recovery-activations' / (digest + '.json'), context)
            activation = {'path': str(receipt), 'sha256': digest, 'payload': payload}
            data['recovery_activation'] = activation
            data['generation'] = data.get('generation', 0) + 1
            if same:
                data['iteration_allocation'] = allocation
                data['prelaunch_resume_authorized'] = data.get('invocations', 0) + 1
                if after_dispatch:
                    data['role_resume_authorized'] = data.get('invocations', 0) + 1
            else:
                # This also freezes an unused reservation: an old delegated
                # worker cannot initialize it after a replacement is allocated.
                data['status'] = 'superseded'
            return {'proof': data['prelaunch_recovery'], 'resume': same, 'activation': activation}
        return mutate(path, {}, recover)


class Session:
    def __init__(self, context, prepared, reviewers, *, path=None, resume=False, scope='', rules=''):
        version = 3 if prepared.get('preparation_version', 1) >= 4 else 2 if prepared.get('preparation_version', 1) >= 3 else 1
        if version >= 2:
            if (any(context[k] != prepared['identity'][field] for k, field in (
                    ('url', 'pr_url'), ('head_sha', 'head_sha'), ('base_sha', 'base_sha')))
                    or list(reviewers) != prepared['reviewer_order']):
                raise ValueError('review session has changed evidence; refresh preparation')
            context = coverage.validate_current(prepared)
        self.context, self.prepared = context, prepared
        if resume and path is None:
            raise ValueError('resuming requires --session-file')
        base = Path(prepared['workflow_dir']) / 'review-sessions' if version >= 2 else directory(context['url'])
        self.path = outside(path or base / (uuid.uuid4().hex + '.json'), context)
        self.validate_storage()
        self.identity = identity(context, prepared, reviewers, scope, rules)
        default = {'session_version': version, 'id': uuid.uuid4().hex, 'identity': self.identity,
                   'status': 'active', 'reviews': {}, 'results': [],
                   'launch_tracking_version': 1, 'execution_history': [], 'generation': 0}
        def initialize(old):
            if resume:
                if old.get('session_version') != version:
                    raise ValueError('legacy session contract; resume through its retained runtime')
                if (old.get('identity') != self.identity
                        or old.get('status') not in {'active', 'incomplete'}):
                    raise ValueError('review session is missing, finished or has changed evidence; start fresh')
            elif old.get('reservation_version') == 1 and old.get('status') == 'reserved':
                allocation = old['iteration_allocation']
                activation = old.get('recovery_activation')
                if activation:
                    if activation_plan(old)['identity'] != self.identity:
                        raise ValueError('recovery reservation has changed evidence; use its pinned scope/rules')
                    # Correct initialization may precede explicit reconciliation.
                    # An existing receipt must already be valid; absence still
                    # blocks execution and acceptance through require_activation.
                    if Path(activation['path']).exists():
                        require_activation(old, self.path)
                old.clear()
                old.update(default, iteration_allocation=allocation)
                if activation:
                    old['recovery_activation'] = activation
            elif old != default:
                raise ValueError('session file already exists; explicitly resume interrupted work')
            if version >= 2:
                reference = {'preparation_id': prepared['preparation_id'], 'engineering_id': engineering_id(prepared)}
                if reference not in old.setdefault('preparations', []):
                    old['preparations'].append(reference)
            self.generation = old.get('generation', 0)
            return old['id']
        with ownership(self.path):
            self.id = mutate(self.path, {} if resume else default, initialize)

    def validate_storage(self):
        validate_storage(self.path, self.context)

    def update(self, operation):
        self.validate_storage()
        def guarded(data):
            if (data.get('id') != self.id or data.get('identity') != self.identity
                    or data.get('generation', 0) != self.generation):
                raise ValueError('review session changed during execution')
            return operation(data)
        return mutate(self.path, {}, guarded)

    @contextmanager
    def execution(self):
        self.validate_storage()
        with ownership(self.path):
            def begin(data):
                require_activation(data, self.path)
                if data.get('quota_recovery', {}).get('status') in {'waiting', 'exhausted'}:
                    raise ValueError('quota recovery is waiting; resume through the review runner')
                if data['status'] not in {'active', 'incomplete'}:
                    raise ValueError('review session finished; start a fresh review')
                history = data.get('execution_history', [])
                if (data.get('iteration_allocation') and history
                        and all(h.get('status') == 'drained' and not h.get('launch_intents') for h in history)
                        and data.get('prelaunch_resume_authorized') != data.get('invocations', 0) + 1):
                    raise ValueError('unlaunched iteration session requires bounded cycle-recover')
                if (data.get('iteration_allocation') and history and history[-1].get('status') == 'drained'
                        and history[-1].get('launch_intents') and data.get('results')
                        and coverage.load(data['results'][-1])['result'].get('role_failures')
                        and data.get('role_resume_authorized') != data.get('invocations', 0) + 1):
                    raise ValueError('failed iteration roles require bounded cycle-recover')
                data['status'] = 'active'
                data['invocations'] = data.get('invocations', 0) + 1
                self.invocation = data['invocations']
                if data.get('launch_tracking_version') == 1:
                    data['execution_history'].append({'number': self.invocation,
                        'status': 'active', 'launch_intents': []})
            self.update(begin)
            try:
                yield self
            finally:
                def drained(data):
                    if data.get('launch_tracking_version') == 1:
                        data['execution_history'][-1]['status'] = 'drained'
                self.update(drained)

    def launch(self, reviewer):
        """Write intent before provider dispatch; a crash afterwards is uncertain."""
        def save(data):
            if data.get('launch_tracking_version') == 1:
                item = data['execution_history'][-1]
                if item['number'] != self.invocation or item['status'] != 'active':
                    raise ValueError('review launch lacks active execution ownership')
                item['launch_intents'].append(reviewer)
        self.update(save)

    def delivery(self, reviewer, path, handoff):
        """Retain the original prompt paths across same-slot transport recovery."""
        self.validate_storage()
        path = coverage.storage_path(Path(path), self.context)
        import hashlib
        def select(data):
            saved = data.setdefault('deliveries', {}).get(reviewer)
            if saved:
                raw = Path(saved['path']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != saved['sha256']:
                    raise ValueError('review delivery changed')
                return saved['path']
            if path.exists():
                raise ValueError('review delivery destination already exists')
            from shared.private_state import write
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            write(path, handoff)
            path.chmod(0o400)
            saved = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            data['deliveries'][reviewer] = saved
            return str(path)
        return Path(self.update(select))

    def cached(self, reviewer):
        def lookup(data):
            item = data['reviews'].get(reviewer, {})
            if item.get('status') != 'completed':
                return None
            saved = read(Path(item['checkpoint']['path']), {})
            if coverage.digest(saved) != item['checkpoint']['sha256']:
                raise ValueError('review checkpoint changed')
            if (saved.get('checkpoint_version') != (2 if self.prepared.get('preparation_version', 1) >= 4 else 1) or saved.get('reviewer') != reviewer
                    or saved.get('status') != 'completed' or saved.get('session_id') != self.id
                    or saved.get('session_identity') != self.identity):
                raise ValueError('review checkpoint does not match this session and reviewer')
            if reviewer != 'architecture-review':
                from shared import review_execution
                descriptor = self.prepared['reviewers'][reviewer]
                binding = (descriptor.get('execution') or {}).get('binding', reviewer)
                if review_execution.requirements(binding, {}):
                    delivery = data.get('deliveries', {}).get(reviewer, {})
                    import hashlib
                    path = delivery.get('path')
                    if not path or hashlib.sha256(Path(path).read_bytes()).hexdigest() != delivery.get('sha256'):
                        raise ValueError('required specialist delivery changed or unavailable')
                    evidence = saved.get('execution_evidence', {})
                    if evidence.get('handoff') != path:
                        raise ValueError('required specialist checkpoint delivery mismatch')
                    review_execution.verify(evidence.get('reference'), binding, descriptor, path, saved['result'])
            return saved['result']
        return self.update(lookup)

    def execution_evidence(self, reviewer):
        def lookup(data):
            item = data['reviews'].get(reviewer, {})
            saved = read(Path(item['checkpoint']['path']), {})
            if coverage.digest(saved) != item['checkpoint']['sha256']:
                raise ValueError('review checkpoint changed')
            return saved.get('execution_evidence', {}).get('reference')
        return self.update(lookup)

    def delivery_reference(self, reviewer):
        def lookup(data):
            reference = data.get('deliveries', {}).get(reviewer)
            if not isinstance(reference, dict):
                raise ValueError('required specialist delivery unavailable')
            import hashlib
            if hashlib.sha256(Path(reference['path']).read_bytes()).hexdigest() != reference['sha256']:
                raise ValueError('required specialist delivery changed')
            return reference['path']
        return self.update(lookup)

    def record(self, reviewer, status, result=None, reason=None, diagnostic=None, execution_evidence=None):
        self.validate_storage()
        if reviewer not in [*self.identity['reviewers'], *(['architecture-review'] if self.prepared.get('preparation_version', 1) >= 4 else [])]:
            raise ValueError('reviewer is outside this session')
        saved = {'checkpoint_version': 2 if self.prepared.get('preparation_version', 1) >= 4 else 1, 'session_id': self.id, 'session_identity': self.identity,
                 'reviewer': reviewer, 'status': status, 'result': result, 'reason': reason,
                 **({'execution_evidence': execution_evidence} if execution_evidence is not None else {}),
                 **({'diagnostic': diagnostic} if diagnostic is not None else {})}
        fingerprint = coverage.digest(saved)
        path = coverage.storage_path(
            self.path.parent / 'review-checkpoints' / (fingerprint + '.json'), self.context)
        def immutable(old):
            if old != saved:
                raise ValueError('review checkpoint changed')
        mutate(path, saved, immutable)
        def save(data):
            # Coordinator failures can happen before cache lookup. They must not
            # replace a completed reviewer's authoritative checkpoint or history.
            if status == 'failed' and data['reviews'].get(reviewer, {}).get('status') == 'completed':
                return
            item = {'status': status, 'checkpoint': {'path': str(path), 'sha256': fingerprint}}
            data.setdefault('review_history', []).append({'reviewer': reviewer, **item})
            data['reviews'][reviewer] = item
        self.update(save)

    def finish(self, result):
        stage = self.update(lambda data: data.get('iteration_allocation', {}).get('architecture_stage'))
        if stage:
            from shared import iteration_architecture
            iteration_architecture.verify(stage, self.prepared)
            if result.get('architecture_review') or result.get('architecture_findings'):
                raise ValueError('iteration cycle cannot contain another architecture review')
            result = {**result, 'iteration_architecture': stage}
        self.validate_storage()
        self.update(lambda data: require_activation(data, self.path))
        if self.prepared.get('preparation_version', 1) >= 3:
            # A drained guard failure remains useful diagnostic evidence even if
            # the checkout changed. Preparation and agreements must still be live.
            coverage.validate_current(self.prepared,
                check_checkout=not (result.get('guard_failure') and not result['complete']))
        item = coverage.retain(self.path, self.context, self.prepared, result, self.id)
        def save(data):
            data['results'].append(item)
            data['status'] = 'completed' if result['complete'] else 'incomplete'
        self.update(save)
        return item


def result_artifact(session_path, item):
    session = read(Path(session_path), {})
    require_activation(session, Path(session_path))
    if (session.get('session_version') not in {1, 2, 3} or not session.get('results')
            or session['results'][-1] != item):
        raise ValueError('result is not the latest registered result of this review session')
    artifact = coverage.load(item)
    stage = session.get('iteration_allocation', {}).get('architecture_stage')
    if artifact['result'].get('iteration_architecture') != stage:
        raise ValueError('review result differs from its allocated architecture stage')
    if (artifact.get('version') != session['session_version'] + 1
            or artifact['session_id'] != session.get('id')
            or artifact['session_identity'] != session.get('identity')
            or session.get('status') != ('completed' if artifact['result']['complete'] else 'incomplete')):
        raise ValueError('result is not the latest registered result of this review session')
    return artifact
