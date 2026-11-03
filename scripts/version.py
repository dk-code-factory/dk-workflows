#!/usr/bin/env python3
"""Set synchronized host versions before a release; does not publish."""
import argparse,json,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('version');a=p.parse_args()
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',a.version):p.error('Use X.Y.Z')
r=Path(__file__).resolve().parent.parent
for path in [r/'.codex-plugin/plugin.json',r/'.claude-plugin/plugin.json']:
 d=json.loads(path.read_text());d['version']=a.version;path.write_text(json.dumps(d,indent=2)+'\n')
path=r/'.claude-plugin/marketplace.json';d=json.loads(path.read_text());d['plugins'][0]['version']=a.version;path.write_text(json.dumps(d,indent=2)+'\n')
