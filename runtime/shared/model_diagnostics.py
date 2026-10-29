"""Durable, private preparation exchanges and correction budgets."""
from contextlib import contextmanager
from contextvars import ContextVar
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from shared.diagnostics import Diagnostic, task
from shared.private_state import read, write
from shared.repository_scope import inside_checkout
from shared.workflow_storage import allocate


CURRENT = ContextVar('model_diagnostics', default=None)


class ValidationFailure(Diagnostic, ValueError):
    """A rejected exchange is evidence, never a preparation assessment."""


class StorageFailure(Diagnostic):
    """Local evidence storage failed after reserving the response slot."""


def preserve_failure(original, secondary):
    """Keep the provider's type and recovery action when recording also fails."""
    detail = ('Diagnostic persistence also failed: ' + str(secondary) +
              ' Private diagnostics: ' + ', '.join(secondary.action['artifacts']) + '.')
    original.add_note(detail)
    if isinstance(original, RuntimeError):
        original.args = (str(original) + '; ' + detail,)
    if hasattr(original, 'action'):
        original.action['artifacts'] += secondary.action['artifacts']
    else:
        original.action = task('Inspect the private evidence and repair diagnostic storage. '
                               'Preserve the original provider failure and correction budget.',
                               *secondary.action['artifacts'])
    original.action['diagnostic'] = (original.action.get('diagnostic', '') + '\n' + detail).strip()


class FieldError(ValueError):
    def __init__(self, field, detail):
        super().__init__(field + ': ' + detail)
        self.field = field


class Exchange:
    def __init__(self, path, value):
        self.path, self.value = path, value

    def save(self):
        try:
            write(self.path, self.value)
        except OSError as exc:
            artifacts = [self.path]
            fallback = None
            fallback_detail = ''
            # The workflow filesystem may be full; a private TMPDIR can still
            # retain the in-memory response and validation evidence.
            try:
                base = Path(tempfile.gettempdir()).resolve()
                if (base.is_relative_to(Path(__file__).resolve().parents[2])
                        or inside_checkout(base, self.value['request']['checkout'])):
                    raise ValueError('private fallback storage must stay outside the plugin and repository')
                fallback = Path(tempfile.mkdtemp(prefix='dk-model-exchange-', dir=base)) / self.path.name
                write(fallback, self.value)
                artifacts.append(fallback)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as fallback_error:
                fallback_detail = f' Fallback persistence failed: {fallback_error}.'
                if fallback is not None:
                    try:
                        if fallback.exists():
                            artifacts.append(fallback)
                        else:
                            fallback.parent.rmdir()
                    except OSError:
                        pass
            raise StorageFailure(
                'model_diagnostics_failed', f'Model exchange could not be saved: {exc}.' + fallback_detail,
                task('Inspect the private evidence and repair storage capacity or permissions. '
                     'Do not reset the correction budget or retry unchanged consumed responses.',
                     *artifacts), outcome='error') from exc

    def start(self, prompt, attempt):
        self.value['status'] = 'running'
        self.value['attempts'].append({'attempt': attempt, 'prompt': prompt})
        self.save()

    def transport(self, **details):
        self.value['attempts'][-1]['provider'] = details
        self.save()

    def response(self, raw):
        self.value['attempts'][-1]['response'] = raw
        self.save()

    def rejected(self, exc):
        item = self.value['attempts'][-1]
        item['validation'] = {'type': type(exc).__name__, 'detail': str(exc)}
        if isinstance(exc, FieldError):
            item['validation']['field'] = exc.field
        self.value['status'] = 'rejected'
        self.save()

    def accepted(self):
        self.value['status'] = 'accepted'
        self.save()

    def provider_failed(self, exc):
        from shared.reviewer_telemetry import output_text
        self.value['attempts'][-1]['provider_error'] = {
            'type': type(exc).__name__, 'detail': str(exc),
            **{key: output_text(getattr(exc, key)) for key in ('stdout', 'stderr')
               if getattr(exc, key, None) is not None}}
        self.value['status'] = 'provider_failed'
        self.save()
        exc.add_note(f'Private diagnostics: {self.path}.')
        if hasattr(exc, 'action'):
            if str(self.path) not in exc.action['artifacts']:
                exc.action['artifacts'].append(str(self.path))
        else:
            exc.action = task('Inspect the retained exchange and repair the provider failure. '
                              'A manual retry may resume the same response slot with the unchanged '
                              'request; do not reset the correction budget.', self.path)

    def failure(self):
        fields = sorted({item['validation']['field'] for item in self.value['attempts']
                         if 'field' in item.get('validation', {})})
        detail = (' Invalid field(s): ' + ', '.join(fields) + '.') if fields else ''
        return ValidationFailure(
            'model_validation_failed',
            'Model JSON invalid after one correction or interrupted validation.' + detail +
            f' Private diagnostics: {self.path}.',
            task('Inspect the retained responses and validation details. Correct the governing evidence '
                 'or provider contract before preparing again. Do not retry unchanged inputs, reset '
                 'the PR run or correction budget, or use rejected responses as an assessment.', self.path),
            outcome='error')


def retained_failure(detail, *artifacts):
    return StorageFailure(
        'model_diagnostics_invalid', 'Retained model exchange is unavailable or invalid: ' + detail,
        task('Inspect the private index and exchange evidence. Restore the original state where '
             'available; otherwise report the unresolved storage or setup failure. '
             'Do not delete state, reset the correction budget, or retry unchanged consumed responses.',
             *artifacts), outcome='error')


def retained(path, identity):
    """Validate stored authority before it can permit any further response."""
    missing = object()
    try:
        try:
            index = read(path, missing)
        except RecursionError as exc:
            raise retained_failure(str(exc), path) from exc
        if index is missing:
            return None, None
        if (not isinstance(index, dict) or set(index) != {'status', 'evidence'}
                or index['status'] not in {'running', 'accepted'}
                or not isinstance(index['evidence'], str)):
            raise ValueError('invalid index schema')
        evidence = Path(index['evidence'])
        try:
            resolved = evidence.resolve(strict=True)
            owned_root = path.parent.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise retained_failure('exchange evidence path could not be resolved', path, evidence) from exc
        if (not evidence.is_absolute() or resolved.name != 'model-exchange.json'
                or not resolved.parent.name.startswith('workflow-')
                or resolved.parent.parent != owned_root):
            raise ValueError('index evidence pointer is outside owned exchange storage')
        try:
            value = read(evidence, None)
        except RecursionError as exc:
            raise retained_failure(str(exc), path, evidence) from exc
        if (not isinstance(value, dict)
                or set(value) != {'schema_version', 'identity', 'request', 'status', 'attempts'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['identity'] != identity
                or value['status'] not in {'running', 'accepted', 'rejected', 'provider_failed'}):
            raise ValueError('invalid exchange schema or identity')
        request = value['request']
        if (not isinstance(request, dict) or set(request) != {'prompt', 'checkout'}
                or not all(isinstance(item, str) for item in request.values())
                or not Path(request['checkout']).is_absolute()
                or ('prompt' in identity and request != {key: identity[key] for key in request})):
            raise ValueError('invalid exchange request')
        attempts = value['attempts']
        if not isinstance(attempts, list) or (not attempts and value['status'] != 'running'):
            raise ValueError('invalid attempt history')
        previous = None
        for item in attempts:
            if (not isinstance(item, dict)
                    or set(item) - {'attempt', 'prompt', 'response', 'provider', 'validation', 'provider_error'}
                    or type(item.get('attempt')) is not int or item['attempt'] not in {0, 1}
                    or not isinstance(item.get('prompt'), str)
                    or ('response' in item and not isinstance(item['response'], str))
                    or ('provider' in item and not isinstance(item['provider'], dict))):
                raise ValueError('invalid attempt schema')
            for key in ('validation', 'provider_error'):
                if key in item and (not isinstance(item[key], dict)
                                    or not all(isinstance(item[key].get(field), str) for field in ('type', 'detail'))):
                    raise ValueError('invalid attempt diagnostic')
            if ('validation' in item and ('response' not in item or 'provider_error' in item)
                    or 'provider_error' in item and 'response' in item
                    or 'field' in item.get('validation', {}) and not isinstance(item['validation']['field'], str)):
                raise ValueError('inconsistent consumed response')
            if previous is None:
                valid = item['attempt'] == 0
            else:
                valid = (item['attempt'] == previous['attempt'] and 'provider_error' in previous
                         or item['attempt'] == previous['attempt'] + 1 and 'validation' in previous)
            if not valid:
                raise ValueError('invalid response-slot transition')
            previous = item
        required = {'accepted': 'response', 'rejected': 'validation', 'provider_failed': 'provider_error'}
        if (value['status'] in required and required[value['status']] not in attempts[-1]
                or value['status'] == 'accepted' and 'validation' in attempts[-1]
                or index['status'] == 'accepted' and value['status'] != 'accepted'):
            raise ValueError('inconsistent exchange status')
        return index, value
    except (OSError, ValueError, TypeError) as exc:
        raise retained_failure(str(exc), path) from exc


@contextmanager
def exchange(prompt, checkout, scope, *, retain_accepted=None):
    if scope is None:
        yield None
        return
    # The index survives process restarts and is independent of optional telemetry.
    # Include the immutable runtime and evidence, but no output-directory identity.
    request = {'prompt': prompt, 'checkout': str(Path(checkout).resolve())}
    identity = {**({} if isinstance(scope, dict) else request),
                'scope': scope, 'runtime': str(Path(__file__).resolve().parent)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    reserved = allocate(repository=checkout)
    directory = reserved.parent / 'model-exchanges'
    directory.mkdir(mode=0o700, exist_ok=True)
    reserved.rmdir()
    path = directory / (key + '.json')
    lock = directory / (key + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(lock, os.O_RDWR)
        created = False
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        index, prior = retained(path, identity)
        if index is None and not created:
            # The lock records a prior reservation even if setup never wrote an
            # index. Missing state must not make that reservation fresh again.
            raise retained_failure('index missing for a prior exchange reservation', path, lock)
        if prior and prior['status'] == 'accepted' and index['status'] != 'accepted':
            raise retained_failure('prior acceptance did not confirm eligibility for another response',
                                   path, index['evidence'])
        if prior and prior['status'] == 'accepted' and retain_accepted is not None:
            try:
                response = json.loads(prior['attempts'][-1]['response'])
                selected = retain_accepted(response)
            except (ValueError, RuntimeError) as exc:
                raise retained_failure('accepted routing response is invalid', path, index['evidence']) from exc
            if selected:
                journal = Exchange(Path(index['evidence']), prior)
                if prior.get('request') != request:
                    raise journal.failure()
                # Retain only caller-selected routing, never assessment authority.
                yield journal
                return
        if prior and prior['status'] not in {'accepted', 'provider_failed'}:
            raise Exchange(Path(index['evidence']), prior).failure()
        if (prior and prior['status'] == 'provider_failed' and isinstance(scope, dict)
                and prior.get('request') != request):
            # Equivalent evidence preserves the budget, but an old correction
            # prompt must never execute against relocated or deleted sources.
            raise Exchange(Path(index['evidence']), prior).failure()
        # Keep successful histories immutable as well; the index points to the
        # current exchange and never grants authority to reuse a model answer.
        evidence = (Path(index['evidence']) if prior and prior['status'] == 'provider_failed'
                    else allocate(repository=checkout) / 'model-exchange.json')
        value = prior if prior and prior['status'] == 'provider_failed' else {
            'schema_version': 1, 'identity': identity, 'request': request,
            'status': 'running', 'attempts': []}
        journal = Exchange(evidence, value)
        journal.save()
        write(path, {'status': 'running', 'evidence': str(evidence)})
        token = CURRENT.set(journal)
        try:
            yield journal
            if journal.value['status'] == 'accepted':
                # Only successful durable journal acceptance can permit another
                # exchange. This optional marker cannot invalidate that result;
                # a failed marker may instead conservatively block a later call.
                try:
                    write(path, {'status': 'accepted', 'evidence': str(evidence)})
                except OSError:
                    pass
        finally:
            CURRENT.reset(token)


def transport(*, output=None, failure=None, response=None, **details):
    journal = CURRENT.get()
    if journal is not None:
        if response is not None:
            journal.value['attempts'][-1]['response'] = response
        if failure is not None and output is not None:
            details['output_path'] = str(output)
            try:
                details['response_base64'] = base64.b64encode(output.read_bytes()).decode('ascii')
            except OSError as exc:
                details['response_read_error'] = str(exc)
        journal.transport(**details)
