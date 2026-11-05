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
    if assessment.get('complete') is not True or assessment.get('blockers'):
        problems.append('Native terminal assessment is incomplete or blocked')
    if experiment in (1,2,3):
        if assessment.get('acceptance') != scenario['acceptance']:
            problems.append('Terminal assessment does not retain exact acceptance')
        if assessment.get('source') != scenario['source']:
            problems.append('Terminal assessment does not retain exact acceptance source')
    if experiment in (1,5):
        if not verified['results'] or any(e.get('claim') for e in manifest['entries'].values()):
            problems.append('Lifecycle completion is missing or has unfinished claims')
        if any(r['status'] != 'complete' or not r['original'].strip() for r in verified['results']):
            problems.append('Lifecycle lacks complete original outputs')
    if experiment in (2,3):
        initial = json.loads((root/'initial-manifest.json').read_text())
        prior_stable = next(e for e in initial['entries'].values() if e['components']['task']['id'] == 'stable')
        current_stable = manifest['entries'].get(prior_stable['key'], {})
        if current_stable.get('result') != prior_stable['result'] or current_stable.get('attempts') != prior_stable['attempts']:
            problems.append('Unchanged work was repeated or original evidence was replaced')
        run = manifest['runs'].get('retained', {})
        for counter in ('retries','ci_repairs','ci_reruns'):
            if run.get(counter) != initial['runs']['retained'][counter]:
                problems.append('Retained '+counter+' changed')
        if experiment == 2:
            if any(e.get('claim') for e in manifest['entries'].values()):
                problems.append('Interrupted claim did not finish')
            if len(run.get('cycles', [])) != 2:
                problems.append('Selective invalidation did not retain and advance review cycles')
            if any(r['status'] != 'complete' for r in verified['results']):
                problems.append('Resumed tasks did not complete')
        else:
            if len(run.get('progress', [])) < 243:
                problems.append('Appended progress was not retained')
            if run.get('cycles') != ['c1']:
                problems.append('Read-only coverage assessment changed cycles')
            if not any(e.get('claim') for e in manifest['entries'].values()):
                problems.append('Incomplete coverage was incorrectly completed')
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
                                       for p in pairs], correctness_passed=all(c['passed'] for c in checks))
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
