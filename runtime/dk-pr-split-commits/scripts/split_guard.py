#!/usr/bin/env python3
"""Guard an authorized rewrite of a PR commit series."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared.review_loop import require_complete
from shared.commit_message_lines import validate_commits
from shared import series_assessment

STATE = '.git/pr-split-state.json'

def run(*args, check=True):
    p = subprocess.run(args, text=True, capture_output=True)
    if check and p.returncode: raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return p.stdout.strip()

def git(*args): return run('git', *args)

def state_path():
    return Path(git('rev-parse','--git-path','pr-split-state.json')).resolve()

def write_state(data):
    path = state_path()
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pr-split-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def remote_identity(url):
    if '://' not in url and ':' in url and not url.startswith('/'):
        host, path = url.split(':', 1)
        host = host.rsplit('@', 1)[-1]
    else:
        parsed = urlparse(url)
        host, path = parsed.hostname, parsed.path
    parts = path.strip('/').removesuffix('.git').split('/')
    if not host or len(parts) != 2 or not all(parts):
        return None
    return host.lower(), '/'.join(parts).lower()

def pr_identity(pr):
    parsed = urlparse(pr.get('url') or '')
    parts = parsed.path.strip('/').split('/')
    head_repo = (pr.get('headRepository') or {}).get('nameWithOwner')
    if (not parsed.hostname or len(parts) != 4 or parts[2] != 'pull'
            or parts[3] != str(pr.get('number')) or not head_repo
            or len(head_repo.split('/')) != 2):
        raise RuntimeError('PR repository identity is incomplete')
    base_repo = '/'.join(parts[:2]).lower()
    head_repo = head_repo.lower()
    if pr.get('isCrossRepository') is not (head_repo != base_repo):
        raise RuntimeError('PR repository identity is inconsistent')
    return parsed.hostname.lower(), base_repo, head_repo

def check_remote(remote, host, head_repo, cross_repo):
    push_urls=git('remote','get-url','--push','--all',remote).splitlines()
    if len(push_urls) != 1:
        raise RuntimeError('upstream remote must have exactly one push URL')
    for url in (git('remote','get-url',remote), push_urls[0]):
        identity = remote_identity(url)
        if identity != (host, head_repo):
            if not cross_repo and identity is None:
                continue  # Local-path remotes used for same-repository work.
            raise RuntimeError('upstream remote does not identify the PR head repository')

def current():
    branch=git('symbolic-ref','--short','HEAD')
    if git('status','--porcelain=v1'):
        raise RuntimeError('worktree must be clean')
    upstream=git('rev-parse','--abbrev-ref','--symbolic-full-name','@{upstream}')
    remote,remote_branch=upstream.split('/',1)
    push_url=git('remote','get-url','--push',remote)
    sha=run('git','ls-remote','--heads',push_url,remote_branch).split('\t')[0]
    if not sha: raise RuntimeError('upstream branch is missing')
    if git('rev-parse','HEAD') != sha:
        raise RuntimeError('local HEAD must match remote PR head before prepare')
    return branch,remote,remote_branch,sha

def prepare(base, pr_arg=None, mode='split'):
    if state_path().exists():
        raise RuntimeError('existing split state requires recovery; refusing to overwrite')
    branch,remote,remote_branch,sha=current()
    target=[pr_arg] if pr_arg else []
    pr=json.loads(run('gh','pr','view',*target,'--json','number,url,headRefName,headRefOid,isCrossRepository,headRepository'))
    host,base_repo,head_repo=pr_identity(pr)
    if pr['isCrossRepository'] and pr_arg != pr['url']:
        raise RuntimeError('fork PR requires its explicit PR URL')
    check_remote(remote,host,head_repo,pr['isCrossRepository'])
    if pr['headRefName'] != remote_branch or pr['headRefOid'] != sha:
        raise RuntimeError('PR head does not match owned upstream branch')
    base_sha=git('merge-base',base,'HEAD')
    commits=git('rev-list','--reverse',f'{base_sha}..HEAD').splitlines()
    if not commits: raise RuntimeError('PR has no commits to split')
    author=git('config','user.email').lower()
    others=[]
    for commit in commits:
        if git('show','-s','--format=%ae',commit).lower()!=author:
            others.append(commit[:12])
    if others: raise RuntimeError('commits by another author: '+', '.join(others))
    data={'branch':branch,'remote':remote,'remote_branch':remote_branch,'remote_sha':sha,
          'original_head':sha,'original_tree':git('rev-parse','HEAD^{tree}'),
          'base':base_sha,'pr':pr['number'],'pr_url':pr['url'],'host':host,
          'base_repo':base_repo,'head_repo':head_repo,'author':author,'mode':mode}
    if mode == 'regroup':
        live = series_assessment.live_snapshot(pr['url'], Path.cwd())
        if (live['final_head'] != sha or live['target_tip'] != git('rev-parse',base)
                or base_sha != live['target_tip']):
            raise RuntimeError('reviewed head or target tip changed before cleanup')
        data.update(target_tip=live['target_tip'], target_branch=live['target_branch'])
        data['recovery_ref'] = f'refs/pr-split-recovery/{sha}'
        git('update-ref',data['recovery_ref'],sha)
    write_state(data)
    print(json.dumps(data,indent=2))

def verify(data):
    if git('status','--porcelain=v1'): raise RuntimeError('worktree must be clean')
    if git('symbolic-ref','--short','HEAD')!=data['branch']:
        raise RuntimeError('branch changed')
    if git('rev-parse','HEAD^{tree}')!=data['original_tree']:
        raise RuntimeError('rewritten tree differs from original')
    if git('merge-base',data['base'],'HEAD')!=data['base']:
        raise RuntimeError('base is no longer ancestor')
    commits=git('rev-list','--reverse',f"{data['base']}..HEAD").splitlines()
    minimum = 1 if data.get('mode', 'split') == 'regroup' else 2
    if len(commits)<minimum:
        raise RuntimeError('regroup requires at least one logical commit' if minimum == 1
                           else 'split requires at least two logical commits')
    if any(git('show','-s','--format=%ae',c).lower()!=data['author'] for c in commits):
        raise RuntimeError('rewritten series has unexpected author')
    message_errors = validate_commits(commits, Path.cwd())
    if message_errors:
        raise RuntimeError('rewritten commit messages exceed 80 characters:\n' + '\n'.join(message_errors))
    check_remote(data['remote'],data['host'],data['head_repo'],data['head_repo'] != data['base_repo'])
    push_url=git('remote','get-url','--push',data['remote'])
    remote_sha=run('git','ls-remote','--heads',push_url,data['remote_branch']).split('\t')[0]
    if remote_sha!=data['remote_sha']:
        raise RuntimeError('remote SHA changed; refusing push')
    pr=json.loads(run('gh','pr','view',data['pr_url'],'--json','number,url,headRefName,headRefOid,isCrossRepository,headRepository'))
    host,base_repo,head_repo=pr_identity(pr)
    if (pr['number'] != data['pr'] or host != data['host'] or base_repo != data['base_repo']
            or head_repo != data['head_repo'] or pr['headRefName'] != data['remote_branch']
            or pr['headRefOid'] != data['remote_sha']):
        raise RuntimeError('PR head ownership changed')
    if data.get('mode') == 'regroup':
        live = series_assessment.live_snapshot(data['pr_url'], Path.cwd())
        if (live['final_head'] != data['remote_sha'] or live['target_tip'] != data['target_tip']
                or live['target_branch'] != data['target_branch']):
            raise RuntimeError('PR head or target tip changed; cleanup incomplete')
    print(f"verified {len(commits)} commits; original tree preserved; remote SHA {remote_sha}")

def assess(data, output, reasons_file):
    if data.get('mode') != 'regroup':
        raise ValueError('assessment requires prepare --mode regroup')
    if git('status','--porcelain=v1') or git('symbolic-ref','--short','HEAD') != data['branch']:
        raise RuntimeError('assessment requires the clean prepared branch')
    final = git('rev-parse','HEAD')
    live = series_assessment.live_snapshot(data['pr_url'], Path.cwd())
    if final != live['final_head']:
        raise RuntimeError('publication is unverified; cleanup incomplete')
    if final == data['original_head']:
        verify(data)
        outcome = 'no_op'
    else:
        if data.get('publication') != 'published' or data.get('final_head') != final:
            raise RuntimeError('guarded publication is unconfirmed; reconcile recovery state')
        outcome = 'published'
    artifact = dict(schema_version=1, pr_url=data['pr_url'], head_repo=data['head_repo'],
                    reviewed_head=data['original_head'], reviewed_tree=data['original_tree'],
                    target_tip=data['target_tip'], target_branch=data['target_branch'],
                    final_head=final, outcome=outcome, commit_count=live['commit_count'],
                    reasons=json.loads(Path(reasons_file).read_text(encoding='utf-8')))
    series_assessment.validate(artifact)
    result = series_assessment.check(artifact, live, final)
    if result != 'clean':
        raise RuntimeError(f'{result}; cleanup incomplete')
    path = series_assessment.save(output, artifact, Path.cwd())
    data['assessment'] = str(path)
    write_state(data)
    history = state_path().parent / 'pr-split-history'
    history.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(dir=history, prefix=f"{data['original_head']}-", suffix='.json')
    os.close(fd)
    os.replace(state_path(), name)
    print(json.dumps({'assessment':str(path), 'recovery_state':name, **artifact}, sort_keys=True))

def reconcile(data):
    if data.get('mode') != 'regroup' or data.get('publication') not in {'pending','published'}:
        raise RuntimeError('no cleanup publication to reconcile')
    if git('status','--porcelain=v1') or git('symbolic-ref','--short','HEAD') != data['branch']:
        raise RuntimeError('reconciliation requires the clean prepared branch')
    check_remote(data['remote'],data['host'],data['head_repo'],data['head_repo'] != data['base_repo'])
    remote_sha=run('git','ls-remote','--heads',git('remote','get-url','--push',data['remote']),
                   data['remote_branch']).split('\t')[0]
    live = series_assessment.live_snapshot(data['pr_url'], Path.cwd())
    if (remote_sha != data['final_head'] or live['final_head'] != data['final_head']
            or git('rev-parse','HEAD') != data['final_head']
            or live['head_repo'] != data['head_repo']
            or live['final_tree'] != data['original_tree']
            or live['target_tip'] != data['target_tip'] or live['target_branch'] != data['target_branch']):
        raise RuntimeError('publication is uncertain; retain recovery state; cleanup incomplete')
    data['publication'] = 'published'
    write_state(data)
    print('verified published cleanup; recovery state retained')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('command',choices=['prepare','verify','push','assess','reconcile'])
    ap.add_argument('--base')
    ap.add_argument('--pr', help='PR URL; required for a fork PR')
    ap.add_argument('--mode', choices=['split','regroup'], default='split', help='Preparation mode')
    ap.add_argument('--output', help='Assessment JSON path outside the repository')
    ap.add_argument('--reasons-file', help='JSON list explaining the final commit boundaries')
    ap.add_argument('--allow-force-push', action='store_true',
                    help='Required when the user requests publishing rewritten commits')
    ap.add_argument('--review-state', help='Completed split review loop token required for push')
    args=ap.parse_args()
    if args.command=='prepare':
        if not args.base: raise ValueError('--base is required')
        prepare(args.base,args.pr,args.mode); return
    if args.command=='push' and not args.allow_force_push:
        raise ValueError('push requires --allow-force-push after a request to publish rewritten commits')
    if args.command in {'push','assess'}:
        if not args.review_state:
            raise ValueError(f'{args.command} requires --review-state from a completed split review loop')
        require_complete(args.review_state, 'split')
    path=state_path()
    data=json.loads(path.read_text())
    if args.command=='reconcile':
        reconcile(data); return
    if args.command=='assess':
        if not args.output or not args.reasons_file:
            raise ValueError('assess requires --output and --reasons-file')
        assess(data,args.output,args.reasons_file); return
    verify(data)
    if args.command=='push':
        if data.get('mode') == 'regroup':
            if data.get('publication'):
                raise RuntimeError('cleanup push already attempted; reconcile before any retry')
            if git('rev-parse','HEAD') == data['original_head']:
                raise RuntimeError('unchanged cleanup series requires a no-op assessment, not a push')
            data.update(publication='pending',final_head=git('rev-parse','HEAD'))
            write_state(data)
        run('git','push',f"--force-with-lease={data['remote_branch']}:{data['remote_sha']}",
            data['remote'],f"HEAD:refs/heads/{data['remote_branch']}")
        if data.get('mode') == 'regroup':
            reconcile(data)
        else:
            path.unlink()
        print('pushed rewritten PR series')

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'pr split: {exc}',file=sys.stderr); sys.exit(1)
