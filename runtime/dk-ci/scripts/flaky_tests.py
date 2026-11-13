#!/usr/bin/env python3
"""Discover/list/synchronize private issue-backed GitHub Actions flakes."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
from shared.flaky_tests import Registry, identity
from shared.flaky_actions import GitHub, discover
from shared.pr_flakes import compact
import skill_config


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='HOST/OWNER/REPO (PR base repository)')
    parser.add_argument('--cwd', default='.', help='repository path; private data must be outside it')
    parser.add_argument('--options', type=Path, help='Invocation-local skill options JSON')
    parser.add_argument('--upstream')
    parser.add_argument('--compact', action='store_true', help='counts, test identities and evidence references without proof history')
    parser.add_argument('--offset', type=int, default=0, help='compact result offset')
    parser.add_argument('--limit', type=int, default=20, help='compact result page size (1..100)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='read saved entries/candidates without GitHub calls; not fresh verification')
    listing = sub.add_parser('list', help='refresh linked issues and list active/candidate/inactive entries')
    listing.add_argument('--history', action='store_true', help='include named-test observations')
    sub.add_parser('sync', help='refresh every linked issue directly and reverify entries')
    scan = sub.add_parser('discover', help='scan bounded Actions history and recently updated open issues')
    scan.add_argument('--days', type=int)
    scan.add_argument('--max-runs', type=int)
    scan.add_argument('--max-issues', type=int)
    scan.add_argument('--targets', type=Path, help='JSON list of exact workflow/job/test/signature contexts')
    show = sub.add_parser('show', help='refresh and read one exact test context')
    show.add_argument('--workflow', required=True)
    show.add_argument('--job', required=True)
    show.add_argument('--test', required=True)
    show.add_argument('--signature', required=True)
    show.add_argument('--history', action='store_true', help='include only this test context\'s observations')
    args = parser.parse_args()
    if args.offset < 0 or not 1 <= args.limit <= 100:
        parser.error('offset must be nonnegative and limit must be between 1 and 100')
    try:
        registry = Registry(args.repo, args.cwd)
        api = GitHub(registry.repo)
        if args.command == 'discover':
            local = json.loads(args.options.read_text()) if args.options else {}
            for argument, key in [('days', 'lookback_days'), ('max_runs', 'max_runs'), ('max_issues', 'max_issues')]:
                if getattr(args, argument) is not None:
                    local[key] = getattr(args, argument)
            options = skill_config.for_context('dk-workflows:dk-ci-flakes', repository=args.repo,
                                                upstream=args.upstream, options=local)['options']
            kwargs = {}
            if args.targets:
                targets = json.loads(args.targets.read_text())
                if (not isinstance(targets, list) or not targets or any(not isinstance(item, dict)
                        or any(not isinstance(value, str) or not value.strip() for value in identity(item))
                        for item in targets)):
                    raise ValueError('targets must be a nonempty list of exact workflow/job/test/signature contexts')
                kwargs['targets'] = targets
            output = discover(api, registry, options['lookback_days'], options['max_runs'], options['max_issues'], **kwargs)
        elif args.command == 'status':
            output = {'entries': list(registry.load().get('entries', {}).values()), 'stored_only': True}
        else:
            target = ({key: getattr(args, key) for key in ('workflow', 'job', 'test', 'signature')}
                      if args.command == 'show' else None)
            output = registry.synchronize([], api.issue, targets=[target] if target else None)
            if args.command in {'list', 'show'} and args.history:
                saved = registry.load()
                output['observations'] = [item for item in saved.get('observations', [])
                    if target is None or (identity(item)[:3] == identity(target)[:3]
                        and (item.get('outcome') != 'fail' or item.get('signature') == target['signature']))]
        saved = registry.load()
        # Unsupported or unlinked failures stay visible as candidates.
        linked = {tuple(identity(entry)) for entry in saved.get('entries', {}).values()}
        output['unlinked_candidates'] = [o for o in saved.get('observations', [])
                                        if o['outcome'] == 'fail' and tuple(identity(o)) not in linked]
        if args.command == 'show':
            output['unlinked_candidates'] = [item for item in output['unlinked_candidates']
                                            if identity(item) == identity(target)]
        output['registry_file'] = str(registry.path)
        if args.compact:
            output = compact(output, offset=args.offset, limit=args.limit)
        print(json.dumps(output, sort_keys=True))
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'flaky-tests: {exc}\n')


if __name__ == '__main__':
    main()
