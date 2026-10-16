#!/usr/bin/env python3
"""Validate one reviewer's findings against the pinned diff contract."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
import sys
from pathlib import Path


def added_lines(patch):
    import re
    changed = {}
    path = None
    line = None
    for raw in patch.splitlines():
        if raw.startswith('diff --git '):
            path, line = None, None
        elif raw.startswith('+++ b/'):
            path = raw[6:]
            changed.setdefault(path, set())
        elif raw.startswith('@@'):
            match = re.search(r'\+(\d+)(?:,\d+)?', raw)
            line = int(match.group(1)) if match else None
        elif path and line is not None:
            if raw.startswith('+') and not raw.startswith('+++'):
                changed[path].add(line)
                line += 1
            elif raw.startswith(' '):
                line += 1
    return changed


def validate(result, changed):
    if not isinstance(result, dict) or set(result) != {'findings'} or not isinstance(result['findings'], list):
        raise ValueError('expected one JSON object with only a findings array')
    for index, item in enumerate(result['findings'], 1):
        label = f'finding {index}'
        if not isinstance(item, dict) or set(item) != {'severity', 'path', 'line', 'message', 'fix'}:
            raise ValueError(f'{label}: expected severity, path, line, message, and fix')
        if item['severity'] not in ('Major', 'Minor', 'Nit'):
            raise ValueError(f'{label}: severity must be Major, Minor, or Nit')
        if not isinstance(item['path'], str) or item['path'] not in changed:
            raise ValueError(f'{label}: path must be a changed file in the pinned diff')
        if type(item['line']) is not int or item['line'] not in changed[item['path']]:
            raise ValueError(f'{label}: line must be an added line in {item["path"]}; eligible lines: {sorted(changed[item["path"]])}')
        for key in ('message', 'fix'):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f'{label}: {key} must be nonempty text')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patch', required=True)
    parser.add_argument('--result', required=True)
    args = parser.parse_args()
    try:
        result = json.loads(Path(args.result).read_text())
        validate(result, added_lines(Path(args.patch).read_text()))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'invalid review result: {exc}', file=sys.stderr)
        return 1
    print('valid review result')
    return 0


if __name__ == '__main__':
    sys.exit(main())
