#!/usr/bin/env python3
"""Manage private settings for bundled PR skills."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import fcntl
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from shared import ci_exceptions, pr_size, pr_partition_policy
from shared.repository_scope import identities, repository
from shared.private_state import read, write, atomic_text
import commit_policy


def main(arguments=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cwd',default='.')
    p.add_argument('--pr')
    sub=p.add_subparsers(dest='command',required=True)
    pr_partition_policy.add_parser(sub)
    c=sub.add_parser('pr-size');s=c.add_subparsers(dest='action',required=True)
    for action in ('list','set','reset'):
        a=s.add_parser(action);a.add_argument('--scope',choices=['repo','user','effective'],default='effective' if action=='list' else 'repo')
        if action=='set':
            a.add_argument('--added-lines-threshold',type=int)
            a.add_argument('--evacuate-unrelated',choices=pr_size.MODES)
            a.add_argument('--split-related',choices=pr_size.MODES)
        if action=='reset':a.add_argument('keys',nargs='*')
    c=sub.add_parser('ci-exceptions');s=c.add_subparsers(dest='action',required=True)
    for action in ('list','add','remove','reset'):
        a=s.add_parser(action);a.add_argument('--scope',choices=['repo','user','effective'],default='effective' if action=='list' else 'repo')
        if action=='add':
            a.add_argument('--kind',choices=['test','pipeline'],required=True)
            a.add_argument('--workflow',required=True);a.add_argument('--reason',required=True)
            for key in ('job','test','signature'):a.add_argument('--'+key)
        if action=='remove':a.add_argument('id')
    for name in ('rules','drafting','completion','commit-policy'):
        c=sub.add_parser(name);s=c.add_subparsers(dest='action',required=True)
        for action in ('show','set','reset'):
            a=s.add_parser(action);a.add_argument('--scope',choices=['repo','user','effective'],default='effective' if action=='show' else 'repo')
            if action=='set':a.add_argument('--file',type=Path,required=True)
    a=p.parse_args(arguments)
    if a.command=='pr-partition':return pr_partition_policy.dispatch(a,a.pr)
    if a.command=='ci-exceptions':
        # Exceptions belong to the PR base repository, even in a fork checkout.
        from shared.flaky_actions import pr_repository
        key='remote:'+pr_repository(a.pr) if a.pr else repository(a.cwd)
        if a.action=='list':return ci_exceptions.load(key,a.scope)
        value={k:getattr(a,k) for k in ('kind','workflow','job','test','signature','reason')} if a.action=='add' else None
        return ci_exceptions.configure(key,a.action,scope=a.scope,value=value,entry_id=getattr(a,'id',None))
    chain=identities(a.cwd,a.pr)
    if a.command=='commit-policy' and a.action=='show':return commit_policy.load(chain,a.scope)
    if a.command=='pr-size':
        if a.action=='list':return pr_size.load(chain,a.scope)
        settings={k:getattr(a,k) for k in pr_size.DEFAULTS if getattr(a,k,None) is not None}
        return pr_size.configure(chain,a.action,scope=a.scope,settings=settings,keys=getattr(a,'keys',None))
    data=ci_exceptions.data_root()
    def location(key):
        if a.command=='commit-policy':return commit_policy.path(key)
        if a.command=='completion':
            base=data.parent/'dk-pr-iter/completion-prompts'
            return base/'user.txt' if key=='user' else base/'repositories'/(hashlib.sha256(key.encode()).hexdigest()[:24]+'.txt')
        folder=(data/'user' if a.command=='rules' else data) if key=='user' else data/hashlib.sha256(key.encode()).hexdigest()[:24]
        return folder/(a.command+'.json')
    if a.action=='show':
        levels=chain+['user'] if a.scope=='effective' else ['user' if a.scope=='user' else chain[0]]
        if a.command=='drafting':
            merged, sources = {}, {}
            for key in reversed(levels):
                target=location(key)
                if target.exists():
                    for role,value in read(target,{}).items():
                        merged[role]=value
                        sources[role]={'source':key,'file':str(target)}
            return {'value':merged,'sources':sources}
        for key in levels:
            target=location(key)
            if target.exists():
                value={'prompt':target.read_text()} if a.command=='completion' else read(target,None)
                if a.command=='completion' and (not value['prompt'].strip() or '\x00' in value['prompt']):
                    raise ValueError('Malformed completion prompt; repair it rather than falling back')
                if a.command=='rules' and not value.get('rules'):continue
                return {'value':value,'source':key,'file':str(target)}
        if a.command=='completion':
            target=Path(__file__).resolve().parents[1]/'docs/pr/default-completion.md'
            return {'value':{'prompt':target.read_text()},'source':'default','file':str(target)}
        return {'value':None,'source':'default'}
    if a.scope=='effective':raise ValueError('Mutations require repo or user scope')
    target=location('user' if a.scope=='user' else chain[0])
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with target.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if a.action=='reset':target.unlink(missing_ok=True)
        else:
            if a.command=='completion':
                text=a.file.read_text()
                if not text.strip() or '\x00' in text:raise ValueError('Nonempty completion prompt text required')
                atomic_text(target,text)
            else:
                value=json.loads(a.file.read_text())
                if a.command=='commit-policy':value=commit_policy.validate(value)
                if not isinstance(value,dict) or not value:raise ValueError('Nonempty domain settings object required')
                write(target,value)
    return {'action':a.action,'file':str(target)}

if __name__=='__main__':
    try:print(json.dumps(main(),sort_keys=True))
    except (ValueError,KeyError,OSError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr);sys.exit(2)
