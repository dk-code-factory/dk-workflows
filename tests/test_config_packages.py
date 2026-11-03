from pathlib import Path
import importlib.util
import json
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


def test_migration_is_explicit_without_cache_or_runtime_import():
    migrated=config.migrate([{'skill':'review:a','host':'current'}, {'command':['old-runner']},
                             {'skill':'dk-workflows:dk-pr-description'}],{'commit_message':'builtin'})
    assert migrated['proposed']['reviewers']==[{'skill':'review:a','required':True},
        {'skill':'dk-workflows:dk-pr-description','required':True}]
    assert migrated['unresolved_bindings']==[{'command':['old-runner']}]
    assert migrated['domain_owners']['architecture']=='dk-architecture'
    assert migrated['drafting_for_domain_owner']['commit_message']=='builtin'


@pytest.mark.parametrize('settings',[{'architecture':{}},{'reviewers':[{'command':['x']}]},
                                    {'reviewers':[{'skill':'x','host':'codex'}]},
                                    {'iteration':{'max_cycles':6}}])
def test_core_rejects_domain_execution_and_budget_configuration(settings):
    with pytest.raises(ValueError):config.validate(settings)




def test_distribution_validation():
    spec=importlib.util.spec_from_file_location('distribution_check',ROOT/'scripts/check.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.check()['valid']


def test_legacy_aliases_map_to_native_skills_and_internal_options_are_not_silently_lost():
    result=config.migrate({'reviewers':['code-review-claude-ad',
        {'skill':'custom:review','nested_review_roles':['OldInternalRole']} ]})
    assert result['proposed']['reviewers']==[{'skill':'code-review-ad:code-review-ad','required':True}]
    assert result['unresolved_bindings'][0]['nested_review_roles']==['OldInternalRole']


def test_temporary_pr_plugin_bindings_resolve_to_bundled_skills_without_rewriting_private_files():
    settings={'skills':{'reply':'dk-pr-skills:dk-pr-reply-review'},
              'reviewers':[{'skill':'review:external'}],
              'domain_settings':{'dk-pr-skills':'existing private PR settings'}}
    path=config.path('github.com/owner/repo');write(path,settings)
    effective=config.effective('github.com/owner/repo')['settings']
    assert effective['skills']['reply']=='dk-workflows:dk-pr-reply-review'
    assert effective['domain_settings']=={'dk-workflows':'existing private PR settings'}
    assert json.loads(path.read_text())==settings
    skills={p.parent.name for p in (ROOT/'codex/skills').glob('*/SKILL.md')}
    assert len(skills)==16
    for identity in effective['skills'].values():
        assert identity.split(':',1)[0]=='dk-workflows'
        assert identity.split(':',1)[1] in skills


def test_bundled_helpers_work_from_a_copied_plugin_without_an_external_repository(tmp_path):
    import shutil
    installed=tmp_path/'plugin'
    shutil.copytree(ROOT,installed,ignore=shutil.ignore_patterns('.git','__pycache__','.pytest_cache','.dependencies'))
    for script in ('dk.py','config.py','pr_config.py','publish.py'):
        result=subprocess.run([sys.executable,str(installed/'scripts'/script),'--help'],cwd=tmp_path,text=True,capture_output=True)
        assert result.returncode==0,result.stderr
    assert not (installed/'test-dependencies.json').exists()
