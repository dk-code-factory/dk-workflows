"""Offline publisher interface; the native caller chooses every reply request.

No model or GitHub connection is made here. Source fixtures are evidence, not a
router. Keep exercise state outside the repository and inspect the saved writes.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'runtime'))
from shared.diagnostics import Diagnostic


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publisher = load('feedback_exercise_publisher', ROOT/'scripts/publish.py')
fixture = load('feedback_exercise_remote', Path(__file__).with_name('github_remote.py'))


def initialize(root):
    if root.exists() or root.is_relative_to(ROOT):
        raise ValueError('Use a new external exercise directory')
    root.mkdir(parents=True)
    state = fixture.initial()
    # The reply under review and the review-body finding share a review. Only
    # the actual inline source has an originating thread.
    state['replies'] = [
        {'id': 41, 'body': 'Please handle the empty input.', 'user': {'login': 'reviewer'},
         'html_url': fixture.PR+'#discussion_r41', 'pull_request_review_id': 789},
        {'id': 99, 'in_reply_to_id': 41, 'body': 'Was the empty input fixed?',
         'user': {'login': 'reviewer'}, 'html_url': fixture.PR+'#discussion_r99',
         'pull_request_review_id': 789},
    ]
    (root/'remote.json').write_text(json.dumps(state))
    evidence = {
        'pr': fixture.PR, 'head': fixture.HEAD,
        'validation': 'Empty input now returns an empty list; targeted check passes.',
        'threads': [{'id': 'PRRT_fixture', 'comments': [
            {'databaseId': v['id'], 'reference': 'comment-'+str(v['id']),
             'url': v['html_url'], 'text': v['body']} for v in state['replies']]}],
        'discussion': [
            {'kind': 'review', 'id': 789, 'url': fixture.PR+'#pullrequestreview-789',
             'author': 'reviewer', 'text': 'In code.py:42, handle the empty input too.'},
            {'kind': 'issue_comment', 'id': 901, 'url': fixture.PR+'#issuecomment-901',
             'author': 'maintainer', 'text': 'Does the empty input return an empty list?'},
        ],
        'affected_sources': ['comment-99', 'pullrequestreview-789', 'issuecomment-901'],
    }
    (root/'evidence.json').write_text(json.dumps(evidence, indent=2)+'\n')
    for label, path in [('skill.md', ROOT/'codex/skills/dk-pr-feedback-reply/SKILL.md'),
                        ('publication.md', ROOT/'docs/pr/publication.md')]:
        (root/label).write_bytes(path.read_bytes())
    return {'root': str(root), 'evidence': str(root/'evidence.json'),
            'skill': str(root/'skill.md'), 'publication': str(root/'publication.md')}


def operate(root, action, request=None, operation=None, lose_response=False):
    state_path = root/'remote.json'
    remote = fixture.Remote(json.loads(state_path.read_text()))
    original = {key: getattr(publisher, key) for key in ('metadata', 'api', 'run')}
    for key in original:
        setattr(publisher, key, getattr(remote, key))
    try:
        if action == 'submit':
            if lose_response:
                remote.state['lose_response'].append(request['kind'])
            prepared = publisher.prepare(request)
            try:
                result = publisher.publish(prepared['operation'])
            except Diagnostic as error:
                return {'operation': prepared['operation'], 'result': error.envelope()}
            return {'operation': prepared['operation'], 'result': result}
        if action == 'reconcile':
            return publisher.publish(operation, reconcile=True)
        if action == 'inspect':
            return remote.state
        raise ValueError('Unsupported action')
    finally:
        state_path.write_text(json.dumps(remote.state, indent=2)+'\n')
        for key, value in original.items():
            setattr(publisher, key, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('action', choices=['init', 'submit', 'reconcile', 'inspect'])
    parser.add_argument('--request', type=Path)
    parser.add_argument('--operation')
    parser.add_argument('--lose-response', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    if root.is_relative_to(ROOT):
        raise ValueError('Exercise state must be outside the repository')
    os.environ['XDG_STATE_HOME'] = str(root/'state')
    if args.action == 'init':
        result = initialize(root)
    else:
        request = json.loads(args.request.read_text()) if args.request else None
        result = operate(root, args.action, request, args.operation, args.lose_response)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
