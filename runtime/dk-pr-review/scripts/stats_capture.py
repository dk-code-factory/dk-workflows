"""Best-effort iteration-only capture, separate from the reviewer result contract."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import importlib.util
import json
import sys
import time
from copy import copy
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())
sys.path.insert(0, str(ROOT))
from shared.private_state import read, mutate
from shared.reviewer_telemetry import select_trace

spec = importlib.util.spec_from_file_location('review_stats_v1', ROOT / 'dk-pr-iter/scripts/review_stats.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def outside_checkout(path, context):
    for key in ('checkout', 'repository'):
        if context.get(key) and Path(path).resolve().is_relative_to(Path(context[key]).resolve()):
            raise ValueError('review statistics must stay outside the repository and checkout')


def iteration_state(repo, pr):
    # Older operational records preserve casing. Read them without moving or
    # merging counters; statistics themselves always use the normalized base repo.
    folders = [api.stats_home()]
    for part in repo.split('/'):
        folders = [child for folder in folders if folder.exists() for child in folder.iterdir()
                   if child.is_dir() and child.name.lower() == part]
    paths = [folder / f'pr-{pr}.json' for folder in folders if (folder / f'pr-{pr}.json').exists()]
    if len(paths) > 1:
        raise ValueError('ambiguous iteration record casing; pass --stats-cycle and --stats-attempt')
    return read(paths[0], {}) if paths else {}


class Capture:
    def __init__(self, context, preparation, reviewers, cycle=None, attempt=None):
        self.lock = RLock()
        self.context, self.preparation, self.reviewers = context, preparation, reviewers
        self.repo, pr = api.pr_identity(context['url'])
        outside_checkout(api.ledger_path(self.repo), context)
        outside_checkout(Path(context['patch']).with_name('review-stats.json'), context)
        state = iteration_state(self.repo, pr) if cycle is None or attempt is None else {}
        if cycle is None:
            cycles = state.get('cycles', [])
            if not cycles:
                raise ValueError('iteration cycle was not recorded before review')
            cycle = cycles[-1]['number']
        attempt = attempt or str(1 + sum(r['number'] == cycle for r in state.get('cycle_retries', [])))
        self.run = api.begin_review(pr_url=context['url'], cycle=cycle, head=context['head_sha'],
                                    preparation_id=preparation['preparation_id'],
                                    reviewers=reviewers, attempt=attempt)
        self.run_id = self.run['id']
        self.directory = api.ledger_path(self.repo).parent / 'review-stats-artifacts' / self.run_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.sidecar = Path(context['patch']).with_name('review-stats.json')
        self.update_sidecar()
        self.safe(api.render_reports)

    def warning(self, exc):
        print(f'review stats missing data; artifacts: {self.directory}: {exc}', file=sys.stderr)

    def safe(self, function, *args, **kwargs):
        try:
            with self.lock:
                return function(*args, **kwargs)
        except Exception as exc:
            self.warning(exc)
            return None

    def fork(self):
        # Invocation fields belong to one worker; ledger and sidecar share a lock.
        return copy(self)

    def update_sidecar(self):
        self.sidecar.write_text(json.dumps({'api_version': 1, 'repository': self.repo,
            'run_id': self.run_id, 'ledger': str(api.ledger_path(self.repo)),
            'run': self.run, 'report': str(api.ledger_path(self.repo).with_suffix('.md'))}, indent=2) + '\n')

    @contextmanager
    def invocation(self, name, handoff, descriptor):
        review = self.run['reviews'][name]
        trace = self.directory / (review['id'] + '-execution.json')
        self.trace, self.name, self.start = trace, name, time.monotonic()
        self.prompt_fingerprint = api.digest({'descriptor': descriptor, 'handoff': handoff})
        self.artifact = self.safe(self.retain_sources, handoff, descriptor)
        self.safe(api.record_review, self.repo, self.run_id, name, status='running',
                  prompt_fingerprint=self.prompt_fingerprint)
        with select_trace(str(trace)):
            yield

    def retain_sources(self, handoff, descriptor):
        snapshot = {'handoff': handoff, 'descriptor': descriptor,
                    'patch': Path(self.context['patch']).read_text(),
                    'metadata': json.loads(Path(self.context['metadata']).read_text())}
        return api.retain(self.repo, 'sources', snapshot)

    def raw(self, value):
        api.retain(self.repo, 'raw', {'run_id': self.run_id, 'reviewer': self.name, 'stdout': value})

    def failure(self, name, reason):
        result = api.record_review(self.repo, self.run_id, name, status='failed', reason=reason)
        self.run['reviews'][name] = result
        self.update_sidecar()
        api.render_reports()

    def finish(self, status, result=None, reason=None, duration_seconds=None):
        raw = self.safe(api.retain, self.repo, 'results', {'run_id': self.run_id,
                               'reviewer': self.name, 'result': result, 'reason': reason})
        trace = read(self.trace, {'attempts': []})
        attempts = trace['attempts']
        # Leaf execution totals are informative only; parent wall time is counted once.
        metadata = {'duration_seconds': time.monotonic() - self.start if duration_seconds is None else duration_seconds,
                    'prompt_fingerprint': self.prompt_fingerprint,
                    'artifacts': [p for p in (self.artifact, raw, str(self.trace) if self.trace.exists() else None) if p],
                    'correction_retries': max(a['attempt'] for a in attempts) if attempts else None,
                    'provider': attempts[-1]['provider'] if attempts else None,
                    'model': attempts[-1]['model'] if attempts else None,
                    'usage': [a['usage'] for a in attempts] if attempts and all(a['usage'] is not None for a in attempts) else None,
                    'reason': reason}
        result = self.safe(api.record_review, self.repo, self.run_id, self.name, status=status,
                           findings=result['findings'] if status == 'completed' else [], **metadata)
        if result:
            self.run['reviews'][self.name] = result
        self.safe(self.update_sidecar)
        self.safe(api.render_reports)


def for_iteration(context, preparation, reviewers, cycle=None, attempt=None):
    if preparation.get('mode') != 'iteration':
        return None
    try:
        return Capture(context, preparation, reviewers, cycle, attempt)
    except Exception as exc:
        print(f'review stats missing data: {exc}; retained handoff directory: '
              f'{Path(context["patch"]).parent}', file=sys.stderr)
        try:
            return OfflineCapture(context, preparation, reviewers, cycle, attempt)
        except Exception as recovery_error:
            print(f'review stats recovery unavailable: {recovery_error}', file=sys.stderr)
            return None


class OfflineCapture:
    """Retain replay inputs beside the external handoff if private storage fails."""
    def __init__(self, context, preparation, reviewers, cycle, attempt):
        self.lock = RLock()
        self.directory = Path(context['patch']).parent / 'review-stats-recovery'
        outside_checkout(self.directory, context)
        self.context = context
        self.identity = {'pr_url': context['url'], 'cycle': cycle, 'head': context['head_sha'],
                         'preparation_id': preparation['preparation_id'],
                         'reviewers': reviewers, 'attempt': attempt or '1'}
        self.run_id = 'r-' + api.digest(self.identity)
        self.identity['run_id'] = self.run_id
        self.safe(self.save, 'begin', self.identity)

    def safe(self, function, *args, **kwargs):
        try:
            with self.lock:
                return function(*args, **kwargs)
        except Exception as exc:
            print(f'review stats missing data; recovery: {self.directory}: {exc}', file=sys.stderr)

    def fork(self):
        return copy(self)

    def save(self, category, payload):
        path = self.directory / (category + '-' + api.digest(payload) + '.json')
        mutate(path, payload, lambda _: None)
        return str(path)

    @contextmanager
    def invocation(self, name, handoff, descriptor):
        self.name, self.start = name, time.monotonic()
        self.trace = self.directory / (api.digest([self.run_id, name]) + '-execution.json')
        self.safe(self.sources, handoff, descriptor)
        with select_trace(str(self.trace)):
            yield

    def sources(self, handoff, descriptor):
        return self.save('sources', {'handoff': handoff, 'descriptor': descriptor,
                                    'patch': Path(self.context['patch']).read_text()})

    def raw(self, value):
        self.save('raw', {'run_id': self.run_id, 'reviewer': self.name, 'stdout': value})

    def finish(self, status, result=None, reason=None, duration_seconds=None):
        self.save('record-review', {'run_id': self.run_id, 'reviewer': self.name, 'status': status,
            'findings': result['findings'] if status == 'completed' else [],
            'duration_seconds': time.monotonic() - self.start if duration_seconds is None else duration_seconds,
            'reason': reason,
            'artifacts': [str(self.trace)] if self.trace.exists() else []})

    def failure(self, name, reason):
        self.save('record-review', {'run_id': self.run_id, 'reviewer': name, 'status': 'failed', 'reason': reason})
