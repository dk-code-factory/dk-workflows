"""Domain-owned GitHub write handlers. All uncertain writes reconcile by reading."""
import re
from urllib.parse import quote, urlencode

KINDS = {'ci-approve', 'ci-rerun', 'resolve-thread', 'request-review', 'create-draft-pr', 'post-review', 'merge-pr'}


def positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(name + ' must be a positive integer')
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('A full captured head SHA is required')
    return value


class Handler:
    def __init__(self, request, *, api, metadata, run, endpoint):
        self.request = request
        self.api, self.metadata, self.run = api, metadata, run
        self.endpoint = endpoint
        self.host, self.root, self.number = endpoint(request['pr'])

    def get(self, suffix):
        return self.api(self.request['pr'], self.root + suffix)

    def merger(self):
        from shared.pr_merge import Merge
        return Merge(self.request, api=self.api, run=self.run, endpoint=self.endpoint)

    def ci_approval(self):
        from shared.ci_approval import Approval
        return Approval(self)

    def pages(self, suffix, *, event_types=None):
        separator = '&' if '?' in suffix else '?'
        pages = self.run(['gh', 'api', '--hostname', self.host,
                          self.root + suffix + separator + 'per_page=100', '--paginate', '--slurp'])
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise ValueError('Incomplete paginated GitHub response')
        values = [value for page in pages for value in page]
        if event_types is not None:
            values = [value for value in values if value.get('event') in event_types]
        ids = [v.get('id', v.get('filename')) for v in values]
        if any(i is None for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Incomplete or duplicated GitHub objects')
        return values

    def post(self, suffix, payload):
        return self.run(['gh', 'api', '--hostname', self.host, self.root + suffix,
                         '--method', 'POST', '--input', '-'], payload=payload, json_output=False)

    def graphql(self, query, variables):
        value = self.run(['gh', 'api', '--hostname', self.host, 'graphql', '--input', '-'],
                         payload={'query': query, 'variables': variables})
        if not isinstance(value, dict) or value.get('errors') or not isinstance(value.get('data'), dict):
            raise ValueError('Incomplete GraphQL response')
        return value['data']

    def thread(self):
        query = '''query($id:ID!,$after:String){node(id:$id){... on PullRequestReviewThread{
          id isResolved viewerCanResolve pullRequest{url headRefOid}
          comments(first:100,after:$after){nodes{id databaseId url body author{login}}
          pageInfo{hasNextPage endCursor}}}}}'''
        cursor, comments, seen, snapshot = None, [], set(), None
        while True:
            node = self.graphql(query, {'id': self.request['thread_id'], 'after': cursor}).get('node')
            if not isinstance(node, dict) or node.get('id') != self.request['thread_id']:
                raise ValueError('Review thread is unavailable')
            if node.get('pullRequest') != {'url': self.request['pr'], 'headRefOid': self.request['head']}:
                raise ValueError('Thread belongs to a different PR or head')
            state = {k: node[k] for k in ('id', 'isResolved', 'viewerCanResolve', 'pullRequest')}
            if snapshot is not None and snapshot != state:
                raise ValueError('Thread changed during pagination')
            snapshot = state
            connection = node['comments']
            comments.extend(connection['nodes'])
            page = connection['pageInfo']
            if page['hasNextPage'] is False:
                break
            cursor = page['endCursor']
            if not cursor or cursor in seen:
                raise ValueError('Incomplete review thread pagination')
            seen.add(cursor)
        ids = [c['id'] for c in comments]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate thread comments')
        return {**snapshot, 'comments': comments}

    def requested(self):
        value = self.get('/pulls/' + self.number + '/requested_reviewers')
        return {'reviewers': sorted(v['login'] for v in value['users']),
                'team_reviewers': sorted(v['slug'] for v in value['teams'])}

    def branch(self, name):
        return self.get('/git/ref/heads/' + quote(name, safe=''))['object']['sha']

    def candidates(self):
        owner = self.root.split('/')[1]
        suffix = '/pulls?' + urlencode({'state': 'all', 'head': owner + ':' + self.request['head_branch'],
                                        'base': self.request['base_branch']})
        return self.pages(suffix)

    def approval_identity(self):
        actor = self.api(self.request['pr'], 'user')
        author = self.get('/pulls/' + self.number).get('user') or {}
        if (type(actor.get('id')) is not int or actor['id'] <= 0
                or type(author.get('id')) is not int or author['id'] <= 0
                or not actor.get('login')):
            raise ValueError('Approval requires complete actor and author identities')
        if actor['id'] == author['id']:
            raise ValueError('Cannot approve the authenticated account\'s own PR')
        return {'actor_id': actor['id'], 'author_id': author['id'], 'actor': actor['login']}

    def validate_comments(self):
        comments = self.request.get('comments', [])
        if not isinstance(comments, list):
            raise ValueError('Review comments must be a list')
        if not comments:
            return
        files = self.pages('/pulls/' + self.number + '/files')
        lines = {}
        for file in files:
            old = new = None
            for line in file.get('patch', '').splitlines():
                hunk = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
                if hunk:
                    old, new = map(int, hunk.groups())
                elif old is not None:
                    if line.startswith('+'):
                        lines.setdefault((file['filename'], 'RIGHT'), set()).add(new)
                        new += 1
                    elif line.startswith('-'):
                        lines.setdefault((file['filename'], 'LEFT'), set()).add(old)
                        old += 1
                    elif line.startswith(' '):
                        old += 1
                        new += 1
        for comment in comments:
            if (not isinstance(comment, dict) or set(comment) - {'path', 'line', 'side', 'body'}
                    or not isinstance(comment.get('body'), str) or not comment['body'].strip()
                    or type(comment.get('line')) is not int
                    or comment['line'] not in lines.get((comment.get('path'), comment.get('side')), set())):
                raise ValueError('Review comment must identify a changed line in the current diff')

    def prepare(self):
        request = self.request
        if request['kind'] == 'ci-approve':
            return self.ci_approval().prepare()
        if request['kind'] == 'merge-pr':
            return self.merger().prepare()
        sha(request.get('head'))
        expected = {'metadata': self.metadata(request['pr'])}
        if expected['metadata'].get('url') != request['pr'] or expected['metadata'].get('headRefOid') != request['head']:
            raise ValueError('PR identity or head changed')
        kind = request['kind']
        if kind == 'ci-rerun':
            positive(request.get('run_id'), 'run_id')
            positive(request.get('attempt'), 'attempt')
            if not isinstance(request.get('reason'), str) or not request['reason'].strip():
                raise ValueError('Transient-failure diagnosis is required before rerunning failed jobs')
            value = self.get('/actions/runs/' + str(request['run_id']))
            expected['run'] = self.run_snapshot(value)
            if (value['run_attempt'] != request['attempt'] or value['head_sha'] != request['head']
                    or value['status'] != 'completed' or value['conclusion'] not in {'failure', 'timed_out'}):
                raise ValueError('Rerun requires the current completed failed attempt at the exact PR head')
        elif kind == 'resolve-thread':
            if not isinstance(request.get('thread_id'), str) or not request['thread_id']:
                raise ValueError('thread_id is required')
            positive(request.get('reply_id'), 'reply_id')
            expected['thread'] = self.thread()
            actor = self.api(request['pr'], 'user')['login']
            answers = [c for c in expected['thread']['comments'] if c['databaseId'] == request['reply_id']
                       and (c.get('author') or {}).get('login') == actor and c.get('body', '').strip()]
            if len(answers) != 1 or not expected['thread']['viewerCanResolve']:
                raise ValueError('Resolution requires your verified posted answer in the exact thread')
            request['answer'] = answers[0]
        elif kind == 'request-review':
            for field in ('reviewers', 'team_reviewers'):
                values = request.setdefault(field, [])
                if (not isinstance(values, list) or len(values) != len(set(values))
                        or any(not isinstance(v, str) or not re.fullmatch('[A-Za-z0-9_-]+', v) for v in values)):
                    raise ValueError('Reviewer names must be unique native logins or team slugs')
            if not (request['reviewers'] or request['team_reviewers']):
                raise ValueError('At least one reviewer is required')
            expected['requested'] = self.requested()
        elif kind == 'create-draft-pr':
            for field in ('title', 'body', 'head_branch', 'base_branch'):
                if not isinstance(request.get(field), str) or not request[field].strip():
                    raise ValueError(field + ' is required')
            if '\n' in request['title'] or '\r' in request['title']:
                raise ValueError('Title must be one line')
            sha(request.get('branch_sha'))
            sha(request.get('base_sha'))
            repo = self.get('')
            if repo['default_branch'] != request['base_branch'] or request['head_branch'] == request['base_branch']:
                raise ValueError('Partition draft PRs must target the verified default branch')
            self.check_branches()
            request['actor'] = self.api(request['pr'], 'user')['login']
            expected['existing'] = self.candidates()
            if expected['existing']:
                raise ValueError('A PR already exists for this branch; inspect it instead of creating another')
        elif kind == 'post-review':
            if request.setdefault('event', 'COMMENT') not in {'COMMENT', 'APPROVE', 'REQUEST_CHANGES'}:
                raise ValueError('Unsupported review event')
            if not isinstance(request.setdefault('body', ''), str):
                raise ValueError('Review body must be text')
            self.validate_comments()
            request['actor'] = self.api(request['pr'], 'user')['login']
            if request['event'] == 'APPROVE':
                expected['approval_identity'] = self.approval_identity()
            expected['review_ids'] = [v['id'] for v in self.pages('/pulls/' + self.number + '/reviews')]
        return expected

    @staticmethod
    def run_snapshot(value):
        return {key: value[key] for key in ('id', 'head_sha', 'run_attempt', 'status', 'conclusion')}

    def check_branches(self):
        request = self.request
        if self.branch(request['head_branch']) != request['branch_sha'] or self.branch(request['base_branch']) != request['base_sha']:
            raise ValueError('Partition branch or default target changed')

    def check(self, expected, verified):
        request, kind = self.request, self.request['kind']
        if kind == 'ci-approve':
            return self.ci_approval().check(expected, verified)
        if kind == 'merge-pr':
            return self.merger().check(expected, verified)
        if self.metadata(request['pr']) != expected['metadata']:
            raise ValueError('PR head or governing metadata changed')
        if kind == 'ci-rerun':
            run = self.get('/actions/runs/' + str(request['run_id']))
            if run['head_sha'] != request['head'] or run['id'] != request['run_id']:
                raise ValueError('Workflow run identity changed')
            if not verified and self.run_snapshot(run) != expected['run']:
                raise ValueError('Failed workflow attempt changed before rerun')
        elif kind == 'resolve-thread':
            live = self.thread()
            if (not verified and live != expected['thread']) or request['answer'] not in live['comments']:
                raise ValueError('Review thread or verified answer changed')
        elif kind == 'request-review':
            if not verified and self.requested() != expected['requested']:
                raise ValueError('Review requests changed before publication')
        elif kind == 'create-draft-pr':
            self.check_branches()
            if not verified and self.candidates() != expected['existing']:
                raise ValueError('PR appeared before creation; inspect it first')
        elif kind == 'post-review' and not verified:
            if request['event'] == 'APPROVE' and self.approval_identity() != expected['approval_identity']:
                raise ValueError('Approval actor or author changed')
            if [v['id'] for v in self.pages('/pulls/' + self.number + '/reviews')] != expected['review_ids']:
                raise ValueError('Reviews changed before publication')
            self.validate_comments()

    def send(self, expected, operation_id):
        request, kind = self.request, self.request['kind']
        if kind == 'ci-approve':
            return self.ci_approval().send()
        if kind == 'merge-pr':
            return self.merger().send()
        if kind == 'ci-rerun':
            return self.post('/actions/runs/' + str(request['run_id']) + '/rerun-failed-jobs', {})
        if kind == 'resolve-thread':
            if expected['thread']['isResolved']:
                return {'already_satisfied': True}
            return self.graphql('''mutation($id:ID!,$operation:String!){resolveReviewThread(
              input:{threadId:$id,clientMutationId:$operation}){thread{id isResolved}}}''',
                                {'id': request['thread_id'], 'operation': operation_id})
        if kind == 'request-review':
            if all(set(request[key]) <= set(expected['requested'][key]) for key in ('reviewers', 'team_reviewers')):
                return {'already_satisfied': True}
            return self.post('/pulls/' + self.number + '/requested_reviewers',
                             {k: request[k] for k in ('reviewers', 'team_reviewers')})
        if kind == 'create-draft-pr':
            return self.post('/pulls', {'title': request['title'], 'body': request['body'],
                                      'head': request['head_branch'], 'base': request['base_branch'], 'draft': True})
        return self.post('/pulls/' + self.number + '/reviews',
                         {'commit_id': request['head'], 'event': request['event'], 'body': request['body'],
                          'comments': request.get('comments', [])})

    def observe(self, expected):
        request, kind = self.request, self.request['kind']
        if kind == 'ci-approve':
            return self.ci_approval().observe(expected)
        if kind == 'merge-pr':
            return self.merger().observe(expected)
        if kind == 'ci-rerun':
            run = self.get('/actions/runs/' + str(request['run_id']))
            if run['id'] != request['run_id'] or run['head_sha'] != request['head'] or run['run_attempt'] != request['attempt'] + 1:
                raise ValueError('Exactly one new workflow attempt is not yet observable; do not resend')
            return {'run_id': run['id'], 'attempt': run['run_attempt'], 'head': run['head_sha']}
        if kind == 'resolve-thread':
            thread = self.thread()
            if not thread['isResolved'] or request['answer'] not in thread['comments']:
                raise ValueError('Thread resolution is unconfirmed')
            return {'thread_id': thread['id'], 'reply_id': request['reply_id'], 'resolved': True}
        if kind == 'request-review':
            live = self.requested()
            if any(not set(request[key]) <= set(live[key]) for key in ('reviewers', 'team_reviewers')):
                raise ValueError('Requested reviewers are not observable; do not resend')
            return {'head': request['head'], **live}
        if kind == 'create-draft-pr':
            matches = [v for v in self.candidates() if v['id'] not in {x['id'] for x in expected['existing']}
                       and v['state'] == 'open' and v['draft'] is True and v['title'] == request['title']
                       and v['body'] == request['body'] and v['head']['ref'] == request['head_branch']
                       and v['head']['sha'] == request['branch_sha'] and v['base']['ref'] == request['base_branch']
                       and v['base']['sha'] == request['base_sha'] and v['user']['login'] == request['actor']]
            if len(matches) != 1:
                raise ValueError('Draft PR is absent or ambiguous; do not create another')
            return {'id': matches[0]['id'], 'url': matches[0]['html_url'], 'head': request['branch_sha'], 'draft': True}
        state = {'COMMENT': 'COMMENTED', 'APPROVE': 'APPROVED', 'REQUEST_CHANGES': 'CHANGES_REQUESTED'}[request['event']]
        matches = [v for v in self.pages('/pulls/' + self.number + '/reviews')
                   if v['id'] not in expected['review_ids'] and v['commit_id'] == request['head']
                   and v['state'] == state and v.get('body', '') == request['body']
                   and v['user']['login'] == request['actor']]
        if len(matches) != 1:
            raise ValueError('Submitted review is absent or ambiguous; do not resend')
        review = matches[0]
        positive(review['id'], 'review id')
        comments = self.pages('/pulls/' + self.number + '/reviews/' + str(review['id']) + '/comments')
        if len(comments) != len(request.get('comments', [])):
            raise ValueError('Published review comment count differs from the prepared payload')
        verified_comments = []
        for listed in comments:
            comment_id = positive(listed['id'], 'comment id')
            comment = self.get('/pulls/comments/' + str(comment_id))
            pr_url = 'https://' + self.host + '/api/v3/' + self.root + '/pulls/' + self.number
            if self.host == 'github.com':
                pr_url = 'https://api.github.com/' + self.root + '/pulls/' + self.number
            if (type(comment.get('id')) is not int or comment['id'] != comment_id
                    or type(comment.get('pull_request_review_id')) is not int
                    or comment['pull_request_review_id'] != review['id']
                    or comment.get('pull_request_url') != pr_url
                    or (comment.get('user') or {}).get('login') != request['actor']
                    or comment.get('original_commit_id') != request['head']
                    or comment.get('in_reply_to_id') is not None
                    or comment.get('start_line') is not None
                    or comment.get('original_start_line') is not None):
                raise ValueError('Published review comment identity differs from the prepared review')
            # List responses may omit locations. Any supplied evidence must still
            # agree with the authoritative object fetched by its database id.
            for key in ('path', 'body', 'line', 'side', 'original_line', 'commit_id',
                        'original_commit_id', 'pull_request_review_id', 'pull_request_url',
                        'in_reply_to_id', 'start_line', 'original_start_line'):
                if listed.get(key) is not None and listed[key] != comment.get(key):
                    raise ValueError('Published review comment evidence is inconsistent')
            if ((listed.get('user') or {}).get('login') is not None
                    and listed['user']['login'] != request['actor']):
                raise ValueError('Published review comment actor is inconsistent')
            # GitHub can move a comment or clear its current line after a push.
            # Only its original location at the submitted commit proves intent.
            line = comment.get('original_line')
            if line is None and comment.get('commit_id') == request['head']:
                line = comment.get('line')
            if type(line) is not int or line <= 0 or comment.get('side') not in {'LEFT', 'RIGHT'}:
                raise ValueError('Published review comment original location is unavailable')
            verified_comments.append({'id': comment_id, 'path': comment.get('path'),
                                      'line': line, 'side': comment['side'], 'body': comment.get('body')})
        projection = lambda v: {k: v.get(k) for k in ('path', 'line', 'side', 'body')}
        expected_comments = [projection(v) for v in request.get('comments', [])]
        if sorted(map(repr, [projection(v) for v in verified_comments])) != sorted(map(repr, expected_comments)):
            raise ValueError('Published review comments differ from the prepared payload')
        return {'id': review['id'], 'url': review['html_url'], 'head': request['head'],
                'comments': verified_comments}
