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
    import codecs
    import re
    changed = {}
    path = None
    line = None
    for raw in patch.split('\n'):
        if raw.startswith('diff --git '):
            path, line = None, None
        elif line is None and raw.startswith(('+++ b/', '+++ "b/')):
            filename = raw[4:].split('\t', 1)[0]
            if filename.startswith('"'):
                quoted = filename[1:-1].encode('utf-8', 'surrogateescape')
                filename = codecs.escape_decode(quoted)[0].decode('utf-8', 'surrogateescape')
            path = filename[2:]
            changed.setdefault(path, set())
        elif raw.startswith('@@'):
            match = re.search(r'\+(\d+)(?:,\d+)?', raw)
            line = int(match.group(1)) if match else None
        elif path and line is not None:
            if raw.startswith('+'):
                changed[path].add(line)
                line += 1
            elif raw.startswith(' '):
                line += 1
    return changed


def field_summary(fields):
    names = sorted(str(field) for field in fields)
    shown = [name[:80] for name in names[:10]]
    return repr(shown) + (f' ({len(names)} fields total)' if len(names) > 10 else '')


def validate(result, changed):
    if not isinstance(result, dict) or set(result) != {'findings'}:
        detail = ('; unexpected fields: ' + field_summary(set(result) - {'findings'})) if isinstance(result, dict) else ''
        raise ValueError('$: expected one JSON object with only a findings array' + detail)
    if not isinstance(result['findings'], list):
        raise ValueError('/findings: expected an array')
    for index, item in enumerate(result['findings']):
        label = f'/findings/{index}'
        if not isinstance(item, dict) or set(item) != {'severity', 'path', 'line', 'message', 'fix'}:
            fields = {'severity', 'path', 'line', 'message', 'fix'}
            missing = field_summary(fields - set(item) if isinstance(item, dict) else fields)
            extra = field_summary(set(item) - fields if isinstance(item, dict) else [])
            raise ValueError(f'{label}: expected severity, path, line, message, and fix; '
                             f'missing fields: {missing}; unexpected fields: {extra}')
        if item['severity'] not in ('Major', 'Minor', 'Nit'):
            raise ValueError(f'{label}/severity: severity must be Major, Minor, or Nit')
        if not isinstance(item['path'], str) or item['path'] not in changed:
            raise ValueError(f'{label}/path: path must be a changed file in the pinned diff')
        if type(item['line']) is not int or item['line'] not in changed[item['path']]:
            eligible = sorted(changed[item['path']])
            raise ValueError(f'{label}/line: line must be an added line in the pinned diff; '
                             f'eligible lines (first 10 of {len(eligible)}): {eligible[:10]}')
        for key in ('message', 'fix'):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f'{label}/{key}: {key} must be nonempty text')
    return result


def validate_response(result, changed):
    if isinstance(result, dict) and result.get('status') in ('incomplete', 'tokens_exhausted'):
        if set(result) != {'status', 'findings', 'message'} or result.get('findings') != []:
            raise ValueError('$: incomplete response requires only status, an empty findings array, and message')
        if not isinstance(result.get('message'), str) or not result['message'].strip():
            raise ValueError('/message: incomplete response requires a nonempty reason')
        return result
    return validate(result, changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patch')
    parser.add_argument('--result')
    parser.add_argument('--schema', action='store_true', help='Print the provider response JSON schema')
    args = parser.parse_args()
    if args.schema:
        print(Path(__file__).with_name('review-result.schema.json').read_text())
        return 0
    if not args.patch or not args.result:
        parser.error('--patch and --result are required for validation')
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
