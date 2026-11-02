"""Recovery authority comes from transport types, never failure prose."""
import subprocess

from shared.diagnostics import Diagnostic


def role_failure(exc, guard=False):
    code, category = type(exc).__name__, 'unknown'
    if guard:
        category = 'guard'
    elif isinstance(exc, subprocess.TimeoutExpired):
        category, code = 'infrastructure', 'transport_timeout'
    elif isinstance(exc, OSError):
        category, code = 'infrastructure', 'transport_os_error'
    elif isinstance(exc, Diagnostic):
        code = exc.code
        if code in {'provider_output_failed', 'analysis_provider_unavailable'}:
            category = 'infrastructure'
        elif code == 'model_validation_failed':
            category = 'response'
    elif isinstance(exc, ValueError):
        category = 'response'
    eligible = category == 'infrastructure'
    return {'failure_version': 1, 'category': category, 'code': code,
            'response_slot_unchanged': eligible, 'recovery_eligible': eligible}


def eligible(value):
    return (isinstance(value, dict) and set(value) == {'failure_version', 'category', 'code',
            'response_slot_unchanged', 'recovery_eligible'} and type(value['failure_version']) is int
            and value['failure_version'] == 1 and value['category'] == 'infrastructure'
            and value['code'] in {'transport_timeout', 'transport_os_error',
                                 'provider_output_failed', 'analysis_provider_unavailable'}
            and value['response_slot_unchanged'] is True and value['recovery_eligible'] is True)
