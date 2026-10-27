import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch
import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
from shared import bindings as b
from shared import executors as e


def plugin(tmp_path, host='claude', name='third-party', skill='review'):
    root=tmp_path/name
    (root/f'.{host}-plugin').mkdir(parents=True)
    (root/'native/skills'/skill).mkdir(parents=True)
    (root/'native/skills'/skill/'SKILL.md').write_text('Review read-only')
    (root/f'.{host}-plugin/plugin.json').write_text(json.dumps({'name':name,'version':'1.2.3','skills':'./native/skills/'}))
    return root


def registry(tmp_path, root, monkeypatch, records=None):
    home=tmp_path/'profile';(home/'plugins').mkdir(parents=True)
    (home/'plugins/installed_plugins.json').write_text(json.dumps({'plugins':records or {'third-party@test':[{'installPath':str(root),'version':'1.2.3'}]}}))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(home));monkeypatch.setenv('DK_HOST','claude')
    monkeypatch.setattr(b.shutil,'which',lambda n:'/bin/'+n)


def test_registry_manifest_selects_native_directory(tmp_path,monkeypatch):
    root=plugin(tmp_path);registry(tmp_path,root,monkeypatch)
    r=b.resolve({'skill':'third-party:review','host':'current'})
    assert r['executor']=='claude' and r['version']=='1.2.3'
    assert Path(r['procedure'])==root/'native/skills/review/SKILL.md'


def test_registry_rejects_ambiguity_without_picking_cached_version(tmp_path,monkeypatch):
    root=plugin(tmp_path);registry(tmp_path,root,monkeypatch,{'third-party@one':[{'installPath':str(root)}],'third-party@two':[{'installPath':str(root)}]})
    with pytest.raises(ValueError,match='found 2'):b.resolve('third-party:review')


def test_claude_command_is_a_selectable_skill(tmp_path,monkeypatch):
    root=plugin(tmp_path);(root/'commands').mkdir();(root/'commands/review-pr.md').write_text('Read-only command')
    registry(tmp_path,root,monkeypatch)
    assert b.resolve('third-party:review-pr')['procedure'].endswith('commands/review-pr.md')


def test_codex_registry_version_is_authoritative(tmp_path,monkeypatch):
    root=tmp_path/'plugins/cache/fixture/third-party/1.2.3';root.mkdir(parents=True)
    p=plugin(root,host='codex')
    # CLI may supply an explicit installedPath; no cache globbing is needed.
    monkeypatch.setenv('DK_HOST','codex');monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    monkeypatch.setattr(b.shutil,'which',lambda n:'/bin/'+n)
    record={'name':'third-party','pluginId':'third-party@fixture','marketplaceName':'fixture','version':'1.2.3','installedPath':str(p)}
    monkeypatch.setattr(b.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess([],0,json.dumps({'installed':[record]}),''))
    assert b.resolve('third-party:review')['root']==str(p)
    record['version']='9.9.9'
    with pytest.raises(ValueError,match='disagree'):b.resolve('third-party:review')


@pytest.mark.parametrize('value',[{'skill':'a:b','host':'bad'}, {'command':'echo hi'}, {'command':['echo','hi']}, 'dk-pr-review', 'dk-pr-iter'])
def test_invalid_and_recursive_bindings(value):
    with pytest.raises(ValueError):b.normalize(value)


def test_legacy_aliases_are_normalized_without_mutation():
    value=['code-review-claude-ad','code-review-claude-pr-review-toolkit']
    result=b.selection(value)
    assert result==[{'skill':'code-review-ad:code-review-ad','host':'claude'},{'skill':'pr-review-toolkit:review-pr','host':'claude'}]
    assert value[0]=='code-review-claude-ad'
    assert b.selection([])==[]


def test_command_uses_argument_vector_no_shell(tmp_path,monkeypatch):
    script=tmp_path/'provider.py';script.write_text('import sys,json\nprint(json.dumps({"received":sys.argv[1:]}))')
    handoff=tmp_path/'handoff ; $(false).json';handoff.write_text('{}')
    value={'command':[sys.executable,str(script),'{handoff}','literal;$(false)']}
    actual=json.loads(e.invoke(value,handoff,tmp_path,'ignored'))
    assert actual['received']==[str(handoff),'literal;$(false)']


def test_command_recursion_is_blocked(tmp_path,monkeypatch):
    value={'command':[sys.executable,'{handoff}']};monkeypatch.setenv('DK_BINDING_STACK',json.dumps([b.key(value)]))
    with pytest.raises(ValueError,match='Recursive'):e.invoke(value,tmp_path/'handoff',tmp_path,'')


def test_claude_executor_preserves_read_only_and_detects_failed_envelope(tmp_path,monkeypatch):
    monkeypatch.setenv('DK_HOST','claude')
    def run(argv,**kw):
        assert '--permission-mode' in argv and 'plan' in argv
        assert '--disallowedTools' in argv and 'Edit,Write,NotebookEdit' in argv
        return subprocess.CompletedProcess(argv,0,json.dumps({'subtype':'error_max_turns','is_error':True,'result':'exhausted'}),'')
    monkeypatch.setattr(e.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='exhausted'):e.execute('Read evidence',tmp_path)


def test_native_executor_recursion_is_blocked(tmp_path,monkeypatch):
    monkeypatch.setenv('DK_BINDING_STACK',json.dumps(['third-party:review']))
    with pytest.raises(ValueError,match='Recursive'):e.execute('Review',tmp_path,resolved={'executor':'claude','binding':{'skill':'third-party:review'}})


def test_owned_plugin_lifecycle_surrounds_read_only_execution_even_on_failure(tmp_path,monkeypatch):
    root=plugin(tmp_path,host='codex',name='owned')
    manifest=root/'.codex-plugin/plugin.json';data=json.loads(manifest.read_text());data['repository']='https://github.com/dk-code-factory/owned';manifest.write_text(json.dumps(data))
    (root/'scripts').mkdir();events=tmp_path/'events.jsonl'
    (root/'scripts/update.py').write_text('import json,os,sys\nfrom pathlib import Path\n'
        'p=Path(os.environ["DK_TEST_EVENTS"])\n'
        'with p.open("a") as f:f.write(sys.argv[1]+"\\n")\n'
        'print(json.dumps({"token":"fixture-token","action":"continue","identity":{"name":"owned"}}))\n')
    monkeypatch.setenv('DK_TEST_EVENTS',str(events))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    policy = tmp_path / 'config/dk-code-factory/updates.json'
    policy.parent.mkdir(parents=True)
    policy.write_text('{"mode":"off"}')
    from shared.runtime_bundles import updater
    api = updater()
    installed = api.installed
    def identity(path, host):
        if path == root:
            return {'name':'owned','host':host,'marketplace':None,'version':'1.2.3',
                    'repository':'dk-code-factory/owned','pinned':True,'scope':'user','root':str(path)}
        return installed(path, host)
    monkeypatch.setattr(api, 'installed', identity)
    observed = []
    def process(prompt,checkout,host,env,*args):
        receipt = json.loads(env['DK_UPDATE_MANAGED_RECEIPT'])
        observed.append(receipt['token'])
        assert env['DK_UPDATE_PARENT'] == receipt['token']
        assert Path(receipt['runtime_root']).is_dir()
        assert 'do not duplicate' in prompt
        raise RuntimeError('interrupted provider')
    monkeypatch.setattr(e,'execute_process',process)
    resolved={'executor':'codex','binding':{'skill':'owned:review'},'procedure':str(root/'native/skills/review/SKILL.md'),'root':str(root)}
    with pytest.raises(RuntimeError,match='interrupted provider'):e.execute('Review evidence',tmp_path,resolved=resolved)
    ledger = json.loads((tmp_path / 'state/dk-code-factory/updates/state.json').read_text())
    assert ledger['invocations'][observed[0]]['complete']
    assert not events.exists()


def test_third_party_updater_is_not_invoked(tmp_path,monkeypatch):
    root=plugin(tmp_path,host='codex')
    (root/'scripts').mkdir();(root/'scripts/update.py').write_text('raise RuntimeError("must not run")')
    assert e.managed_update({'root':str(root),'executor':'codex'},dict(os.environ),'entry') is None


def test_by_name_agent_and_custom_command_resources_are_fingerprinted(tmp_path,monkeypatch):
    import importlib.util
    file=Path(__file__).resolve().parents[1]/'runtime/dk-pr-review/scripts/run_reviewers.py'
    spec=importlib.util.spec_from_file_location('binding_descriptor',file);runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    root=plugin(tmp_path);registry(tmp_path,root,monkeypatch)
    (root/'agents').mkdir();agent=root/'agents/security.md';agent.write_text('Check authorization')
    (root/'custom-commands').mkdir();command=root/'custom-commands/review-pr.md';command.write_text('Dispatch security agent')
    manifest=root/'.claude-plugin/plugin.json';data=json.loads(manifest.read_text());data['commands']='./custom-commands/';manifest.write_text(json.dumps(data))
    first=runner.describe_reviewer('third-party:review')
    retained_agent = Path(first['execution']['root']) / agent.relative_to(root)
    assert str(retained_agent) in {s['path'] for s in first['sources']}
    assert retained_agent.read_text() == agent.read_text()
    retained_command = Path(first['execution']['root']) / command.relative_to(root)
    assert str(retained_command) in {s['path'] for s in first['sources']}
    assert retained_command.read_text() == command.read_text()
    agent.write_text('Changed authorization method')
    assert runner.describe_reviewer('third-party:review')!=first


@pytest.mark.parametrize('detail',['Usage limit reached','You hit your session limit','Insufficient credits'])
def test_explicit_provider_exhaustion_is_classified(detail):
    with pytest.raises(e.ProviderExhausted):e.provider_failure(detail)


def test_authentication_and_ordinary_failures_remain_blocking():
    for detail in ['invalid access token; insufficient credits','network timeout','max turns exhausted']:
        with pytest.raises(RuntimeError) as failure:e.provider_failure(detail)
        assert not isinstance(failure.value,e.ProviderExhausted)


def test_usage_exhaustion_reaches_reviewers_as_explicit_skipped_coverage(tmp_path,monkeypatch):
    import importlib.util
    file=Path(__file__).resolve().parents[1]/'runtime/dk-pr-review/scripts/run_reviewers.py'
    spec=importlib.util.spec_from_file_location('exhausted_provider',file);runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    patch_file=tmp_path/'diff.patch';patch_file.write_text('')
    handoff=tmp_path/'handoff.json';handoff.write_text(json.dumps({'patch':str(patch_file)}))
    def invoke(*a,**kw):raise e.ProviderExhausted('Usage limit reached')
    monkeypatch.setattr(runner,'invoke',invoke)
    result=runner.run_skill_reviewer({'skill':'provider:review'},tmp_path,handoff,tmp_path)
    assert result=={'status':'tokens_exhausted','findings':[],'message':'Usage limit reached'}


def test_command_quota_failure_preserves_exhaustion_in_real_subprocess(tmp_path):
    import importlib.util
    file=Path(__file__).resolve().parents[1]/'runtime/dk-pr-review/scripts/run_reviewers.py'
    spec=importlib.util.spec_from_file_location('quota_transport',file);runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    patch_file=tmp_path/'diff.patch';patch_file.write_text('')
    handoff=tmp_path/'handoff.json';handoff.write_text(json.dumps({'patch':str(patch_file)}))
    script=tmp_path/'quota.py';script.write_text('import sys\nprint("Usage limit reached",file=sys.stderr)\nsys.exit(1)\n')
    result=runner.run_skill_reviewer({'command':[sys.executable,str(script),'{handoff}']},tmp_path,handoff,tmp_path)
    assert result['status']=='tokens_exhausted' and result['findings']==[]



def test_codex_inventory_reused_only_within_operation_and_manifests_stay_live(tmp_path, monkeypatch):
    root = plugin(tmp_path, host='codex')
    monkeypatch.setenv('DK_HOST', 'codex')
    monkeypatch.setattr(b.shutil, 'which', lambda n: '/bin/' + n)
    record = {'name': 'third-party', 'pluginId': 'third-party@fixture',
              'version': '1.2.3', 'installedPath': str(root)}
    inventory = {'installed': [record]}
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess([], 0, json.dumps(inventory), '')
    monkeypatch.setattr(b.subprocess, 'run', run)
    with b.registry_snapshot():
        for _ in range(5):
            assert b.resolve('third-party:review')['version'] == '1.2.3'
        with b.registry_snapshot():
            b.resolve('third-party:review')
        assert len(calls) == 1
        manifest = root / '.codex-plugin/plugin.json'
        original = manifest.read_text()
        manifest.write_text(original.replace('1.2.3', '9.9.9'))
        with pytest.raises(ValueError, match='disagree'):
            b.resolve('third-party:review')
        manifest.write_text(original)
    inventory['installed'] = []
    with b.registry_snapshot():
        with pytest.raises(ValueError, match='found 0'):
            b.resolve('third-party:review')
    assert len(calls) == 2


def test_registry_failure_not_cached_and_profile_change_requires_fresh_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(b.shutil, 'which', lambda n: '/bin/' + n)
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args[0], 30)
        return subprocess.CompletedProcess([], 0, '{"installed": []}', '')
    monkeypatch.setattr(b.subprocess, 'run', run)
    with b.registry_snapshot():
        with pytest.raises(subprocess.TimeoutExpired):
            b.codex_plugins()
        assert b.codex_plugins() == []
        assert b.codex_plugins() == []
        monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'other-profile'))
        assert b.codex_plugins() == []
    assert len(calls) == 3



def test_describe_cli_shares_inventory_across_reviewer_descriptors(tmp_path, monkeypatch, capsys):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'runtime/dk-pr-review/scripts/run_reviewers.py'
    spec = importlib.util.spec_from_file_location('inventory_runner', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    root = plugin(tmp_path, host='codex')
    style = root / 'native/skills/style'
    style.mkdir()
    (style / 'SKILL.md').write_text('Review style read-only')
    monkeypatch.setenv('DK_HOST', 'codex')
    monkeypatch.setattr(b.shutil, 'which', lambda n: '/bin/' + n)
    record = {'name': 'third-party', 'pluginId': 'third-party@fixture',
              'version': '1.2.3', 'installedPath': str(root)}
    calls = []
    def registry_run(*args, **kwargs):
        if args[0][0] == 'codex':
            calls.append(args[0])
        return subprocess.CompletedProcess([], 0, json.dumps({'installed': [record]}), '')
    monkeypatch.setattr(b.subprocess, 'run', registry_run)
    monkeypatch.setattr(runner, 'run', lambda *args: json.dumps({'reviewers': [
        {'skill': 'third-party:review'}, {'skill': 'third-party:style'}]}))
    context = tmp_path / 'context.json'
    context.write_text(json.dumps({'repository': str(tmp_path), 'url': 'https://github.com/o/r/pull/1'}))
    monkeypatch.setattr(sys, 'argv', ['run_reviewers.py', '--context', str(context), '--describe'])
    for expected_calls in [1, 2]:
        runner.main()
        assert len(json.loads(capsys.readouterr().out)['reviewers']) == 2
        assert len(calls) == expected_calls



@pytest.mark.parametrize('invalid', [[], {'installed': None}, {'installed': ['invalid']}])
def test_malformed_registry_response_not_cached(monkeypatch, invalid):
    responses = iter([invalid, {'installed': []}])
    monkeypatch.setattr(b.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess([], 0, json.dumps(next(responses)), ''))
    with b.registry_snapshot():
        with pytest.raises(ValueError, match='installed array'):
            b.codex_plugins()
        assert b.codex_plugins() == []


@pytest.mark.parametrize('declared,registered,valid', [
    (None, '1.2.3', True), ('1.2.3', None, True), (None, None, True),
    ('1.2.3', '1.2.3', True), ('9.9.9', '1.2.3', False),
])
def test_discovery_and_retained_execution_share_version_contract(tmp_path, monkeypatch, declared, registered, valid):
    from shared.runtime_bundles import retained_binding
    root = plugin(tmp_path)
    manifest = root / '.claude-plugin/plugin.json'
    value = json.loads(manifest.read_text())
    if declared is None:
        value.pop('version')
    else:
        value['version'] = declared
    manifest.write_text(json.dumps(value))
    registry(tmp_path, root, monkeypatch, {'third-party@test': [
        {'installPath': str(root), **({'version': registered} if registered else {})}]})
    binding = {'skill': 'third-party:review', 'host': 'claude'}
    if not valid:
        with pytest.raises(ValueError, match='disagree'):
            b.resolve(binding)
        # Retained validation compares the same pair independently of discovery.
        resolved = {'binding': binding, 'identity': b.key(binding), 'executor': 'claude',
                    'root': str(root), 'procedure': str(root / 'native/skills/review/SKILL.md'),
                    'version': registered}
        retained = retained_binding(resolved)
        with pytest.raises(ValueError, match='disagree'):
            with b.pinned_bindings({b.key(binding): {'execution': retained}}):
                pytest.fail('mismatched retained version accepted')
        return
    retained = retained_binding(b.resolve(binding))
    descriptor = {b.key(binding): {'execution': retained}}
    with b.pinned_bindings(descriptor):
        assert b.resolve(binding) == retained
        monkeypatch.setattr(e, 'execute_process', lambda *a, **kw: '{"findings":[]}')
        handoff = tmp_path / 'handoff.json'
        handoff.write_text('{}')
        assert json.loads(e.invoke(binding, handoff, tmp_path, 'Review')) == {'findings': []}
    source = Path(retained['procedure'])
    source.chmod(0o600)
    source.write_text('Tampered instructions')
    with pytest.raises((ValueError, RuntimeError), match='changed'):
        with b.pinned_bindings(descriptor):
            pytest.fail('tampered retained package accepted')
