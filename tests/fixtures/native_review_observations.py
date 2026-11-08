"""Explicit offline stand-ins for native host events, never production evidence."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'runtime'))

from native_review import binding
from state import capture


CONTRACT = {'schema': 1, 'skill_version': 'fixture-1', 'passes': []}


def observations(spec, claim, original):
    value = {**binding(capture(spec)['components'], claim),
             'availability': 'available', 'inventory_complete': True,
             'invocation': {'handle': 'fixture-root-' + claim['id'], 'state': 'completed',
                            'output_sha256': hashlib.sha256(original).hexdigest(),
                            'receipt': 'offline terminal fixture'}, 'passes': []}
    for requirement in spec['native_review']['passes']:
        role = requirement['role']
        handle = 'fixture-' + role + '-' + claim['id']
        value['passes'].append({'role': role, 'handle': handle, 'parent': value['invocation']['handle'],
            'state': 'completed', 'output': role + ': No findings',
            'spawn_receipt': 'offline spawn fixture', 'terminal_receipt': 'offline terminal fixture',
            'drivers': [{'argv': argv, 'handle': handle, 'call_id': handle + ':' + str(index),
                         'state': 'completed', 'exit_code': 0, 'output': 'Follow-up prompt',
                         'receipt': 'offline tool fixture'}
                        for index, argv in enumerate(requirement['drivers'])]})
    return json.dumps(value).encode()


PREFLIGHT_CAPABILITIES = {'delegation': True, 'native_evidence': {
    'availability': 'available', 'inventory_complete': True, 'invocation_results': True,
    'child_results': True, 'tool_observations': True}}


def preflight_requirements(calls):
    """Resolved no-child review contracts for availability-focused offline tests."""
    return {location: CONTRACT for location, _ in calls if 'reviewers' in location.split('/')}
