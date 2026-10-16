#!/usr/bin/env python3
"""Validate distributable resources and enforce semantic version bumps."""
import argparse,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def version(v):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",v):raise ValueError('Stable semantic version required')
    return tuple(map(int,v.split('.')))

def check(base=None):
    manifests=[json.loads((ROOT/f'.{host}-plugin/plugin.json').read_text()) for host in ('codex','claude')]
    assert manifests[0]['name']==manifests[1]['name']
    assert manifests[0]['version']==manifests[1]['version']
    version(manifests[0]['version'])
    skills=[]
    for host,manifest in zip(('codex','claude'),manifests):
        directory=(ROOT/manifest['skills']).resolve();assert directory.is_relative_to(ROOT)
        entries=sorted(directory.glob('*/SKILL.md'));assert entries
        skills.append([p.parent.name for p in entries])
        for p in entries:
            content=p.read_text();assert re.search(r'(?m)^name: '+re.escape(p.parent.name)+r'$',content)
            assert 'description:' in content and 'lifecycle' in content
            for link in re.findall(r'\]\(([^)]+)\)',content):
                if '://' in link:continue
                target=(p.parent/link).resolve();assert target.is_relative_to(ROOT) and target.is_file(),str(target)
    assert skills[0]==skills[1]
    for host,filename in [('codex','.agents/plugins/marketplace.json'),('claude','.claude-plugin/marketplace.json')]:
        catalog=json.loads((ROOT/filename).read_text());assert catalog['name']==manifests[0]['name']
        assert [p['name'] for p in catalog['plugins']]==[manifests[0]['name']]
    for p in ROOT.rglob('*'):
        if '.git' in p.parts or '__pycache__' in p.parts or '.pytest_cache' in p.parts:continue
        if p.is_symlink():assert p.resolve().is_relative_to(ROOT),str(p)
        if p.is_file() and p.suffix in ('.py','.md','.json','.js','.yml','.yaml'):
            text=p.read_text()
            assert ('/'+'home/') not in text and ('/'+'extra/') not in text, f'Machine-specific path: {p}'
    if base:
        previous=json.loads(subprocess.check_output(['git','show',base+':.codex-plugin/plugin.json'],cwd=ROOT,text=True))
        changes=subprocess.check_output(['git','diff','--name-only',base,'HEAD'],cwd=ROOT,text=True).splitlines()
        if any(p.startswith(('codex/','claude/','common/','runtime/','scripts/')) for p in changes):
            assert version(manifests[0]['version'])>version(previous['version']),'Skill/runtime changes require a version bump'
    return {'name':manifests[0]['name'],'version':manifests[0]['version'],'skills':skills[0],'valid':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base');args=parser.parse_args()
    try:print(json.dumps(check(args.base)))
    except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
