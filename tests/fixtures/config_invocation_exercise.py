"""Score a native guidance decision exercise; never invoke agents or workflows."""
import argparse
import json
from pathlib import Path


CONFIG_CASES = {'commit-policy-question', 'split-question', 'schema-question',
                'explicit-inspection', 'explicit-change'}
MUTATION_CASES = {'explicit-change', 'preference'}


def assess(cases, results):
    expected = {case['id']: case for case in cases['cases']}
    if (not isinstance(results, list) or any(not isinstance(row, dict) for row in results)
            or len(results) != len(expected)
            or {row.get('id') for row in results} != set(expected)):
        raise ValueError('Require exactly one result for every case')
    rows = []
    for result in results:
        case_id = result['id']
        for key in ('skills_to_invoke', 'guidance_to_load', 'helper_operations', 'blockers'):
            if (not isinstance(result.get(key), list)
                    or any(not isinstance(value, str) for value in result[key])):
                raise ValueError('Require string lists for ' + key)
        if type(result.get('changes_saved_settings')) is not bool:
            raise ValueError('Require a boolean settings-mutation decision')
        invokes = any('dk-workflows:dk-config' in skill or skill == 'dk-config'
                      for skill in result['skills_to_invoke'])
        loads = any('codex/skills/dk-config/SKILL.md' in path
                    for path in result['guidance_to_load'])
        # Inspecting skill source while developing the package is legitimate.
        load_allowed = case_id in CONFIG_CASES or case_id == 'source-inspection'
        failures = []
        if invokes != (case_id in CONFIG_CASES):
            failures.append('configuration invocation')
        if loads and not load_allowed:
            failures.append('unnecessary configuration guidance')
        if result['changes_saved_settings'] != (case_id in MUTATION_CASES):
            failures.append('settings mutation')
        if case_id == 'missing-reviewer' and not result['blockers']:
            failures.append('missing required reviewer blocker')
        rows.append({'id': case_id, 'origin': expected[case_id]['origin'],
                     'invokes_config': invokes, 'loads_config_guidance': loads,
                     'changes_saved_settings': result['changes_saved_settings'],
                     'failures': failures})
    return {'cases': len(rows), 'passed': sum(not row['failures'] for row in rows),
            'unnecessary_config_invocations': sum(row['invokes_config'] and row['id']
                                                  not in CONFIG_CASES for row in rows),
            'results': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--cases', type=Path,
                        default=Path(__file__).with_name('config-invocation-cases.json'))
    args = parser.parse_args()
    report = assess(json.loads(args.cases.read_text()), json.loads(args.results.read_text()))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] == report['cases'] else 1)
