#!/usr/bin/env python3
"""Build a read-only approver selection handoff; native skills own discovery."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import skill_config


def selection(pr, head, *, upstream=None, publisher_options=None):
    parsed = urlsplit(pr)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port or parsed.query or parsed.fragment or len(parts) != 4
            or parts[2] != 'pull' or not parts[3].isdigit() or int(parts[3]) <= 0
            or any(not part for part in parts[:2])):
        raise ValueError('Canonical PR URL required')
    if not isinstance(head, str) or not re.fullmatch('[0-9a-f]{40}', head):
        raise ValueError('A full captured head SHA is required')
    repository = parsed.hostname.lower() + '/' + '/'.join(parts[:2]).lower()
    loaded = skill_config.effective('dk-workflows:dk-pr-publish', repository, upstream,
                                    options=publisher_options)
    override = loaded['options']['review_requests']
    return {'pr': 'https://' + repository + '/pull/' + str(int(parts[3])), 'head': head,
            'action': 'discover' if override is None else 'return',
            'source': 'automatic' if override is None else 'override',
            'selection': override, 'sources': loaded['sources'],
            'provenance': loaded['provenance']['review_requests']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pr', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--upstream')
    parser.add_argument('--publisher-options', type=Path, help='Invocation-local publisher options')
    args = parser.parse_args()
    options = json.loads(args.publisher_options.read_text()) if args.publisher_options else None
    print(json.dumps(selection(args.pr, args.head, upstream=args.upstream,
                               publisher_options=options), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
