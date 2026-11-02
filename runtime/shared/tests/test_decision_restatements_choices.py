"""Only exact Decisions JSON copies bypass protected choice fingerprints."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import decision_restatements


KEY = 'choices/0'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@pytest.fixture
def sources(tmp_path):
    result = []
    for side, head in (('old', 'a' * 40), ('current', 'b' * 40)):
        decisions = {'verified': True, 'head_sha': head, 'local_decisions': []}
        values = {}
        for key, name in ((KEY, 'choice'), ('decisions', 'decisions')):
            path = tmp_path / f'{side}-{name}.json'
            path.write_text(json.dumps(decisions))
            values[key] = {'path': str(path)}
        result.append(values)
    return result


def matches(sources):
    return decision_restatements.choices_match(KEY, *sources, SimpleNamespace(read=read))


@pytest.mark.parametrize('changed', [False, True])
def test_plain_text_is_not_a_decisions_duplicate(sources, changed):
    before = copy.deepcopy(sources)
    contents = ('Coordinator: preserve the agreed behavior.\n',
                'Coordinator: change the agreed behavior.\n' if changed else
                'Coordinator: preserve the agreed behavior.\n')
    for source, content in zip(sources, contents):
        Path(source[KEY]['path']).write_text(content)
    assert matches(sources) is False
    assert sources == before
    assert [Path(source[KEY]['path']).read_text() for source in sources] == list(contents)


def test_each_choice_must_match_its_own_decisions_context(sources):
    assert read(sources[0][KEY]['path']) != read(sources[1][KEY]['path'])
    assert matches(sources) is True


@pytest.mark.parametrize('side', [0, 1])
def test_choice_from_other_context_is_not_a_duplicate(sources, side):
    Path(sources[side][KEY]['path']).write_text(
        Path(sources[1 - side]['decisions']['path']).read_text())
    assert matches(sources) is False


@pytest.mark.parametrize('side', [0, 1])
def test_different_json_is_not_a_duplicate(sources, side):
    Path(sources[side][KEY]['path']).write_text(json.dumps({'unverified': 'new obligation'}))
    assert matches(sources) is False


@pytest.mark.parametrize('side', [0, 1])
@pytest.mark.parametrize('key', [KEY, 'decisions'])
def test_missing_source_is_not_a_duplicate(sources, side, key):
    del sources[side][key]
    assert matches(sources) is False


@pytest.mark.parametrize('side', [0, 1])
@pytest.mark.parametrize('key', [KEY, 'decisions'])
def test_missing_file_still_raises(sources, side, key):
    Path(sources[side][key]['path']).unlink()
    with pytest.raises(FileNotFoundError):
        matches(sources)


@pytest.mark.parametrize('side', [0, 1])
@pytest.mark.parametrize('key', [KEY, 'decisions'])
def test_unreadable_file_still_raises(sources, side, key):
    path = Path(sources[side][key]['path'])
    path.unlink()
    path.mkdir()
    with pytest.raises(IsADirectoryError):
        matches(sources)


@pytest.mark.parametrize('side', [0, 1])
def test_malformed_decisions_error_is_not_swallowed(sources, side):
    Path(sources[side]['decisions']['path']).write_text('{malformed')
    with pytest.raises(json.JSONDecodeError):
        matches(sources)


def test_identical_malformed_json_cannot_qualify_as_duplicate(sources):
    for source in sources:
        for item in source.values():
            Path(item['path']).write_text('{malformed')
    assert matches(sources) is False
