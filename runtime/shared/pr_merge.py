"""Guarded, head-bound PR merging and read-only recovery of uncertain writes."""
import json
import re
from urllib.parse import quote


class Merge:
    def __init__(self, request, *, api, run, endpoint):
        self.request, self.api, self.run = request, api, run
        self.host, self.root, self.number = endpoint(request['pr'])

    def get(self, suffix):
        return self.api(self.request['pr'], self.root + suffix)

    def pull(self):
        value = self.get('/pulls/' + self.number)
        if (value.get('html_url') != self.request['pr']
                or value.get('head', {}).get('sha') != self.request['head']):
            raise ValueError('Merge PR identity or reviewed head changed')
        return value

    def ready(self):
        request = self.request
        pull = self.pull()
        actor = self.api(request['pr'], 'user')
        author = pull.get('user') or {}
        if (type(actor.get('id')) is not int or actor['id'] <= 0
                or not actor.get('login') or type(author.get('id')) is not int or author['id'] <= 0):
            raise ValueError('Merge actor and PR author identities are required')
        if actor['id'] == author['id']:
            raise ValueError('Incoming-review merging requires another author\'s PR')
        if (pull.get('state') != 'open' or pull.get('merged') is not False
                or pull.get('draft') is not False or pull.get('mergeable') is not True
                or pull.get('mergeable_state') != 'clean'
                or pull.get('base', {}).get('sha') != request['base']):
            raise ValueError('PR is not ready to merge at the reviewed target')
        base_ref = pull['base'].get('ref')
        if (not isinstance(base_ref, str) or not base_ref
                or self.get('/git/ref/heads/' + quote(base_ref, safe=''))['object']['sha'] != request['base']):
            raise ValueError('Merge target tip changed')
        review = self.run(['gh', 'pr', 'view', request['pr'], '--json',
                           'url,headRefOid,baseRefOid,state,isDraft,mergeable,mergeStateStatus,reviewDecision'])
        if (review.get('url') != request['pr'] or review.get('headRefOid') != request['head']
                or review.get('baseRefOid') != request['base'] or review.get('state') != 'OPEN'
                or review.get('isDraft') is not False or review.get('mergeable') != 'MERGEABLE'
                or review.get('mergeStateStatus') != 'CLEAN' or review.get('reviewDecision') != 'APPROVED'):
            raise ValueError('Current repository review and merge requirements are not satisfied')
        checks = self.run(['gh', 'pr', 'checks', request['pr'], '--json', 'name,bucket,state,link,workflow'])
        if (not isinstance(checks, list) or not checks
                or any(not isinstance(check, dict) or check.get('bucket') != 'pass' for check in checks)):
            raise ValueError('Automatic merge requires current successful CI checks')
        # Bind the check observation to source pins on both sides of the read.
        if self.pull() != pull:
            raise ValueError('PR changed while observing merge readiness')
        return {'actor': {'id': actor['id'], 'login': actor['login']},
                'author_id': author['id'], 'review': review,
                'checks': sorted(checks, key=lambda value: json.dumps(value, sort_keys=True))}

    def prepare(self):
        for field in ('head', 'base'):
            if not isinstance(self.request.get(field), str) or not re.fullmatch('[0-9a-f]{40}', self.request[field]):
                raise ValueError('Merge requires full reviewed head and target SHAs')
        if self.request.get('merge_method') not in {'squash', 'merge'}:
            raise ValueError('Merge method must be explicitly squash or merge')
        return self.ready()

    def check(self, expected, verified):
        if verified:
            self.observe(expected)
        elif self.ready() != expected:
            raise ValueError('Merge readiness changed before dispatch')

    def send(self):
        request = self.request
        return self.run(['gh', 'api', '--hostname', self.host,
                         self.root + '/pulls/' + self.number + '/merge',
                         '--method', 'PUT', '--input', '-'],
                        payload={'sha': request['head'], 'merge_method': request['merge_method']},
                        json_output=False)

    def observe(self, expected):
        pull = self.pull()
        commit_sha = pull.get('merge_commit_sha')
        if (pull.get('merged') is not True or pull.get('state') != 'closed'
                or (pull.get('merged_by') or {}).get('id') != expected['actor']['id']
                or not isinstance(commit_sha, str) or not re.fullmatch('[0-9a-f]{40}', commit_sha)):
            raise ValueError('Merge is absent or ambiguous; do not resend')
        commit = self.get('/git/commits/' + commit_sha)
        parents = [parent['sha'] for parent in commit['parents']]
        wanted = [self.request['base']]
        if self.request['merge_method'] == 'merge':
            wanted.append(self.request['head'])
        if commit.get('sha') != commit_sha or parents != wanted:
            raise ValueError('Merge commit does not match the reviewed target/method; do not resend')
        return {'url': self.request['pr'], 'head': self.request['head'],
                'base': self.request['base'], 'merge_commit': commit_sha,
                'merge_method': self.request['merge_method'], 'actor': expected['actor'], 'merged': True}
