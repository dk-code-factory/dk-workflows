"""Independent local feature probes, not native review or workflow coverage.

Keep these expected outcomes outside reviewer inputs. The evaluator separately
matches actual findings/dispositions and verifies selected-stage/native evidence.
"""
import json
from pathlib import Path
import subprocess
import sys

import workflow_experiment as exercise


PROGRAM = '''import json,sys
from html.parser import HTMLParser
from greeting import greeting

class Paragraph(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.starts, self.ends, self.text = [], [], []
    def handle_starttag(self, tag, attrs): self.starts.append((tag, attrs))
    def handle_endtag(self, tag): self.ends.append(tag)
    def handle_data(self, text): self.text.append(text)

def call(name):
    try:
        value = greeting(name)
        if not isinstance(value, (tuple,list)) or len(value) != 2:
            return {'error':'Expected HTML/history pair'}
        return {'html':value[0], 'history':value[1]}
    except Exception as error:
        return {'error':type(error).__name__+': '+str(error)}

name = '<b>Ada & Lin</b>'
html = call(name)
parser = Paragraph()
try: parser.feed(html.get('html', ''))
except Exception: pass
first, second = call('Ada'), call('Lin')
checks = {
    'html-text': {'passed': (parser.starts == [('p', [])] and parser.ends == ['p']
        and ''.join(parser.text) == 'Hello, '+name), 'observed':html},
    'per-call-history': {'passed': (first.get('history') == ['Ada'] and
        second.get('history') == ['Lin']), 'observed':[first,second]},
}
if sys.argv[1] == 'guest':
    guest = call('')
    checks['empty-name'] = {'passed':guest == {'html':'<p>Hello, guest</p>', 'history':['']},
                            'observed':guest}
print(json.dumps(checks))
'''


def probe(root, *, guest_required=False):
    if type(guest_required) is not bool:
        raise ValueError('Guest requirement must be explicit boolean')
    root = Path(root)
    state = json.loads((root/'remote.json').read_text())
    head = exercise.git(root/'checkout', 'rev-parse', 'HEAD').decode().strip()
    if head != state['metadata']['headRefOid']:
        raise ValueError('Quality checkout differs from published head')
    if exercise.git(root/'checkout', 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('Quality checkout has unpublished changes')
    result = subprocess.run([sys.executable, '-c', PROGRAM,
                             'guest' if guest_required else 'original'],
        cwd=root/'checkout', env={**exercise.git_environment(), 'PYTHONDONTWRITEBYTECODE':'1'},
        text=True, capture_output=True)
    if result.returncode:
        return {'probe_complete': False, 'head': head, 'stdout': result.stdout, 'stderr': result.stderr}
    checks = json.loads(result.stdout)
    return {'probe_complete': True, 'head': head, 'checks': checks,
            'violated_requirements': [key for key, value in checks.items() if not value['passed']],
            'source': {'requirements': 'README.md', 'requirements_sha256': exercise.sha(root/'checkout/README.md'),
                       'implementation_sha256': exercise.sha(root/'checkout/greeting.py')},
            'scope': 'Feature behavior only; native finding quality and workflow coverage require independent evaluation.'}
