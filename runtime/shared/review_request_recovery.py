"""Read-only evidence and explicit no-resend decisions for uncertain review requests."""
import fcntl
from pathlib import Path

from shared import publication
from shared.private_state import write


class Recovery:
    def __init__(self, handler):
        self.handler = handler

    def load(self, path):
        value = publication.load(path, 'request-review')
        if value['status'] != 'pending':
            raise ValueError('Review-request recovery requires an uncertain pending request')
        return value

    def snapshot(self, value):
        request = value['request']['approved']
        handler = self.handler(request)
        live = handler.metadata(request['pr'])
        if live.get('url', '').lower() != request['pr'].lower():
            raise ValueError('Review-request PR identity changed')
        requested = handler.requested()
        reviewers = {name.casefold() for name in request['reviewers']}
        teams = {name.casefold() for name in request['team_reviewers']}
        events = []
        for event in handler.pages('/issues/' + handler.number + '/timeline',
                                   event_types={'review_requested', 'review_request_removed'}):
            user = (event.get('requested_reviewer') or {}).get('login')
            team = (event.get('requested_team') or {}).get('slug')
            if ((user or '').casefold() in reviewers or (team or '').casefold() in teams):
                events.append({key: event.get(key) for key in ('id', 'event', 'created_at')}
                              | {'reviewer': user, 'team': team})
        reviews = []
        for review in handler.pages('/pulls/' + handler.number + '/reviews'):
            if ((review.get('user') or {}).get('login') or '').casefold() in reviewers:
                reviews.append({key: review.get(key) for key in ('id', 'state', 'submitted_at', 'commit_id')}
                               | {'reviewer': review['user']['login']})
        # Timeline/reviews are historical context, not proof this operation sent.
        return {'pr': {key: live[key] for key in ('url', 'headRefOid')},
                'requested': requested, 'events': events, 'reviews': reviews}

    def report(self, path, value, snapshot):
        return {'operation': str(path), 'historical_status': value['status'],
                'historical_failure': value.get('failure'),
                'historical_origin': value.get('origin') or 'unknown',
                'created_at': value.get('created_at', 'unknown'),
                'dispatch_started_at': value.get('dispatch_started_at', 'unknown'),
                'request_payload': {key: value['request']['approved'][key]
                                    for key in ('reviewers', 'team_reviewers')},
                'delivery': 'unknown', 'root_cause': 'unknown',
                'fingerprint': publication.digest(snapshot), 'current_observation': snapshot}

    def inspect(self, path):
        path = Path(path).resolve()
        with path.with_name(path.name + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = self.load(path)
            snapshot = self.snapshot(value)
            # One compact observation supports admission; unchanged reads do not
            # append copies of evidence or disturb the original intent/failure.
            observation = {'fingerprint': publication.digest(snapshot), 'snapshot': snapshot}
            if value.get('review_request_observation') != observation:
                value['review_request_observation'] = observation
                write(path, value)
            return self.report(path, value, snapshot)

    def dispose(self, path, decision):
        if (not isinstance(decision, dict) or set(decision) != {'decision', 'reason', 'observation'}
                or decision['decision'] != 'no_resend'
                or not isinstance(decision['reason'], str) or not decision['reason'].strip()):
            raise ValueError('Use no_resend with a substantive reassessment reason and inspected observation')
        path = Path(path).resolve()
        with (path.parent.parent/'.dispatch.lock').open('a') as dispatch, path.with_name(path.name + '.lock').open('a') as lock:
            fcntl.flock(dispatch, fcntl.LOCK_EX)
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = self.load(path)
            snapshot = self.snapshot(value)
            fingerprint = publication.digest(snapshot)
            if (decision['observation'] != fingerprint
                    or (value.get('review_request_observation') or {}).get('fingerprint') != fingerprint):
                raise ValueError('Review-request evidence changed; inspect and reassess before disposition')
            record = dict(decision)
            if value.get('review_request_disposition') != record:
                value['review_request_disposition'] = record
                write(path, value)
            return {'operation': str(path), 'status': 'disposed', 'delivery': 'unknown',
                    'disposition': record}

    def current(self, value):
        record = value.get('review_request_disposition')
        if (not record or record.get('decision') != 'no_resend'
                or publication.digest(self.snapshot(value)) != record.get('observation')):
            raise ValueError('Inspect and reassess the current uncertain review request')
        return record

    def reconcile(self, path, value):
        record = self.current(value)
        return {'operation': str(path), 'status': 'disposed', 'historical_status': 'pending',
                'delivery': 'unknown', 'review_obligation': 'no_resend', 'disposition': record}
