"""Small behavior fixtures shared by preparation's integration consumers."""
import hashlib
import importlib.util
import json
from pathlib import Path
from shared.workflow_storage import allocate
from shared import architecture
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fixture_preparation', ROOT / 'dk-pr-decisions/scripts/preparation.py')
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def seed_architecture(root):
    """Retain an inspected baseline so subprocess fixtures need no extra model."""
    path = architecture.git(root, 'ls-files').splitlines()[0]
    item = {'text': 'No extra architecture policy in this fixture.', 'origin': 'inferred',
            'rationale': 'Inspected fixture source.', 'sources': [{'path': path, 'start': 1, 'end': 1}]}
    architecture.ensure(root, architecture.git(root, 'rev-parse', 'HEAD').strip(),
        analyzer=lambda *_: {'knowledge': {key: [item] for key in architecture.KNOWLEDGE},
                             'rules': [], 'proposals': [], 'conflicts': []})


def architecture_review(prepared, context, decisions):
    return architecture.review(prepared['architecture'], context, decisions,
        reviewer=lambda *_: {'complete': True, 'reason': '', 'coverage': []})


def interrupt_after_checkpoint(completed_reviewer, result, ready):
    """Establish durable success before an interruption fixture closes launches."""
    from shared.bindings import key
    def review(reviewer, *args):
        if key(reviewer) == completed_reviewer:
            return result
        deadline = time.monotonic() + 5
        while not ready():
            if time.monotonic() >= deadline:
                raise AssertionError('successful reviewer checkpoint was not established')
            time.sleep(.005)
        raise KeyboardInterrupt()
    return review


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


def response(value=None):
    value = value if value is not None else analysis()
    try:
        locations = prep.graph_api.conclusions(value)
    except (KeyError, TypeError, AttributeError):
        locations = {}
    return {'analysis': value, 'graph': [
        {'location': location, 'depends_on': [], 'paths': [{'path': 'transform.py', 'kind': 'file'}],
         'sources': ['production/0'], 'decisions': []} for location in locations]}


def retain(parent, ids=None, unmapped=()):
    return {'dispositions': [{'id': key, 'action': 'retain', 'reason': 'Inspected changed evidence; boundary still applies'}
                             for key in (ids if ids is not None else parent['graph'])],
            'additions': [], 'impacts': [{'change': change, 'affected': [],
                                        'rationale': 'Inspected the new source against governing conclusions'}
                                       for change in unmapped]}


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


def promote(value, context, decisions):
    """Use the fresh contract in tests of the former run-record CLI adapter."""
    if value['preparation_version'] >= 3:
        return
    # Legacy unit fixtures use a synthetic base OID. Fresh architecture needs a
    # readable target baseline; their single real commit is the fixture baseline.
    context['base_sha'] = context['head_sha']
    context['repository_identity'] = 'path:' + str(Path(context['repository']).resolve())
    seed_architecture(context['checkout'])
    directory = Path(context['patch']).parent / 'fresh-inputs'
    directory.mkdir(exist_ok=True)
    def put(name, content):
        path = directory / name
        path.write_text(json.dumps(content))
        return str(path)
    decisions.setdefault('decisions', [])
    absence = put('absence.json', {'verified_absence': True, 'reason': 'No other fixture evidence'})
    evidence = {name: [absence] for name in prep.EVIDENCE}
    evidence['production'] = [str(Path(context['checkout']) / 'x.py')]
    evidence['reviewers'] = [put('reviewer-' + str(i) + '.json', d) for i, d in enumerate(value['reviewers'].values())]
    manifest = put('manifest.json', {'mode': value['mode'], 'context': put('context.json', context),
                                    'decisions': put('decisions.json', decisions), 'evidence': evidence})
    saved = prep.prepare(manifest, directory / 'output', analyzer=lambda *_: response(value['analysis']))
    current = prep.read(saved['artifact'])
    value.clear()
    value.update(current)


class Package:
    def __init__(self, root):
        self.root = Path(root)
        self.versions = allocate()
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
        seed_architecture(self.repo)
        self.context = {'url': 'https://github.com/example/fixture/pull/1', 'base_sha': self.head,
                        'repository_identity': 'path:' + str(self.repo.resolve()),
                        'head_sha': self.head, 'checkout': str(self.repo), 'repository': str(self.repo), 'workflow_dir': str(self.versions),
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
        return prep.prepare(self.manifest_path, self.versions,
                            analyzer=lambda prompt, checkout: response(result or analysis()))
