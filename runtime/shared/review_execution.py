"""Mandatory native specialist execution evidence, separate from findings and analytics.

Only native host tool events establish launch/completion. Reviewer prose, result
JSON and optional statistics cannot establish that a specialist actually ran.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import re
import uuid

from shared import bindings
from shared.private_state import write
from shared.workflow_storage import allocate

_CURRENT = ContextVar('required_review_execution', default=None)
_CORE = {
    'code-review-ad:code-review-ad': ['Toolkit', 'Standards', 'Spec'],
    'code-review-matpocock:code-review-matpocock': ['Standards'],
    'pr-review-toolkit:review-pr': ['code-reviewer'],
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


_CONDITIONAL = {
    'code-review-ad:code-review-ad': ('Toolkit', ['Performance']),
    'code-review-matpocock:code-review-matpocock': ('Standards', ['Spec']),
    'pr-review-toolkit:review-pr': ('code-reviewer', ['pr-test-analyzer', 'comment-analyzer',
        'silent-failure-hunter', 'type-design-analyzer', 'code-simplifier']),
}


# Reviewed method texts, independent of host installation paths. A changed
# method requires an explicit profile update; version identity alone is insufficient.
_METHODS = {('code-review-ad:code-review-ad', 'codex'): {'version': '1.0.7',
                                              'sources': {'README.md': '1b37764f9be1f7e88c7ac24f9a89e38c340c8afede29cd96d76ff9033a2434ef',
                                                          'claude/skills/code-review-ad/SKILL.md': 'b835b10b8ce29dc989afc6c493532892e811676030fd651519b55184fc9ebaa9',
                                                          'codex/skills/code-review-ad/SKILL.md': '849e473799ff91cfd38c59de6cc5b280847dd009b24ede703b37c067bd5335aa',
                                                          'common/claude/procedure.md': '0254c4fc242035831312a86bb909f830fd92071c0cd2da5e699d08e559255293',
                                                          'common/claude/review.md': '69dba6a9ca654c6ee034be80c54bb747f63be42caf8b07b780604374fe04457f',
                                                          'common/codex/checks/bounds.md': 'fab80818b97579775b6edc45f94480195023081b088a31ecea36850d79f40ee4',
                                                          'common/codex/checks/claims.md': 'b95a46e09a34d7fd7c4d4d3f5a7c1cc73266b968d14480f1bc037e110a4fd53c',
                                                          'common/codex/checks/concurrency.md': '21018771d850570479eb63e505a160897f553e9c8d33c39e6965a67185d2bd82',
                                                          'common/codex/checks/counterexamples.md': 'f6967146a4f57bf16883a21663bb40a44d8d539d03f0667398d249e8b4e947f8',
                                                          'common/codex/checks/lifecycle.md': '501eb6cfe179acf8a7ed28a62d9530ac3f242b8675efa6ceebb9f630e3b5232d',
                                                          'common/codex/checks/pools.md': 'e02c342d8c48163fcb51eb49aeaa81ceb31cb70bf8867918cce759ef7ba5d944',
                                                          'common/codex/checks/standards.md': 'bc5301dd1b0d40c150d74143a41b7831b4195aa57f39e1c9de4f5ad563f59f1a',
                                                          'common/codex/passes/performance.md': '4a776a01a7d8742fe980ee36c2156797c68863e4ec386785a85c1ed8d4663cdc',
                                                          'common/codex/passes/spec.md': '6eeb4841110941a677012d546e629131e8182f7ce600fc5d1ba9438e2742e349',
                                                          'common/codex/passes/standards.md': '687efa713215b1acb3f838252873309d504e21abb6eee5491d70015e0f205393',
                                                          'common/codex/passes/toolkit.md': '5033c2cf052084219a0e7ff50770d9fdecd2a75fcdbf0a92357f154469a85c69',
                                                          'common/codex/procedure.md': 'c4cb2621348e508d6dc021ecc61d1dc8799916b94a41ce4c833cddb566070530',
                                                          'common/codex/review.md': '0a848aff54cb76cc15abad9ab534c6afb43415d2e218b29160ae3fb3fdf466b8',
                                                          'common/inspection.md': '80582feb0b2ec1db49eb0618b959fc7395c4a451c6420a205cf7450e3b125cce',
                                                          'common/lifecycle.md': '6236e33483836e48b3bb5ba013a0f9f999f04d9fafa46d4f1accf650d4e7c468',
                                                          'common/reporting.md': '89edeba76749bc006420d6d7f33440ee9c8df71432836c72c2e0e76c698cd5e6'}},
 ('code-review-ad:code-review-ad', 'claude'): {'version': '1.0.7',
                                               'sources': {'README.md': '1b37764f9be1f7e88c7ac24f9a89e38c340c8afede29cd96d76ff9033a2434ef',
                                                           'claude/skills/code-review-ad/SKILL.md': 'b835b10b8ce29dc989afc6c493532892e811676030fd651519b55184fc9ebaa9',
                                                           'codex/skills/code-review-ad/SKILL.md': '849e473799ff91cfd38c59de6cc5b280847dd009b24ede703b37c067bd5335aa',
                                                           'common/claude/procedure.md': '0254c4fc242035831312a86bb909f830fd92071c0cd2da5e699d08e559255293',
                                                           'common/claude/review.md': '69dba6a9ca654c6ee034be80c54bb747f63be42caf8b07b780604374fe04457f',
                                                           'common/codex/checks/bounds.md': 'fab80818b97579775b6edc45f94480195023081b088a31ecea36850d79f40ee4',
                                                           'common/codex/checks/claims.md': 'b95a46e09a34d7fd7c4d4d3f5a7c1cc73266b968d14480f1bc037e110a4fd53c',
                                                           'common/codex/checks/concurrency.md': '21018771d850570479eb63e505a160897f553e9c8d33c39e6965a67185d2bd82',
                                                           'common/codex/checks/counterexamples.md': 'f6967146a4f57bf16883a21663bb40a44d8d539d03f0667398d249e8b4e947f8',
                                                           'common/codex/checks/lifecycle.md': '501eb6cfe179acf8a7ed28a62d9530ac3f242b8675efa6ceebb9f630e3b5232d',
                                                           'common/codex/checks/pools.md': 'e02c342d8c48163fcb51eb49aeaa81ceb31cb70bf8867918cce759ef7ba5d944',
                                                           'common/codex/checks/standards.md': 'bc5301dd1b0d40c150d74143a41b7831b4195aa57f39e1c9de4f5ad563f59f1a',
                                                           'common/codex/passes/performance.md': '4a776a01a7d8742fe980ee36c2156797c68863e4ec386785a85c1ed8d4663cdc',
                                                           'common/codex/passes/spec.md': '6eeb4841110941a677012d546e629131e8182f7ce600fc5d1ba9438e2742e349',
                                                           'common/codex/passes/standards.md': '687efa713215b1acb3f838252873309d504e21abb6eee5491d70015e0f205393',
                                                           'common/codex/passes/toolkit.md': '5033c2cf052084219a0e7ff50770d9fdecd2a75fcdbf0a92357f154469a85c69',
                                                           'common/codex/procedure.md': 'c4cb2621348e508d6dc021ecc61d1dc8799916b94a41ce4c833cddb566070530',
                                                           'common/codex/review.md': '0a848aff54cb76cc15abad9ab534c6afb43415d2e218b29160ae3fb3fdf466b8',
                                                           'common/inspection.md': '80582feb0b2ec1db49eb0618b959fc7395c4a451c6420a205cf7450e3b125cce',
                                                           'common/lifecycle.md': '6236e33483836e48b3bb5ba013a0f9f999f04d9fafa46d4f1accf650d4e7c468',
                                                           'common/reporting.md': '89edeba76749bc006420d6d7f33440ee9c8df71432836c72c2e0e76c698cd5e6'}},
 ('code-review-matpocock:code-review-matpocock', 'codex'): {'version': '1.0.1',
                                                            'sources': {'README.md': '55deef6fa9161c878aa79a5c54b5fd5e87c4068d0372bd8c846915278648a3cd',
                                                                        'claude/skills/code-review-matpocock/SKILL.md': '635291e5a29012223d5818bf3102adf237d2f7e41f1717046e3d151cc7e85748',
                                                                        'codex/skills/code-review-matpocock/SKILL.md': 'e6cc30f61690a03b9b6d1935eec939ada14448aab7fbe453aacaf3192c76f2b3',
                                                                        'common/claude/procedure.md': 'c0818f379edd38427bfd4b045f03c2233d6d9a2eacd9b3bb0cfd017f7c18114a',
                                                                        'common/codex/procedure.md': 'e42fadaffb20665890715c78d66b1ab4aee9e6bad5b3c3b636fd9c0e27185b7e',
                                                                        'common/lifecycle.md': '6236e33483836e48b3bb5ba013a0f9f999f04d9fafa46d4f1accf650d4e7c468'}},
 ('code-review-matpocock:code-review-matpocock', 'claude'): {'version': '1.0.1',
                                                             'sources': {'README.md': '55deef6fa9161c878aa79a5c54b5fd5e87c4068d0372bd8c846915278648a3cd',
                                                                         'claude/skills/code-review-matpocock/SKILL.md': '635291e5a29012223d5818bf3102adf237d2f7e41f1717046e3d151cc7e85748',
                                                                         'codex/skills/code-review-matpocock/SKILL.md': 'e6cc30f61690a03b9b6d1935eec939ada14448aab7fbe453aacaf3192c76f2b3',
                                                                         'common/claude/procedure.md': 'c0818f379edd38427bfd4b045f03c2233d6d9a2eacd9b3bb0cfd017f7c18114a',
                                                                         'common/codex/procedure.md': 'e42fadaffb20665890715c78d66b1ab4aee9e6bad5b3c3b636fd9c0e27185b7e',
                                                                         'common/lifecycle.md': '6236e33483836e48b3bb5ba013a0f9f999f04d9fafa46d4f1accf650d4e7c468'}},
 ('pr-review-toolkit:review-pr', 'claude'): {'version': None,
                                             'sources': {'README.md': '364cb1d46aa64a01016e4ef5f41dfea5bd5e3cf530459409a376feba93dd2b49',
                                                         'agents/code-reviewer.md': '019395c3ce457460115cc703e2f4a86fed4bbe560dc58355051c4d877155d366',
                                                         'agents/code-simplifier.md': '976ddb22b84bc5a714216531a75db5e73169554add6b953442beeedb49b56891',
                                                         'agents/comment-analyzer.md': '4a9c1f2eb8234a4b9231983e75739663d512e1aed242388964a165a719b84698',
                                                         'agents/pr-test-analyzer.md': 'fcb1cde9ba7b21694b508766a8d6a79bc91bed9982f828f816210059934f46b4',
                                                         'agents/silent-failure-hunter.md': 'fa9b0daec5a267e7e66435cc48b3328301fc9f70c3af259fe248881327a1babc',
                                                         'agents/type-design-analyzer.md': 'c1cf67843d3c4fd27ddf6b24aa92521414b16c01610e7f7e87212c7b8681198d',
                                                         'commands/review-pr.md': '5e70c17293a044e1bf9d092c80b5da8b4fd5802ebb07dc53993dec4ba7ce2fc4'}}}


def method_profile(binding, descriptor):
    binding = bindings.normalize(binding)
    execution = descriptor.get("execution") or {}
    expected = _METHODS.get((binding.get("skill"), execution.get("executor")))
    if binding.get("skill") not in _CORE:
        return {"status": "declared", "sources": {}}
    if expected is None or expected["version"] != execution.get("version"):
        return {"status": "unsupported", "reason": "unknown installed method version or executor"}
    root = Path(execution.get("root", "/__missing_review_method_root__"))
    actual = {}
    for source in descriptor.get("sources", []):
        path = Path(source.get("path", ""))
        if path.is_relative_to(root) and path.suffix == ".md":
            actual[str(path.relative_to(root))] = source.get("sha256")
    if actual != expected["sources"]:
        return {"status": "unsupported", "reason": "unknown installed method procedure texts"}
    return {"status": "supported", "sources_sha256": digest(actual)}


def requirements(binding, handoff):
    binding = bindings.normalize(binding)
    required = list(_CORE.get(binding.get('skill'), []))
    if 'nested_review_roles' in binding:
        required.extend(role for role in binding['nested_review_roles']
                        if role not in required and role not in conditional(binding)[1])
    return required


def conditional(binding):
    normalized = bindings.normalize(binding)
    host = normalized.get('host')
    if host == 'current':
        host = bindings.current_host()
    if normalized.get('skill') == 'code-review-ad:code-review-ad' and host == 'claude':
        # The pinned Claude procedure requires exactly three agents. Its Codex
        # sibling has a conditional fourth Performance pass.
        return None, []
    return _CONDITIONAL.get(normalized.get('skill'), (None, []))


def identity(binding, descriptor, handoff, handoff_path):
    execution = descriptor.get('execution') or {}
    return {'reviewer': bindings.key(binding), 'binding': bindings.normalize(binding),
            'executor': execution.get('executor'), 'version': execution.get('version'),
            'bundle_digest': execution.get('bundle_digest'), 'procedure': execution.get('procedure'),
            'descriptor_sha256': digest(descriptor), 'method_profile': method_profile(binding, descriptor),
            'head_sha': handoff['head_sha'],
            'base_sha': handoff['base_sha'], 'pr_url': handoff.get('pr_url'),
            'response_budget': handoff.get('response_budget'),
            'handoff_sha256': hashlib.sha256(Path(handoff_path).read_bytes()).hexdigest()}


def instructions(collector):
    required = collector['required']
    primary, optional = conditional(collector['binding']['binding'])
    text = ('\nNative execution evidence is mandatory for these independent passes: ' + ', '.join(required) + '. '
            'When launching each pass, put exactly DK_REVIEW_PASS=<role> on its own line in the child prompt, '
            'using the exact role spelling above (or the declared conditional role). Launch a separate child '
            'for each role and wait for its completed result. Preserve all installed-skill obligations. '
            'Claude Task/Agent calls must run synchronously; a background launch acknowledgement is not completion. '
            'Do not put multiple role markers in one prompt. A clean findings object cannot substitute for execution.\n')
    if optional:
        binding = collector['binding']
        example = {'head_sha': binding['head_sha'], 'base_sha': binding['base_sha'],
                   'invocation': collector['invocation'], 'descriptor_sha256': binding['descriptor_sha256'],
                   'roles': {role: {'required': False, 'reason': 'Replace with source-backed applicability rationale.'}
                             for role in optional}}
        text += (f'Conditional passes are {optional}. Give the independent {primary} child the exact '
                 f'installed procedure at {binding["procedure"]} and all linked applicability criteria, '
                 'including requested performance review, hot-path/repository performance evidence, and '
                 'Toolkit specialist and post-clean-review conditions when applicable. Matpocock may '
                 'skip Spec only after explicit user confirmation that no specification exists; '
                 'absence of a discovered spec alone is insufficient. That child must '
                 'assess each conditional role against the pinned scope. Its own completed report must '
                 'include one line DK_REVIEW_APPLICABILITY_JSON= followed by a JSON object of this shape: '
                 + json.dumps(example) + '. Replace every example rationale with actual evidence and '
                 'set required truthfully. The runner accepts applicability only from that native '
                 'completed independent child, never from your final response. Launch every required '
                 'conditional role and retain its native completion. Unknown applicability blocks '
                 'completion unless all potentially applicable roles actually complete. This is '
                 'semantic reviewer evidence, not a deterministic proof of inapplicability.\n')
    return text


@contextmanager
def collect(binding, descriptor, handoff_path, checkout):
    handoff = json.loads(Path(handoff_path).read_text())
    required = requirements(binding, handoff)
    if not required:
        yield None
        return
    collector = {'binding': identity(binding, descriptor, handoff, handoff_path),
                 'required': required, 'invocation': uuid.uuid4().hex, 'attempts': [],
                 'directory': allocate(repository=checkout)}
    token = _CURRENT.set(collector)
    try:
        yield collector
    finally:
        _CURRENT.reset(token)


def active():
    return _CURRENT.get()


def record(host, stdout, stderr, returncode, attempt):
    collector = active()
    if collector is None:
        return
    value = {'version': 1, 'invocation': collector['invocation'], 'binding': collector['binding'],
             'host': host, 'attempt': attempt, 'stdout': stdout, 'stderr': stderr,
             'returncode': returncode}
    path = collector['directory'] / f'attempt-{len(collector["attempts"])}.json'
    write(path, value)
    collector['attempts'].append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})


def _events(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('native event stream unavailable')
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('unsupported native event stream')
    return rows


def _role(prompt):
    if not isinstance(prompt, str):
        return None
    markers = re.findall(r'^DK_REVIEW_PASS=([A-Za-z0-9_.:-]+)\s*$', prompt, re.M)
    return markers[0] if len(markers) == 1 else None


def codex_passes(raw):
    rows = _events(raw)
    starts = [r for r in rows if r.get('type') == 'thread.started' and isinstance(r.get('thread_id'), str)]
    if len(starts) != 1 or not any(r.get('type') == 'turn.completed' for r in rows):
        raise ValueError('incomplete or unsupported Codex event stream')
    root = starts[0]['thread_id']
    launched, completed, reports = {}, set(), {}
    for row in rows:
        item = row.get('item', {})
        if row.get('type') != 'item.completed' or not isinstance(item, dict) or item.get('type') != 'collab_tool_call':
            continue
        if item.get('sender_thread_id') != root or item.get('status') != 'completed':
            continue
        receivers = item.get('receiver_thread_ids', [])
        if item.get('tool') == 'spawn_agent':
            role = _role(item.get('prompt'))
            if role and isinstance(receivers, list) and len(receivers) == 1 and receivers[0] != root:
                if receivers[0] in launched:
                    raise ValueError('duplicate Codex specialist identity')
                launched[receivers[0]] = role
        states = item.get('agents_states', {})
        if item.get('tool') == 'send_input':
            if (not isinstance(receivers, list) or not receivers
                    or any(not isinstance(child, str) or not child for child in receivers)):
                raise ValueError('unsupported Codex reactivation receiver state')
            for child in receivers:
                if child not in launched:
                    continue
                completed.discard(child)
                reports.pop(child, None)
                if not isinstance(states, dict) or not isinstance(states.get(child), dict):
                    raise ValueError('unsupported Codex reactivation state')
        if isinstance(states, dict):
            for child, state in states.items():
                if child not in launched:
                    continue
                if (not isinstance(state, dict) or state.get('status') not in
                        {'pending_init', 'running', 'completed', 'interrupted', 'errored', 'shutdown', 'not_found'}):
                    raise ValueError('unsupported Codex specialist state')
                if (state.get('status') == 'completed' and isinstance(state.get('message'), str) and state['message'].strip()):
                    completed.add(child)
                    reports[child] = {'role': launched[child], 'text': state['message']}
                elif item.get('tool') != 'close_agent':
                    completed.discard(child)
                    reports.pop(child, None)
    return {'launched': sorted(launched.values()),
            'completed': sorted(launched[child] for child in completed),
            'unfinished': sorted(launched[child] for child in launched.keys() - completed),
            'reports': reports, 'native_session': root}


def claude_passes(raw):
    rows = _events(raw)
    results = [r for r in rows if r.get('type') == 'result']
    if len(results) != 1 or results[0].get('subtype') != 'success' or results[0].get('is_error'):
        raise ValueError('incomplete or unsupported Claude event stream')
    session = results[0].get('session_id')
    if not isinstance(session, str) or not session:
        raise ValueError('Claude native session identity unavailable')
    launched, completed, reports = {}, set(), {}
    identities, aliases = {}, {}
    for row in rows:
        # Parent tool-use and tool-result events only; nested transcripts cannot
        # be substituted for an independent direct specialist of this reviewer.
        if row.get('session_id') != session or row.get('parent_tool_use_id') is not None:
            continue
        message = row.get('message', {})
        content = message.get('content', []) if isinstance(message, dict) else []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if row.get('type') == 'assistant' and block.get('type') == 'tool_use' and block.get('name') == 'SendMessage':
                args = block.get('input', {})
                if not isinstance(args, dict):
                    raise ValueError('unsupported Claude specialist reactivation')
                target, message = args.get('to'), args.get('message')
                if isinstance(message, str):
                    calls = (set(launched) if target == '*' else
                             {identities.get(target), aliases.get(target)} if isinstance(target, str) else set())
                    if not isinstance(target, str) or not target:
                        raise ValueError('unsupported Claude specialist reactivation target')
                    for call in calls - {None}:
                        completed.discard(call)
                        reports.pop(call, None)
                elif not isinstance(message, dict) or message.get('type') not in {
                        'shutdown_request', 'shutdown_response', 'plan_approval_response'}:
                    raise ValueError('unsupported Claude specialist message')
            elif row.get('type') == 'assistant' and block.get('type') == 'tool_use' and block.get('name') in ('Task', 'Agent'):
                args = block.get('input', {})
                role = _role(args.get('prompt')) if isinstance(args, dict) else None
                call = block.get('id')
                if isinstance(args, dict) and args.get('resume'):
                    if role or args['resume'] in identities:
                        raise ValueError('unsupported Claude specialist resume evidence')
                if role and isinstance(call, str) and call:
                    if call in launched:
                        raise ValueError('duplicate Claude specialist call')
                    launched[call] = role
                    alias = args.get('name')
                    if isinstance(alias, str) and alias:
                        if alias in aliases:
                            raise ValueError('duplicate Claude specialist name')
                        aliases[alias] = call
            elif row.get('type') == 'user' and block.get('type') == 'tool_result':
                call = block.get('tool_use_id')
                content = block.get('content')
                text = content if isinstance(content, str) else '\n'.join(
                    p.get('text', '') for p in content if isinstance(p, dict)) if isinstance(content, list) else ''
                if call in launched and not block.get('is_error') and isinstance(text, str) and text.strip():
                    if text.startswith('Async agent launched successfully.\n'):
                        continue
                    # The native synchronous result appends this completion envelope;
                    # ordinary text and background-launch acknowledgements lack it.
                    match = re.search(
                        r"(?m)^agentId: ([A-Za-z0-9_-]+) \(use SendMessage with to: '\1' to continue this agent\)"
                        r'(?:\nworktreePath: [^\n]*\nworktreeBranch: [^\n]*)?'
                        r'\n<usage>total_tokens: [0-9]+\ntool_uses: [0-9]+\nduration_ms: [0-9]+</usage>\s*\Z', text)
                    if match is None:
                        raise ValueError('unsupported Claude specialist result envelope')
                    child = match[1]
                    if child in identities and identities[child] != call:
                        raise ValueError('duplicate Claude specialist identity')
                    identities[child] = call
                    completed.add(call)
                    reports[call] = {'role': launched[call], 'text': text}
    return {'launched': sorted(launched.values()),
            'completed': sorted(launched[child] for child in completed),
            'unfinished': sorted(launched[child] for child in launched.keys() - completed),
            'reports': reports, 'native_session': session}


def assess(attempts, invocation, expected, required):
    observed, unknown = [], []
    if expected.get('method_profile', {}).get('status') == 'unsupported':
        unknown.append(expected['method_profile']['reason'])
    for ref in attempts:
        path = Path(ref['path'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('specialist execution evidence changed')
        item = json.loads(raw)
        if item.get('version') != 1 or item.get('invocation') != invocation or item.get('binding') != expected:
            raise ValueError('specialist execution evidence identity mismatch')
        if item.get('host') != expected.get('executor'):
            raise ValueError('specialist execution evidence executor identity mismatch')
        if item.get('returncode') != 0:
            unknown.append('provider failure')
            continue
        parser = {'codex': codex_passes, 'claude': claude_passes}.get(item.get('host'))
        if parser is None:
            unknown.append('unsupported native host')
            continue
        try:
            observed.append(parser(item.get('stdout')))
        except (ValueError, TypeError, KeyError) as exc:
            unknown.append(str(exc))
    completed = {role for item in observed for role in item['completed']}
    launched = {role for item in observed for role in item['launched']}
    unfinished = {role for item in observed for role in item['unfinished']}
    missing = (set(required) | launched) - completed | unfinished
    primary, potential = conditional(expected['binding'])
    assessments = []
    for observation in observed:
        for report in observation['reports'].values():
            if report['role'] != primary:
                continue
            lines = re.findall(r'^DK_REVIEW_APPLICABILITY_JSON=(.+)$', report['text'], re.M)
            if len(lines) != 1:
                continue
            try:
                value = json.loads(lines[0])
            except ValueError:
                continue
            if (not isinstance(value, dict) or set(value) != {'head_sha', 'base_sha', 'invocation', 'descriptor_sha256', 'roles'}
                    or value.get('head_sha') != expected['head_sha'] or value.get('base_sha') != expected['base_sha']
                    or value.get('invocation') != invocation or value.get('descriptor_sha256') != expected['descriptor_sha256']
                    or not isinstance(value.get('roles'), dict) or set(value['roles']) != set(potential)):
                continue
            if any(not isinstance(item, dict) or set(item) != {'required', 'reason'}
                   or type(item['required']) is not bool or not isinstance(item['reason'], str) or not item['reason'].strip()
                   for item in value['roles'].values()):
                continue
            assessments.append(value)
    for role in potential:
        if role in completed and role not in unfinished:
            continue
        if not assessments:
            unknown.append('conditional applicability unavailable: ' + role)
        elif any(value['roles'][role]['required'] for value in assessments):
            missing.add(role)
    status = 'complete' if not missing and not unknown and observed else 'unavailable' if unknown or not observed else 'missing'
    return {'status': status, 'required': required, 'completed': sorted(completed),
            'missing': sorted(missing), 'observed': observed, 'unknown': unknown,
            'applicability_assessments': assessments}


def finish(collector, result):
    if collector is None:
        return None
    assessment = assess(collector['attempts'], collector['invocation'], collector['binding'], collector['required'])
    value = {'version': 1, 'invocation': collector['invocation'], 'binding': collector['binding'],
             'attempts': collector['attempts'], 'assessment': assessment, 'original_result': result}
    path = collector['directory'] / 'specialist-execution.json'
    write(path, value)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def verify(reference, binding, descriptor, handoff_path, result=None):
    handoff = json.loads(Path(handoff_path).read_text())
    required = requirements(binding, handoff)
    if not required:
        return
    if not isinstance(reference, dict):
        raise ValueError('required specialist execution evidence unavailable; historical clean JSON is not execution proof')
    raw = Path(reference['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference.get('sha256'):
        raise ValueError('specialist execution evidence changed')
    value = json.loads(raw)
    expected = identity(binding, descriptor, handoff, handoff_path)
    if value.get('version') != 1 or value.get('binding') != expected:
        raise ValueError('specialist execution evidence identity mismatch')
    if result is not None and value.get('original_result') != result:
        raise ValueError('specialist execution evidence result mismatch')
    actual = assess(value['attempts'], value['invocation'], expected, required)
    if actual != value.get('assessment'):
        raise ValueError('specialist execution assessment changed')
    if actual['status'] != 'complete':
        raise ValueError('required specialist execution ' + actual['status'] + ': ' +
                         ', '.join(actual['missing'] or actual['unknown']) + '; evidence: ' + reference['path'])
