"""Transient recovery projections from typed conditions and original state."""
import json
from functools import wraps
from copy import deepcopy

from shared.diagnostics import Diagnostic, command, human, task


RECOVERY = {
    'stale_inputs': 'Rebuild the affected assignment from current governing inputs; preserve original results and allowances.',
    'evidence_missing': 'Restore the referenced original evidence before reusing or completing affected work.',
    'evidence_damaged': 'Restore intact original evidence; do not replace results or reset claims and allowances.',
    'evidence_unavailable': 'Restore access to the referenced evidence, then inspect again.',
    'execution_contract_missing': 'Resolve the installed execution contract and rebuild the affected native assignment.',
    'execution_evidence_invalid': 'Inspect original native execution evidence and complete the affected required contract using supported native tools.',
    'execution_evidence_missing': 'Obtain the original native terminal observations through supported host tools; keep required execution incomplete until established.',
    'execution_capability_missing': 'Use supported native tools with the required execution and terminal evidence; keep affected work incomplete until available.',
    'active_claim': 'Reconcile the original claim and any domain writes before explicit recovery with its original identity and budgets.',
    'definition_required': 'Supply the current explicit definition for this original task without resetting its results or allowances.',
    'configuration_invalid': 'Correct the referenced configuration under the configuration request workflow; preserve original decisions and receipts.',
    'admission_required': 'Complete the original configuration request admission and any sourced human decisions before applying it.',
    'unclassified_failure': 'Inspect the original input and evidence to establish the cause and any earlier write outcome before choosing recovery.',
}


class Condition(Diagnostic, ValueError):
    """Retain ValueError compatibility for existing direct helper consumers."""


class EvidenceProblem(str):
    """Carry a transient access cause while preserving the legacy problem string."""
    def __new__(cls, problem, code):
        value = super().__new__(cls, problem)
        value.code = code
        return value


def evidence_code(problem):
    return getattr(problem, 'code', 'evidence_missing' if problem in {
        'missing_result', 'missing_result_artifact'} else 'evidence_damaged')


def condition(code, message, *artifacts, action=None, dispatched=False, outcome='blocked'):
    return Condition(code, message, action or task(RECOVERY[code], *artifacts),
                     dispatched=dispatched, outcome=outcome)


def from_exception(exc, *artifacts):
    bound = exc if isinstance(exc, Diagnostic) else getattr(exc, 'workflow_diagnostic', None)
    if isinstance(bound, Diagnostic):
        value = deepcopy(bound.envelope()['diagnostic'])
        value['action']['artifacts'] = list(dict.fromkeys([
            *value['action'].get('artifacts', []), *map(str, artifacts)]))
        return value
    if isinstance(exc, FileNotFoundError):
        code = 'evidence_missing'
    elif isinstance(exc, json.JSONDecodeError):
        code = 'evidence_damaged'
    elif isinstance(exc, OSError):
        code = 'evidence_unavailable'
    else:
        code = 'unclassified_failure'
    if getattr(exc, 'filename', None):
        artifacts = (*artifacts, exc.filename)
    return condition(code, str(exc), *artifacts, dispatched=None,
                     outcome='unknown').envelope()['diagnostic']


def failure(exc, *artifacts):
    """Keep the legacy blocked response without asserting remote non-delivery."""
    return {'status': 'blocked', 'reason': str(exc),
            'diagnostic': from_exception(exc, *artifacts)}


def cli_failure(exc, argv):
    paths = {'--file', '--spec', '--definition', '--report', '--original', '--native-evidence',
             '--available', '--capabilities', '--native-reviews', '--source', '--operation',
             '--options', '--workflow'}
    identities = {'--key', '--claim', '--run', '--request-id'}
    artifacts = [value if option in paths else option[2:] + ':' + value
                 for option, value in zip(argv, argv[1:]) if option in paths | identities]
    return failure(exc, *artifacts)


def configuration_validation(function):
    """Mark schema failures where configuration validation owns the condition."""
    @wraps(function)
    def validate(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Diagnostic:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise condition('configuration_invalid', str(exc)) from exc
    return validate


def annotate(value, code, message, *artifacts, action=None, dispatched=False,
             outcome='blocked'):
    return {**value, 'diagnostic': condition(code, message, *artifacts, action=action,
            dispatched=dispatched, outcome=outcome).envelope()['diagnostic']}


def allowance(message, *, run, kind):
    return condition('allowance_exhausted', message, action=human(
        'The retained ' + kind + ' allowance is exhausted. Obtain an explicit applicable user decision; do not reset the run or counters.',
        'run:' + run))


def publication(value, operation):
    """Only pending dispatch permits the original read-only reconciliation."""
    if value['status'] != 'pending':
        return None
    return condition('publication_uncertain', 'The original publication outcome is unresolved.',
        action=command(value['reconcile_argv'], operation), dispatched=True,
        outcome='unknown').envelope()['diagnostic']
