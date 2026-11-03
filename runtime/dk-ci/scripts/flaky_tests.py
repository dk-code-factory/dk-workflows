#!/usr/bin/env python3
"""Discover/list/synchronize private issue-backed GitHub Actions flakes."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared.flaky_tests import Registry
from shared.flaky_actions import GitHub, discover


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='HOST/OWNER/REPO (PR base repository)')
    parser.add_argument('--cwd', default='.', help='repository path; private data must be outside it')
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list', help='refresh linked issues and list active/candidate/inactive entries')
    listing.add_argument('--history', action='store_true', help='include legacy and named-test observations')
    sub.add_parser('sync', help='refresh every linked issue directly and reverify entries')
    scan = sub.add_parser('discover', help='scan bounded Actions history and recently updated open issues')
    scan.add_argument('--days', type=int, default=30)
    scan.add_argument('--max-runs', type=int, default=100)
    scan.add_argument('--max-issues', type=int, default=500)
    args = parser.parse_args()
    try:
        registry = Registry(args.repo, args.cwd)
        api = GitHub(registry.repo)
        if args.command == 'discover':
            output = discover(api, registry, args.days, args.max_runs, args.max_issues)
        else:
            output = registry.synchronize([], api.issue)
            if args.command == 'list' and args.history:
                saved = registry.load()
                output.update(legacy_tests=saved.get('tests', {}), observations=saved.get('observations', []))
        saved = registry.load()
        # Unsupported or unlinked failures stay visible as candidates.
        output['unlinked_candidates'] = [o for o in saved.get('observations', [])
            if o['outcome'] == 'fail' and not any(
                all(o.get(k) == e.get(k) for k in ('workflow', 'job', 'test', 'signature'))
                for e in saved.get('entries', {}).values())]
        output['registry_file'] = str(registry.path)
        print(json.dumps(output, sort_keys=True))
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'flaky-tests: {exc}\n')


if __name__ == '__main__':
    main()
