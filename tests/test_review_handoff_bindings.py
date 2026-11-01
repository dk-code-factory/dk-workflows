"""Compact review delivery is explicit and remains pinned through execution."""
import copy

import pytest

from shared import bindings


@pytest.mark.parametrize('provider', [{'skill': 'example:review', 'host': 'codex'},
                                     {'command': ['review-tool', '{handoff}']}])
def test_compact_opt_in_preserves_provider_identity_and_legacy_defaults(provider):
    original = copy.deepcopy(provider)
    compact = {**provider, 'handoff_contract': 'git-diff-v2'}
    assert bindings.normalize(provider) == original
    assert bindings.normalize({**provider, 'handoff_contract': 'git-diff-v1'}) == original
    assert bindings.normalize(compact) == compact
    assert bindings.key(provider) == bindings.key(compact)
    assert bindings.selection([compact]) == [compact]
    assert provider == original
    with pytest.raises(ValueError, match='unique'):
        bindings.selection([provider, compact])


@pytest.mark.parametrize('contract', [None, '', 'git-diff-v3', [], {}, True])
def test_unknown_contracts_do_not_fall_back_silently(contract):
    with pytest.raises(ValueError, match='handoff_contract'):
        bindings.normalize({'skill': 'example:review', 'handoff_contract': contract})


def test_drafting_cannot_opt_into_a_review_contract():
    with pytest.raises(ValueError, match='handoff_contract'):
        bindings.normalize({'skill': 'example:draft', 'handoff_contract': 'git-diff-v2'}, drafting=True)


def test_pinned_binding_cannot_mask_a_changed_contract():
    provider = {'command': ['review-tool', '{handoff}']}
    selected = {'binding': {**provider, 'handoff_contract': 'git-diff-v2'}, 'executor': 'command'}
    token = bindings._PINNED_BINDINGS.set({bindings.key(provider): selected})
    try:
        with pytest.raises(ValueError, match='handoff contract changed'):
            bindings.resolve(provider)
    finally:
        bindings._PINNED_BINDINGS.reset(token)
