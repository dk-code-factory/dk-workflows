#!/usr/bin/env python3
"""Reuse successful main validation only for the exact tagged commit."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode


def validated_run(repository, head):
    query = urlencode({'head_sha': head, 'branch': 'main', 'event': 'push',
                       'status': 'success', 'per_page': 1})
    endpoint = f'repos/{repository}/actions/workflows/ci.yml/runs?{query}'
    try:
        response = subprocess.run(['gh', 'api', endpoint], text=True,
                                  capture_output=True, timeout=30, check=True)
        runs = json.loads(response.stdout)['workflow_runs']
        if not isinstance(runs, list):
            return None
        for run in runs:
            if (isinstance(run, dict) and run.get('head_sha') == head
                    and run.get('head_branch') == 'main' and run.get('event') == 'push'
                    and run.get('status') == 'completed' and run.get('conclusion') == 'success'
                    and type(run.get('id')) is int and run['id'] > 0):
                return run
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        print('Validation evidence unavailable; running the complete suite.', file=sys.stderr)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--head', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        parser.error('--repository must be OWNER/REPO')
    if not re.fullmatch(r'[0-9a-f]{40}', args.head):
        parser.error('--head must be a full commit SHA')
    run = validated_run(args.repository, args.head)
    reuse = 'true' if run else 'false'
    if output := os.environ.get('GITHUB_OUTPUT'):
        with Path(output).open('a') as stream:
            stream.write(f'reuse={reuse}\n')
    if run:
        print(f'Reusing successful main Validate run {run["id"]} for {args.head}.')
    else:
        print(f'No successful main validation confirmed for {args.head}; running tests.')


if __name__ == '__main__':
    main()
