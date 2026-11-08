"""Offline host fixture for configuration handoffs, never native execution evidence."""

def dispatch(handoff, host, *, existing_agent=None):
    if not host.available:
        return {'status': 'blocked', 'request_id': handoff.get('request_id')}
    if existing_agent is not None:
        return host.resume(existing_agent, handoff)
    return host.spawn(handoff)
