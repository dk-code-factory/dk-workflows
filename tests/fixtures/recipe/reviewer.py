"""Selected native-skill stand-in; the test coordinator invokes it explicitly."""
import argparse
import json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--skill',required=True);p.add_argument('--checkout',type=Path,required=True)
p.add_argument('--log',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--fail',action='store_true')
a=p.parse_args()
with a.log.open('a') as log:log.write(json.dumps({'skill':a.skill})+'\n')
findings=[] if 'VALUE = 2' in (a.checkout/'code.py').read_text() else [{'id':'value','text':'The settled scope requires returning two.'}]
a.output.write_text(json.dumps({'complete':not a.fail,'findings':findings,'reason':'fixture usage limit' if a.fail else ''}))
