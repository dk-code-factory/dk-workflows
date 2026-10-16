"""Small behavior fixtures shared by preparation's integration consumers."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fixture_preparation', ROOT / 'dk-pr-decisions/scripts/preparation.py')
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def analysis(reviewers=('example',)):
    def conclusion(text):
        return {'text': text, 'agreement': 'interpretation', 'material': False,
                'rationale': 'The inspected fixture has a local transform and no interaction boundary.',
                'evidence': ['production/0#L1']}
    return {
        'boundaries': {name: [conclusion('Preserve the explicitly supported local transform')]
                       for name in prep.DIMENSIONS},
        'placement': {name: {**conclusion('Keep the transform and its regression in the owning module'),
                             'locations': ['transform.py' if name != 'tests' else 'tests/test_transform.py']}
                      for name in ('production', 'tests', 'reuse', 'entrypoints', 'dependencies')},
        'test_strategy': [{'behavior': 'Transform preserves supported values', 'existing': 'Unit regression',
                           'missing': 'none', 'level': 'unit', 'location': 'tests/test_transform.py',
                           'command': 'python3 -m unittest discover -s tests',
                           'rationale': 'No subprocess, environment or serialization boundary',
                           'limitation': 'No external service contract is claimed', 'evidence': ['production/0#L1']}],
        'questions': [], 'reviewer_addenda': {name: 'Check the supported transform using existing unit evidence.' for name in reviewers},
        'worker_instructions': 'Use transform.py and tests/test_transform.py; stop on a material scope change.',
        'completion_criteria': 'Supported behavior reproduced, placement coherent, validation sufficient, complexity proportionate.',
        'sync_required': False, 'sync_reason': ''}


def descriptor(name, source):
    return {'descriptor_version': 1, 'reviewer': name, 'complete': True, 'missing': [],
            'sources': [{'path': str(source), 'sha256': prep.file_hash(source)}],
            'aspects': ['Spec'], 'effective_prompt': 'Read-only defect review of the supported transform.'}


def artifact(context, decisions, sources, reviewers=('example',), result=None):
    result = result or analysis(reviewers)
    source = next(iter(sources.values()))
    sources = {**{key: dict(source) for key in ('manifest', 'context', 'decisions', 'metadata', 'patch')},
               **{name + '/0': dict(source) for name in prep.EVIDENCE}, **sources}
    for field in ('patch', 'metadata'):
        sources[field] = {'path': context[field], 'sha256': prep.file_hash(context[field])}
    descriptors = {name: descriptor(name, Path(next(iter(sources.values()))['path'])) for name in reviewers}
    value = {'preparation_version': 1, 'mode': 'iteration',
             'identity': {'pr_url': context['url'], 'base_sha': context['base_sha'], 'head_sha': context['head_sha'],
                          'body_hash': decisions['body_hash'], 'decisions_hash': prep.digest(decisions)},
             'sources': sources, 'reviewers': descriptors, 'analysis': result, 'readiness': prep.readiness(result)}
    value['preparation_id'] = prep.digest(value)
    return value


class Package:
    def __init__(self, root):
        self.root = Path(root)
        self.repo = self.root / 'checkout'
        self.repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=self.repo, text=True).strip()
        git('init', '-q')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.test')
        (self.repo / 'transform.py').write_text('def transform(value):\n    return value\n')
        git('add', '.')
        git('commit', '-qm', 'Transform fixture')
        self.head = git('rev-parse', 'HEAD')
        self.context = {'url': 'https://github.com/example/fixture/pull/1', 'base_sha': self.head,
                        'head_sha': self.head, 'checkout': str(self.repo), 'repository': str(self.repo),
                        'patch': str(self.root / 'diff.patch'), 'metadata': str(self.root / 'metadata.json')}
        (self.root / 'diff.patch').write_text('diff --git a/transform.py b/transform.py\n+++ b/transform.py\n@@ -1 +1 @@\n-old\n+new\n')
        (self.root / 'metadata.json').write_text(json.dumps({'body': '', 'title': 'Transform fix'}))
        self.decisions = {'pr_url': self.context['url'], 'head_sha': self.head,
                          'body_hash': hashlib.sha256(b'').hexdigest(), 'live_decisions': '',
                          'discussion_decisions': [], 'local_decisions': [], 'verified': True, 'decisions': []}
        self.write('context.json', self.context)
        self.write('decisions.json', self.decisions)
        self.write('absence.json', {'verified_absence': True, 'reason': 'No previous run or linked issue in this fixture'})
        self.write('user.json', {'instruction': 'Preserve the supported local transform; keep changes local.'})
        self.write('reviewer.json', descriptor('example', self.repo / 'transform.py'))
        evidence = {name: ['absence.json'] for name in prep.EVIDENCE}
        evidence.update({'production': [str(self.repo / 'transform.py')], 'user_instructions': ['user.json'],
                         'reviewers': ['reviewer.json']})
        self.manifest = {'mode': 'standalone', 'context': 'context.json', 'decisions': 'decisions.json', 'evidence': evidence}
        self.manifest_path = self.write('manifest.json', self.manifest)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, indent=2))
        return path

    def prepare(self, result=None):
        return prep.prepare(self.manifest_path, self.root / 'versions',
                            analyzer=lambda prompt, checkout: result or analysis())
