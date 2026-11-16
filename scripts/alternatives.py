#!/usr/bin/env python3
"""Build alternatives handoffs, evaluate accepted gates and draft bounded Decisions."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import alternatives
from state import read
from tasks import Tasks


def body_text(path):
    with path.open(encoding='utf-8', newline='') as stream:
        return stream.read()


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
    sub.add_parser('fix-gate', help='Discover initial fix alternatives work or reuse its accepted PR plan')
    p = sub.add_parser('governing-body')
    p.add_argument('--body', type=Path, required=True)
    p.add_argument('--operation', type=Path, required=True)
    p = sub.add_parser('decision')
    p.add_argument('--comparison', required=True, help='Accepted comparison task key')
    p.add_argument('--selection', required=True, help='Accepted settled selection task key')
    p.add_argument('--impact', help='Current nonmaterial impact permitting retained selection reuse')
    p.add_argument('--body', type=Path, required=True)
    args = parser.parse_args()
    tasks = Tasks(args.repository, args.pr, checkout=args.checkout)
    if args.command == 'fix-gate':
        from fix_alternatives import gate
        result = gate(tasks, options=read(args.options) if args.options else None, upstream=args.upstream)
    elif args.command == 'gate':
        result = alternatives.gate(tasks, read(args.file), options=read(args.options) if args.options else None,
                                   upstream=args.upstream, analysis_only=args.analysis_only)
    elif args.command == 'governing-body':
        result = {'body': alternatives.governing_body(body_text(args.body), args.operation, tasks, upstream=args.upstream)}
    else:
        selected = alternatives.accepted(tasks, args.selection, historical=args.impact is not None)
        impact_key = args.impact or selected['dependencies'].get('impact')
        accepted = alternatives.accepted(tasks, args.comparison, historical=impact_key is not None)
        if impact_key:
            impact = alternatives.accepted(tasks, impact_key)
            alternatives.linked(impact, 'comparison', accepted)
            alternatives.validate_selection_impact(impact, selected)
            if impact['report']['impact'] != 'nonmaterial':
                raise ValueError('Changed alternatives analysis requires refresh')
        alternatives.linked(selected, 'comparison', accepted)
        if not selected['report'].get('choice'):
            raise ValueError('Alternatives choice remains pending')
        rendered = alternatives.render_decision(accepted['report'], accepted['policy']['scoring'],
                                               selected['report']['choice'], selection=selected['report'])
        heading = alternatives.skill_config.effective('dk-workflows:dk-pr-decisions', args.repository,
                                                      args.upstream)['options']['section_heading']
        result = {'body': alternatives.replace_decision(body_text(args.body), rendered, heading=heading),
                  'decision_type': 'alternatives'}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}))
        raise SystemExit(2)
