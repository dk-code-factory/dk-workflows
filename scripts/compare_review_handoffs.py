#!/usr/bin/env python3
"""Compare private retained full/compact captures offline; never run providers."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared.compact_compatibility import AUTHORITY, compare, private_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--report', required=True, type=Path, help='New private report outside repository')
    args = parser.parse_args(argv)
    try:
        target = private_path(args.report)
        try:
            report = compare(args.manifest)
        except (ValueError, OSError, TypeError, KeyError, IndexError, AttributeError) as exc:
            report = {'version': 1, **AUTHORITY, 'status': 'invalid', 'issues': [str(exc)]}
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        print(json.dumps({'status': report['status'], 'report': str(target), **AUTHORITY}))
        return {'observed': 0, 'incomplete': 1, 'invalid': 2}[report['status']]
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
