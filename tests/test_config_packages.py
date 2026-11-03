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
        {'skill':'dk-pr-skills:dk-pr-description','required':True}]
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
