"""Typed workflow conditions, independent of provider or CLI error prose."""
import json


def command(argv, *artifacts):
    return {'kind': 'command', 'argv': [str(x) for x in argv],
            'artifacts': [str(x) for x in artifacts]}


def task(text, *artifacts):
    return {'kind': 'agent_task', 'task': text, 'artifacts': [str(x) for x in artifacts]}


def human(text, *artifacts):
    return {'kind': 'human_input', 'request': text, 'artifacts': [str(x) for x in artifacts]}


class Diagnostic(RuntimeError):
    def __init__(self, code, message, action, *, dispatched=False, outcome='blocked'):
        super().__init__(message)
        self.code, self.action, self.dispatched = code, action, dispatched
        self.outcome = outcome

    def envelope(self):
        return {'status': self.outcome, 'diagnostic': {
            'code': self.code, 'message': str(self), 'action': self.action,
            'dispatched': self.dispatched, 'outcome': self.outcome}}


def emit(exc):
    print(json.dumps(exc.envelope(), sort_keys=True))


def preparation_failure(exc, argv):
    """Classify input/read failures by type; never infer workflow state from prose."""
    fields = {'--operation', '--state', '--record', '--run-record', '--payload',
              '--title-file', '--body-file', '--gate-arguments'}
    artifacts = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value in fields]
    code = 'evidence_unavailable' if isinstance(exc, OSError) else 'preparation_invalid'
    detail = str(exc)
    for key in ('stdout', 'stderr'):
        value = getattr(exc, key, None)
        if value:
            detail += '\n' + key + ':\n' + str(value)
    if hasattr(exc, 'response'):
        detail += '\nresponse:\n' + json.dumps(exc.response)
    elif hasattr(exc, 'doc'):
        detail += '\nresponse:\n' + exc.doc
    return Diagnostic(code, detail, task(
        'Inspect the retained evidence, correct the reported input or read failure, and prepare again.', *artifacts),
        outcome='error')
