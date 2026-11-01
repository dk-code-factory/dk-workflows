"""Reduce prompt duplication while preserving governing evidence and reassessment."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preparation_fixtures import Package, prep, response, retain


def test_analyst_keeps_governing_sections_and_complete_input_index():
    procedure = (Path(prep.__file__).parents[1] / 'references/preparation.md').read_text()
    decisions = {'exact_choice': 'Preserve the agreed interface and its exception.'}
    sources = {'user_instructions/0': {'path': '/private/exact-answer.json', 'sha256': 'original'}}
    prompt = prep.analysis_prompt({'mode': 'standalone'}, {}, decisions, sources, {})
    for heading in ('Analyze', 'Refresh', 'Human answers and synchronization',
                    'Work selection and completion evidence'):
        section = procedure.split('## ' + heading + '\n', 1)[1].split('\n## ', 1)[0]
        assert section in prompt
    for heading in ('Collect and run', 'Fresh workflow storage and compatibility',
                    'Rejected model output and recovery'):
        assert '\n## ' + heading not in prompt
    assert 'instruction-sources.md' in prompt
    assert 'Missing required procedures or evidence block preparation.' in prompt
    inventory = json.loads(prompt.split('INPUT INDEX (read original paths):\n', 1)[1])
    assert inventory['decisions'] == decisions
    assert inventory['sources'] == sources
    assert len(prompt) < len(procedure)


def test_unrecognized_procedure_sections_remain_governing():
    procedure = ('# Engineering preparation\nIntroductory requirements.\n\n'
                 '## New analysis constraint\nKeep this new requirement.\n\n'
                 '### Nested requirement\nKeep this too.\n\n'
                 '## Rejected model output and recovery\nCoordinator recovery.\n\n'
                 '## Future requirement\nUnknown sections still govern.\n')
    instructions = prep.analyst_procedure(procedure)
    assert instructions.startswith('# Engineering preparation\nIntroductory requirements.')
    assert 'Keep this new requirement.' in instructions
    assert '### Nested requirement\nKeep this too.' in instructions
    assert 'Unknown sections still govern.' in instructions
    assert 'Coordinator recovery.' not in instructions


@pytest.mark.parametrize('full', [False, True])
def test_reassessment_retains_governing_parent_without_duplicate_conclusions(tmp_path, full):
    package = Package(tmp_path)
    first = package.prepare()
    parent = prep.read(first['artifact'])
    # Future governing fields must survive along with existing evidence and history.
    parent['future_constraint'] = {'choice': 'Retain this exact condition.'}
    original = copy.deepcopy(parent)
    selected = set(parent['graph']) if full else {next(iter(parent['graph']))}
    prompt = prep.reassessment_prompt('Original request.', first['artifact'], parent,
                                     ['transform.py'], ['user_instructions/0'], selected,
                                     ['path:new.py'], full=full)
    binding = json.JSONDecoder().raw_decode(prompt.split('REASSESSMENT ', 1)[1].split('\n', 1)[1])[0]
    assert binding['parent'] == prep.model_view(original)
    assert binding['parent']['analysis'] == original['analysis']
    assert binding['parent']['graph'] == original['graph']
    assert binding['parent']['future_constraint'] == original['future_constraint']
    assert len(json.dumps(binding['parent'])) < len(json.dumps(original))
    assert binding['parent_path'] == first['artifact']
    assert binding['affected_ids'] == sorted(selected)
    assert binding['changed_paths'] == ['transform.py']
    assert binding['changed_sources'] == ['user_instructions/0']
    assert binding['unmapped_changes'] == ['path:new.py']
    assert binding['full_reassessment'] is full
    assert 'affected' not in binding and 'remaining_index' not in binding
    assert 'Resolve affected_ids through parent.graph' in prompt
    assert parent == original


def test_compact_incremental_request_preserves_analysis_and_zero_call_reuse(tmp_path):
    package = Package(tmp_path)
    initial_prompts, refreshed_prompts = [], []
    def initial(prompt, checkout):
        initial_prompts.append(prompt)
        return response()
    first = prep.prepare(package.manifest_path, package.versions, analyzer=initial)
    parent = prep.read(first['artifact'])
    package.write('user.json', {'instruction': 'Keep the supported transform and its current interface.'})
    def reassess(prompt, checkout):
        refreshed_prompts.append(prompt)
        binding = json.JSONDecoder().raw_decode(prompt.split('REASSESSMENT ', 1)[1].split('\n', 1)[1])[0]
        assert binding['parent'] == prep.model_view(parent)
        return retain(parent)
    refreshed = prep.prepare(package.manifest_path, package.versions,
                             previous_preparation=first['artifact'], analyzer=reassess)
    artifact = prep.load(refreshed['artifact'], package.context, package.decisions)
    assert len(initial_prompts) == len(refreshed_prompts) == 1
    assert artifact['analysis'] == parent['analysis'] and artifact['graph'] == parent['graph']
    assert refreshed['readiness'] == 'ready'
    assert refreshed['refresh']['mode'] == 'incremental'
    assert refreshed['refresh']['model_call_count'] == 1
    assert refreshed['refresh']['changed_sources'] == ['user_instructions/0']
    def unexpected(*args):
        pytest.fail('Unchanged preparation must not invoke a model')
    reused = prep.prepare(package.manifest_path, package.versions,
                         previous_preparation=refreshed['artifact'], analyzer=unexpected)
    assert reused['artifact'] == refreshed['artifact']
    assert reused['refresh']['model_call_count'] == 0
