#!/usr/bin/env python3
"""Persistent, bounded operational record for one PR iteration."""
import sys
from pathlib import Path
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))
import argparse
import json
import re
from pathlib import Path
import sys
import importlib.util
import copy
import uuid

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir())))

SHA = re.compile(r"^[0-9a-f]{40}$")
CLASSIFICATIONS = {"valid", "already_fixed", "unsupported", "pre_existing"}


from shared.private_state import root, read, write, mutate
from shared.flaky_tests import Registry, record_flaky
from shared import review_coverage, review_sessions

_spec = importlib.util.spec_from_file_location('iteration_preparation',
    next(p for p in Path(__file__).resolve().parents if (p / 'shared').is_dir()) / 'dk-pr-decisions/scripts/preparation.py')
preparation_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preparation_api)


def start_run(record, *, resume=False):
    """Start a distinct bounded request, or explicitly resume interrupted work."""
    active = record.get('iteration_run')
    if resume:
        if not active or active['status'] != 'active':
            raise ValueError('no active iteration run to resume')
        return active
    if active and active['status'] == 'active':
        raise ValueError('iteration run is active; resume or finish it before starting another')
    if any(record.get(field) for field in ('cycles', 'cycle_retries', 'repairs', 'review_results')) and not active:
        raise ValueError('legacy record requires explicit adoption with adopt-run before continuing')
    if active:
        snapshot = copy.deepcopy({k: v for k, v in record.items() if k != 'run_history'})
        record.setdefault('run_history', []).append(snapshot)
    for field in ('cycles', 'cycle_retries', 'repairs', 'review_results', 'review_sessions'):
        record[field] = []
    record.pop('preparation', None)
    record['iteration_run'] = {'id': uuid.uuid4().hex, 'status': 'active'}
    return record['iteration_run']


def adopt_run(record):
    """Migrate a legacy record without discarding its exhausted counters."""
    if record.get('iteration_run'):
        raise ValueError('record already has an iteration run')
    record['iteration_run'] = {'id': uuid.uuid4().hex, 'status': 'active', 'legacy': True}
    return record['iteration_run']


def finish_run(record, status, reason):
    active = record.get('iteration_run')
    if (not active or active['status'] != 'active' or not reason.strip()
            or status not in {'completed', 'limited'}):
        raise ValueError('finishing requires an active run and an outcome reason')
    active.update(status=status, reason=reason)
    return active


def attach_preparation(record, path, pr_url, head):
    artifact = preparation_api.gate(path, pr_url=pr_url, head_sha=head, require_ready=False)
    if artifact['mode'] != 'iteration':
        raise ValueError('iteration requires iteration preparation')
    raw = preparation_api.read(artifact['sources']['decisions']['path'])
    decisions = raw.get('data', raw)
    if any(choice not in decisions['local_decisions'] for choice in record.get('local_decisions', [])):
        raise ValueError('preparation omits run-record local choices')
    item = {'path': str(Path(path).resolve()), 'id': artifact['preparation_id'],
            'head': head, 'pr_url': pr_url,
            'local_decisions_hash': preparation_api.digest(record.get('local_decisions', []))}
    history = record.setdefault('preparations', [])
    if item not in history:
        history.append(item)
    record['preparation'] = item
    return item


def work_gate(record, head, *, require_ready=True):
    if record.get('iteration_run') and record['iteration_run']['status'] != 'active':
        raise ValueError('iteration run is finished; start a new run')
    item = record.get('preparation')
    if not item:
        raise ValueError('preparation required before initial work or review')
    if item['local_decisions_hash'] != preparation_api.digest(record.get('local_decisions', [])):
        raise ValueError('local decisions changed; refresh preparation')
    artifact = preparation_api.gate(item['path'], pr_url=item['pr_url'], head_sha=head,
                                    require_ready=require_ready)
    if artifact['preparation_id'] != item['id']:
        raise ValueError('preparation artifact changed')
    return artifact


def assess(record, ref, head, preparation_id, disposition, evidence, reason):
    """Keep validity independent from scope; never rewrite earlier scope assessments."""
    require_head(head)
    artifact = work_gate(record, head, require_ready=disposition == 'within_scope')
    if preparation_id != artifact['preparation_id']:
        raise ValueError('assessment preparation mismatch')
    if disposition not in {'within_scope', 'outside_scope', 'decision_blocked'} or not all((ref, evidence, reason)):
        raise ValueError('scope assessment requires disposition, evidence and rationale')
    item = {'reference': ref, 'head': head, 'preparation_id': preparation_id,
            'disposition': disposition, 'evidence': evidence, 'reason': reason}
    entries = record.setdefault('assessments', [])
    old = next((entry for entry in entries if all(entry[k] == item[k]
                for k in ('reference', 'head', 'preparation_id'))), None)
    if old and old != item:
        raise ValueError('assessment already recorded; refresh preparation to reassess')
    if not old:
        entries.append(item)
    return item


def fix_plan(record, ref, head):
    artifact = work_gate(record, head, require_ready=False)
    assessment = next((item for item in record.get('assessments', [])
                       if item['reference'] == ref and item['head'] == head
                       and item['preparation_id'] == artifact['preparation_id']), None)
    if not assessment:
        raise ValueError('scope and remediation-depth assessment required before a fix')
    # Scope is evaluated first. A small pre-existing defect never overrides an exclusion.
    if assessment['disposition'] != 'within_scope':
        return {'action': 'skip' if assessment['disposition'] == 'outside_scope' else 'decision_blocked',
                'assessment': assessment}
    work_gate(record, head)
    classification = record.get('threads', {}).get(ref, {}).get(head)
    if not classification:
        raise ValueError('defect validity assessment required before a fix')
    return {'action': classification['disposition'], 'assessment': assessment,
            'classification': classification['classification'], 'preparation_id': artifact['preparation_id'],
            'placement': artifact['analysis']['placement'], 'test_strategy': artifact['analysis']['test_strategy'],
            'instructions': artifact['analysis']['worker_instructions']}


def require_head(head):
    if not SHA.fullmatch(head):
        raise ValueError("head must be a full lowercase SHA")


def classify(record, ref, head, classification, evidence, reason, production_lines=None):
    require_head(head)
    if not ref or classification not in CLASSIFICATIONS or not evidence or not reason:
        raise ValueError("thread requires reference, classification, evidence, and reason")
    if classification == "pre_existing":
        if production_lines is None or production_lines < 0:
            raise ValueError("pre-existing issue requires a nonnegative production-line estimate")
        disposition = "deferred" if production_lines > 20 else "fix"
    else:
        if production_lines is not None:
            raise ValueError("production-line estimate applies only to pre-existing issues")
        disposition = "fix" if classification == "valid" else "no_change"
    item = dict(classification=classification, evidence=evidence, reason=reason, disposition=disposition)
    if production_lines is not None:
        item["production_lines"] = production_lines
    by_head = record.setdefault("threads", {}).setdefault(ref, {})
    existing = by_head.get(head)
    if existing and existing != item:
        raise ValueError("thread already classified differently at this head")
    by_head[head] = item
    return item


def append_unique(record, field, item, key):
    entries = record.setdefault(field, [])
    if any(entry[key] == item[key] for entry in entries):
        raise ValueError(f"duplicate {field} {item[key]}")
    entries.append(item)
    return item


def next_cycle(record, head):
    require_head(head)
    work_gate(record, head)
    number = len(record.setdefault("cycles", [])) + 1
    if number > 5:
        raise ValueError("five review cycles exhausted")
    return append_unique(record, "cycles", {"number": number, "head": head}, "number")


def review_attempt(record, head):
    run = record.get('iteration_run', {})
    if not run.get('id') or run.get('status') != 'active':
        raise ValueError('an active iteration run is required')
    cycles = record.get('cycles', [])
    if not cycles:
        raise ValueError('record a review cycle before launching reviewers')
    last = cycles[-1]
    retries = [r for r in record.get('cycle_retries', []) if r['number'] == last['number']]
    if head != (retries[-1]['head'] if retries else last['head']):
        raise ValueError('review head differs from recorded cycle')
    return {'iteration_run_id': run['id'], 'cycle': last['number'], 'attempt': 1 + len(retries)}


def review_session(record, head, record_path):
    work_gate(record, head)
    identity = review_attempt(record, head)
    entries = record.setdefault('review_sessions', [])
    item = next((v for v in entries if all(v[k] == value for k, value in identity.items())), None)
    if item is None:
        item = {**identity, 'head': head, 'session_file': str(
            Path(record_path).resolve().parent / 'review-sessions' / (uuid.uuid4().hex + '.json'))}
        entries.append(item)
    if item['head'] != head:
        raise ValueError('iteration review session head changed')
    return item


def register_review(record, output, head):
    prepared = work_gate(record, head)
    identity = review_attempt(record, head)
    binding = next((v for v in record.get('review_sessions', [])
                    if all(v[k] == value for k, value in identity.items())), None)
    if binding is None:
        raise ValueError('allocate an iteration review session before registering results')
    item = output['review_artifact']
    artifact = review_sessions.result_artifact(binding['session_file'], item)
    if (artifact['head'] != head or artifact['preparation_id'] != prepared['preparation_id']
            or artifact['pr_url'] != prepared['identity']['pr_url']):
        raise ValueError('review result differs from the active preparation')
    registered = {**item, **identity, 'session_file': binding['session_file']}
    entries = record.setdefault('review_results', [])
    if registered not in entries:
        entries.append(registered)
    return registered


def verify_review(record_path, prepared):
    record = read(Path(record_path), {})
    # A final regroup may change the head while preserving the reviewed tree.
    run = record.get('iteration_run', {})
    if not run.get('id') or run.get('status') != 'active':
        raise ValueError('an active iteration run is required')
    if not record.get('review_results'):
        raise ValueError('no configured-reviewer results in this iteration run')
    item = record['review_results'][-1]
    cycles = record.get('cycles', [])
    attempt = 1 + sum(r['number'] == item['cycle'] for r in record.get('cycle_retries', []))
    if (item['iteration_run_id'] != run['id'] or not cycles
            or item['cycle'] != cycles[-1]['number'] or item['attempt'] != attempt
            or not any(s['session_file'] == item['session_file'] and all(s[k] == item[k]
                       for k in ('iteration_run_id', 'cycle', 'attempt'))
                       for s in record.get('review_sessions', []))):
        raise ValueError('current iteration cycle lacks review coverage')
    reference = {k: item[k] for k in ('path', 'sha256')}
    review_sessions.result_artifact(item['session_file'], reference)
    artifact = review_coverage.verify(reference, prepared)
    for finding in artifact['result']['findings']:
        ref = 'review-finding:' + review_coverage.digest(finding)
        classification = record.get('threads', {}).get(ref, {}).get(artifact['head'])
        assessment = next((a for a in record.get('assessments', [])
                           if a['reference'] == ref and a['head'] == artifact['head']
                           and a['preparation_id'] == artifact['preparation_id']), None)
        if not classification or not assessment:
            raise ValueError('review finding lacks validity and scope assessment: ' + ref)
        if assessment['disposition'] == 'decision_blocked' or (
                assessment['disposition'] == 'within_scope'
                and classification['disposition'] not in {'no_change', 'deferred'}):
            raise ValueError('review finding remains actionable: ' + ref)
    return artifact


def retry_exhausted_cycle(record, head):
    """Retry the last incomplete usage-limited review once without a sixth cycle."""
    require_head(head)
    work_gate(record, head)
    cycles = record.get("cycles", [])
    if not cycles:
        raise ValueError("no review cycle to retry")
    last = cycles[-1]
    number = last["number"]
    prefix = f"review-cycle-{number}:"
    events = [event for event in record.get("events", [])
              if event.get("kind") == "review" and event.get("key", "").startswith(prefix)
              and event.get('iteration_run_id') == record.get('iteration_run', {}).get('id')]
    exhausted = any(event.get("key", "").endswith(":incomplete")
                    and any(term in event.get("detail", "").lower()
                            for term in ("session limit", "usage limit", "tokens_exhausted"))
                    for event in events)
    if not exhausted or any(not event.get("key", "").endswith(":incomplete") for event in events):
        raise ValueError("last cycle is not an incomplete usage-limited review")
    retries = record.setdefault("cycle_retries", [])
    if any(item["number"] == number for item in retries):
        raise ValueError("usage-limited review retry already recorded")
    item = {"number": number, "prior_head": last["head"], "head": head}
    retries.append(item)
    return item


def decision_route(options, independently_checked, settled_option=None):
    """Require evidence review before either settled or human-selected material choice."""
    if len(set(options)) < 2 or not independently_checked:
        raise ValueError("material decision needs viable options and independent evidence check")
    if settled_option is None:
        return "ask_human"
    if settled_option not in options:
        raise ValueError("settled choice is not a viable option")
    return "record_and_implement"


def record_local_decision(record, text, source):
    if not isinstance(text, str) or not text.strip() or not isinstance(source, str) or not source.strip():
        raise ValueError("local decision requires text and source")
    item = {"text": text.strip(), "source": source.strip()}
    decisions = record.setdefault("local_decisions", [])
    if any(existing["text"] == item["text"] and existing != item for existing in decisions):
        raise ValueError("local decision has conflicting sources; reconcile before continuing")
    if item not in decisions:
        decisions.append(item)
    return item


def reviewer_assessments(record, repo, links):
    """Reuse normal classification/scope evidence for compact analytics links."""
    spec = importlib.util.spec_from_file_location('iteration_review_stats',
        Path(__file__).with_name('review_stats.py'))
    stats = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stats)
    if not isinstance(links, list):
        raise ValueError('review assessment links must be a list')
    assessments = []
    for link in links:
        if (not isinstance(link, dict) or set(link) != {'id', 'reference', 'head', 'finding_ids',
                                                       'canonical_issue', 'severity'}
                or not isinstance(link['finding_ids'], list) or not link['finding_ids']
                or any(not isinstance(fid, str) or not fid for fid in link['finding_ids'])):
            raise ValueError('review assessment needs identity, finding IDs, canonical link and severity')
        ref, head = link['reference'], link['head']
        require_head(head)
        classification = record.get('threads', {}).get(ref, {}).get(head)
        if not classification:
            raise ValueError('classify the reviewer finding before recording analytics')
        scope = next((a for a in reversed(record.get('assessments', []))
                      if a['reference'] == ref and a['head'] == head), None)
        if not scope:
            raise ValueError('assess reviewer scope before recording analytics')
        kind = classification['classification']
        verdict = 'rejected' if kind == 'unsupported' else 'confirmed'
        disposition = ('unknown' if kind == 'unsupported' else
                       'excluded' if scope['disposition'] == 'outside_scope' else
                       'deferred' if scope['disposition'] == 'decision_blocked' or classification['disposition'] == 'deferred' else
                       'fixed' if kind == 'already_fixed' else 'actionable')
        # A link may cover several independently attributed observations of one defect.
        for finding_id in link['finding_ids']:
            assessments.append({'id': link['id'] + ':' + finding_id,
                'finding_id': finding_id, 'canonical_issue': link['canonical_issue'],
                'verdict': verdict, 'severity': link['severity'], 'disposition': disposition,
                'evidence': list(dict.fromkeys([classification['evidence'], scope['evidence']])),
                'reason': classification['reason'] + '; scope: ' + scope['reason']})
    result = stats.record_assessments(repo, assessments)
    stats.render_reports()
    return result


def attempt(record, kind, cycle, head, run_id=None):
    require_head(head)
    if cycle < 0 or cycle > len(record.get("cycles", [])):
        raise ValueError("unknown cycle")
    if kind == "repair":
        work_gate(record, head)
        entries = record.setdefault("repairs", [])
        matching = [x for x in entries if x["cycle"] == cycle]
        limit = 5
    else:
        if not run_id:
            raise ValueError("rerun requires Actions run ID")
        entries = record.setdefault("reruns", [])
        matching = [x for x in entries if x["head"] == head]
        limit = 3
    if len(matching) >= limit:
        raise ValueError(f"{kind} limit exhausted")
    item = {"cycle": cycle, "head": head, "number": len(matching) + 1}
    if run_id:
        item["run_id"] = run_id
    entries.append(item)
    return item


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", type=int)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    sub.add_parser('start-run')
    sub.add_parser('resume-run')
    sub.add_parser('adopt-run')
    finish = sub.add_parser('finish-run')
    finish.add_argument('--status', choices=('completed', 'limited'), required=True)
    finish.add_argument('--reason', required=True)
    stats = sub.add_parser('review-assess')
    stats.add_argument('--file', required=True, help='Compact links to existing finding classifications and scope')
    prep = sub.add_parser('preparation')
    prep.add_argument('--file', required=True)
    prep.add_argument('--pr-url', required=True)
    prep.add_argument('--head', required=True)
    gate = sub.add_parser('work-gate')
    gate.add_argument('--head', required=True)
    fix = sub.add_parser('fix-plan')
    fix.add_argument('--head', required=True)
    fix.add_argument('--ref', required=True)
    assessment = sub.add_parser('assess')
    for arg in ('ref', 'head', 'preparation-id', 'evidence', 'reason'):
        assessment.add_argument('--' + arg, required=True)
    assessment.add_argument('--disposition', required=True,
                            choices=('within_scope', 'outside_scope', 'decision_blocked'))
    t = sub.add_parser("thread")
    t.add_argument("--ref", required=True); t.add_argument("--head", required=True); t.add_argument("--classification", choices=sorted(CLASSIFICATIONS), required=True)
    t.add_argument("--evidence", required=True); t.add_argument("--reason", required=True); t.add_argument("--production-lines", type=int)
    e = sub.add_parser("event")
    for arg in ("key", "kind", "head", "detail"):
        e.add_argument("--" + arg, required=True)
    c = sub.add_parser("cycle"); c.add_argument("--head", required=True)
    cr = sub.add_parser("cycle-retry"); cr.add_argument("--head", required=True)
    session = sub.add_parser('review-session'); session.add_argument('--head', required=True)
    result = sub.add_parser('review-result')
    result.add_argument('--head', required=True); result.add_argument('--file', required=True)
    d = sub.add_parser("decision")
    d.add_argument("--text", required=True); d.add_argument("--source", required=True)
    for kind in ("repair", "rerun"):
        q = sub.add_parser(kind); q.add_argument("--cycle", type=int, required=True); q.add_argument("--head", required=True)
        if kind == "rerun": q.add_argument("--run-id", required=True)
    f = sub.add_parser("flaky")
    for arg in ("test", "signature", "head", "attempt", "url"):
        f.add_argument("--" + arg, required=True)
    f.add_argument("--outcome", choices=("fail", "pass"), required=True)
    sub.add_parser("flaky-show")
    a = p.parse_args()
    try:
        base = root(a.repo)
        if a.command.startswith("flaky"):
            path = Registry(a.repo).path
            result = read(path, {"tests": {}}) if a.command == "flaky-show" else mutate(path, {"tests": {}}, lambda d: record_flaky(d, a.test, a.signature, a.head, a.outcome, a.attempt, a.url))
        else:
            if not a.pr or a.pr < 1: raise ValueError("positive --pr required")
            path = base / f"pr-{a.pr}.json"
            default = {"pr": a.pr, "threads": {}, "events": [], "cycles": [], "cycle_retries": [], "repairs": [], "reruns": [], "local_decisions": []}
            if a.command == "show": result = read(path, default)
            elif a.command in {'start-run', 'resume-run'}:
                result = mutate(path, default, lambda d: start_run(d, resume=a.command == 'resume-run'))
            elif a.command == 'adopt-run': result = mutate(path, default, adopt_run)
            elif a.command == 'finish-run':
                result = mutate(path, default, lambda d: finish_run(d, a.status, a.reason))
            elif a.command == 'review-assess':
                result = reviewer_assessments(read(path, default), a.repo, json.loads(Path(a.file).read_text()))
            elif a.command == 'preparation': result = mutate(path, default, lambda d: attach_preparation(d, a.file, a.pr_url, a.head))
            elif a.command == 'work-gate':
                artifact = work_gate(read(path, default), a.head)
                result = {'gate': 'ready', 'preparation_id': artifact['preparation_id']}
            elif a.command == 'assess': result = mutate(path, default, lambda d: assess(d, a.ref, a.head, a.preparation_id, a.disposition, a.evidence, a.reason))
            elif a.command == 'fix-plan': result = fix_plan(read(path, default), a.ref, a.head)
            elif a.command == "thread": result = mutate(path, default, lambda d: classify(d, a.ref, a.head, a.classification, a.evidence, a.reason, a.production_lines))
            elif a.command == "event":
                require_head(a.head)
                result = mutate(path, default, lambda d: append_unique(d, "events", vars(a) | {
                    "head": a.head, 'iteration_run_id': d.get('iteration_run', {}).get('id')}, "key"))
            elif a.command == "cycle": result = mutate(path, default, lambda d: next_cycle(d, a.head))
            elif a.command == "cycle-retry": result = mutate(path, default, lambda d: retry_exhausted_cycle(d, a.head))
            elif a.command == 'review-session': result = mutate(path, default, lambda d: review_session(d, a.head, path))
            elif a.command == 'review-result':
                result = mutate(path, default, lambda d: register_review(d, json.loads(Path(a.file).read_text()), a.head))
            elif a.command == "decision": result = mutate(path, default, lambda d: record_local_decision(d, a.text, a.source))
            else: result = mutate(path, default, lambda d: attempt(d, a.command, a.cycle, a.head, getattr(a, "run_id", None)))
        print(json.dumps(result, sort_keys=True))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        p.exit(2, f"run_state: {exc}\n")


if __name__ == "__main__":
    main()
