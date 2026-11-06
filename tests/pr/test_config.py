"""Private domain settings preserve source text and scope precedence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('domain_config',ROOT/'scripts/pr_config.py')
config=importlib.util.module_from_spec(spec);spec.loader.exec_module(config)


def test_user_rules_and_completion_prompts_preserve_exact_sources(monkeypatch,tmp_path):
    monkeypatch.setattr(config,'identities',lambda *_:['remote:github.com/fork/repo','remote:github.com/base/repo'])
    data=Path(os.environ['XDG_DATA_HOME'])
    rules=data/'codex-repo-rules/user/rules.json';rules.parent.mkdir(parents=True)
    rules.write_text(json.dumps({'rules':[{'id':'r1','text':'Preserve this user instruction'}],'conflicts':[]}))
    result=config.main(['rules','show'])
    assert result['file']==str(rules) and result['value']['rules'][0]['id']=='r1'
    prompt=data/'dk-pr-iter/completion-prompts/user.txt';prompt.parent.mkdir(parents=True)
    prompt.write_text('Exact existing user completion instructions.\n')
    result=config.main(['completion','show'])
    assert result['value']['prompt']==prompt.read_text() and result['file']==str(prompt)
    prompt.write_text('')
    import pytest
    with pytest.raises(ValueError,match='Malformed'):
        config.main(['completion','show'])


def test_drafting_inherits_individual_roles(monkeypatch):
    identity='remote:github.com/fork/repo'
    monkeypatch.setattr(config,'identities',lambda *_:[identity])
    data=Path(os.environ['XDG_DATA_HOME'])/'codex-repo-rules';data.mkdir(parents=True)
    (data/'drafting.json').write_text(json.dumps({'commit_message':'user:commit','issue_description':'user:issue'}))
    repository=data/hashlib.sha256(identity.encode()).hexdigest()[:24];repository.mkdir()
    (repository/'drafting.json').write_text(json.dumps({'commit_message':'repo:commit'}))
    result=config.main(['drafting','show'])
    assert result['value']=={'commit_message':'repo:commit','issue_description':'user:issue'}


def test_structured_drafting_selections_preserve_options(monkeypatch):
    monkeypatch.setattr(config,'identities',lambda *_:['remote:github.com/owner/repo'])
    data=Path(os.environ['XDG_DATA_HOME'])/'codex-repo-rules';data.mkdir(parents=True)
    original={'pr_description':{'skill':'custom:description','options':{'style':'concise'}}}
    (data/'drafting.json').write_text(json.dumps(original))
    assert config.main(['drafting','show'])['value']==original


def test_split_stage_reads_preserve_scoped_policies_and_exact_pr_choices(monkeypatch):
    chain = ['remote:github.com/fork/repo', 'remote:github.com/upstream/repo']
    monkeypatch.setattr(config, 'identities', lambda *_: chain)
    config.pr_size.configure(['user'], 'set', scope='user',
                             settings={'added_lines_threshold': 700})
    config.pr_size.configure([chain[1]], 'set', settings={'split_related': 'manual'})
    config.pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'disabled'})
    pr = 'https://github.com/upstream/repo/pull/7'
    instruction = 'Keep the migration together.\nSplit only independent changes.\n'
    config.pr_partition_policy.configure(pr, 'instructions', reason='User conditions',
                                          source='user request', instructions=instruction)
    config.pr_partition_policy.configure(pr, 'disable', reason='Whole-PR opt-out',
                                          source='later user request')
    root = Path(os.environ['XDG_DATA_HOME'])

    def snapshot():
        return {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in root.rglob('*') if p.is_file()}

    original = snapshot()
    result = config.main(['--cwd', '.', '--pr', pr, 'pr-size', 'list'])
    assert result['pr_size'] == {'added_lines_threshold': 700, 'split_related': 'manual',
                                'evacuate_unrelated': 'disabled'}
    assert result['settings']['added_lines_threshold']['source'] == 'user'
    assert result['settings']['split_related']['source'] == 'upstream'
    assert result['settings']['evacuate_unrelated']['source'] == 'repo'
    policy = config.main(['--cwd', '.', '--pr', pr, 'pr-partition', 'show'])
    assert policy['enabled'] is False and policy['instructions'] == instruction
    assert policy['instructions_source']['source'] == 'user request'
    assert len(policy['history']) == 2
    assert snapshot() == original
    other = config.main(['--pr', pr.replace('/7', '/8'), 'pr-partition', 'show'])
    assert other['enabled'] is True and other['configured'] is False
    assert snapshot() == original

def test_drafting_uses_shared_invocation_and_preserves_schema_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'identities', lambda *_: ['remote:github.com/owner/repo'])
    selection = {'skill': 'team:writer', 'instructions': 'Preserve this original prompt.\n',
                 'agent': {'model': 'chosen', 'reasoning_effort': 'high'}, 'options': {'style': 'concise'}}
    value = {'$schema': 'drafting.schema.json', 'pr_description': selection}
    source = tmp_path/'drafting-input.json'
    source.write_text(json.dumps(value))
    written = config.main(['drafting', 'set', '--scope', 'user', '--file', str(source)])
    assert json.loads(Path(written['file']).read_text()) == value
    assert config.main(['drafting', 'show'])['value'] == {'pr_description': selection}


@pytest.mark.parametrize('selection', [
    '/skills/writer', {'skill': 'writer', 'agent': {'model': ''}},
    {'skill': 'writer', 'agent': {'reasoning_effort': 'unknown'}},
    {'skill': 'writer', 'id': 'old-style-id'},
])
def test_malformed_drafting_calls_are_rejected_before_saving(monkeypatch, tmp_path, selection):
    monkeypatch.setattr(config, 'identities', lambda *_: ['remote:github.com/owner/repo'])
    source = tmp_path/'invalid.json'
    source.write_text(json.dumps({'pr_description': selection}))
    with pytest.raises(ValueError):
        config.main(['drafting', 'set', '--scope', 'user', '--file', str(source)])
    assert not (Path(os.environ['XDG_DATA_HOME'])/'codex-repo-rules/drafting.json').exists()
