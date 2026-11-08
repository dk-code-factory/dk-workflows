"""Offline agent responses establish protocol behavior, not native adherence."""
def report(contract, evidence='retained:primary-result', unfinished=()):
    return {'complete': not unfinished, 'checklist': [
        {'id': item['id'], 'status': 'unfinished', 'reason': 'Still needs assigned work.'}
        if item['id'] in unfinished else
        {'id': item['id'], 'status': 'completed', 'evidence': [evidence]}
        for item in contract['items']]}


def accept(tasks, result):
    assert result['action'] == 'recheck'
    return tasks.finish(result['key'], result['claim'], b'Checked every item against retained evidence.',
                        report(result['checklist'], result['result']), round_id=result['round'])


def finish(tasks, *args, **kwargs):
    result = tasks.finish(*args, **kwargs)
    return accept(tasks, result) if result.get('action') == 'recheck' else result
