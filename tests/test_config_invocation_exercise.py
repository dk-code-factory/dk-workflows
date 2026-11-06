"""Reject incomplete native trial results instead of reporting false success."""
import json
from pathlib import Path

import pytest

from config_invocation_exercise import assess, CONFIG_CASES, MUTATION_CASES


CASES = json.loads((Path(__file__).parent/'fixtures/config-invocation-cases.json').read_text())


def decisions():
    return [{'id': case['id'],
             'skills_to_invoke': ['dk-workflows:dk-config'] if case['id'] in CONFIG_CASES else [],
             'guidance_to_load': [], 'helper_operations': [],
             'changes_saved_settings': case['id'] in MUTATION_CASES,
             'blockers': ['Missing required reviewer'] if case['id'] == 'missing-reviewer' else []}
            for case in CASES['cases']]


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'nonboolean', 'missing-blocker'])
def test_native_trial_requires_complete_typed_results_and_retained_blockers(damage):
    result = decisions()
    if damage == 'missing':
        result.pop()
    elif damage == 'duplicate':
        result[-1] = result[0]
    elif damage == 'nonboolean':
        result[0]['changes_saved_settings'] = 'false'
    else:
        next(row for row in result if row['id'] == 'missing-reviewer')['blockers'] = []
        report = assess(CASES, result)
        assert report['passed'] == report['cases'] - 1
        return
    with pytest.raises(ValueError):
        assess(CASES, result)


def test_native_trial_reports_unnecessary_loading_and_mutation():
    result = decisions()
    assert assess(CASES, result)['passed'] == len(result)
    result[0]['skills_to_invoke'].append('dk-workflows:dk-config')
    result[0]['guidance_to_load'].append('codex/skills/dk-config/SKILL.md')
    result[0]['changes_saved_settings'] = True
    report = assess(CASES, result)
    assert report['unnecessary_config_invocations'] == 1
    assert report['passed'] == len(result) - 1
    assert len(report['results'][0]['failures']) == 3
