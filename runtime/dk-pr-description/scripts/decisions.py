#!/usr/bin/env python3
"""Inspect or replace the bounded PR Decisions section in a body file."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import importlib.util
from pathlib import Path
import re
import sys

_spec = importlib.util.spec_from_file_location('decision_api', next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())/'dk-pr-decisions/scripts/decisions.py')
decision_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(decision_api)

HEADER=re.compile(r'^## Decisions\s*$',re.M)
NEXT=re.compile(r'^## [^\n]+$',re.M)

def span(body):
    hits=list(HEADER.finditer(body))
    if len(hits)>1: raise ValueError('multiple Decisions sections')
    if not hits: return None
    start=hits[0].start()
    nxt=NEXT.search(body,hits[0].end())
    return start,nxt.start() if nxt else len(body)

def section(body):
    bounds=span(body)
    return body[bounds[0]:bounds[1]].strip() if bounds else ''

def replace(body,new):
    new=new.strip()
    if new and not new.startswith('## Decisions\n'):
        raise ValueError('new section must start with ## Decisions')
    for history in decision_api.HISTORY.finditer(section(body)):
        if history[0] not in new:
            raise ValueError('existing decision history must be preserved verbatim')
    records = decision_api.body_entries(new) + decision_api.history_records(new)
    ids = {item['id'] for item in records}
    if any(item.get('superseded_by') and item['superseded_by'] not in ids for item in records):
        raise ValueError('decision history references a missing replacement')
    active = decision_api.HISTORY.sub('', new)
    bullets=[line for line in active.splitlines() if line.startswith('- ')]
    if len(bullets)>12: raise ValueError('Decisions section exceeds 12 entries')
    bounds=span(body)
    if bounds:
        parts=[part for part in (body[:bounds[0]].rstrip(), new,
                                 body[bounds[1]:].lstrip()) if part]
        result='\n\n'.join(parts)
    elif new:
        result=body.rstrip()+'\n\n'+new+'\n'
    else: result=body
    return result.rstrip()+'\n'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['extract','replace'])
    p.add_argument('body',type=Path)
    p.add_argument('--section',type=Path)
    args=p.parse_args()
    body=args.body.read_text()
    if args.command=='extract': print(section(body)); return
    if not args.section: raise ValueError('--section required')
    args.body.write_text(replace(body,args.section.read_text()))

if __name__=='__main__':
    try: main()
    except Exception as exc: print(f'pr decisions: {exc}',file=sys.stderr); sys.exit(1)
