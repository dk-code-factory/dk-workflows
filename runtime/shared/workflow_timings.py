"""Best-effort diagnostic spans; never inputs to a workflow gate."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import time
import uuid

from shared.private_state import mutate, read
from shared.preparation_graph import digest

ACTIVE = ContextVar('workflow_timing', default=None)


def path(scope):
    root = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    return root / 'dk-code-factory/workflow-timings' / (digest(scope) + '.json')


def save(scope, item):
    try:
        mutate(path(scope), {'timing_version': 1, 'spans': []}, lambda value: value['spans'].append(item))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass


def model_call():
    active = ACTIVE.get()
    if active is not None:
        active['model_calls'] += 1


@contextmanager
def span(scope, role, *, reused=False, retry=False, clock=time.time):
    parent = ACTIVE.get()
    item = {'id': uuid.uuid4().hex, 'role': role, 'start': clock(), 'end': None,
            'model_calls': 0, 'retry': retry, 'reused': reused,
            'parent': parent['id'] if parent else None, 'outcome': 'unknown',
            'invocation': os.environ.get('DK_UPDATE_PARENT')}
    save(scope, dict(item))
    token = ACTIVE.set(item)
    try:
        yield item
        item['outcome'] = 'returned'
    finally:
        item['end'] = clock()
        ACTIVE.reset(token)
        save(scope, item)


def query(scope):
    value = read(path(scope), {'spans': []})
    latest = {item['id']: item for item in value['spans']}
    spans = list(latest.values())
    intervals = sorted((item['start'], item['end']) for item in spans if item['end'] is not None)
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    covered = sum(end - start for start, end in merged)
    # Inclusive parent spans do not count as another concurrent activity.
    boundaries = sorted({point for pair in intervals for point in pair})
    overlap = 0
    for start, end in zip(boundaries, boundaries[1:]):
        active = {item['id'] for item in spans if item['end'] is not None
                  and item['start'] <= start and item['end'] >= end}
        parents = set()
        for identity in active:
            parent = latest[identity].get('parent')
            while parent in latest:
                parents.add(parent)
                parent = latest[parent].get('parent')
        if len(active - parents) > 1:
            overlap += end - start
    extent = max((end for _, end in intervals), default=0) - min((start for start, _ in intervals), default=0)
    return {'timing_version': 1, 'scope': scope, 'spans': spans,
            'elapsed_seconds': extent, 'active_seconds': covered,
            'overlapping_seconds': overlap,
            'model_calls': sum(item['model_calls'] for item in spans),
            'retries': sum(bool(item['retry']) for item in spans),
            'reuse': sum(bool(item['reused']) for item in spans),
            'unknown_spans': [item['id'] for item in spans if item['end'] is None],
            'unknown_interval_seconds': max(0, extent - covered),
            'provider_usage': 'unknown; model calls count transport dispatches once at their innermost span'}
