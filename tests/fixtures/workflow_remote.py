"""Offline GitHub reads for native default-workflow exercises.

Unknown endpoints fail. Reuse the existing publication simulator and its lost
response journals; source/commit reads come from the real local bare repository.
This is explicit experiment infrastructure, never a production transport.
"""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit


spec = importlib.util.spec_from_file_location('workflow_publication_remote',
    Path(__file__).resolve().parents[1]/'pr/fixtures/github_remote.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class Remote(base.Remote):
    def local_git(self, *args):
        return subprocess.check_output([os.environ['DK_RECIPE_GIT'], '--no-lazy-fetch',
            '-C', os.environ['DK_RECIPE_BARE'], *args], text=True).strip()

    def commit(self, revision):
        values = self.local_git('show', '-s', '--format=%H%n%P%n%an%n%ae%n%cI', revision).splitlines()
        return {'sha': values[0], 'parents': [{'sha': value} for value in values[1].split()],
                'author': {'login': 'author', 'id': 1},
                'commit': {'author': {'name': values[2], 'email': values[3], 'date': values[4]},
                           'committer': {'date': values[4]}}}

    def api(self, pr, path, *, payload=None):
        prefix = 'repos/owner/repo'
        suffix = urlsplit(path[len(prefix):]).path if path.startswith(prefix) else None
        if suffix == '/pulls/7':
            value = super().api(pr, path, payload=payload)
            repository = {'id': 10, 'full_name': 'owner/repo', 'name': 'repo',
                          'owner': {'login': 'owner'}, 'clone_url': 'https://github.com/owner/repo.git'}
            value['head'].update(ref='pr-branch', repo=repository)
            value['base'].update(repo=repository)
            return value
        if suffix == '/pulls/7/files':
            revisions = (self.state['branches']['main'], self.state['metadata']['headRefOid'])
            return [{'filename': name, 'patch': self.local_git('diff', *revisions, '--', name)}
                    for name in self.local_git('diff', '--name-only', *revisions).splitlines()]
        if suffix == '/pulls/7/commits':
            revisions = self.local_git('rev-list', '--reverse', self.state['branches']['main']+'..'+
                                       self.state['metadata']['headRefOid']).splitlines()
            return [self.commit(revision) for revision in revisions]
        if suffix and suffix.startswith('/commits/'):
            return self.commit(unquote(suffix.rsplit('/', 1)[-1]))
        if suffix and suffix.startswith('/contents/'):
            query = parse_qs(urlsplit(path).query)
            revision = query.get('ref', ['main'])[0]
            filename = unquote(suffix[len('/contents/'):])
            result = subprocess.run([os.environ['DK_RECIPE_GIT'], '--no-lazy-fetch', '-C',
                os.environ['DK_RECIPE_BARE'], 'show', revision+':'+filename],
                text=True, capture_output=True)
            if result.returncode:
                raise OSError('HTTP 404: Not Found')
            import base64
            return {'encoding': 'base64', 'content': base64.b64encode(result.stdout.encode()).decode()}
        if suffix == '/actions/runs':
            return {'total_count': 1, 'workflow_runs': [deepcopy(self.state['run'])]}
        if suffix in ('/actions/runs/77/jobs', '/actions/runs/77/attempts/1/jobs'):
            return {'total_count': 1, 'jobs': [{'id': 771, 'run_id': 77, 'name': 'test',
                'status': self.state['run']['status'], 'conclusion': self.state['run']['conclusion']}]}
        return super().api(pr, path, payload=payload)

    def graphql(self, payload):
        query = payload['query']
        if 'repository(' not in query or 'reviewThreads(' not in query:
            return super().run(['gh', 'api', '--hostname', 'github.com', 'graphql'], payload=payload)
        threads = []
        if self.state['thread']['comments']:
            threads.append(deepcopy(self.state['thread']))
        for review in self.state['reviews']:
            for comment in self.state['review_comments'].get(str(review['id']), []):
                threads.append({'id': 'PRRT_'+str(comment['id']), 'isResolved': False,
                    'viewerCanResolve': True, 'isOutdated': False, 'path': comment['path'],
                    'line': comment['line'], 'originalLine': comment['line'], 'diffSide': comment['side'],
                    'comments': [{'id': 'comment-'+str(comment['id']), 'databaseId': comment['id'],
                        'body': comment['body'], 'url': base.PR+'#discussion_r'+str(comment['id']),
                        'createdAt': '2026-11-01T01:00:00Z', 'updatedAt': '2026-11-01T01:00:00Z',
                        'author': comment['user'], 'pullRequestReview': {'id': str(review['id']),
                                                                     'databaseId': review['id']}}]})
        for thread in threads:
            thread['comments'] = {'nodes': thread['comments'],
                                  'pageInfo': {'hasNextPage': False, 'endCursor': None}}
        return {'data': {'repository': {'pullRequest': {
            'id': 'PR_fixture', 'url': base.PR, 'headRefOid': self.state['metadata']['headRefOid'],
            'reviewThreads': {'nodes': threads, 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}}

    def run(self, argv, *, payload=None, json_output=True, **kwargs):
        if argv[1:3] == ['pr', 'view']:
            fields = argv[argv.index('--json')+1].split(',')
            changes = self.local_git('diff', '--numstat', self.state['branches']['main'],
                                     self.state['metadata']['headRefOid']).splitlines()
            additions = sum(int(line.split('\t')[0]) for line in changes)
            deletions = sum(int(line.split('\t')[1]) for line in changes)
            metadata = {**self.state['metadata'], **self.state['merge_gate'],
                'number': 7, 'headRefName': 'pr-branch', 'baseRefName': 'main',
                'baseRefOid': self.state['branches']['main'], 'isCrossRepository': False,
                'headRepository': {'id': 'repository', 'name': 'repo', 'nameWithOwner': 'owner/repo',
                                   'owner': {'login': 'owner'}},
                'closingIssuesReferences': [], 'author': self.state['pr_author'],
                'files': self.api(base.PR, 'repos/owner/repo/pulls/7/files'),
                'additions': additions, 'deletions': deletions}
            if 'commits' in fields:
                metadata['commits'] = [{'oid': value['sha']} for value in
                                       self.api(base.PR, 'repos/owner/repo/pulls/7/commits')]
            return {key: metadata[key] for key in fields if key in metadata}
        if len(argv) > 1 and argv[1] == 'api':
            fields, endpoint, method = {}, None, 'GET'
            index = 2
            while index < len(argv):
                item = argv[index]
                if item in ('--hostname', '--input', '--method', '-X', '-f', '-F', '--field', '--raw-field'):
                    value = argv[index+1]
                    if item in ('--method', '-X'):
                        method = value
                    if item in ('-f', '-F', '--field', '--raw-field'):
                        key, value = value.split('=', 1)
                        if item in ('-F', '--field'):
                            try:
                                value = json.loads(value)
                            except ValueError:
                                pass
                        fields[key] = value
                    index += 2
                elif item.startswith('-'):
                    if item not in ('--paginate', '--slurp'):
                        raise AssertionError('Unsupported fixture API flag: '+item)
                    index += 1
                else:
                    if endpoint is not None:
                        raise AssertionError('Multiple fixture API endpoints')
                    endpoint = item
                    index += 1
            if endpoint == 'graphql':
                data = payload or {'query': fields.pop('query'), 'variables': fields}
                return self.graphql(data)
            value = self.api(base.PR, endpoint, payload=payload if method != 'GET' else None)
            return [value] if '--slurp' in argv else value
        return super().run(argv, payload=payload, json_output=json_output, **kwargs)
