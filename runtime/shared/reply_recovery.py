"""Explicit private dispositions for historically verified feedback replies."""
import fcntl
from pathlib import Path
import re
import subprocess
import uuid

from shared import publication
from shared.private_state import write


def same_comment_url(actual, expected):
    if not isinstance(actual, str):
        return False
    actual_pr, separator, fragment = actual.partition('#')
    expected_pr, _, expected_fragment = expected.partition('#')
    return separator == '#' and actual_pr.lower() == expected_pr.lower() and fragment == expected_fragment


def positive_id(value):
    return type(value) is int and value > 0


def same_parent(actual, expected):
    return (actual is None and expected is None) or (positive_id(actual) and positive_id(expected) and actual == expected)


class Recovery:
    def __init__(self, *, api, metadata, endpoint, replies, handler):
        self.api, self.metadata, self.endpoint = api, metadata, endpoint
        self.replies, self.handler = replies, handler

    def load(self, path):
        value = publication.load(path)
        if (value['request']['kind'] not in {'reply-inline', 'reply-comment'}
                or value['status'] != 'verified'
                or not positive_id(value.get('receipt', {}).get('id'))):
            raise ValueError('Reply recovery requires an original verified receipt; uncertain initial writes require reconciliation')
        return value

    def snapshot(self, value):
        request = value['request']['approved']
        pr = request['pr']
        live = self.metadata(pr)
        if (not isinstance(live, dict)
                or any(not isinstance(live.get(key), str) for key in ('url', 'headRefOid', 'title', 'body'))
                or not re.fullmatch(r'[0-9a-f]{40}', live['headRefOid'])):
            raise ValueError('Recovery PR metadata is unavailable or ambiguous')
        if live.get('url', '').lower() != pr.lower():
            raise ValueError('Recovery PR identity changed')
        actor = self.api(pr, 'user')['login']
        if actor != request['actor']:
            raise ValueError('Recovery actor changed')
        source = request.get('source')
        if (not isinstance(source, dict) or type(source.get('id')) is not int
                or source['id'] <= 0 or source.get('kind') not in {'inline_comment', 'issue_comment', 'review'}):
            raise ValueError('Recovery requires the retained original structured feedback source')
        _, root, number = self.endpoint(pr)
        routes = {'inline_comment': f'{root}/pulls/comments/',
                  'issue_comment': f'{root}/issues/comments/',
                  'review': f'{root}/pulls/{number}/reviews/'}
        fragments = {'inline_comment': 'discussion_r', 'issue_comment': 'issuecomment-',
                     'review': 'pullrequestreview-'}
        expected_url = pr + '#' + fragments[source['kind']] + str(source['id'])
        original = self.api(pr, routes[source['kind']] + str(source['id']))
        user = original.get('user') if isinstance(original, dict) else None
        if (not isinstance(original, dict) or not positive_id(original.get('id'))
                or not same_comment_url(source.get('url'), expected_url) or original.get('id') != source['id']
                or not same_comment_url(original.get('html_url'), expected_url) or not isinstance(original.get('body'), str)
                or not isinstance(user, dict) or not isinstance(user.get('login'), str) or not user['login'].strip()):
            raise ValueError('Original feedback identity or read access is unavailable or ambiguous')
        context = None
        if request['kind'] == 'reply-inline':
            if source['kind'] != 'inline_comment' or not source.get('thread_id'):
                raise ValueError('Inline recovery requires the original thread identity')
            thread = self.handler({**request, 'pr': live['url'], 'head': live['headRefOid'],
                                   'thread_id': source['thread_id']}).thread()
            ids = [c['databaseId'] for c in thread['comments']]
            if (any(not positive_id(v) for v in ids) or len(ids) != len(set(ids))
                    or not positive_id(request['parent'])
                    or ids.count(source['id']) != 1 or ids.count(request['parent']) != 1
                    or not ids or ids[0] != request['parent']):
                raise ValueError('Original source and routing parent are not in the exact original thread')
            source_parent = original.get('in_reply_to_id')
            if ((source['id'] == request['parent'] and source_parent is not None)
                    or (source['id'] != request['parent'] and (not positive_id(source_parent)
                        or source_parent != request['parent']))):
                raise ValueError('Original source parent contradicts the retained routing parent and thread root')
            context = {'id': thread['id'], 'source': source['id'], 'parent': request['parent']}
        elif source['kind'] == 'inline_comment':
            raise ValueError('An inline source requires its originating thread')
        receipt = value['receipt']
        route = (f'{root}/pulls/comments/' if request['kind'] == 'reply-inline'
                 else f'{root}/issues/comments/') + str(receipt['id'])
        items = self.replies(request, filter_parent=False)
        ids = [item.get('id') for item in items]
        if any(not positive_id(v) for v in ids) or len(ids) != len(set(ids)):
            raise ValueError('Reply read access is ambiguous')
        required = {source['id']} if source['kind'] != 'review' else set()
        if request['kind'] == 'reply-inline':
            required.add(request['parent'])
        if not required.issubset(ids):
            raise ValueError('Reply list omits the accessible original source or routing parent')
        if source['kind'] != 'review':
            listed_source = next(item for item in items if item['id'] == source['id'])
            listed_user = listed_source.get('user')
            if (('body' in listed_source and listed_source['body'] != original['body'])
                    or ('in_reply_to_id' in listed_source
                        and not same_parent(listed_source['in_reply_to_id'], original.get('in_reply_to_id')))
                    or ('html_url' in listed_source and not same_comment_url(listed_source['html_url'], expected_url))
                    or ('user' in listed_source and (not isinstance(listed_user, dict)
                        or listed_user.get('login') != user['login']))):
                raise ValueError('Original feedback list and individual observation disagree; refresh read evidence')
        try:
            remote = self.api(pr, route)
            if not isinstance(remote, dict):
                raise ValueError('Reply read response is unavailable or ambiguous')
        except subprocess.CalledProcessError as exc:
            # A 404 establishes unavailability to this caller, not deletion.
            if not re.search(r'\bHTTP 404\b', exc.stderr or '') or receipt['id'] in ids:
                raise
            remote = None
        if remote is None:
            state = 'unavailable'
        else:
            fragment = 'discussion_r' if request['kind'] == 'reply-inline' else 'issuecomment-'
            if (not positive_id(remote.get('id')) or remote.get('id') != receipt['id']
                    or not same_comment_url(remote.get('html_url'), pr+'#'+fragment+str(receipt['id']))
                    or remote.get('user', {}).get('login') != actor or ids.count(receipt['id']) != 1
                    or (request['kind'] == 'reply-inline' and (not positive_id(remote.get('in_reply_to_id'))
                        or remote.get('in_reply_to_id') != request['parent']))):
                raise ValueError('Reply identity, membership or read access is ambiguous')
            listed = next(item for item in items if item['id'] == receipt['id'])
            if (any(listed.get(key) != remote.get(key) for key in ('body', 'user', 'html_url'))
                    or not same_parent(listed.get('in_reply_to_id'), remote.get('in_reply_to_id'))):
                raise ValueError('Reply list and individual observation disagree; refresh read evidence')
            state = 'present' if remote.get('body') == request['body'] else 'edited'
            if not isinstance(remote.get('body'), str):
                raise ValueError('Reply payload is unavailable')
        return {'pr': live, 'actor': actor, 'source': original, 'thread': context,
                'reply_state': state, 'reply': remote}

    def inspect(self, path):
        path = Path(path).resolve()
        self.load(path)
        with path.with_name(path.name + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = self.load(path)
            try:
                snapshot = self.snapshot(value)
            except Exception as exc:
                value.setdefault('reply_observations', []).append({'state': 'evidence_unavailable', 'reason': str(exc)})
                write(path, value)
                raise
            observation = {'fingerprint': publication.digest(snapshot), 'snapshot': snapshot}
            value.setdefault('reply_observations', []).append(observation)
            write(path, value)
            return {'operation': str(path), 'historical_receipt': value['receipt'], **observation}

    def dispose(self, path, decision):
        if (not isinstance(decision, dict) or set(decision) != {'decision', 'reason', 'observation'}
                or decision['decision'] not in {'no_replacement', 'replacement_required'}
                or not isinstance(decision['reason'], str) or not decision['reason'].strip()
                or not isinstance(decision['observation'], str)):
            raise ValueError('Disposition requires decision, substantive reassessment reason and inspected observation fingerprint')
        path = Path(path).resolve()
        self.load(path)
        with (path.parent.parent/'.dispatch.lock').open('a') as dispatch, path.with_name(path.name + '.lock').open('a') as lock:
            fcntl.flock(dispatch, fcntl.LOCK_EX)
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = self.load(path)
            snapshot = self.snapshot(value)
            fingerprint = publication.digest(snapshot)
            if ((snapshot['reply_state'] == 'present' and decision['decision'] != 'no_replacement')
                    or fingerprint != decision['observation']
                    or not any(v.get('fingerprint') == fingerprint for v in value.get('reply_observations', []))):
                raise ValueError('Inspect and reassess the current reply; a present original only permits no_replacement')
            record = {**decision, 'snapshot': snapshot}
            records = value.setdefault('reply_dispositions', [])
            if records and all(records[-1].get(k) == v for k, v in record.items()):
                return {'operation': str(path), 'disposition': records[-1], 'reused': True}
            if (records and records[-1].get('replacement_operation')
                    and publication.load(records[-1]['replacement_operation'])['status'] == 'pending'):
                raise ValueError('Reconcile the uncertain linked replacement before another disposition')
            record['disposition_id'] = uuid.uuid4().hex
            records.append(record)
            write(path, value)
            return {'operation': str(path), 'disposition': record, 'reused': False}

    def current(self, value):
        records = value.get('reply_dispositions', [])
        if not records:
            raise ValueError('A verified unavailable or edited reply needs an explicit disposition')
        record = records[-1]
        if publication.digest(self.snapshot(value)) != record['observation']:
            raise ValueError('Reply recovery evidence changed; inspect and reassess the answer obligation')
        return record

    def replacement(self, request):
        original = self.load(request['replaces'])
        record = self.current(original)
        approved = original['request']['approved']
        if (record['decision'] != 'replacement_required' or record.get('replacement_operation')
                or not isinstance(request.get('authorization'), str) or not request['authorization'].strip()
                or any(request.get(k) != approved.get(k) for k in ('pr', 'kind', 'source', 'parent'))
                or request.get('head') != record['snapshot']['pr']['headRefOid']):
            raise ValueError('Replacement requires a current replacement_required disposition, separate authorization and exact source/destination/head')
        return record['disposition_id']

    def link(self, original_path, replacement_path):
        path = Path(original_path).resolve()
        with path.with_name(path.name + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = self.load(path)
            record = self.current(value)
            replacement = publication.load(replacement_path)['request']['approved']
            if (record['decision'] != 'replacement_required'
                    or replacement.get('_reply_disposition') != record.get('disposition_id')):
                raise ValueError('Replacement preparation belongs to a superseded disposition')
            if record.get('replacement_operation') not in {None, str(replacement_path)}:
                raise ValueError('A replacement is already prepared; recover that operation')
            record['replacement_operation'] = str(replacement_path)
            write(path, value)

    def receipt(self, value):
        record = self.current(value)
        if record['decision'] == 'replacement_required':
            path = record.get('replacement_operation')
            if not path:
                raise ValueError('The current answer still requires a separately authorized replacement')
            replacement = self.load(path)
            if self.snapshot(replacement)['reply_state'] != 'present':
                raise ValueError('The linked replacement answer is not currently verified')
        # This historical receipt cannot satisfy resolve-thread's live-answer gate.
        return value['receipt']
