#!/usr/bin/env python3
"""Identify a PR's owner and select the fix or incoming-review workflow; read-only."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import config
import invocation
import skill_config
from shared.github_reads import run
from shared.pr_ownership import classify


def route(pr, *, upstream=None, api=None, options=None):
    parsed = urlsplit(pr)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port or parsed.query or parsed.fragment or len(parts) != 4
            or parts[2] != 'pull' or not parts[3].isdigit() or int(parts[3]) <= 0
            or any(not v for v in parts[:2])):
        raise ValueError('Canonical PR URL required')
    host = parsed.hostname.lower()
    repository = host + '/' + '/'.join(parts[:2]).lower()
    canonical = 'https://' + repository + '/pull/' + str(int(parts[3]))
    if api is None:
        def api(path):
            result = run(['gh', 'api', '--hostname', host, path],
                         text=True, capture_output=True)
            if result.returncode:
                raise ValueError(result.stderr.strip() or 'GitHub identity is unavailable')
            return json.loads(result.stdout)
    pull = api('repos/' + '/'.join(parts[:2]) + '/pulls/' + parts[3])
    if (not isinstance(pull, dict) or pull.get('html_url', '').lower() != canonical
            or pull.get('number') != int(parts[3])
            or not isinstance(pull.get('head'), dict)
            or not isinstance(pull['head'].get('sha'), str)
            or not re.fullmatch('[0-9a-f]{40}', pull['head']['sha'])):
        raise ValueError('PR identity is unavailable or inconsistent')
    settings = config.effective(repository, upstream)['settings']
    owned = skill_config.effective('dk-workflows:dk-pr-iter', repository, upstream, options=options)
    ownership = classify(host, pull.get('user'), api('user'), owned['options']['personal_accounts'])
    route = 'own_pr' if ownership['ownership'] == 'mine' else 'other_pr'
    process = settings['processes']['pr-iter']['routes'][route]
    call = config.select(settings, process)
    if options and 'routes' in options or owned['provenance']['routes'] not in owned['sources'][:2]:
        call = skill_config.configured_call(owned['options']['routes'][route], repository, upstream)
        matches = [name for name, entry in settings['processes'].items()
                   if invocation.normalize(entry['coordinator']).get('skill') == call.get('skill')]
        process = matches[0] if len(matches) == 1 else call.get('skill', process)
    skill = call.get('skill')
    return {'pr': canonical, 'repository': repository, 'head': pull['head']['sha'],
            **ownership, 'process': process, 'skill': skill, 'invocation': call}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pr', required=True)
    parser.add_argument('--upstream', help='HOST/OWNER/REPO used for inherited workflow settings')
    parser.add_argument('--options', type=Path, help='Invocation-local routing options')
    args = parser.parse_args()
    print(json.dumps(route(args.pr, upstream=args.upstream,
                           options=json.loads(args.options.read_text()) if args.options else None), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
