"""Bound usage-limit recovery to one restart of the original review session."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time

from shared.github_reads import run
from shared import review_coverage


BACKOFF_SECONDS = 60
MAX_WAIT_SECONDS = 60


def supported_timestamp(value):
    """Check the timestamp range used by the durable checkpoint renderer."""
    if not math.isfinite(value):
        return None
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return value


def reset_time(message, now):
    """Unknown advertised reset times must never become immediate retries."""
    relative = re.search(r'(retry[- ]after[: ]+|try again in\s+)(\d+(?:\.\d+)?)\s*'
                         r'([a-z]+)?', message, re.I)
    if relative:
        unit = (relative[3] or '').lower()
        units = {'s': 1, 'sec': 1, 'secs': 1, 'second': 1, 'seconds': 1,
                 'm': 60, 'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60,
                 'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,
                 'd': 86400, 'day': 86400, 'days': 86400,
                 'w': 604800, 'week': 604800, 'weeks': 604800}
        if not unit and relative[1].lower().startswith('retry'):
            unit = 'seconds'  # Numeric Retry-After values are seconds.
        if unit not in units:
            return None
        remainder = message[relative.end():]
        if re.match(r'\s*(?:(?:,|and|\+)\s*)?(?:\d|and\b|half\b|[½¼¾]|[:/])',
                    remainder, re.I):
            return None
        return supported_timestamp(now + float(relative[2]) * units[unit])
    absolute = re.search(r'(\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d))', message)
    if absolute:
        try:
            return supported_timestamp(datetime.fromisoformat(absolute[1].replace('Z', '+00:00')).timestamp())
        except (OverflowError, OSError, ValueError):
            return None
    # Codex reports a local wall time without a zone. It is unsafe to infer the
    # provider's zone from the coordinator's environment.
    if re.search(r'try again (?:at|in)|resets? (?:at|on|in)|retry[- ]after', message, re.I):
        return None
    return now


def live_evidence(context):
    """Recheck the remote scope as well as retained local evidence before restart."""
    result = run(['gh', 'pr', 'view', context['url'], '--json',
                  'headRefOid,baseRefOid,title,body'], cwd=context['repository'],
                 text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError('quota recovery cannot verify live PR evidence: ' + result.stderr.strip())
    live = json.loads(result.stdout)
    metadata = json.loads(Path(context['metadata']).read_text())
    if (live['headRefOid'] != context['head_sha'] or live['baseRefOid'] != context['base_sha']
            or any(live[key] != metadata.get(key) for key in ('title', 'body'))):
        raise ValueError('quota recovery PR evidence changed; refresh context and preparation within the existing cycle budget')


def plan(data, result, *, clock=time.time):
    """Update recovery with the result in the same durable session mutation."""
    now = clock()
    reasons = [{'role': item['reviewer'], 'reason': item['reason']}
               for item in result.get('skipped_reviewers', [])]
    if result.get('architecture_accounting', {}).get('exhausted'):
        reasons.append({'role': 'architecture-review', 'reason': result['reason']})
    state = data.get('quota_recovery')
    if result.get('complete'):
        if state:
            state['status'] = 'completed'
        return state
    if (not reasons or result.get('guard_failure') or result.get('failed_reviewers') or result.get('role_failures')
            or result.get('outcome') != 'usage_exhausted'):
        if state:
            state['status'] = 'blocked'
        return state
    deadlines = [reset_time(item['reason'], now) for item in reasons]
    known_deadline = max([now + BACKOFF_SECONDS, *(value for value in deadlines if value is not None)])
    deadline = None if any(value is None for value in deadlines) else known_deadline
    if state and state['restarts'] >= 1:
        state.update(status='exhausted', reasons=reasons, not_before=deadline,
                     known_not_before=known_deadline)
        return state
    state = {'version': 1, 'status': 'waiting', 'restarts': 0,
             'not_before': deadline, 'known_not_before': known_deadline,
             'reasons': reasons, 'history': [],
             'original_result': data['results'][-1]}
    data['quota_recovery'] = state
    return state


def pending(session):
    return session.update(lambda data: data.get('quota_recovery'))


def resolve_reset(session, value):
    """Record a verified reset timezone without changing the retry budget."""
    reset = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if reset.tzinfo is None:
        raise ValueError('--quota-reset-at requires an ISO-8601 timestamp with timezone')
    timestamp = supported_timestamp(reset.timestamp())
    if timestamp is None:
        raise ValueError('--quota-reset-at is outside the supported timestamp range')
    def retain(data):
        state = data.get('quota_recovery')
        if not state or state['status'] not in {'waiting', 'exhausted'} or state['not_before'] is not None:
            raise ValueError('--quota-reset-at requires an unresolved advertised reset')
        state['not_before'] = max(timestamp, state['known_not_before'])
        state['resolved_reset'] = value
    session.update(retain)


def restart(session, *, manual=False, clock=time.time, sleep=time.sleep):
    """Wait only a short, interruptible backoff; retain longer waits for resume."""
    state = pending(session)
    if not state or state['status'] not in ({'waiting', 'exhausted'} if manual else {'waiting'}):
        return False
    deadline = state['not_before']
    if deadline is None or deadline - clock() > MAX_WAIT_SECONDS:
        return False
    delay = max(0, deadline - clock())
    if delay:
        sleep(delay)
    if clock() < deadline:
        return False
    # Verify before charging or dispatch. Changed inputs require a new current
    # preparation and the caller's existing review-cycle allocation.
    review_coverage.validate_current(session.prepared)
    live_evidence(session.context)
    def reserve(data):
        saved = data['quota_recovery']
        if saved != state or (saved['status'] == 'waiting' and saved['restarts']):
            raise ValueError('quota recovery checkpoint changed')
        automatic = saved['status'] == 'waiting'
        if automatic:
            saved['restarts'] = 1
        saved['status'] = 'started'
        saved['history'].append({'started_at': clock(), 'roles': [item['role'] for item in saved['reasons']],
                                 'automatic': automatic, 'prior_result': data['results'][-1]})
    session.update(reserve)
    return True


def checkpoint(session, state, resume_argv):
    if not state:
        return None
    return {**state, 'session_file': str(session.path), 'session_id': session.id,
            'resume_argv': resume_argv,
            'resume_after': (datetime.fromtimestamp(state['not_before'], timezone.utc).isoformat()
                             if state['not_before'] is not None else None),
            'next_action': ('Continue the already authorized verification and publication workflow.'
                if state['status'] == 'completed' else
                'Refresh changed evidence and preparation within the existing review budget.'
                if state['status'] == 'blocked' else
                'Retain this checkpoint. Resume the same session after provider quota is available; '
                'when the reset timezone is unknown, verify it and append --quota-reset-at with '
                'an ISO-8601 timestamp including timezone to resume_argv. '
                'No review publication is authorized by this checkpoint.')}
