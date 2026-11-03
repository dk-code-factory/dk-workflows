"""Existing private settings keep their meaning and location after extraction."""
import hashlib
import importlib.util
import json
from pathlib import Path
import os

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('domain_config',ROOT/'scripts/config.py')
config=importlib.util.module_from_spec(spec);spec.loader.exec_module(config)


def test_legacy_user_rules_and_completion_prompts_are_not_lost(monkeypatch,tmp_path):
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
