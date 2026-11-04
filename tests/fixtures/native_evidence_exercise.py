"""Offline native-coordinator operations for comparing PR evidence guidance.

Agents choose their operations. Only GitHub transport is replaced; production
collection, pagination, consecutive stability reads and artifact checks run.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BASE = '0393ad669dee3f27fdda8bd5ab2f7a5dfebb2c62'
PR = 'https://github.com/fixture/evidence/pull/7'
HEAD = 'a' * 40


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def source_fixture():
    def comment(number, body):
        return {'id': 'C'+str(number), 'databaseId': number, 'body': body,
                'url': PR+'#discussion_r'+str(number), 'author': {'login': 'maintainer'}}
    return {'metadata': {'number': 7, 'url': PR, 'state': 'OPEN', 'title': 'Fix retries',
                         'body': 'Scope: fix retries.\n## Decisions\n- Keep the public retry limit at three.',
                         'headRefName': 'fix', 'headRefOid': HEAD,
                         'headRepository': {'name': 'evidence'}, 'baseRefName': 'main',
                         'baseRefOid': 'b'*40, 'closingIssuesReferences': [],
                         'isCrossRepository': False},
            'inline': [comment(1, 'Fix the retry loop: stop after the third failed attempt.'),
                       comment(2, 'Add retry metrics.'),
                       comment(3, 'Defer metrics to a separate issue; keep this change focused.')],
            'reviews': [{'id': 4, 'body': 'The retry bound needs correction.', 'state': 'CHANGES_REQUESTED',
                         'html_url': PR+'#pullrequestreview-4', 'user': {'login': 'maintainer'}}],
            'comments': [{'id': 5, 'body': 'Settled: keep the retry limit at three; do not add configuration.',
                          'html_url': PR+'#issuecomment-5', 'user': {'login': 'maintainer'}}]}


def initialize(root, arm, revision=BASE):
    if arm not in ('before', 'after'):
        raise ValueError('Choose before or after')
    if root.exists() or root.is_relative_to(ROOT):
        raise ValueError('Use a new directory outside the repository')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args])
    runtime = git('rev-parse', '--verify', revision+'^{commit}').decode().strip()
    paths = git('ls-tree', '-r', '--name-only', runtime).decode().splitlines()
    root.mkdir(parents=True)
    interface = Path(__file__).read_bytes()
    (root/'frozen').mkdir()
    (root/'frozen/interface.py').write_bytes(interface)
    hashes = {}
    for name in paths:
        if name.startswith('runtime/') and name.endswith('.py'):
            path = root/'frozen'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(git('show', runtime+':'+name))
            hashes[name] = sha(path.read_bytes())
    guidance = ([str(p.relative_to(ROOT)) for folder in ('runtime', 'docs', 'codex')
                 for p in sorted((ROOT/folder).rglob('*.md'))] if arm == 'after' else paths)
    for name in guidance:
        if name.startswith(('runtime/', 'docs/', 'codex/')) and name.endswith('.md'):
            path = root/'guidance'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT/name).read_bytes() if arm == 'after' else git('show', runtime+':'+name))
    (root/'checkout').mkdir()
    save(root/'sources.json', source_fixture())
    (root/'instructions.txt').write_bytes(b'Preserve the public retry limit of three.\r\nDo not implement deferred metrics.\n')
    exercise = {'arm': arm, 'runtime': runtime, 'pr': PR, 'head': HEAD,
                'tasks': ['feedback', 'decisions', 'read-comments'],
                'direct_instructions': 'Analyze only. Do not publish, change code or implement deferred work.',
                'file_instructions': 'instructions.txt', 'interface_sha256': sha(interface),
                'runtime_hashes': hashes, 'source_sha256': sha((root/'sources.json').read_bytes()),
                'guidance_hashes': {str(p.relative_to(root/'guidance')): sha(p.read_bytes())
                                    for p in sorted((root/'guidance').rglob('*.md'))},
                'operations': {'read': 'PATH', 'collect': 'no arguments; returns receipt_file',
                               'view': 'ROLE RECEIPT_FILE (read-comments uses feedback view)',
                               'verify': 'RECEIPT_FILE; run independently before accepting each task',
                               'finish': 'JSON with complete, tasks, sources, instructions, decisions and dispositions',
                               'summarize': 'no arguments'}}
    save(root/'exercise.json', exercise)
    return {'root': str(root), 'interface': str(root/'frozen/interface.py'),
            'runtime': runtime, 'source_sha256': exercise['source_sha256']}


class Transport:
    """Read-only fake transport with two thread pages and a paginated reply."""
    def __init__(self, sources):
        self.sources = sources
        self.reads = []

    def returned(self, kind, value):
        value = deepcopy(value)
        self.reads.append({'kind': kind, 'bytes': len(encoded(value))})
        return value

    def threads(self, args, cwd):
        if args[:2] == ['pr', 'view']:
            return self.returned('metadata', self.sources['metadata'])
        if args[:4] != ['api', '--hostname', 'github.com', 'graphql']:
            raise ValueError('Unexpected offline thread request')
        fields = dict(arg.split('=', 1) for arg in args if '=' in arg)
        page = lambda nodes, more=False, cursor=None: {'nodes': nodes, 'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}
        if 'threadId' in fields:
            return self.returned('reply-page', {'data': {'node': {'comments': page([self.sources['inline'][2]])}}})
        if fields.get('cursor') == 'page-2':
            thread = {'id': 'T2', 'isResolved': True, 'path': 'retry.py', 'line': 5,
                      'comments': page([self.sources['inline'][1]], True, 'reply-2')}
            connection = page([thread])
        else:
            thread = {'id': 'T1', 'isResolved': False, 'path': 'retry.py', 'line': 2,
                      'comments': page([self.sources['inline'][0]])}
            connection = page([thread], True, 'page-2')
        return self.returned('thread-page', {'data': {'repository': {'pullRequest': {'reviewThreads': connection}}}})

    def conversation(self, args, cwd):
        if args[:2] == ['pr', 'view']:
            return self.returned('head', {'headRefOid': self.sources['metadata']['headRefOid']})
        if args[:1] != ['api']:
            raise ValueError('Unexpected offline conversation request')
        endpoint = args[args.index('--slurp')+1]
        if endpoint.endswith('/reviews'):
            return self.returned('reviews-pages', [self.sources['reviews'], []])
        if endpoint.endswith('/comments'):
            return self.returned('comments-pages', [[], self.sources['comments']])
        raise ValueError('Unexpected offline endpoint')


def inside(root, name):
    path = (root/name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Read only this exercise arm')
    return path


def production(root):
    # Locate the helper within the selected frozen revision.
    script = next((root/'frozen/runtime').glob('*/scripts/pr_evidence.py'))
    sys.path.insert(0, str(script.parent))
    import pr_evidence
    return pr_evidence


def operate(root, command, args, transport):
    if command == 'read':
        data = inside(root, args[0]).read_bytes()
        return {'text': data.decode('utf-8'), 'bytes': len(data), 'sha256': sha(data)}
    if command == 'finish':
        result = json.loads(args[0])
        save(root/'native-result.json', result)
        return {'saved': True}
    if command == 'summarize':
        trace = [json.loads(line) for line in (root/'trace.jsonl').read_text().splitlines()]
        trace = [row for row in trace if row['command'] != 'summarize']
        reads = [value for row in trace for value in row['remote_reads']]
        return {'initial_collections': sum(row['command'] == 'collect' for row in trace),
                'acceptance_verifications': sum(row['command'] == 'verify' for row in trace),
                'remote_reads': len(reads), 'remote_returned_bytes': sum(row['bytes'] for row in reads),
                'returned_bytes': sum(row['output_bytes'] for row in trace),
                'failed_operations': sum(not row['ok'] for row in trace),
                'operations': trace,
                'assessment': json.loads((root/'native-result.json').read_text()) if (root/'native-result.json').exists() else None}
    evidence = production(root)
    cwd = root/'checkout'
    with patch.dict(os.environ, {'XDG_STATE_HOME': str(root/'state')}), \
         patch.object(evidence.threads, 'run_gh', transport.threads), \
         patch.object(evidence.conversation, 'gh_json', transport.conversation):
        if command == 'collect':
            receipt = evidence.collect(PR, cwd, instruction_files=[root/'instructions.txt'])
            receipts = root/'receipts'
            number = len(list(receipts.glob('*.json')))+1
            path = receipts/(str(number)+'.json')
            save(path, receipt)
            return {'receipt_file': str(path.relative_to(root)), 'receipt': receipt}
        if command == 'view':
            role, name = args
            if role not in ('feedback', 'decisions', 'read-comments'):
                raise ValueError('Unknown role')
            receipt = json.loads(inside(root, name).read_text())
            return evidence.view(receipt['path'], 'feedback' if role == 'read-comments' else role,
                                 cwd, sha256=receipt['sha256'], pr=PR, head=HEAD)
        if command == 'verify':
            receipt = json.loads(inside(root, args[0]).read_text())
            return evidence.verify(receipt['path'], cwd, sha256=receipt['sha256'], pr=PR, head=HEAD)
    raise ValueError('Unknown command')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('command', choices=['init', 'read', 'collect', 'view', 'verify', 'finish', 'summarize'])
    parser.add_argument('args', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    root = options.root.resolve()
    if options.command == 'init':
        result = initialize(root, *options.args)
    else:
        transport = Transport(json.loads((root/'sources.json').read_text()))
        ok = True
        try:
            result = operate(root, options.command, options.args, transport)
        except (ValueError, OSError, LookupError, TypeError, RuntimeError) as error:
            result = {'error': type(error).__name__, 'detail': str(error)}
            ok = False
        data = result.encode() if isinstance(result, str) else encoded(result)
        row = {'command': options.command, 'args': options.args, 'ok': ok,
               'output_bytes': len(data), 'remote_reads': transport.reads}
        with (root/'trace.jsonl').open('a') as output:
            output.write(encoded(row).decode()+'\n')
        if not ok:
            print(data.decode())
            return 2
    print(result if isinstance(result, str) else encoded(result).decode())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
