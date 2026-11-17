#!/usr/bin/env python3
"""Build full/delta prose handoffs for native skills without executing models."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
from prose import handoff
from state import read
from tasks import Tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--pr', required=True)
    parser.add_argument('--checkout', default='.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--descriptor', type=Path,
                        help='One ordinary managed task or selected finalizer descriptor')
    source.add_argument('--key', help='Resume an existing managed descriptor without reconstructing it')
    parser.add_argument('--head', help='Current immutable head commit, required with --key')
    args = parser.parse_args()
    tasks = Tasks(args.repository, args.pr, checkout=args.checkout)
    if args.key:
        if not args.head:
            raise ValueError('Saved prose handoffs require the current --head commit')
        with tasks.store.locked() as manifest:
            descriptor = deepcopy(manifest['entries'].get(args.key, {}).get('managed'))
        if descriptor is None:
            raise ValueError('Prose task has no retained managed definition')
        descriptor['definition']['git']['head'] = args.head
    else:
        if args.head:
            raise ValueError('Supply head in the explicit descriptor or use --key')
        descriptor = read(args.descriptor)
    return handoff(tasks, descriptor)


if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
