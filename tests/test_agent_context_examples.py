"""The proposed agent-context schema and every example in docs/agent-context.md agree."""
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT/'schemas/agent-context.schema.json').read_text())
DOC = (ROOT/'docs/agent-context.md').read_text()
BLOCKS = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', DOC, re.S)]
DOCUMENTS = [block for block in BLOCKS if isinstance(block, dict) and 'agent' in block]
ITEMS = [block for block in BLOCKS if isinstance(block, dict) and 'kind' in block]
SCOPES = {'global': 0, 'repo': 3, 'pr': 4, 'run': 5}


def validator(definition=None):
    schema = SCHEMA if definition is None else {**SCHEMA, '$ref': f'#/$defs/{definition}'}
    if definition is not None:
        schema = {key: value for key, value in schema.items()
                  if key not in ('properties', 'required', 'additionalProperties', 'type')}
    return Draft202012Validator(schema)


def owner(context, scope):
    repo = context['repo'].split('/')
    return {'global': [], 'repo': repo, 'pr': [*repo, str(context.get('pr'))],
            'run': [*repo, str(context.get('pr')), str(context.get('run'))]}[scope]


def expand(address, context):
    """Packed addresses take their owner segments from the document context."""
    if address.startswith('/'):
        return address
    path, _, pointer = address.partition('#')
    kind, scope, *name = path.split('/')
    assert scope == 'global' or context.get({'repo': 'repo', 'pr': 'pr', 'run': 'run'}[scope])
    return '/' + '/'.join([kind, scope, *owner(context, scope), *name]) + ('#' + pointer if pointer else '')


def pack(address, context):
    path, _, pointer = address.partition('#')
    kind, scope, *rest = path.lstrip('/').split('/')
    count = SCOPES[scope]
    if rest[:count] != owner(context, scope):
        return address
    return '/'.join([kind, scope, *rest[count:]]) + ('#' + pointer if pointer else '')


def references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('$ref', '$id') and isinstance(item, str):
                yield item
            else:
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def test_schema_is_valid_and_examples_exist():
    Draft202012Validator.check_schema(SCHEMA)
    assert len(DOCUMENTS) == 3 and ITEMS


@pytest.mark.parametrize('document', DOCUMENTS, ids=lambda document: document['agent'])
def test_context_documents_validate_and_pack_their_own_owners(document):
    validator().validate(document)
    context = document['context']
    for address in references(document):
        canonical = expand(address, context)
        assert pack(canonical, context) == address, address


def test_standalone_items_validate():
    for item in ITEMS:
        validator('item').validate(item)


def test_addressing_table_round_trips():
    context = {'repo': 'github.com/acme/project', 'pr': 42, 'run': 'run-7'}
    rows = re.findall(r'^\| (global|repo|pr|run) \| `(/[^`]+)` \| `([^`]+)` \|$', DOC, re.M)
    assert {row[0] for row in rows} == set(SCOPES)
    for _, canonical, packed in rows:
        assert pack(canonical, context) == packed
        assert expand(packed, context) == canonical


@pytest.mark.parametrize('address', [
    'decision/team/x', '/decision/repo/github.com/acme/x', 'decision/pr', 'note/global/x',
    '/decision/pr/github.com/acme/project/0/x', 'evidence/pr/x#events',
])
def test_malformed_addresses_are_rejected(address):
    assert not validator('address').is_valid(address)


def test_other_repositories_stay_canonical():
    context = {'repo': 'github.com/acme/project', 'pr': 42}
    upstream = '/decision/repo/github.com/upstream/project/state-format'
    assert pack(upstream, context) == upstream
    assert validator('address').is_valid(upstream)


def test_truncated_copies_require_text_and_items_cannot_mix_with_references():
    assert not validator('reference').is_valid({'$ref': 'instruction/repo/x', 'truncated': True})
    assert not validator('item').is_valid({'$ref': 'decision/pr/x', '$id': 'decision/pr/x', 'kind': 'decision',
                                           'scope': 'pr', 'source': {'$ref': 'evidence/pr/y'}})
