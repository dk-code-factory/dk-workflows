#!/usr/bin/env python3
"""Render a read-only PR thread report from the Decisions analysis cache."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import importlib.util
import json
import sys
from pathlib import Path

CORE = next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir()) / 'dk-pr-decisions' / 'scripts' / 'decisions.py'
spec = importlib.util.spec_from_file_location('dk_pr_decisions', CORE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def main(context_json=False, pr=None):
    state, path = core.scan(pr)
    report = core.render(state['pr'], state['viewer'], state)
    report_path = path.with_suffix('.md')
    core.atomic_write(report_path, report)
    if context_json:
        print(json.dumps(core.compact(state), ensure_ascii=False, sort_keys=True))
    else:
        print(report, end='')


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--context-json', action='store_true')
        parser.add_argument('--pr', help='PR URL, including fork-based PRs')
        args = parser.parse_args()
        main(context_json=args.context_json, pr=args.pr)
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f'dk-pr-read-comments: {exc}', file=sys.stderr)
        sys.exit(1)
