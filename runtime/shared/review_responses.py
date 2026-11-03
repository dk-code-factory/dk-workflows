"""Shared incomplete-review response contract for validation and retained proof."""


def validate_incomplete_response(value):
    if not isinstance(value, dict) or value.get('status') not in ('incomplete', 'tokens_exhausted'):
        return False
    if set(value) != {'status', 'findings', 'message'} or value.get('findings') != []:
        raise ValueError('$: incomplete response requires only status, an empty findings array, and message')
    if not isinstance(value.get('message'), str) or not value['message'].strip():
        raise ValueError('/message: incomplete response requires a nonempty reason')
    return True


def is_quota_response(value):
    if not isinstance(value, dict) or value.get('status') != 'tokens_exhausted':
        return False
    try:
        return validate_incomplete_response(value)
    except ValueError:
        return False
