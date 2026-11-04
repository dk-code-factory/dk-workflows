"""Classify personal PR ownership from immutable host-scoped account ids."""


def classify(host, author, actor, identities=()):
    def account(value):
        if (not isinstance(value, dict) or type(value.get('id')) is not int
                or value['id'] <= 0 or not isinstance(value.get('login'), str)
                or not value['login']):
            raise ValueError('PR author and authenticated account identities must be complete')
        return {'id': value['id'], 'login': value['login']}

    author, actor = account(author), account(actor)
    mine = author['id'] == actor['id'] or any(
        identity['host'].lower() == host.lower() and identity['id'] == author['id']
        for identity in identities)
    return {'host': host.lower(), 'author': author, 'actor': actor,
            'ownership': 'mine' if mine else 'others',
            'skill': 'dk-workflows:dk-pr-fix' if mine else 'dk-workflows:dk-pr-review'}
