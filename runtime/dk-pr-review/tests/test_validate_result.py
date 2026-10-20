import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1]/'scripts/validate_result.py'
spec = importlib.util.spec_from_file_location('validate_result', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_validator_checks_full_contract_and_changed_line(tmp_path):
    patch = tmp_path/'diff.patch'
    patch.write_text('diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n')
    result = tmp_path/'result.json'
    finding = {'severity': 'Major', 'path': 'x.py', 'line': 1,
               'message': 'breaks callers', 'fix': 'restore behavior'}
    result.write_text(json.dumps({'findings': [finding]}))
    command = [sys.executable, str(SCRIPT), '--patch', str(patch), '--result', str(result)]
    assert subprocess.run(command, capture_output=True, text=True).returncode == 0
    result.write_text(json.dumps({'findings': [{**finding, 'line': 2}]}))
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 1
    assert 'added line' in failed.stderr
    with pytest.raises(ValueError, match='expected one JSON object'):
        module.validate([], module.added_lines(patch.read_text()))


def test_validation_errors_identify_field_and_bound_large_line_evidence():
    valid = {'severity': 'Major', 'path': 'x.py', 'line': 1, 'message': 'Defect', 'fix': 'Restore'}
    changed = {'x.py': set(range(1, 100001))}
    for field, value in [('severity', 'Major|Minor|Nit'), ('path', 'elsewhere.py'),
                         ('line', 100001), ('message', ''), ('fix', False)]:
        with pytest.raises(ValueError) as caught:
            module.validate({'findings': [{**valid, field: value}]}, changed)
        assert '/findings/0/' + field in str(caught.value)
        assert len(str(caught.value)) < 500
    missing = {k: v for k, v in valid.items() if k != 'fix'}
    with pytest.raises(ValueError, match="missing fields: \\['fix'\\]"):
        module.validate({'findings': [missing]}, changed)


def test_incomplete_is_valid_only_with_empty_findings_and_a_reason():
    valid = {'status': 'incomplete', 'findings': [], 'message': 'Source unavailable'}
    assert module.validate_response(valid, {}) == valid
    for changed in ({'message': ''}, {'findings': [{}]}, {'extra': True}):
        with pytest.raises(ValueError):
            module.validate_response({**valid, **changed}, {})
    with pytest.raises(ValueError):
        module.validate(valid, {})  # A stopped review cannot pass the completed-result CLI.


def test_schema_is_available_without_patch_and_has_valid_examples():
    result = subprocess.run([sys.executable, str(SCRIPT), '--schema'],
                            text=True, capture_output=True, check=True)
    schema = json.loads(result.stdout)
    assert schema['oneOf'][0]['additionalProperties'] is False
    for example in schema['examples']:
        module.validate_response(example, {'src/example.py': {1}})
