#!/usr/bin/env python3
"""Manage private settings for bundled PR skills."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from shared import ci_exceptions, pr_size, pr_partition_policy
from shared.repository_scope import identities, repository
from config_requests import apply_bound
from settings import domain_load, domain_path
import commit_policy

import invocation


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
    # A request ID belongs to each mutating subcommand, including partition choices.
    for domain_parser in sub.choices.values():
        for action_group in domain_parser._subparsers._group_actions:
            for action_name, action_parser in action_group.choices.items():
                if action_name not in {'show', 'list'}:
                    action_parser.add_argument('--request-id')
    a=p.parse_args(arguments)
    if a.action in {'show', 'list'}:
        if a.command == 'pr-partition':
            return pr_partition_policy.load(a.pr)
        if a.command == 'ci-exceptions':
            from shared.flaky_actions import pr_repository
            key = 'remote:' + pr_repository(a.pr) if a.pr else repository(a.cwd)
            return ci_exceptions.load(key, a.scope)
        chain = identities(a.cwd, a.pr)
        return pr_size.load(chain, a.scope) if a.command == 'pr-size' else domain_load(a.command, chain, a.scope)
    operation = {'domain': a.command, 'action': a.action}
    if a.command == 'pr-partition':
        operation.update(reason=a.reason, source=a.source_file.read_text() if a.source_file else a.source)
        if a.action == 'instructions':
            operation['instructions'] = a.file.read_text() if a.file else a.text
    else:
        operation['scope'] = a.scope
        if a.command == 'ci-exceptions':
            if a.action == 'add':
                operation['value'] = {k: getattr(a, k) for k in ('kind', 'workflow', 'job', 'test', 'signature', 'reason')}
            elif a.action == 'remove':
                operation['id'] = a.id
        elif a.command == 'pr-size':
            if a.action == 'set':
                operation['value'] = {k: getattr(a, k) for k in pr_size.DEFAULTS if getattr(a, k, None) is not None}
            else:
                operation['keys'] = a.keys
        elif a.action == 'set':
            operation['value'] = a.file.read_text() if a.command == 'completion' else json.loads(a.file.read_text())
    context = {'checkout': a.cwd, **({'pr': a.pr} if a.pr else {})}
    apply_bound(a.request_id, operation, context)
    if a.command == 'pr-partition':
        return pr_partition_policy.load(a.pr)
    if a.command == 'pr-size':
        return pr_size.load(identities(a.cwd, a.pr))
    if a.command == 'ci-exceptions':
        from shared.flaky_actions import pr_repository
        key = 'remote:' + pr_repository(a.pr) if a.pr else repository(a.cwd)
        return ci_exceptions.load(key)
    target = domain_path(a.command, 'user' if a.scope == 'user' else identities(a.cwd, a.pr)[0])
    return {'action': a.action, 'file': str(target)}

if __name__=='__main__':
    try:print(json.dumps(main(),sort_keys=True))
    except (ValueError,KeyError,OSError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr);sys.exit(2)
