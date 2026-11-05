"""Independent outcome checks and normalized public records; no native launches."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
import dollar_exercise
import dollar_trials
import dollar_usage


def sha(data):
    return hashlib.sha256(data).hexdigest()


def assess(root, experiment):
    verified = dollar_exercise.verify(root)
    assessment = verified['assessment'] or {}
    manifest = verified['manifest']
    scenario = verified['scenario']
    problems = []
    if not verified['frozen_intact'] or not verified['inputs_intact']:
        problems.append('Frozen runtime/guidance/inputs changed')
    if assessment.get('complete') is not True or (assessment.get('blockers') and experiment != 3):
        problems.append('Native terminal assessment is incomplete or blocked')
    if experiment in (1,2,3,6):
        if assessment.get('acceptance') != scenario['acceptance']:
            problems.append('Terminal assessment does not retain exact acceptance')
        if assessment.get('source') != scenario['source']:
            problems.append('Terminal assessment does not retain exact acceptance source')
    if experiment in (1,5) or (experiment == 6 and 5 in scenario['accepted_proposals']):
        if not verified['results'] or any(e.get('claim') for e in manifest['entries'].values()):
            problems.append('Lifecycle completion is missing or has unfinished claims')
        if any(r['status'] != 'complete' or not r['original'].strip() for r in verified['results']):
            problems.append('Lifecycle lacks complete original outputs')
    if experiment in (2,3,6) and verified['current_status']:
        initial = json.loads((root/'initial-manifest.json').read_text())
        prior_stable = next(e for e in initial['entries'].values() if e['components']['task']['id'] == 'stable')
        current_stable = manifest['entries'].get(prior_stable['key'], {})
        if current_stable.get('result') != prior_stable['result'] or current_stable.get('attempts') != prior_stable['attempts']:
            problems.append('Unchanged work was repeated or original evidence was replaced')
        run = manifest['runs'].get('retained', {})
        for counter in ('retries','ci_repairs','ci_reruns'):
            if run.get(counter) != initial['runs']['retained'][counter]:
                problems.append('Retained '+counter+' changed')
        current = {t['task']:t for t in verified['current_status']['tasks']}
        if not current['stable']['reusable']:
            problems.append('Unchanged original evidence is not currently reusable')
        if experiment in (2,6):
            if not all(t['reusable'] for t in current.values()):
                problems.append('Resumed work lacks fresh complete evidence')
            if any(e.get('claim') for e in manifest['entries'].values()):
                problems.append('Interrupted claim did not finish')
            if len(run.get('cycles', [])) != 2:
                problems.append('Selective invalidation did not retain and advance review cycles')
            if any(r['status'] != 'complete' for r in verified['results']):
                problems.append('Resumed tasks did not complete')
        else:
            for key,old in initial['entries'].items():
                if manifest['entries'].get(key) != old:
                    problems.append('Read-only history assessment changed retained task state')
            if current['changed']['reusable'] or current['unfinished']['reusable']:
                problems.append('Incomplete or changed work incorrectly satisfies coverage')
            if len(run.get('progress', [])) < 243:
                problems.append('Appended progress was not retained')
            if run.get('cycles') != ['c1']:
                problems.append('Read-only coverage assessment changed cycles')
            if not any(e.get('claim') for e in manifest['entries'].values()):
                problems.append('Incomplete coverage was incorrectly completed')
    if experiment in (3,6) and (experiment == 3 or 3 in scenario['accepted_proposals']):
        progress = manifest['runs'].get('retained',{}).get('progress',[])
        initial = json.loads((root/'initial-manifest.json').read_text())['runs']['retained']['progress']
        if progress[:len(initial)] != initial:
            problems.append('Complete original progress was changed or lost')
        before = json.loads((root/'initial-manifest.json').read_text())
        unfinished = next(e['claim']['id'] for e in before['entries'].values() if e.get('claim'))
        if not any(p.get('claim') == unfinished and p.get('value') for p in progress[len(initial):]):
            problems.append('No new progress was retained for the unfinished claim')
        if sum(p.get('value',{}).get('external_event') == 'append-1' for p in progress) > 1:
            problems.append('Controlled appended event was duplicated')
    if experiment == 5 or (experiment == 6 and 5 in scenario['accepted_proposals']):
        entries = list(manifest['entries'].values())
        roles = [e['components']['task']['id'] for e in entries]
        if ('content-review' not in roles or not any(r.startswith('cycle-hook:') for r in roles) or
                not any(r.startswith('finalizer:') for r in roles)):
            problems.append('Discovery did not complete ordinary, hook and finalizer tasks')
        for entry in entries:
            if entry['components']['task']['id'].startswith(('cycle-hook:','finalizer:')):
                if any(a.get('cycle') is not None for a in entry['attempts']):
                    problems.append('Hook/finalizer consumed reviewer-cycle bookkeeping')
    if experiment == 4 or (experiment == 6 and 4 in scenario['accepted_proposals']):
        observations = list((root/'state').glob('**/ci-observations/pr-7.json'))
        if len(observations) != 1:
            problems.append('CI observation identity is missing or ambiguous')
        else:
            observation = json.loads(observations[0].read_text())['current']
            if observation['head'] != scenario['head'] or observation['pr'] != scenario['pr']:
                problems.append('CI observation identity changed')
            if observation.get('last_result',{}).get('result') != 'success':
                problems.append('CI did not observe successful exact-head checks')
            if observation.get('deadline') != observation['started'] + observation['window_seconds']:
                problems.append('CI deadline was reset or unnecessarily extended')
        transport = json.loads((root/'ci-transport.json').read_text()) if (root/'ci-transport.json').exists() else {}
        if transport.get('checks',0) < 3:
            problems.append('Pending and terminal CI transports were not both consumed')
    result_refs = [{'task':r['task'],'status':r['status'],'normalized':r['normalized'],
                    'original_sha256':sha(r['original'].encode())} for r in verified['results']]
    return {'passed':not problems,'problems':problems,'frozen_intact':verified['frozen_intact'],
            'inputs_intact':verified['inputs_intact'],
            'assessment_sha256':sha((root/'artifacts/assessment.json').read_bytes()) if assessment else None,
            'native_complete':assessment.get('complete'), 'native_blockers':assessment.get('blockers'),
            'exact_acceptance':scenario.get('acceptance'), 'acceptance_source':scenario.get('source'),
            'results':result_refs,
            'retained_counters':{name:{k:v for k,v in run.items() if k not in ('progress','review_tasks')}
                                 for name,run in manifest['runs'].items()},
            'progress_counts':{name:len(run.get('progress',[])) for name,run in manifest['runs'].items()}}


def report(root, experiment):
    pairs = []
    checks = []
    observations = {'baseline':[], 'candidate':[]}
    for i, order in enumerate(dollar_trials.ORDERS,1):
        pair = {'order': order}
        for arm in ('baseline','candidate'):
            path = root/(str(i)+'-'+arm)
            if not (path/'host-outcome.json').exists():
                raise ValueError('An initiated arm is still running; do not assess a partial experiment')
            usage = json.loads((path/'usage.json').read_text())
            if not usage.get('telemetry_complete'):
                usage = dollar_trials.observed_partial(path,
                    json.loads((path/'host-outcome.json').read_text())['exit_code'], usage.get('error','Incomplete telemetry'))
            check = assess(path, experiment)
            checks.append(check)
            pair[arm] = {'usage':usage,'correctness':check,
                         'freeze':json.loads((path/'freeze.json').read_text())}
            cost = usage.get('api_equivalent_cost_usd',usage.get('observed_api_equivalent_cost_usd'))
            observations[arm].append(Decimal(cost))
        pairs.append(pair)
    gate = dollar_usage.evaluate_pairs([{k:(v['usage'] if k != 'order' else v) for k,v in p.items()}
                                       for p in pairs], correctness_passed=(all(c['passed'] for c in checks)
                                           if all(c['assessment_sha256'] for c in checks) else None))
    result = {'proposal':experiment, 'decision':gate,
              'correctness_passed':all(c['passed'] for c in checks),
              'native_arm_count':6, 'pairs':pairs,
              'observed_costs_usd':{arm:list(map(str,costs)) for arm,costs in observations.items()},
              'observed_medians_usd':{arm:str(median(costs)) for arm,costs in observations.items()},
              'observed_total_usd':str(sum(sum(costs) for costs in observations.values())),
              'billing_basis':dollar_usage.RATE_CARD['billing_basis'],
              'limitations':['Only complete telemetry may accept a production change.',
                             'Three pairs provide bounded evidence. Raw traces remain private.']}
    normalized = json.dumps(result,sort_keys=True,indent=2).replace(str(root),'EXPERIMENT')
    return normalized+'\n'


if __name__=='__main__':
    print(report(Path(sys.argv[1]),int(sys.argv[2])),end='')
