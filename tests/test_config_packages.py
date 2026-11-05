from pathlib import Path
import importlib.util
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
import config
from state import write


def test_settings_precedence_and_missing_selected_skills():
    write(config.path(),{'reviewers':[{'skill':'review:a'}],'skills':{'reply':'custom:reply'}})
    write(config.path('github.com/upstream/repo'),{'reviewers':[{'skill':'review:b','required':False}]})
    write(config.path('github.com/fork/repo'),{'skills':{'description':'custom:description'}})
    effective=config.effective('github.com/fork/repo','github.com/upstream/repo')['settings']
    assert effective['reviewers']==[{'skill':'review:b','required':False}]
    assert effective['skills']['reply']=='custom:reply'
    assert effective['skills']['description']=='custom:description'
    assert config.preflight(effective,[])['missing_selected_skills']
    available=list(effective['skills'].values())+['review:b']
    assert config.preflight(effective,available)['ready']


@pytest.mark.parametrize('settings',[{'architecture':{}},{'reviewers':[{'command':['x']}]},
                                    {'reviewers':[{'skill':'x','host':'codex'}]},
                                    {'iteration':{'max_cycles':6}}])
def test_core_rejects_domain_execution_and_budget_configuration(settings):
    with pytest.raises(ValueError):config.validate(settings)


def test_distribution_validation():
    spec=importlib.util.spec_from_file_location('distribution_check',ROOT/'scripts/check.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.check()['valid']


def test_bundled_helpers_work_from_a_copied_plugin_without_an_external_repository(tmp_path):
    import shutil
    installed=tmp_path/'plugin'
    shutil.copytree(ROOT,installed,ignore=shutil.ignore_patterns('.git','__pycache__','.pytest_cache','.dependencies'))
    for script in ('dk.py','config.py','pr_config.py','publish.py','preferences.py','commit_policy.py','ci_approve.py'):
        result=subprocess.run([sys.executable,str(installed/'scripts'/script),'--help'],cwd=tmp_path,text=True,capture_output=True)
        assert result.returncode==0,result.stderr
    assert not (installed/'test-dependencies.json').exists()


def test_default_roles_resolve_to_bundled_skills():
    skills={p.parent.name for p in (ROOT/'codex/skills').glob('*/SKILL.md')}
    assert len(skills)==20
    for identity in config.DEFAULTS['skills'].values():
        owner,name=identity.split(':',1)
        assert owner=='dk-workflows' and name in skills


def test_settings_roundtrip_preserves_native_options_and_references(tmp_path):
    import json
    settings={'reviewers':[{'skill':'custom:review','required':False,'options':{'passes':['spec']}}],
              'skills':{'reply':'custom:reply'}, 'domain_settings':{'custom':'private/settings'}}
    source=tmp_path/'settings.json';source.write_text(json.dumps(settings))
    command=[sys.executable,str(ROOT/'scripts/config.py'),'--repository','github.com/owner/repo']
    result=subprocess.run([*command,'set','--file',str(source)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(config.path('github.com/owner/repo').read_text())==settings
    result=subprocess.run([*command,'show'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    effective=json.loads(result.stdout)['settings']
    for key,value in settings.items():
        assert (all(effective[key][k]==v for k,v in value.items())
                if isinstance(value,dict) else effective[key]==value)


def test_test_runner_skips_empty_runtime_suites(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('test_runner', ROOT/'scripts/test.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    (tmp_path/'tests/pr').mkdir(parents=True)
    (tmp_path/'tests/test_workflow.py').touch()
    (tmp_path/'runtime/empty/tests/__pycache__').mkdir(parents=True)
    shared = tmp_path/'runtime/shared/tests'
    shared.mkdir(parents=True)
    (shared/'test_shared.py').touch()
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    calls = []
    monkeypatch.setattr(runner.subprocess, 'run', lambda argv, **kwargs:
                        calls.append(argv) or subprocess.CompletedProcess(argv, 0))
    runner.main()
    assert len(calls) == 3
    assert str(shared) in calls[-1]
    assert all(str(tmp_path/'runtime/empty/tests') not in call for call in calls)
