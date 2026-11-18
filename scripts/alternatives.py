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
    parser.add_argument('--pr')
    parser.add_argument('--checkout', type=Path)
    parser.add_argument('--upstream')
    parser.add_argument('--options', type=Path)
    parser.add_argument('--policy-options', type=Path, help='Invocation-local shared assessment options')
    parser.add_argument('--alternatives-options', type=Path, help='Options for the invoked alternatives skill')
    parser.add_argument('--skill', help='Alternatives skill for policy reads; defaults to dk-issue-alternatives')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('gate')
    p.add_argument('--file', type=Path, required=True, help='Accepted stage keys and publication operation paths')
    p.add_argument('--analysis-only', action='store_true')
    p.add_argument('--manual', action='store_true', help='Validate current alignment for an explicit manual request')
    sub.add_parser('fix-gate', help='Discover initial fix alternatives work or reuse its accepted PR plan')
    p = sub.add_parser('plan', help='Discover shared assessment and per-decision alternatives work')
    p.add_argument('--workflow', choices=['fix', 'review'], required=True)
    p.add_argument('--request', type=Path, help='Original explicit manual request evidence')
    p.add_argument('--review-operation', type=Path, help='Existing reconciled alternatives rejection operation')
    p.add_argument('--analysis-only', action='store_true')
    sub.add_parser('policy', help='Read the shared assessment, evaluation and selection policy')
    p = sub.add_parser('issue-evaluate', help='Validate native issue-only analysis in memory, without a PR record')
    p.add_argument('--assessment', type=Path, required=True)
    p.add_argument('--comparison', type=Path, required=True)
    p.add_argument('--selection', type=Path, required=True)
    p.add_argument('--target', required=True, help='Selected target branch or commit')
    p = sub.add_parser('governing-body')
    p.add_argument('--body', type=Path, required=True)
    p.add_argument('--operation', type=Path, required=True)
    p = sub.add_parser('decision')
    p.add_argument('--comparison', required=True, help='Accepted comparison task key')
    p.add_argument('--selection', required=True, help='Accepted settled selection task key')
    p.add_argument('--impact', help='Current nonmaterial impact permitting retained selection reuse')
    p.add_argument('--body', type=Path, required=True)
    args = parser.parse_args()
    if args.command not in {'policy', 'issue-evaluate'} and not args.pr:
        parser.error('--pr is required for PR-managed work')
    tasks = Tasks(args.repository, args.pr, checkout=args.checkout) if args.pr else None
    if args.command == 'policy':
        from pr_assessment import resolve
        result = resolve(args.repository, args.upstream, skill=args.skill,
                         policy_options=read(args.policy_options) if args.policy_options else None,
                         alternatives_options=read(args.alternatives_options) if args.alternatives_options else None)
    elif args.command == 'issue-evaluate':
        from assessment_alternatives import compare, select
        from pr_assessment import resolve
        from state import git
        policy = resolve(args.repository, args.upstream, owner=alternatives.FIX_OWNER,
                         options=read(args.options) if args.options else None,
                         policy_options=read(args.policy_options) if args.policy_options else None,
                         alternatives_options=read(args.alternatives_options) if args.alternatives_options else None)
        target = git(args.checkout or Path.cwd(), 'rev-parse', '--verify', args.target + '^{commit}').decode().strip()
        comparison = read(args.comparison)
        compared = compare(comparison, read(args.assessment), comparison['rubric'], target, has_pr=False)
        selection = read(args.selection)
        choice = select(compared, policy['selection'], selection)
        if selection.get('choice') != choice:
            raise ValueError('Issue selection disagrees with shared policy')
        if selection.get('mode') == 'manual':
            source = selection['user_source']
            if source not in selection.get('evidence', []) or not body_text(Path(source)).strip():
                raise ValueError('Manual issue selection requires its original user source')
        alternatives.evidence(selection)
        if choice and (not alternatives.text(selection.get('reason')) or
                       set(selection.get('rejection_reasons', {})) != {s['id'] for s in compared['solutions']} - {choice} or
                       any(not alternatives.text(v) for v in selection['rejection_reasons'].values())):
            raise ValueError('Settled issue selection requires rationale and rejection reasons')
        result = {'comparison': compared, 'choice': choice, 'can_prompt': policy['selection']['mode'] != 'automatic'}
    elif args.command == 'plan':
        from assessment_alternatives import gate
        result = gate(tasks, alternatives.FIX_OWNER if args.workflow == 'fix' else alternatives.OWNER,
                      options=read(args.options) if args.options else None,
                      policy_options=read(args.policy_options) if args.policy_options else None,
                      alternatives_options=read(args.alternatives_options) if args.alternatives_options else None,
                      upstream=args.upstream, request=str(args.request.absolute()) if args.request else None,
                      review_operation=str(args.review_operation) if args.review_operation else None, analysis_only=args.analysis_only)
    elif args.command == 'fix-gate':
        from fix_alternatives import gate
        result = gate(tasks, options=read(args.options) if args.options else None, upstream=args.upstream)
    elif args.command == 'gate':
        result = alternatives.gate(tasks, read(args.file), options=read(args.options) if args.options else None,
                                   upstream=args.upstream, analysis_only=args.analysis_only, manual=args.manual)
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
