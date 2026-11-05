"""Offline GitHub transport shared by domain and full-recipe acceptance tests."""
from copy import deepcopy
from urllib.parse import urlsplit, parse_qs

PR = 'https://github.com/owner/repo/pull/7'
HEAD = 'a' * 40


def initial():
    return {'metadata': {'url': PR, 'headRefOid': HEAD, 'title': 'Fix the value', 'body': 'Scope: return two.\n## Decisions\n- Return two.'},
            'actor': 'author', 'run': {'id': 77, 'head_sha': HEAD, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'failure'},
            'actor_id': 1, 'pr_author': {'id': 2, 'login': 'contributor'},
            'merge_gate': {'baseRefOid': 'b'*40, 'state': 'OPEN', 'isDraft': False,
                           'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN', 'reviewDecision': 'APPROVED'},
            'pull': {'state': 'open', 'draft': False, 'merged': False, 'mergeable': True,
                     'mergeable_state': 'clean', 'base': {'sha': 'b'*40, 'ref': 'main'}},
            'merge_commit': None,
            'thread': {'id': 'PRRT_fixture', 'isResolved': False, 'viewerCanResolve': True,
                       'comments': [{'id': 'comment-42', 'databaseId': 42, 'url': PR+'#discussion_r42',
                                     'body': 'Verified answer', 'author': {'login': 'author'}}]},
            'requested': {'users': [], 'teams': []}, 'branches': {'main': 'b'*40, 'partition': 'c'*40},
            'pulls': [], 'reviews': [], 'review_comments': {}, 'replies': [], 'issue_comments': [], 'writes': {}, 'lose_response': [],
            'files': [{'filename': 'code.py', 'patch': '@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 2'}],
            'checks': [{'name': 'test', 'bucket': 'pass', 'state': 'SUCCESS', 'link': PR+'/checks/1', 'workflow': 'CI'}]}


class Remote:
    def __init__(self, state=None):
        self.state = initial() if state is None else state

    def metadata(self, pr):
        if pr != self.state['metadata']['url']:
            raise ValueError('Unknown fixture PR')
        return deepcopy(self.state['metadata'])

    def changed(self, kind, value):
        self.state['writes'][kind] = self.state['writes'].get(kind, 0) + 1
        if kind in self.state['lose_response']:
            self.state['lose_response'].remove(kind)
            raise OSError('Fixture lost response after applying '+kind)
        return value

    def api(self, pr, path, *, payload=None):
        if path == 'user':
            return {'login': self.state['actor'], 'id': self.state['actor_id']}
        prefix = 'repos/owner/repo'
        if not path.startswith(prefix):
            raise AssertionError('Unexpected fixture API: '+path)
        parsed = urlsplit(path[len(prefix):])
        suffix = parsed.path
        if suffix == '':
            return {'default_branch': 'main'}
        if suffix == '/pulls/7':
            return deepcopy({**self.state['pull'], 'html_url': PR,
                             'head': {'sha': self.state['metadata']['headRefOid']},
                             'user': self.state['pr_author']})
        if suffix == '/pulls/7/merge':
            assert payload['sha'] == self.state['metadata']['headRefOid']
            commit_sha = 'e'*40
            parents = [self.state['branches']['main']]
            if payload['merge_method'] == 'merge':
                parents.append(payload['sha'])
            self.state['merge_commit'] = {'sha': commit_sha, 'parents': [{'sha': v} for v in parents]}
            self.state['pull'].update(merged=True, state='closed', merge_commit_sha=commit_sha,
                                      merged_by={'id': self.state['actor_id'], 'login': self.state['actor']})
            self.state['branches']['main'] = commit_sha
            return self.changed('merge-pr', {'merged': True, 'sha': commit_sha})
        if suffix == '/git/commits/' + 'e'*40:
            return deepcopy(self.state['merge_commit'])
        if suffix.startswith('/git/ref/heads/'):
            from urllib.parse import unquote
            return {'object': {'sha': self.state['branches'][unquote(suffix[len('/git/ref/heads/'):])]}}
        if suffix == '/actions/runs/77':
            return deepcopy(self.state['run'])
        if suffix == '/actions/runs/77/rerun-failed-jobs':
            assert payload == {}
            self.state['run'].update(run_attempt=self.state['run']['run_attempt']+1, status='queued', conclusion=None)
            return self.changed('ci-rerun', '')
        if suffix == '/pulls/7/requested_reviewers':
            if payload is None:
                return deepcopy(self.state['requested'])
            self.state['requested'] = {'users': [{'login':v} for v in payload['reviewers']],
                                       'teams': [{'slug':v} for v in payload['team_reviewers']]}
            return self.changed('request-review', deepcopy(self.state['requested']))
        if suffix == '/pulls':
            if payload is None:
                query = parse_qs(parsed.query)
                return deepcopy([v for v in self.state['pulls'] if v['head']['ref'] == query.get('head',[':'])[0].split(':',1)[-1]])
            value = {'id': 500+len(self.state['pulls']), 'number': 8+len(self.state['pulls']),
                     'html_url': PR.replace('/7','/8'), 'state':'open', 'draft':payload['draft'],
                     'title':payload['title'], 'body':payload['body'], 'user':{'login':self.state['actor']},
                     'head':{'ref':payload['head'],'sha':self.state['branches'][payload['head']]},
                     'base':{'ref':payload['base'],'sha':self.state['branches'][payload['base']]}}
            self.state['pulls'].append(value)
            return self.changed('create-draft-pr',deepcopy(value))
        if suffix == '/pulls/7/files':
            return deepcopy(self.state['files'])
        if suffix == '/pulls/7/reviews':
            if payload is None:
                return deepcopy(self.state['reviews'])
            review_id=100+len(self.state['reviews'])
            value={'id':review_id,'commit_id':payload['commit_id'],'body':payload['body'],
                   'state':{'COMMENT':'COMMENTED','APPROVE':'APPROVED','REQUEST_CHANGES':'CHANGES_REQUESTED'}[payload['event']],
                   'user':{'login':self.state['actor']},'html_url':PR+'#review-'+str(review_id)}
            self.state['reviews'].append(value)
            next_comment_id = 1 + max((comment['id']
                for comments in self.state['review_comments'].values() for comment in comments), default=199)
            self.state['review_comments'][str(review_id)] = [
                {'id':next_comment_id+i, 'pull_request_review_id':review_id,
                 'pull_request_url':'https://api.github.com/repos/owner/repo/pulls/7',
                 'user':{'login':self.state['actor']}, 'commit_id':payload['commit_id'],
                 'original_commit_id':payload['commit_id'], 'original_line':v['line'], **v}
                for i,v in enumerate(payload['comments'])]
            return self.changed('post-review',deepcopy(value))
        if suffix.startswith('/pulls/7/reviews/') and suffix.endswith('/comments'):
            return deepcopy(self.state['review_comments'][suffix.split('/')[-2]])
        if suffix.startswith('/pulls/comments/'):
            comment_id = int(suffix.rsplit('/', 1)[-1])
            return deepcopy(next(comment for comments in self.state['review_comments'].values()
                                 for comment in comments if comment['id'] == comment_id))
        if suffix == '/issues/7/comments':
            if payload is None:
                return deepcopy(self.state['issue_comments'])
            comment_id = 300 + len(self.state['issue_comments'])
            value = {'id': comment_id, 'body': payload['body'],
                     'user': {'login': self.state['actor']},
                     'html_url': PR+'#issuecomment-'+str(comment_id)}
            self.state['issue_comments'].append(value)
            return self.changed('reply-comment', deepcopy(value))
        if suffix == '/pulls/7/comments':
            return deepcopy(self.state['replies'])
        if suffix == '/pulls/7/comments/41/replies':
            value={'id':42,'in_reply_to_id':41,'body':payload['body'],'user':{'login':self.state['actor']},'html_url':PR+'#discussion_r42'}
            self.state['replies'].append(value)
            self.state['thread']['comments']=[{'id':'comment-42','databaseId':42,'url':value['html_url'],'body':value['body'],'author':value['user']}]
            return self.changed('reply-inline',value)
        raise AssertionError('Unexpected fixture API: '+path)

    def run(self, argv, *, payload=None, json_output=True, **kwargs):
        assert argv[0] == 'gh'
        if argv[1:3] == ['pr','view']:
            fields=argv[argv.index('--json')+1].split(',')
            return {k:v for k,v in {**self.metadata(argv[3]), **self.state['merge_gate']}.items() if k in fields}
        if argv[1:3] == ['pr','checks']:
            return deepcopy(self.state['checks'])
        if argv[1:3] == ['pr','edit']:
            if '--title' in argv:self.state['metadata']['title']=argv[argv.index('--title')+1]
            if '--body-file' in argv:
                from pathlib import Path
                self.state['metadata']['body']=Path(argv[argv.index('--body-file')+1]).read_text()
            return self.changed('edit-pr','')
        path=argv[argv.index('--hostname')+2]
        if path == 'graphql':
            if 'mutation' in payload['query']:
                self.state['thread']['isResolved']=True
                return self.changed('resolve-thread',{'data':{'resolveReviewThread':{'thread':{'id':self.state['thread']['id'],'isResolved':True}}}})
            node=deepcopy(self.state['thread'])
            comments=node.pop('comments')
            offset=int(payload['variables'].get('after') or '0')
            node['pullRequest']={'url':self.state['metadata']['url'],'headRefOid':self.state['metadata']['headRefOid']}
            next_offset=offset+100
            node['comments']={'nodes':comments[offset:next_offset], 'pageInfo':{'hasNextPage':next_offset<len(comments),'endCursor':str(next_offset)}}
            return {'data':{'node':node}}
        value=self.api(PR,path,payload=payload)
        return [value] if '--paginate' in argv else value


def request(kind):
    value={'kind':kind,'pr':PR,'head':HEAD}
    if kind=='ci-rerun':value.update(run_id=77,attempt=1,reason='Verified transient infrastructure failure')
    if kind=='resolve-thread':value.update(thread_id='PRRT_fixture',reply_id=42)
    if kind=='request-review':value.update(reviewers=['reviewer'],team_reviewers=['team'])
    if kind=='create-draft-pr':value.update(head_branch='partition',branch_sha='c'*40,base_branch='main',base_sha='b'*40,title='Evacuated scope',body='Independent work')
    if kind=='post-review':value.update(body='Verified findings',comments=[{'path':'code.py','line':1,'side':'RIGHT','body':'Consider the value.'}])
    if kind=='merge-pr':value.update(base='b'*40,merge_method='squash')
    return value
