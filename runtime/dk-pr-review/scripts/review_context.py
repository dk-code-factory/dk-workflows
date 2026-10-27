#!/usr/bin/env python3
"""Fetch one PR's metadata and patch without changing the repository or GitHub."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import codecs
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from shared.github_reads import run as github_run
from shared.pr_identity import resolve
from shared.workflow_storage import allocate

FIELDS = 'number,url,title,body,baseRefName,baseRefOid,headRefName,headRefOid,headRepository,isCrossRepository,files'
LARGE_DIFF_ERROR = re.compile(r'diff exceeded the maximum number of lines|20,?000\s+lines|PullRequest\.diff too_large', re.I)


def command(args, cwd):
    result = github_run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def patch_paths(patch, expected):
    paths = set()
    for line in patch.split('\n'):
        if line.startswith('diff --git '):
            quoted = re.search(r' ("(?:[^"\\]|\\.)*")$', line)
            if quoted:
                raw = quoted[1][1:-1].encode('utf-8', 'surrogateescape')
                path = codecs.escape_decode(raw)[0].decode('utf-8', 'surrogateescape')
                if path.startswith('b/'):
                    paths.add(path[2:])
            else:
                paths.update(path for path in expected if line.endswith(' b/' + path))
    return paths


def remote_identity(remote):
    if '://' not in remote and ':' in remote and not remote.startswith('/'):
        host, path = remote.split(':', 1)
        host = host.rsplit('@', 1)[-1]
    else:
        parsed = urlparse(remote)
        host, path = parsed.hostname, parsed.path
    parts = path.strip('/').removesuffix('.git').split('/')
    if not host or len(parts) != 2 or not all(parts):
        return None
    return host.lower(), '/'.join(parts).lower()


def repository_remote(root, host, repository):
    for name in command(['git', 'remote'], root).splitlines():
        remote = command(['git', 'remote', 'get-url', name], root).strip()
        if remote_identity(remote) == (host.lower(), repository.lower()):
            return remote
    return f'https://{host}/{repository}.git'


def ensure_commit(checkout, sha, root, metadata):
    present = subprocess.run(['git', 'cat-file', '-e', f'{sha}^{{commit}}'], cwd=checkout,
                             capture_output=True)
    if present.returncode == 0:
        return
    url = urlparse(metadata['url'])
    parts = url.path.strip('/').split('/')
    if url.scheme != 'https' or not url.hostname or len(parts) != 4 or parts[2] != 'pull':
        raise RuntimeError('PR URL cannot identify the base repository')
    base_remote = repository_remote(root, url.hostname, '/'.join(parts[:2]))
    fetched = subprocess.run(['git', 'fetch', '--quiet', base_remote, sha], cwd=checkout,
                             text=True, capture_output=True)
    if fetched.returncode == 0:
        return
    # Fork heads may be absent from the base repository. The base must always
    # come from the base repository, so only try the fork for the head.
    if sha != metadata['headRefOid']:
        raise RuntimeError('PR base is unavailable from the base repository')
    _, _, fork = resolve(metadata, root, api=lambda *args: json.loads(command(['gh', *args], root)))
    if not fork or '/' not in fork:
        raise RuntimeError('PR head is unavailable from the base repository')
    host = urlparse(metadata['url']).hostname
    if not host:
        raise RuntimeError('PR URL has no host')
    command(['git', 'fetch', '--quiet', repository_remote(root, host, fork), sha], checkout)


def complete_files(cwd, metadata):
    url = urlparse(metadata['url'])
    parts = url.path.strip('/').split('/')
    if url.scheme != 'https' or not url.hostname or len(parts) != 4 or parts[2] != 'pull' or parts[3] != str(metadata['number']):
        raise RuntimeError('PR URL cannot identify a files endpoint')
    endpoint = f'repos/{parts[0]}/{parts[1]}/pulls/{metadata["number"]}/files'
    pages = json.loads(command(['gh', 'api', '--hostname', url.hostname, '--method', 'GET',
                                '--paginate', '--slurp', endpoint, '-F', 'per_page=100'], cwd))
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise RuntimeError('PR files response is incomplete')
    files = [item for page in pages for item in page]
    if not files or any(not isinstance(item, dict) or not isinstance(item.get('filename'), str)
                        or not item['filename'] for item in files):
        raise RuntimeError('PR files response is incomplete')
    paths = [item['filename'] for item in files]
    if len(paths) != len(set(paths)):
        raise RuntimeError('PR files response has duplicate paths')
    graph_paths = {item.get('path') for item in metadata['files']}
    if not graph_paths.issubset(paths):
        raise RuntimeError('PR files response disagrees with metadata')
    metadata['files'] = [{'path': item['filename'], 'additions': item.get('additions'),
                          'deletions': item.get('deletions'),
                          'changeType': str(item.get('status', '')).upper()} for item in files]
    return set(paths)


def fetch(cwd, pr=None):
    root = Path(command(['git', 'rev-parse', '--show-toplevel'], cwd).strip()).resolve()
    target = [pr] if pr else []
    metadata = json.loads(command(['gh', 'pr', 'view', *target, '--json', FIELDS], cwd))
    required = ('number', 'url', 'title', 'body', 'baseRefName', 'baseRefOid', 'headRefName', 'headRefOid', 'files')
    if any(k not in metadata for k in required):
        raise RuntimeError('PR metadata is incomplete')
    if not isinstance(metadata['files'], list) or not isinstance(metadata['number'], int):
        raise RuntimeError('PR metadata has invalid fields')
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', metadata['headRefOid']):
        raise RuntimeError('PR head SHA is invalid')
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', metadata['baseRefOid']):
        raise RuntimeError('PR base SHA is invalid')
    resolve(metadata, cwd, api=lambda *args: json.loads(command(['gh', *args], cwd)),
            allow_unavailable_head=True)
    if not metadata['files']:
        raise RuntimeError('PR has no changed files')
    try:
        patch = command(['gh', 'pr', 'diff', *target, '--patch'], cwd)
        large_diff = False
    except RuntimeError as exc:
        if not LARGE_DIFF_ERROR.search(str(exc)):
            raise
        patch = None
        large_diff = True
    expected = complete_files(cwd, metadata)
    if None in expected:
        raise RuntimeError('PR metadata has an invalid file path')
    if not large_diff:
        if 'diff --git ' not in patch:
            raise RuntimeError('PR patch is empty or incomplete')
        if not expected.issubset(patch_paths(patch, expected)):
            raise RuntimeError('PR patch does not cover every changed file')
    latest = json.loads(command(['gh', 'pr', 'view', *target, '--json', 'headRefOid,baseRefOid'], cwd))
    if (latest.get('headRefOid') != metadata['headRefOid'] or
            latest.get('baseRefOid') != metadata['baseRefOid']):
        raise RuntimeError('PR head or base changed during fetch; retry from a new snapshot')
    local = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=cwd, text=True, capture_output=True)
    local_head = local.stdout.strip() if local.returncode == 0 else None
    status = subprocess.run(['git', 'status', '--porcelain=v1'], cwd=cwd, text=True, capture_output=True)
    worktree_clean = status.returncode == 0 and not status.stdout.strip()
    folder = allocate(repository=root)
    checkout = folder/'checkout'
    command(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout', str(root), str(checkout)], cwd)
    head = metadata['headRefOid']
    ensure_commit(checkout, head, root, metadata)
    # Native skills inspect base...head even when GitHub supplied a usable diff.
    base = metadata['baseRefOid']
    ensure_commit(checkout, base, root, metadata)
    # gh pr diff --patch is an mbox series for multi-commit PRs. Its hunk
    # coordinates describe intermediate commits, not the final reviewed tree.
    # Normalize it to one pinned base-to-head diff for finding validation.
    series_patch = patch is not None and bool(re.match(r'^From [0-9a-fA-F]{40,64} Mon Sep 17 ', patch))
    if large_diff or series_patch:
        # GitHub's PR patch compares the merge base with the head. The triple
        # dot form keeps that comparison while pinning both endpoint commits.
        diff_args = ['git', '-c', 'core.quotePath=false', 'diff', '--no-ext-diff',
                     '--no-textconv', '--no-color', '--binary', '--full-index',
                     '--find-renames', f'{base}...{head}']
        patch = command(diff_args, checkout)
        local_names = set(command(diff_args + ['--name-only', '-z'], checkout).split('\0')) - {''}
        if 'diff --git ' not in patch:
            raise RuntimeError('PR patch is empty or incomplete')
        if local_names != expected or not local_names.issubset(patch_paths(patch, local_names)):
            raise RuntimeError('PR patch does not cover every changed file')
    command(['git', 'checkout', '--quiet', '--detach', head], checkout)
    if command(['git', 'rev-parse', 'HEAD'], checkout).strip() != head:
        raise RuntimeError('isolated checkout does not match PR head')
    final = json.loads(command(['gh', 'pr', 'view', *target, '--json', 'headRefOid,baseRefOid'], cwd))
    if (final.get('headRefOid') != head or final.get('baseRefOid') != metadata['baseRefOid']):
        raise RuntimeError('PR head or base changed during checkout; retry from a new snapshot')
    (folder/'metadata.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False)+'\n')
    (folder/'diff.patch').write_text(patch)
    context = {'url': metadata['url'], 'repository': str(root), 'base_sha': metadata['baseRefOid'], 'head_sha': head,
            'local_head': local_head, 'worktree_clean': worktree_clean,
            'local_matches_pr': worktree_clean and local_head == metadata['headRefOid'],
            'metadata': str(folder/'metadata.json'), 'patch': str(folder/'diff.patch'),
            'checkout': str(checkout), 'workflow_dir': str(folder), 'context_file': str(folder / 'context.json')}
    (folder / 'context.json').write_text(json.dumps(context, indent=2) + '\n')
    for path in (folder / 'metadata.json', folder / 'diff.patch', folder / 'context.json'):
        path.chmod(0o600)
    return context


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--cwd', default='.')
    p.add_argument('--pr')
    args=p.parse_args()
    print(json.dumps(fetch(Path(args.cwd).resolve(), args.pr), indent=2))

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'pr review context: {exc}', file=sys.stderr)
        sys.exit(1)
