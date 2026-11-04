#!/usr/bin/env python3
"""Durable description edits, replies and exact-lease pushes; no workflow tokens."""
import argparse
import json
import fcntl
import tempfile
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
from shared import publication
from shared.diagnostics import Diagnostic
from shared.github_publications import Handler, KINDS


def run(argv, *, payload=None, cwd=None, json_output=True):
    result = subprocess.run(argv, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True, cwd=cwd, timeout=60)
    return json.loads(result.stdout) if json_output else result.stdout.strip()


def endpoint(pr):
    url = urlsplit(pr)
    parts = url.path.strip('/').split('/')
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or len(parts) != 4 or parts[2] != 'pull'
            or not parts[3].isdigit() or int(parts[3]) <= 0 or any(not v for v in parts[:2])):
        raise ValueError('Canonical PR URL required')
    return url.netloc, f'repos/{parts[0]}/{parts[1]}', parts[3]


def api(pr, path, *, payload=None):
    host, _, _ = endpoint(pr)
    argv = ['gh','api','--hostname',host,path]
    if payload is not None:
        argv += ['--method','POST','--input','-']
    return run(argv,payload=payload)


def metadata(pr):
    return run(['gh','pr','view',pr,'--json','url,headRefOid,title,body'])


def remote_head(request):
    # Exact one-ref read; pushes always use the captured lease.
    ref = 'refs/heads/' + request['branch']
    value = run(['git','ls-remote','--refs',request['remote'],ref],cwd=request['checkout'],json_output=False)
    rows = [line.split() for line in value.splitlines() if line.strip()]
    if not rows and request.get('create_branch') is True:
        return None
    if len(rows) != 1 or rows[0][1] != ref:
        raise ValueError('Remote branch is absent or ambiguous')
    return rows[0][0]


def replies(request):
    pr = request['pr']
    host, root, number = endpoint(pr)
    route = f'{root}/pulls/{number}/comments' if request['kind']=='reply-inline' else f'{root}/issues/{number}/comments'
    pages = run(['gh','api','--hostname',host,route,'--paginate','--slurp'])
    items = [item for page in pages for item in page]
    if request['kind']=='reply-inline':
        items = [item for item in items if item.get('in_reply_to_id') == request['parent']]
    return items


def _prepare(request, directory=None):
    kind = request.get('kind')
    if kind not in {'edit-pr','reply-inline','reply-comment','push'} | KINDS:
        raise ValueError('Unsupported publication kind')
    request = dict(request)
    if kind in KINDS:
        expected = handler(request).prepare()
        target = request['pr']
    elif kind=='push':
        if not all(isinstance(request.get(k),str) and request[k] for k in ('checkout','remote','branch','head')):
            raise ValueError('Push requires checkout, verified remote, branch, head and expected_head')
        run(['git','check-ref-format','refs/heads/'+request['branch']],json_output=False)
        if not request['remote'].startswith(('https://','ssh://','git@')):
            raise ValueError('Use the verified exact remote URL, not a mutable remote alias')
        request.setdefault('expected_head',None)
        heads = [request['head']]
        if request.get('expected_head') is not None:
            heads.append(request['expected_head'])
        elif request.get('create_branch') is not True:
            raise ValueError('An absent lease requires explicit create_branch:true')
        if not all(isinstance(v,str) and len(v)==40 and all(c in '0123456789abcdef' for c in v) for v in heads):
            raise ValueError('Push requires full captured SHAs')
        if remote_head(request) != request['expected_head']:
            raise ValueError('Remote lease changed')
        request['tree'] = run(['git','rev-parse',request['head']+'^{tree}'],cwd=request['checkout'],json_output=False)
        expected = {'head':request['expected_head']}
        target = request['remote']+'#'+request['branch']
    else:
        endpoint(request['pr'])
        expected = metadata(request['pr'])
        if expected.get('url','').lower() != request['pr'].lower():
            raise ValueError('PR identity changed')
        if request.get('head') != expected['headRefOid']:
            raise ValueError('PR head changed')
        target = request['pr']
        if kind=='edit-pr':
            fields = request['fields']
            if not isinstance(fields,dict) or not fields or set(fields)-{'title','body'} or any(not isinstance(v,str) for v in fields.values()):
                raise ValueError('Edit requires reviewed title/body strings')
            if 'title' in fields and (not fields['title'].strip() or '\n' in fields['title']):
                raise ValueError('Title must be a nonempty single line')
        else:
            if not isinstance(request.get('body'),str) or not request['body'].strip():
                raise ValueError('Reply body required')
            if kind=='reply-inline' and (type(request.get('parent')) is not int or request['parent']<=0):
                raise ValueError('Inline source comment id required')
            # Record actor and preexisting IDs so reconciliation never attributes an old reply.
            request['actor'] = api(request['pr'],'user')['login']
            existing = replies(request)
            expected['reply_ids'] = [item['id'] for item in existing]
            matches = [item for item in existing if item.get('body')==request['body']
                       and item.get('user',{}).get('login')==request['actor']]
            if len(matches)>1:
                raise ValueError('Existing identical replies are ambiguous; inspect before sending')
            if matches:
                request['existing_reply'] = matches[0]['id']
    return publication.prepare(kind,target,expected,request,
        [sys.executable,str(Path(__file__).resolve()),'reconcile'],repository=request.get('checkout'), directory=directory)


def handler(request):
    return Handler(request, api=api, metadata=metadata, run=run, endpoint=endpoint)


def prepare(request):
    if not isinstance(request,dict):
        raise ValueError('Publication request must be an object')
    # Recover even if the previous agent lost the printed operation path. The
    # semantic payload is domain-owned; no workflow run/cycle participates.
    from shared.workflow_storage import operations_root
    base = operations_root(repository=request.get('checkout'))
    base.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not isinstance(request,dict):
        raise ValueError('Publication request must be an object')
    request = dict(request)
    if 'pr' in request:
        endpoint(request['pr'])
        request['pr'] = request['pr'].rstrip('/').lower()
    target = request.get('pr') or (request.get('remote','')+'#'+request.get('branch',''))
    scope = base/publication.digest(target)
    scope.mkdir(parents=True,exist_ok=True,mode=0o700)
    payload_hash = publication.digest(request)
    with (scope/'.prepare.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        for path in sorted(scope.glob('*/*.json')):
            retained = publication.load(path)
            approved = retained['request']['approved']
            if approved.get('_payload_hash') == payload_hash:
                return {'operation':str(path),**retained,'reused_intent':True}
            target = request.get('pr') or (request.get('remote','')+'#'+request.get('branch',''))
            if retained['request']['target'] == target and retained['status']=='pending':
                raise ValueError('Uncertain publication on this target requires reconciliation: '+str(path))
        directory = Path(tempfile.mkdtemp(prefix='operation-',dir=scope))
        return _prepare({**request, '_payload_hash': payload_hash},directory)


def publish(path, *, reconcile=False):
    retained = publication.load(path)
    kind = retained['request']['kind']
    if kind not in KINDS | {'edit-pr','reply-inline','reply-comment','push'}:
        raise ValueError('Unsupported publication kind')
    special = handler(retained['request']['approved']) if kind in KINDS else None
    def check(value, verified):
        request, expected = value['request']['approved'], value['request']['expected']
        if special:
            return special.check(expected,verified)
        if kind=='push':
            tree = run(['git','rev-parse',request['head']+'^{tree}'],cwd=request['checkout'],json_output=False)
            if tree!=request['tree'] or remote_head(request)!=(request['head'] if verified else request['expected_head']):
                raise ValueError('Captured push tree or exact remote lease changed')
        elif kind=='edit-pr':
            live = metadata(request['pr'])
            wanted = {**expected, **request['fields']} if verified else expected
            if live!=wanted:
                raise ValueError('PR head/title/body changed; reconcile before preparing any replacement')
        else:
            live = metadata(request['pr'])
            if live != {k:v for k,v in expected.items() if k!='reply_ids'}:
                raise ValueError('PR changed; retained reply receipt does not authorize another publication')
            if not verified and [i['id'] for i in replies(request)] != expected['reply_ids']:
                raise ValueError('Reply conversation changed; refresh before publication')
    def send(value):
        request = value['request']['approved']
        if special:
            return special.send(value['request']['expected'],value['operation_id'])
        if kind=='push':
            return run(['git','push','--force-with-lease=refs/heads/'+request['branch']+':'+(request['expected_head'] or ''),
                        request['remote'],request['head']+':refs/heads/'+request['branch']],
                       cwd=request['checkout'],json_output=False)
        if kind=='edit-pr':
            if all(value['request']['expected'].get(k)==v for k,v in request['fields'].items()):
                return {'already_satisfied':True}
            argv = ['gh','pr','edit',request['pr']]
            if 'title' in request['fields']:argv += ['--title',request['fields']['title']]
            if 'body' in request['fields']:
                body = Path(path).with_name(Path(path).stem+'-body.txt')
                from shared.private_state import atomic_text
                atomic_text(body,request['fields']['body'])
                argv += ['--body-file',str(body)]
            return run(argv,json_output=False)
        if request.get('existing_reply'):
            return {'already_satisfied':True}
        _, root, number = endpoint(request['pr'])
        route = (f'{root}/pulls/{number}/comments/{request["parent"]}/replies' if kind=='reply-inline'
                 else f'{root}/issues/{number}/comments')
        return api(request['pr'],route,payload={'body':request['body']})
    def observe(value):
        request, expected = value['request']['approved'], value['request']['expected']
        if special:
            return special.observe(expected)
        if kind=='push':
            if remote_head(request)!=request['head']:raise ValueError('Push not verified')
            return {'head':request['head']}
        if kind=='edit-pr':
            live = metadata(request['pr'])
            if live!={**expected,**request['fields']}:raise ValueError('Edit not verified')
            return live
        matches = [item for item in replies(request) if (item['id']==request['existing_reply'] if request.get('existing_reply')
                       else item['id'] not in expected['reply_ids'])
                   and item.get('body')==request['body'] and item.get('user',{}).get('login')==request['actor']]
        if len(matches)!=1:raise ValueError('Reply absent or ambiguous; do not resend')
        return {'id':matches[0]['id'],'url':matches[0].get('html_url')}
    scope = Path(path).resolve().parent.parent
    with (scope/'.dispatch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        for other in scope.glob('*/*.json') if retained['status']=='prepared' and not reconcile else []:
            if other.resolve()!=Path(path).resolve() and publication.load(other)['status']=='pending':
                raise ValueError('Reconcile the pending publication before another write: '+str(other))
        return publication.execute(path,kind,check=check,send=send,observe=observe,reconcile=reconcile,
            reconcile_argv=[sys.executable,str(Path(__file__).resolve()),'reconcile'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('prepare');c.add_argument('--file',type=Path,required=True)
    for action in ('publish','reconcile'):
        c=sub.add_parser(action);c.add_argument('--operation',type=Path,required=True)
    a=p.parse_args()
    result=prepare(json.loads(a.file.read_text())) if a.action=='prepare' else publish(a.operation,reconcile=a.action=='reconcile')
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':
    try:main()
    except Diagnostic as exc:
        print(json.dumps(exc.envelope()));sys.exit(2)
    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)}));sys.exit(2)
