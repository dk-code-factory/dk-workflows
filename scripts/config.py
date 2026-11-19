#!/usr/bin/env python3
"""Read, set and validate workflow settings."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))

def main():
    from state import read
    from config_requests import apply_bound
    import config
    import invocation
    import skill_config
    import settings

    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository',required=True)
    p.add_argument('--upstream')
    p.add_argument('--checkout', type=Path, default=Path.cwd())
    p.add_argument('--pr')
    sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('show');c.add_argument('--expanded',action='store_true')
    c.add_argument('--section', action='append', choices=settings.SECTIONS)
    for action in ('set','reset'):
        c=sub.add_parser(action);c.add_argument('--scope',choices=['repo','user'],default='repo')
        c.add_argument('--request-id')
        if action=='set':c.add_argument('--file',type=Path,required=True)
    c=sub.add_parser('preflight');c.add_argument('--available',type=Path,required=True)
    c.add_argument('--capabilities',type=Path)
    c.add_argument('--native-reviews', type=Path, help='Resolved review contracts keyed by occurrence location')
    c.add_argument('--process')
    for action in ('skill-show', 'skill-set', 'skill-reset', 'skill-plan', 'skill-preflight'):
        c = sub.add_parser(action)
        c.add_argument('--skill', required=True, help='Fully qualified plugin:skill')
        c.add_argument('--options', type=Path, help='Invocation-local options; never saved by a read')
        if action in {'skill-plan', 'skill-preflight'}:
            c.add_argument('--workflow', type=Path, help='Live owning workflow skill, task index and invocation options')
        if action in {'skill-set', 'skill-reset'}:
            c.add_argument('--scope', choices=['repo', 'user'], default='repo')
            c.add_argument('--request-id')
        if action == 'skill-set':
            c.add_argument('--file', type=Path, required=True)
        if action == 'skill-plan':
            c.add_argument('--kind', choices=['tasks', 'finalizers', 'reviewers'], required=True)
            c.add_argument('--resolved', action='store_true', help='Include inherited invocations and effective leaf options')
        if action == 'skill-preflight':
            c.add_argument('--stage', choices=['alternatives', 'review', 'finalizers'])
            c.add_argument('--available', type=Path, required=True)
            c.add_argument('--capabilities', type=Path)
            c.add_argument('--native-reviews', type=Path, help='Resolved review contracts keyed by occurrence location')
    for action in ('behavior-show', 'behavior-set', 'behavior-reset'):
        c = sub.add_parser(action)
        c.add_argument('--skill', required=True)
        c.add_argument('--role', default='default')
        c.add_argument('--options', type=Path)
        c.add_argument('--behavior', type=Path, help='Invocation-local role map; never saved by reads')
        if action == 'behavior-show':
            c.add_argument('--section', choices=['default', 'effective', 'sources'])
            c.add_argument('--roles', action='store_true', help='List shipped role names without prompt bodies')
        else:
            c.add_argument('--scope', choices=['repo', 'user'], default='repo')
            c.add_argument('--request-id')
            if action == 'behavior-set':
                c.add_argument('--file', type=Path, required=True, help='Object with override and/or append fields')
    a=p.parse_args()
    if a.action.startswith('behavior-'):
        import behavior
        if a.action != 'behavior-show':
            operation = {'domain': 'behavior', 'action': a.action.removeprefix('behavior-'),
                         'scope': a.scope, 'skill': a.skill, 'role': a.role}
            if a.action == 'behavior-set':
                operation['value'] = read(a.file)
            apply_bound(a.request_id, operation, {'repository': a.repository, 'checkout': str(a.checkout),
                **({'upstream': a.upstream} if a.upstream else {}), **({'pr': a.pr} if a.pr else {})})
        result = ({'skill': a.skill, 'roles': list(behavior.roles(a.skill))}
                  if a.action == 'behavior-show' and a.roles else
                  behavior.resolve(a.skill, a.role, a.repository, a.upstream,
                    behavior=read(a.behavior) if a.behavior else None,
                    options=read(a.options) if a.options else None,
                    section=a.section if a.action == 'behavior-show' else None))
        print(json.dumps(result, sort_keys=True))
        return
    if a.action.startswith('skill-'):
        invocation.skill_name(a.skill)
        local = read(a.options) if a.options else None
        if a.action in {'skill-set', 'skill-reset'}:
            operation = {'domain': 'skill', 'action': a.action.removeprefix('skill-'),
                         'scope': a.scope, 'skill': a.skill}
            if a.action == 'skill-set':
                operation['value'] = read(a.file)
            apply_bound(a.request_id, operation, {'repository': a.repository, 'checkout': str(a.checkout),
                                                  **({'upstream': a.upstream} if a.upstream else {}),
                                                  **({'pr': a.pr} if a.pr else {})})
        if a.action == 'skill-preflight':
            result = invocation.preflight(list(skill_config.calls(a.skill, a.repository, a.upstream, options=local, stage=a.stage,
                                                                  workflow=read(a.workflow) if a.workflow else None)),
                                          read(a.available), read(a.capabilities) if a.capabilities else None,
                                          native_reviews=read(a.native_reviews) if a.native_reviews else None)
        elif a.action == 'skill-plan':
            result = skill_config.plan(a.skill, a.kind, a.repository, a.upstream,
                                       options=local, resolved=a.resolved, workflow=read(a.workflow) if a.workflow else None)
        else:
            result = skill_config.effective(a.skill, a.repository, a.upstream, options=local)
        print(json.dumps(result, sort_keys=True))
        return
    if a.action=='show':
        result = (settings.load_sections(a.repository, checkout=a.checkout, pr=a.pr, upstream=a.upstream,
                                        sections=a.section) if a.section else
                  config.effective(a.repository,a.upstream,expanded=a.expanded))
    elif a.action=='preflight':result=config.preflight(
        config.effective(a.repository,a.upstream)['settings'],read(a.available),
        read(a.capabilities) if a.capabilities else None,process=a.process,
        native_reviews=read(a.native_reviews) if a.native_reviews else None)
    else:
        operation = {'domain': 'workflow', 'action': a.action, 'scope': a.scope}
        if a.action == 'set':
            operation['value'] = read(a.file)
        apply_bound(a.request_id, operation, {'repository': a.repository, 'checkout': str(a.checkout),
                                              **({'upstream': a.upstream} if a.upstream else {}),
                                              **({'pr': a.pr} if a.pr else {})})
        result={'path':str(config.path(a.repository if a.scope == 'repo' else None)), 'action':a.action}
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':
    os.umask(0o077)
    try:main()
    except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
        from shared.recovery_diagnostics import cli_failure
        print(json.dumps(cli_failure(exc, sys.argv[1:])));sys.exit(2)
