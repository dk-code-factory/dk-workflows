#!/usr/bin/env python3
"""Validate independent distributions, links, Python syntax and version identity."""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def version(value):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value):
        raise ValueError('Stable semantic version required')
    return tuple(map(int,value.split('.')))


def package(root):
    manifests=[json.loads((root/f'.{host}-plugin/plugin.json').read_text()) for host in ('codex','claude')]
    assert manifests[0]['name']==manifests[1]['name']
    assert manifests[0]['version']==manifests[1]['version']
    version(manifests[0]['version'])
    entries=[]
    for host,manifest in zip(('codex','claude'),manifests):
        directory=(root/manifest['skills']).resolve()
        assert directory.is_relative_to(root)
        skills=sorted(directory.glob('*/SKILL.md'))
        assert skills
        entries.append([p.parent.name for p in skills])
        for path in skills:
            text=path.read_text()
            assert text.startswith('---\n') and re.search(r'(?m)^name: '+re.escape(path.parent.name)+'$',text)
            assert re.search(r'(?m)^description: .+',text)
    assert entries[0]==entries[1]
    for name in entries[0]:
        source=root/'codex/skills'/name/'SKILL.md'
        target=root/'claude/skills'/name/'SKILL.md'
        assert source.read_bytes()==target.read_bytes(),'Run scripts/sync_skills.py: '+name
    if manifests[0]['name']=='dk-workflows':
        assert entries[0]==['dk-ci', 'dk-ci-flakes', 'dk-commit-message', 'dk-commit-split',
                            'dk-config', 'dk-issue', 'dk-pr-decisions',
                            'dk-pr-description', 'dk-pr-feedback-address',
                            'dk-pr-feedback-read', 'dk-pr-feedback-reply', 'dk-pr-fix',
                            'dk-pr-iter', 'dk-pr-publish', 'dk-pr-review', 'dk-pr-review-post',
                            'dk-pr-reviewers-run', 'dk-pr-split']
    for name in ('.agents/plugins/marketplace.json','.claude-plugin/marketplace.json'):
        catalog=json.loads((root/name).read_text())
        assert catalog['name']==manifests[0]['name']
        assert [p['name'] for p in catalog['plugins']]==[manifests[0]['name']]
    return {'name':manifests[0]['name'],'version':manifests[0]['version'],'skills':entries[0]}


def check(base=None):
    roots=[ROOT]
    distributions=[package(root) for root in roots]
    for root in roots:
        for path in root.rglob('*'):
            if any(part in {'.git','__pycache__','.pytest_cache','.dependencies'} for part in path.relative_to(root).parts):continue
            if path.is_symlink():assert path.resolve().is_relative_to(root),str(path)
            if not path.is_file():continue
            if path.suffix=='.py':ast.parse(path.read_text(),filename=str(path))
            if path.suffix in {'.md','.py','.json','.yml','.yaml'}:
                text=path.read_text()
                assert ('/'+'home/') not in text and ('/'+'extra/') not in text,'Machine path: '+str(path)
            if path.suffix=='.md':
                for link in re.findall(r'\]\(([^)]+)\)',text):
                    if '://' in link or link.startswith('#'):continue
                    target=(path.parent/link.split('#')[0]).resolve()
                    assert target.is_relative_to(root) and target.is_file(),str(path)+': '+link
    for folder in ('runtime','scripts'):
        for path in sorted((ROOT/folder).rglob('*.py')):
            relative=path.relative_to(ROOT)
            if 'tests' in relative.parts or '__pycache__' in relative.parts:continue
            if relative.as_posix() in {'scripts/check.py','scripts/version.py','scripts/test.py'}:continue
            text=path.read_text()
            for forbidden in ('codex exec','claude -p','--ephemeral','shared.executors','preparation_graph','architecture_packet'):
                assert forbidden not in text,'Unexpected core execution/domain integration: '+str(relative)+': '+forbidden
    if base:
        previous=json.loads(subprocess.check_output(['git','show',base+':.codex-plugin/plugin.json'],text=True,cwd=ROOT))
        changes=subprocess.check_output(['git','diff','--name-only',base,'HEAD'],text=True,cwd=ROOT).splitlines()
        if any(p.startswith(('codex/','claude/','runtime/','scripts/','packages/')) for p in changes):
            assert version(distributions[0]['version'])>version(previous['version']),'Runtime changes require a version bump'
    return {'valid':True,'distributions':distributions}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base');a=p.parse_args()
    try:print(json.dumps(check(a.base),sort_keys=True))
    except Exception as exc:raise SystemExit(str(exc))
