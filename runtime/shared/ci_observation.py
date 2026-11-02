"""Durable, exclusively owned CI windows with one pending-only extension."""
import fcntl
import copy
import math
import time
from pathlib import Path

from shared.private_state import read, write, root
from shared.preparation_graph import digest
from shared import publication


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
            return self
        except BaseException:
            self.lock.close()
            raise

    def __exit__(self, *args):
        self.lock.close()

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


def for_pr(pr, head, repo=None, **kwargs):
    from shared.flaky_actions import pr_repository
    repository = pr_repository(pr, repo)
    number = pr.rstrip('/').split('/')[-1]
    if not number.isdigit():
        raise ValueError('CI observation needs a PR number or URL')
    canonical = 'https://' + repository + '/pull/' + str(int(number))
    return Observation(root(repository) / 'ci-observations' / ('pr-' + number + '.json'),
                       canonical, head, **kwargs)
