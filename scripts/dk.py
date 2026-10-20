#!/usr/bin/env python3
"""Route workflow helpers with one repository, PR and host context."""
import argparse
from contextlib import nullcontext, redirect_stdout
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'runtime'))
from shared.command_results import ResultFile, receipt
# Each row is a packaged script, cwd argument placement, PR argument, repo argument.
ROUTES = {
    'config': ('runtime/dk-config/scripts/repo_rules.py', 'before', 'url', False),
    'decisions': ('runtime/dk-pr-decisions/scripts/decisions.py', None, 'url', False),
    'run-state': ('runtime/dk-pr-iter/scripts/run_state.py', None, 'number', True),
    'review-context': ('runtime/dk-pr-review/scripts/review_context.py', 'before', 'url', False),
    'reviewers': ('runtime/dk-pr-review/scripts/run_reviewers.py', None, None, False),
    'inline-feedback': ('runtime/dk-pr-address-reviews/scripts/fetch_unresolved_review_threads.py', 'before', 'url', False),
    'conversation': ('runtime/dk-pr-address-reviews/scripts/fetch_pr_conversation.py', 'before', None, False),
    'preflight': ('runtime/dk-pr-iter/scripts/preflight.py', 'before', 'url', False),
    'ci-wait': ('runtime/dk-pr-iter/scripts/ci_wait.py', None, 'url', False),
    'completion': ('runtime/dk-pr-iter/scripts/completion.py', 'before', 'url', False),
    'completion-threads': ('runtime/dk-pr-iter/scripts/completion_threads.py', 'before', 'url', False),
    'edit-pr': ('runtime/dk-pr-description/scripts/pr_edit_guard.py', None, 'positional', False),
    'description-decisions': ('runtime/dk-pr-description/scripts/decisions.py', None, None, False),
    'report-comments': ('runtime/dk-pr-read-comments/scripts/report_pr_comments.py', None, 'url', False),
    'replies': ('runtime/dk-pr-reply-review/scripts/reply_passes.py', 'before', 'url', False),
    'reply-batch': ('runtime/dk-pr-reply-review/scripts/reply_batch.py', None, None, False),
    'split': ('runtime/dk-pr-split-commits/scripts/split_guard.py', None, 'url', False),
    'flaky-tests': ('runtime/dk-flaky-tests/scripts/flaky_tests.py', 'before', None, True),
    'discovery': ('runtime/shared/discovery_cache.py', 'after', 'url', False),
    'draft': ('runtime/shared/drafting.py', 'before', 'url', False),
    'review-loop': ('runtime/shared/review_loop.py', None, None, False),
    'commit-lines': ('runtime/shared/commit_message_lines.py', None, None, False),
    'validate-review': ('runtime/dk-pr-review/scripts/validate_result.py', None, None, False),
    'update': ('scripts/update.py', None, None, False),
}
PART = re.compile(r'^[A-Za-z0-9_.-]+$')


def repository(value, host='github.com'):
    parts = value.split('/')
    if len(parts) == 2:
        parts.insert(0, host)
    if (len(parts) != 3 or any(not PART.fullmatch(p) or p in ('.', '..') for p in parts)
            or not re.fullmatch(r'[A-Za-z0-9.-]+', parts[0])):
        raise ValueError('--repo must be HOST/OWNER/REPO or OWNER/REPO; use --cwd for a local path')
    return '/'.join(parts).lower()


def identity(pr, repo):
    url, number, anchor = None, None, ''
    if pr and pr.isdecimal():
        if not repo:
            raise ValueError('a numeric --pr requires --repo; a full PR URL needs no repository argument')
        number = int(pr)
    elif pr:
        parsed = urlparse(pr)
        parts = parsed.path.strip('/').split('/')
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.port or parsed.query or len(parts) != 4 or parts[2] != 'pull'
                or not parts[3].isdecimal()):
            raise ValueError('--pr must be an HTTPS pull-request URL or a positive number with --repo')
        resolved = repository('/'.join([parsed.hostname, *parts[:2]]))
        if repo and repository(repo, parsed.hostname) != resolved:
            raise ValueError('--repo conflicts with the PR URL base repository')
        repo, number = resolved, int(parts[3])
        anchor = parsed.fragment
        if anchor and not re.fullmatch(r'pullrequestreview-\d+', anchor):
            raise ValueError('unsupported PR anchor; use a pullrequestreview-ID selection or a canonical URL')
    if repo:
        repo = repository(repo, os.environ.get('GH_HOST', 'github.com'))
    if number is not None:
        if number < 1:
            raise ValueError('--pr must be positive')
        url = 'https://' + repo + '/pull/' + str(number)
        if anchor:
            url += '#' + anchor
    return url, number, repo


def invocation(root, command, arguments, cwd, pr, repo, host, parent):
    relative, cwd_option, pr_kind, repo_option = ROUTES[command]
    script = root / relative
    if not script.is_file():
        raise ValueError(f'Packaged helper unavailable: {relative}; reload the installed plugin')
    cwd = Path(cwd).resolve()
    if not cwd.is_dir():
        raise ValueError('--cwd must name an existing directory')
    url, number, normalized_repo = identity(pr, repo)
    if url and '#' in url and command not in ('inline-feedback', 'replies'):
        raise ValueError('review selections are supported by inline-feedback and replies; use the canonical PR URL for other helpers')
    if url and not pr_kind and not repo_option:
        raise ValueError(f'{command} reads PR identity from its artifacts; omit the common --pr flag')
    if normalized_repo and not url and not repo_option:
        raise ValueError(f'{command} requires a PR URL to use --repo; otherwise select its repository with --cwd')
    if command == 'discovery' and arguments and arguments[0] == 'submit' and url:
        raise ValueError('discovery submit uses its ticket; omit the common --pr flag')
    prefix, suffix = [], []
    if cwd_option:
        (suffix if cwd_option == 'after' else prefix).extend(['--cwd', str(cwd)])
    if pr_kind and url:
        if pr_kind == 'positional':
            suffix.append(url)
        else:
            (suffix if cwd_option == 'after' else prefix).extend(
                ['--pr', str(number) if pr_kind == 'number' else url])
    if repo_option:
        if not normalized_repo:
            raise ValueError(f'{command} requires --pr URL or --repo')
        prefix += ['--repo', normalized_repo]
    reserved = {'--cwd'} if cwd_option else set()
    if pr_kind:
        reserved.add('--pr')
    if repo_option:
        reserved.add('--repo')
    if any(arg.split('=', 1)[0] in reserved for arg in arguments):
        raise ValueError('pass repository context flags before the helper command, not among its native arguments')
    if command == 'update':
        if any(arg.split('=', 1)[0] in ('--host', '--parent') for arg in arguments):
            raise ValueError('pass --host and --parent before the helper command')
        prefix += ['--host', host]
    environment = dict(os.environ, DK_HOST=host)
    if parent is not None:
        environment['DK_UPDATE_PARENT'] = parent
    return [sys.executable, str(script), *prefix, *arguments, *suffix], cwd, environment


class InvocationError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InvocationError(message)


def early_context(arguments):
    """Find storage context before parsing, stopping at the native helper boundary."""
    context = {}
    index = 0
    options = {'--cwd', '--pr', '--repo', '--host', '--parent', '--result-file'}
    while index < len(arguments):
        option, separator, value = arguments[index].partition('=')
        if option in options:
            if not separator:
                index += 1
                if index >= len(arguments):
                    break
                value = arguments[index]
            context[option] = value
        elif option != '--dry-run':
            break
        index += 1
    return context


def dispatch(arguments, result_file=None):
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--pr', help='PR URL, or number with --repo')
    parser.add_argument('--repo', help='HOST/OWNER/REPO or OWNER/REPO')
    parser.add_argument('--host', choices=('codex', 'claude'), default=os.environ.get('DK_HOST'))
    parser.add_argument('--parent', default=os.environ.get('DK_UPDATE_PARENT'))
    parser.add_argument('--dry-run', action='store_true', help='Print argv and workflow context without running a helper')
    parser.add_argument('--result-file', help='Retain full private diagnostics and print a typed terminal receipt')
    parser.add_argument('command', choices=['commands', *ROUTES])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(arguments)
    if args.command == 'commands':
        if args.arguments:
            parser.error('commands accepts no native helper arguments')
        print(json.dumps({'schema_version': 1, 'commands': {
            name: {'script': row[0], 'cwd': row[1], 'pr': row[2], 'repo': row[3]}
            for name, row in ROUTES.items()}}, sort_keys=True))
        return 0
    if args.host not in ('codex', 'claude'):
        parser.error('set --host codex|claude or use the native skill entrypoint environment')
    try:
        argv, cwd, env = invocation(ROOT, args.command, args.arguments, args.cwd,
                                    args.pr, args.repo, args.host, args.parent)
    except ValueError as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(json.dumps({'argv': argv, 'cwd': str(cwd), 'environment': {
            key: env[key] for key in ('DK_HOST', 'DK_UPDATE_PARENT') if key in env}}, sort_keys=True))
        return 0
    if result_file is not None:
        _, code = result_file.run(args.command, argv, cwd, env)
        return code
    status = subprocess.run(argv, cwd=cwd, env=env).returncode
    return status if status >= 0 else 128 - status


def main():
    arguments = sys.argv[1:]
    context = early_context(arguments)
    artifact = None
    if '--result-file' in context:
        try:
            artifact = ResultFile(context['--result-file'], context.get('--cwd', '.'), ROOT)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(json.dumps({**receipt('error', 'artifact_storage',
                'Choose a new writable private artifact path before running the helper.', None, 2),
                'message': str(exc)}))
            return 2
    try:
        with redirect_stdout(artifact.streams['stdout']) if artifact else nullcontext():
            code = dispatch(arguments, artifact)
        if artifact is not None:
            result = (artifact.terminal if artifact.streams['result'].closed
                      else artifact.finish(None, code))
            print(json.dumps(result, sort_keys=True))
        return code
    except InvocationError as exc:
        if artifact is not None:
            print(json.dumps(artifact.finish(None, 2, error=str(exc)), sort_keys=True))
        else:
            print(f'dk.py: {exc}', file=sys.stderr)
        return 2
    except SystemExit as exc:
        if artifact is not None:
            print(json.dumps(artifact.finish(None, exc.code or 0), sort_keys=True))
            return exc.code or 0
        raise
    except OSError as exc:
        if artifact is None:
            print(f'dk.py: {exc}', file=sys.stderr)
        else:
            print(json.dumps({**receipt('error', 'artifact_storage',
                'Inspect retained files and reconcile any executed mutation before resuming.',
                str(artifact.path), 2), 'message': str(exc)}))
        return 2
    finally:
        if artifact is not None:
            artifact.close()


if __name__ == '__main__':
    raise SystemExit(main())
