#!/usr/bin/env python3
"""Build architecture handoffs, evaluate accepted gates and draft bounded Decisions."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import architecture
from state import read
from tasks import Tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--pr', required=True)
    parser.add_argument('--checkout', type=Path)
    parser.add_argument('--upstream')
    parser.add_argument('--options', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('gate')
    p.add_argument('--file', type=Path, required=True, help='Accepted stage keys and publication operation paths')
    p.add_argument('--analysis-only', action='store_true')
    p = sub.add_parser('governing-body')
    p.add_argument('--body', type=Path, required=True)
    p.add_argument('--operation', type=Path, required=True)
    p = sub.add_parser('decision')
    p.add_argument('--comparison', required=True, help='Accepted comparison task key')
    p.add_argument('--selection', required=True, help='Accepted settled selection task key')
    p.add_argument('--body', type=Path, required=True)
    args = parser.parse_args()
    tasks = Tasks(args.repository, args.pr, checkout=args.checkout)
    if args.command == 'gate':
        result = architecture.gate(tasks, read(args.file), options=read(args.options) if args.options else None,
                                   upstream=args.upstream, analysis_only=args.analysis_only)
    elif args.command == 'governing-body':
        result = {'body': architecture.governing_body(args.body.read_text(), args.operation, tasks)}
    else:
        selected = architecture.accepted(tasks, args.selection)
        impact_key = selected['dependencies'].get('impact')
        accepted = architecture.accepted(tasks, args.comparison, historical=impact_key is not None)
        if impact_key:
            impact = architecture.accepted(tasks, impact_key)
            architecture.linked(impact, 'comparison', accepted)
            if impact['report']['impact'] != 'nonmaterial':
                raise ValueError('Changed architecture analysis requires refresh')
        architecture.linked(selected, 'comparison', accepted)
        if not selected['report'].get('choice'):
            raise ValueError('Architecture choice remains pending')
        rendered = architecture.render_decision(accepted['report'], accepted['policy']['scoring'],
                                               selected['report']['choice'], selection=selected['report'])
        heading = architecture.skill_config.effective('dk-workflows:dk-pr-decisions', args.repository,
                                                      args.upstream)['options']['section_heading']
        result = {'body': architecture.replace_decision(args.body.read_text(), rendered, heading=heading),
                  'decision_type': 'architecture'}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        raise SystemExit(2)
