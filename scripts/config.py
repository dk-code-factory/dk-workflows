#!/usr/bin/env python3
"""Read, set and validate workflow settings."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from state import read, write
import config

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository',required=True)
    p.add_argument('--upstream')
    sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('show')
    for action in ('set','reset'):
        c=sub.add_parser(action);c.add_argument('--scope',choices=['repo','user'],default='repo')
        if action=='set':c.add_argument('--file',type=Path,required=True)
    c=sub.add_parser('preflight');c.add_argument('--available',type=Path,required=True)
    a=p.parse_args()
    if a.action=='show':result=config.effective(a.repository,a.upstream)
    elif a.action=='preflight':result=config.preflight(config.effective(a.repository,a.upstream)['settings'],read(a.available))
    else:
        target=config.path(a.repository if a.scope=='repo' else None)
        if a.action=='set':write(target,config.validate(read(a.file)))
        elif target.exists():target.unlink()
        result={'path':str(target),'action':a.action}
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':
    os.umask(0o077)
    try:main()
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)}));sys.exit(2)
