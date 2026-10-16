import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from shared import drafting
spec=importlib.util.spec_from_file_location('role_rules',Path(__file__).resolve().parents[1]/'runtime/dk-config/scripts/repo_rules.py')
rules=importlib.util.module_from_spec(spec);spec.loader.exec_module(rules)


def test_each_role_inherits_independently(tmp_path,monkeypatch):
    monkeypatch.setattr(rules,'DATA',tmp_path/'data')
    fork,upstream=tmp_path/'fork',tmp_path/'upstream'
    rules.atomic_json(rules.drafting_file(fork,'repo'),{'commit_message':{'skill':'one:commit','host':'claude'}})
    rules.atomic_json(rules.drafting_file(upstream,'repo'),{'pr_description':{'skill':'two:pr','host':'codex'}})
    rules.atomic_json(rules.drafting_file(fork,'user'),{'issue_description':{'skill':'three:issue','host':'current'},'commit_message':'builtin'})
    result=rules.drafting_config(fork,upstream_folder=upstream)
    assert [result[k]['source'] for k in ('commit_message','pr_description','issue_description')]==['repo','upstream','user']
    rules.atomic_json(rules.drafting_file(fork,'repo'),{'pr_description':'builtin'})
    assert rules.drafting_config(fork,upstream_folder=upstream)['pr_description']=={'binding':'builtin','source':'repo'}


@pytest.mark.parametrize('role',['commit_message','pr_description','issue_description'])
def test_provider_content_never_grants_publication(tmp_path,monkeypatch,role):
    source=tmp_path/'evidence';source.write_text('verified change')
    handoff=tmp_path/'handoff';handoff.write_text(json.dumps({'contract':'dk-draft-v1','role':role,'sources':[{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}))
    monkeypatch.setattr(drafting,'invoke',lambda *a,**kw:'{"content":"Evidence-based content"}')
    result=drafting.draft({'skill':'third:writer'},role,handoff,tmp_path)
    assert result['content']=='Evidence-based content' and result['publication_authorized'] is False
    source.write_text('changed')
    with pytest.raises(ValueError,match='stale'):drafting.draft({},role,handoff,tmp_path)


def test_extra_provider_authority_and_bad_content_are_rejected(tmp_path,monkeypatch):
    source=tmp_path/'evidence';source.write_text('data')
    handoff=tmp_path/'handoff';handoff.write_text(json.dumps({'contract':'dk-draft-v1','role':'commit_message','sources':[{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}))
    calls=[]
    def invoke(*a,**kw):calls.append(kw);return '{"content":"draft", "publish":true}'
    monkeypatch.setattr(drafting,'invoke',invoke)
    with pytest.raises(ValueError,match='after one correction'):drafting.draft({},'commit_message',handoff,tmp_path)
    assert len(calls)==2


def test_legacy_read_does_not_write_or_require_installed_replacement(tmp_path,monkeypatch):
    monkeypatch.setattr(rules,'DATA',tmp_path/'data')
    path=rules.reviewer_file(tmp_path,'repo');path.write_text('{"reviewers":["code-review-claude-ad"]}')
    before=path.read_bytes()
    assert rules.reviewer_config(tmp_path)['reviewers']==[{'skill':'code-review-ad:code-review-ad','host':'claude'}]
    assert path.read_bytes()==before


def test_command_provider_receives_correction_feedback_on_stdin(tmp_path):
    source=tmp_path/'source';source.write_text('verified')
    handoff=tmp_path/'handoff.json';handoff.write_text(json.dumps({'contract':'dk-draft-v1','role':'commit_message','sources':[{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}))
    before=handoff.read_bytes()
    script=tmp_path/'provider.py';script.write_text('import json,sys,os\nfeedback=sys.stdin.read()\n'
        'assert json.load(open(sys.argv[1]))["contract"]=="dk-draft-v1"\n'
        'if os.environ["DK_PROVIDER_ATTEMPT"]=="1":\n'
        ' assert "80 characters" in feedback and "Prior response:" in feedback\n'
        ' print(json.dumps({"content":"Preserve the verified behavior"}))\n'
        'else: print(json.dumps({"content":"x"*81}))\n')
    result=drafting.draft({'command':[sys.executable,str(script),'{handoff}']},'commit_message',handoff,tmp_path)
    assert result['content']=='Preserve the verified behavior'
    assert handoff.read_bytes()==before
