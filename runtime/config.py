"""Small workflow-owned settings. Domain settings remain references to their owner."""
from pathlib import Path
import os
from state import digest, read

DEFAULTS = {
    'reviewers': [],
    'skills': {
        'feedback': 'dk-workflows:dk-pr-address-reviews',
        'decisions': 'dk-workflows:dk-pr-decisions',
        'description': 'dk-workflows:dk-pr-description',
        'reply': 'dk-workflows:dk-pr-reply-review',
        'partition': 'dk-workflows:dk-pr-split-commits',
        'cleanup': 'dk-workflows:dk-pr-split-commits',
        'ci': 'dk-workflows:dk-ci',
        'flaky_tests': 'dk-workflows:dk-flaky-tests',
        'publish': 'dk-workflows:dk-pr-publish',
    },
    'iteration': {'max_cycles': 5, 'same_cycle_retries': 1, 'ci_repairs_per_cycle': 5,
                  'ci_reruns_per_head': 3},
    'domain_settings': {},
}


def normalize_skill(skill):
    if isinstance(skill, str) and skill.startswith('dk-pr-skills:'):
        mapping = read(Path(__file__).resolve().parents[1]/'docs/v1-skill-mapping.json')
        return mapping.get(skill, skill)
    return skill


def validate(settings):
    if not isinstance(settings, dict) or set(settings) - DEFAULTS.keys():
        raise ValueError('Only reviewers, skills, iteration and domain_settings belong to workflows')
    if not isinstance(settings.get('reviewers', []), list):
        raise ValueError('Reviewers must be a list')
    for key in ('skills', 'domain_settings'):
        if not isinstance(settings.get(key, {}), dict):
            raise ValueError(key + ' must be an object')
    import copy
    settings = copy.deepcopy(settings)
    for reviewer in settings.get('reviewers', []):
        if (not isinstance(reviewer, dict) or set(reviewer) - {'skill', 'required', 'options'}
                or not isinstance(reviewer.get('skill'), str) or not reviewer['skill']
                or type(reviewer.get('required', True)) is not bool
                or not isinstance(reviewer.get('options', {}), dict)):
            raise ValueError('Reviewer requires skill, optional required boolean and execution options')
    reviewers = settings.get('reviewers', [])
    if not isinstance(reviewers, list) or len({x['skill'] for x in reviewers}) != len(reviewers):
        raise ValueError('Reviewers must be a list of unique skills')
    for role, skill in settings.get('skills', {}).items():
        if role not in DEFAULTS['skills'] or not isinstance(skill, str) or not skill:
            raise ValueError('Unknown role or empty selected skill')
    if 'iteration' in settings and settings['iteration'] != DEFAULTS['iteration']:
        raise ValueError('v2 retains the fixed iteration limits')
    for owner, reference in settings.get('domain_settings', {}).items():
        if not isinstance(owner, str) or not isinstance(reference, str) or not reference:
            raise ValueError('Domain settings must be owner-to-reference strings')
    for reviewer in settings.get('reviewers', []):
        reviewer['skill'] = normalize_skill(reviewer['skill'])
    if 'skills' in settings:
        settings['skills'] = {role: normalize_skill(skill) for role, skill in settings['skills'].items()}
    references = settings.get('domain_settings', {})
    if 'dk-pr-skills' in references:
        previous = references.pop('dk-pr-skills')
        if 'dk-workflows' in references and references['dk-workflows'] != previous:
            raise ValueError('Conflicting saved PR settings references require reconciliation')
        references['dk-workflows'] = previous
    return settings


def path(repository=None):
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    if not base.is_absolute():
        raise ValueError('XDG_CONFIG_HOME must be absolute')
    return base/'dk-workflows/v2'/((digest(repository.lower()) if repository else 'user') + '.json')


def effective(repository, upstream=None):
    import copy
    result = copy.deepcopy(DEFAULTS)
    sources = []
    chain = [None] + ([upstream] if upstream else []) + [repository]
    for repo in chain:
        source = path(repo)
        if not source.exists():
            continue
        settings = validate(read(source))
        for key, value in settings.items():
            if key in {'skills', 'domain_settings'}:
                result[key].update(value)
            else:
                result[key] = value
        sources.append(str(source))
    return {'settings': result, 'sources': sources}


def preflight(settings, available):
    """The agent supplies native resolved identities; no host registry inspection."""
    if not isinstance(available, list) or any(not isinstance(x, str) for x in available):
        raise ValueError('Available skill identities must be a list of strings')
    selected = set(settings['skills'].values()) | {r['skill'] for r in settings['reviewers']}
    missing = sorted(selected - set(available))
    return {'ready': not missing, 'missing_selected_skills': missing}


def migrate(reviewers=None, drafting=None):
    """Explicit plan only. v1 caches/runs are never read or copied."""
    mapping = read(Path(__file__).resolve().parents[1]/'docs/v1-skill-mapping.json')
    result, unresolved = [], []
    raw = reviewers if isinstance(reviewers, list) else (reviewers or {}).get('reviewers', [])
    for binding in raw:
        if isinstance(binding, str):
            binding = {'skill': binding}
        if (not isinstance(binding, dict) or not binding.get('skill')
                or set(binding) - {'skill','host','required'}):
            unresolved.append(binding)
            continue
        result.append({'skill': mapping.get(binding['skill'], binding['skill']), 'required': binding.get('required', True)})
    return {'mapping': mapping, 'proposed': {'reviewers': result}, 'unresolved_bindings': unresolved,
            'drafting_for_domain_owner': drafting or {},
            'domain_owners': {'rules': 'dk-workflows', 'drafting': 'dk-workflows',
                              'pr_size': 'dk-workflows', 'pr_partition': 'dk-workflows',
                              'completion': 'dk-workflows', 'ci_exceptions': 'dk-workflows',
                              'architecture': 'dk-architecture'},
            'v1_state': 'Use retained v1 runtime for active runs; never migrate review caches'}
