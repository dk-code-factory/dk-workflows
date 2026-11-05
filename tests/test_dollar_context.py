"""Host instruction drift cannot be treated as a candidate optimization."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent/'fixtures'))
import dollar_context


def seed(root):
    root.mkdir(parents=True)
    rows = [
        {'type':'session_meta','payload':{'base_instructions':{'text':'Pinned host'},
            'cli_version':'1', 'model_provider':'same'}},
        {'type':'turn_context','payload':{'model':'same','effort':'high'}},
        {'type':'response_item','payload':{'type':'message','role':'developer',
            'content':[{'text':'Pinned guidance'}]}},
        {'type':'response_item','payload':{'type':'message','role':'user',
            'content':[{'text':'Work in '+str(root)}]}},
        {'type':'response_item','payload':{'type':'message','role':'assistant',
            'content':[{'text':'Different ordinary result: '+str(root)}]}},
    ]
    (root/'raw.jsonl').write_text('\n'.join(map(json.dumps,rows))+'\n')
    (root/'private-rollout-reference.json').write_text(json.dumps({'path':str(root/'raw.jsonl')}))


def test_starting_context_normalizes_only_arm_paths_and_detects_metadata_or_effective_drift(tmp_path):
    for i in range(1,4):
        for arm in ('baseline','candidate'):
            seed(tmp_path/(str(i)+'-'+arm))
    assert dollar_context.audit(tmp_path)['passed']
    target = tmp_path/'2-baseline/raw.jsonl'
    original = target.read_text()
    target.write_text(original.replace('Pinned host','Different base template'))
    result = dollar_context.audit(tmp_path)
    assert not result['passed']
    assert result['distinct_context_counts']['base_instruction_metadata_sha256'] == 2
    assert result['distinct_context_counts']['effective_initial_messages_sha256'] == 1
    target.write_text(original.replace('Pinned guidance','Different skill inventory'))
    result = dollar_context.audit(tmp_path)
    assert not result['passed']
    assert result['distinct_context_counts']['effective_initial_messages_sha256'] == 2
    target.unlink()
    assert not dollar_context.audit(tmp_path)['passed']
