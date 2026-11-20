"""The proposed agent-context schema and every example in docs/agent-context.md agree."""
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT/'schemas/agent-context.schema.json').read_text())
DOC = (ROOT/'docs/agent-context.md').read_text()
EXAMPLES = DOC.split('\n## Examples\n', 1)[1].split('\n## ', 1)[0]
DOCUMENTS = {re.sub(r'\W+', '-', name.strip().lower()): json.loads(block) for name, block in
             re.findall(r'^### ([^\n]+)\n(?:(?!^### ).)*?```json\n(.*?)\n```', EXAMPLES, re.S | re.M)}
SECTIONS = ['governing', 'execution', 'task', 'evidence']
SCOPES = ['global', 'repo', 'pr', 'run']
OWNER_SEGMENTS = {'global': 0, 'repo': 3, 'pr': 4, 'run': 5}


def validator(definition=None):
    if definition is None:
        return Draft202012Validator(SCHEMA)
    return Draft202012Validator({'$schema': SCHEMA['$schema'], '$defs': SCHEMA['$defs'],
                                 '$ref': f'#/$defs/{definition}'})


def task_owner(task_id):
    """Owner segments of a full task address: repo and PR, plus the run for run tasks."""
    _, scope, *rest = task_id.lstrip('/').split('/')
    return rest[:OWNER_SEGMENTS[scope]]


def owner(task_id, scope):
    segments = task_owner(task_id)
    count = OWNER_SEGMENTS[scope]
    return segments[:count] if count <= len(segments) else None


def expand(address, task_id):
    """Short addresses take their owner segments from task.id."""
    if address.startswith('/'):
        return address
    path, _, pointer = address.partition('#')
    kind, scope, *name = path.split('/')
    segments = owner(task_id, scope)
    assert segments is not None, f'{address} cannot be short under {task_id}'
    return '/' + '/'.join([kind, scope, *segments, *name]) + ('#' + pointer if pointer else '')


def shorten(address, task_id):
    path, _, pointer = address.partition('#')
    kind, scope, *rest = path.lstrip('/').split('/')
    segments = owner(task_id, scope)
    count = OWNER_SEGMENTS[scope]
    if segments is None or rest[:count] != segments:
        return address
    return '/'.join([kind, scope, *rest[count:]]) + ('#' + pointer if pointer else '')


def addresses(document):
    for section in ('governing', 'evidence'):
        yield from document.get(section, {})
    yield from document['task'].get('material', {})


def scope_rank(address):
    return SCOPES.index(address.lstrip('/').split('/')[1])


def test_schema_is_valid_and_examples_exist():
    Draft202012Validator.check_schema(SCHEMA)
    assert set(DOCUMENTS) == {'fixer', 'reviewer', 'reply-writer'}


@pytest.mark.parametrize('name', ['fixer', 'reviewer', 'reply-writer'])
def test_documents_validate_and_shorten_every_address_they_can(name):
    document = DOCUMENTS[name]
    validator().validate(document)
    task_id = document['task']['id']
    for address in addresses(document):
        assert shorten(expand(address, task_id), task_id) == address, address


@pytest.mark.parametrize('name', ['fixer', 'reviewer', 'reply-writer'])
def test_sections_and_governing_items_are_in_cache_order(name):
    document = DOCUMENTS[name]
    assert list(document) == [section for section in SECTIONS if section in document]
    governing = list(document['governing'])
    full = [expand(address, document['task']['id']) for address in governing]
    assert full == sorted(full, key=lambda address: (scope_rank(address), address)), governing


def test_agents_in_one_run_share_the_governing_prefix():
    def prefix(document):
        return json.dumps({'governing': document['governing']}, separators=(',', ':'))[:-1]
    assert prefix(DOCUMENTS['fixer']) == prefix(DOCUMENTS['reviewer'])


def test_addressing_table_round_trips():
    task_id = '/task/run/github.com/acme/project/42/run-7/finalize'
    rows = re.findall(r'^\| (global|repo|pr|run) \| `([^`]+)` \| `(/[^`]+)` \|$', DOC, re.M)
    assert [row[0] for row in rows] == SCOPES
    for _, short, full in rows:
        assert expand(short, task_id) == full
        assert shorten(full, task_id) == short


@pytest.mark.parametrize('address', [
    'decision/team/x', '/decision/repo/github.com/acme/x', 'decision/pr', 'note/global/x',
    '/decision/pr/github.com/acme/project/0/x', 'evidence/pr/x#events', 'task/pr/x', 'schema/global/x',
])
def test_malformed_addresses_are_rejected(address):
    assert not validator('address').is_valid(address)


@pytest.mark.parametrize('task_id', [
    'task/pr/fix', '/task/repo/github.com/acme/project/fix', '/task/pr/github.com/acme/project/42',
    '/decision/pr/github.com/acme/project/42/fix',
])
def test_task_ids_must_be_full_pr_or_run_addresses(task_id):
    assert not validator('task_id').is_valid(task_id)


def test_items_are_complete_and_documents_carry_no_derivable_fields():
    items = validator('items')
    assert not items.is_valid({'decision/pr/x': {'$ref': 'decision/pr/x'}})
    assert not items.is_valid({'instruction/repo/x': {'text': 'cut', 'truncated': True}})
    assert not items.is_valid({'decision/pr/x': {}})
    document = DOCUMENTS['fixer']
    for extra in ({'context': {}}, {'role': {}}):
        assert not validator().is_valid({**document, **extra})
    for field in ('key', 'claim', 'subject'):
        assert not validator().is_valid({**document, 'task': {**document['task'], field: 'x'}})
    assert not validator().is_valid({**document, 'execution': {'native_review': {'passes': ['a'], 'skill_version': '1'}}})
