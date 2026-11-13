#!/usr/bin/env python3
"""Inspect repository instruction choices and read sources without adopting them."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import instruction_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, default=Path.cwd())
    parser.add_argument('--repository')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('show')
    read = sub.add_parser('read')
    read.add_argument('--source', required=True)
    read.add_argument('--discovery', action='store_true')
    args = parser.parse_args()
    result = (instruction_sources.inspect(args.checkout, args.repository) if args.action == 'show' else
              instruction_sources.read_source(args.checkout, args.source, discovery=args.discovery, identity=args.repository))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        sys.exit(2)
