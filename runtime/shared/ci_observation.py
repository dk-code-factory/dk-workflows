"""Durable, exclusively owned CI windows with one pending-only extension."""
import fcntl
import copy
import math
import os
import time
from contextvars import ContextVar
from pathlib import Path

from shared.private_state import read, write, root as data_root
from shared.publication import digest
from shared import publication
from shared.storage_paths import location

ACTIVE = ContextVar('ci_observation', default=None)


def root(repository):
    data_root(repository)  # validates the repository key without touching disk
    path = location('ci')/repository
    from shared.repository_scope import inside_checkout
    if (inside_checkout(path,Path.cwd())
            or path.resolve().is_relative_to(Path(__file__).resolve().parents[2])):
        raise ValueError('CI state must remain outside repositories and package')
    return path


def bind_url(url):
    observation = ACTIVE.get()
    if observation is not None:
        observation.bind(url)


class PermissionDenied(RuntimeError):
    pass


class Observation:
    def __init__(self, path, pr, head, *, timeout=1800, extension=1800,
                 rerun=None, retry_denied=False, clock=time.time):
        if timeout < 0 or extension < 0:
            raise ValueError('invalid CI observation duration')
        self.path, self.pr, self.head = Path(path), pr, head
        self.timeout, self.extension, self.clock = timeout, extension, clock
        self.rerun, self.retry_denied = rerun, retry_denied

    @staticmethod
    def validate_window(value):
        if (not isinstance(value, dict) or not isinstance(value.get('pr'), str)
                or not isinstance(value.get('head'), str)
                or type(value.get('extensions')) is not int or value['extensions'] not in {0, 1}
                or any(type(value.get(key)) not in {int, float} or not math.isfinite(value[key])
                       for key in ('started', 'deadline', 'window_seconds', 'extension_seconds'))
                or value['window_seconds'] < 0 or value['extension_seconds'] < 0
                or value['deadline'] != value['started'] + value['window_seconds'] +
                    value['extensions'] * value['extension_seconds']
                or not isinstance(value.get('events'), list)):
            raise ValueError('CI observation deadline evidence is missing or corrupt; restore it without resetting')

    def __enter__(self):
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock = self.path.with_suffix('.observer.lock').open('w')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.state = read(self.path, {'observation_version': 1, 'history': []})
            if self.state.get('observation_version') != 1:
                raise ValueError('unsupported CI observation state')
            previous = self.state.get('current')
            if not isinstance(self.state.get('history'), list) or self.state['history'] and previous is None:
                raise ValueError('CI observation history is incomplete; restore it without resetting')
            for window in self.state['history'] + ([previous] if previous is not None else []):
                self.validate_window(window)
            rerun_id = None
            if self.rerun:
                operation = publication.load(self.rerun, 'ci-rerun')
                receipt = operation.get('receipt', {})
                approved = operation['request']['approved']
                if (operation['status'] != 'verified' or operation['request']['target'] != self.pr
                        or receipt.get('head') != self.head or approved.get('head') != self.head
                        or receipt.get('attempt') != approved.get('attempt', -1) + 1
                        or str(receipt.get('run_id')) != str(approved.get('run_id'))):
                    raise ValueError('CI window requires a verified new-head rerun receipt')
                rerun_id = digest({'pr': self.pr, 'head': self.head,
                    'run_id': str(receipt['run_id']), 'attempt': receipt['attempt']})
            # Return to a previously seen head without restarting its old budget.
            candidates = [item for item in self.state['history'] + ([previous] if previous else [])
                          if item['head'] == self.head]
            selected = candidates[-1] if candidates else None
            if rerun_id and selected and selected.get('rerun') != rerun_id:
                if any(item.get('rerun') == rerun_id for item in candidates):
                    raise ValueError('CI rerun receipt already observed; cannot reset deadline')
                selected = None
            fresh = selected is None
            if fresh:
                if previous:
                    self.state['history'].append(previous)
                now = self.clock()
                self.current = {'pr': self.pr, 'head': self.head, 'rerun': rerun_id,
                    'started': now, 'deadline': now + self.timeout, 'window_seconds': self.timeout,
                    'extension_seconds': self.extension,
                    'extensions': 0, 'attempts': None, 'events': []}
                self.state['current'] = self.current
            else:
                self.current = copy.deepcopy(selected)
                if previous and previous != selected:
                    self.state['history'].append(previous)
                self.state['current'] = self.current
                if self.current['pr'] != self.pr:
                    raise ValueError('CI observation PR identity changed')
            if self.current.get('permission_denied') and not self.retry_denied:
                raise PermissionDenied('CI access was denied; repair access and explicitly retry with --retry-denied')
            if self.retry_denied:
                self.current.pop('permission_denied', None)
            self.persist()
            self.token = ACTIVE.set(self)
            return self
        except BaseException:
            self.lock.close()
            raise

    def __exit__(self, *args):
        ACTIVE.reset(self.token)
        self.lock.close()

    def bind(self, url):
        if url and canonical(url)[1] != self.pr:
            raise ValueError('CI observation canonical PR identity changed')

    def persist(self):
        write(self.path, self.state)

    def denied(self, exc):
        self.current['permission_denied'] = {'at': self.clock(), 'reason': str(exc)}
        self.persist()

    def update(self, result, progress=None):
        now = self.clock()
        if result['head'] != self.head:
            raise ValueError('CI observation head differs')
        attempts = sorted([{'name': c.get('name'), 'workflow': c.get('workflow'),
                            'link': c.get('link'), 'startedAt': c.get('startedAt')}
                           for c in result.get('checks', [])], key=lambda c: (str(c['name']), str(c['link'])))
        if self.current['attempts'] is None:
            self.current['attempts'] = attempts
            self.current['id'] = digest({key: self.current[key] for key in ('pr', 'head', 'rerun', 'attempts')})
        self.current['latest_attempts'] = attempts
        self.current['last_result'] = result
        if (result['result'] == 'pending' and now >= self.current['deadline']
                and self.current['extensions'] == 0 and self.current['extension_seconds'] > 0):
            self.current['extensions'] = 1
            self.current['deadline'] += self.current['extension_seconds']
            event = {'event': 'ci_deadline_extended', 'observation_id': self.current['id'],
                     'head': self.head, 'deadline': self.current['deadline'], 'timestamp': now}
            self.current['events'].append(event)
            self.persist()
            if progress:
                progress(event)
        self.persist()
        return {'elapsed_seconds': round(now - self.current['started'], 1),
                'remaining_seconds': max(0, self.current['deadline'] - now),
                'extensions': self.current['extensions'], 'observation_id': self.current['id'],
                'deadline': self.current['deadline']}


def canonical(pr, repo=None):
    from shared.flaky_actions import pr_repository
    repository = pr_repository(pr, repo)
    number = pr.rstrip('/').split('/')[-1]
    if not number.isdigit():
        raise ValueError('CI observation needs a PR number or URL')
    return repository, 'https://' + repository + '/pull/' + str(int(number))


class NumberedObservation:
    """Resolve identity in the existing metadata read, before reading checks."""
    def __init__(self, pr, head, repo, options):
        self.pr, self.head, self.repo, self.options = pr, head, repo, options
        self.bound = None
        identity = [str(Path.cwd()), pr, repo, os.environ.get('GH_REPO'), os.environ.get('GH_HOST')]
        self.alias = root('ci/aliases/local') / (digest(identity) + '.json')

    def __enter__(self):
        self.alias.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock = self.alias.with_suffix('.observer.lock').open('w')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            saved = read(self.alias, {})
            if saved.get('permission_denied') and not self.options.get('retry_denied'):
                raise PermissionDenied('CI metadata access was denied; repair access and explicitly retry with --retry-denied')
            if self.options.get('retry_denied') and saved.pop('permission_denied', None):
                write(self.alias, saved)
            self.token = ACTIVE.set(self)
            return self
        except BaseException:
            self.lock.close()
            raise

    def __exit__(self, *args):
        try:
            if self.bound is not None:
                self.bound.__exit__(*args)
        finally:
            ACTIVE.reset(self.token)
            self.lock.close()

    def bind(self, url):
        if not url:
            raise ValueError('numeric CI observation requires the metadata PR URL')
        if self.bound is None:
            _, resolved = canonical(url, self.repo)
            if resolved.rstrip('/').split('/')[-1] != str(int(self.pr)):
                raise ValueError('CI observation PR number differs from metadata')
            bound = for_pr(resolved, self.head, self.repo, **self.options)
            bound.__enter__()
            self.bound, self.pr = bound, resolved
        else:
            self.bound.bind(url)

    def update(self, result, progress=None):
        if self.bound is None:
            self.bind(result.get('url'))
        return self.bound.update(result, progress)

    def denied(self, exc):
        write(self.alias, {'permission_denied': {'at': time.time(), 'reason': str(exc)}})
        if self.bound is not None:
            self.bound.denied(exc)


def for_pr(pr, head, repo=None, **kwargs):
    if pr.isdigit():
        return NumberedObservation(pr, head, repo, kwargs)
    repository, resolved = canonical(pr, repo)
    number = resolved.rstrip('/').split('/')[-1]
    return Observation(root(repository) / 'ci-observations' / ('pr-' + number + '.json'),
                       resolved, head, **kwargs)
