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
from shared.github_reads import run
from shared.pr_ownership import classify


def route(pr, *, api=None):
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
    settings = config.effective(repository)['settings']
    return {'pr': canonical, 'repository': repository, 'head': pull['head']['sha'],
            **classify(host, pull.get('user'), api('user'), settings['identities'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pr', required=True)
    args = parser.parse_args()
    print(json.dumps(route(args.pr), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
