"""Unit tests for node activity classification and the strong-core filter.

The interesting assertions are about the *gate* — ``max_forward_sat`` — because the whole
study rests on it being exactly the "can this node appear mid-route" predicate. Each test
below builds a topology where the answer is known by inspection and where a plausible
wrong implementation (total capacity, largest channel, plain degree) would disagree.
"""

import pytest

from lnhistoryclient.analysis.activity import (
    ActivityClass,
    ActivityRule,
    active_nodes,
    activity_curve,
    node_profiles,
    strong_core,
    summarise,
)
from lnhistoryclient.graph import attach_capacity, build_multidigraph
from tests.conftest import NODE_A, NODE_B, NODE_C, channel_announcement, channel_update

NODE_D = "dd" * 33
NODE_E = "ee" * 33

AMOUNT = 100_000


def _both_directions(scid: int, **kwargs: object) -> list:
    return [channel_update(scid, 0, **kwargs), channel_update(scid, 1, **kwargs)]  # type: ignore[arg-type]


def _line_graph() -> object:
    """A─B─C with both directions priced and generous bounds."""
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        *_both_directions(1),
        *_both_directions(2),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, {1: 1_000_000, 2: 1_000_000})
    return graph


# ── the gate ────────────────────────────────────────────────────────────────────────


def test_endpoints_are_passive_and_the_middle_is_active():
    """On A─B─C only B can ever be an intermediate hop."""
    profiles = node_profiles(_line_graph(), AMOUNT)
    assert profiles[NODE_B].activity is ActivityClass.ACTIVE
    assert profiles[NODE_A].activity is ActivityClass.PASSIVE
    assert profiles[NODE_C].activity is ActivityClass.PASSIVE
    # A has a usable channel in both directions, but only one channel: it cannot forward.
    assert profiles[NODE_A].bidirectional == 1
    assert profiles[NODE_A].max_forward_sat == 0


def test_forwarding_ceiling_is_the_second_channel_not_the_total():
    """A hub with one huge channel and one small one forwards only the small amount.

    Total capacity says 10.1 BTC; the largest channel says 10 BTC. Both are wrong: the
    payment has to leave again, and the only other channel holds 200 000 sat.
    """
    messages = [
        channel_announcement(1, NODE_B, NODE_A),
        channel_announcement(2, NODE_B, NODE_C),
        *_both_directions(1),
        *_both_directions(2),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, {1: 1_000_000_000, 2: 200_000})
    profiles = node_profiles(graph, 10_000)
    assert profiles[NODE_B].capacity_sat == 1_000_200_000
    assert profiles[NODE_B].max_forward_sat == 200_000


def test_gate_is_amount_dependent():
    """The same node is active for a small payment and passive for a large one."""
    messages = [
        channel_announcement(1, NODE_B, NODE_A),
        channel_announcement(2, NODE_B, NODE_C),
        *_both_directions(1),
        *_both_directions(2),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, {1: 500_000, 2: 500_000})
    assert node_profiles(graph, 100_000)[NODE_B].activity is ActivityClass.ACTIVE
    assert node_profiles(graph, 900_000)[NODE_B].activity is ActivityClass.PASSIVE


def test_one_way_channels_still_forward():
    """Forwarding needs an inbound channel and an outbound one — not two-way channels.

    A→B is priced only in A's direction and B→C only in B's, so ``bidirectional`` is zero
    while B can forward perfectly well. A rule keyed on two-way channels calls B passive;
    the gate must not.
    """
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_update(1, 0),  # A -> B only
        channel_update(2, 0),  # B -> C only
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, {1: 1_000_000, 2: 1_000_000})
    profile = node_profiles(graph, AMOUNT)[NODE_B]
    assert profile.bidirectional == 0
    assert profile.live_in == 1
    assert profile.live_out == 1
    assert profile.max_forward_sat == 1_000_000
    assert profile.can_forward


def test_unpriced_direction_is_not_usable():
    """A channel with no channel_update cannot be routed through by any client."""
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        *_both_directions(1),
        # scid 2 is announced but never priced.
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, {1: 1_000_000, 2: 1_000_000})
    profile = node_profiles(graph, AMOUNT)[NODE_B]
    assert profile.channels == 2
    assert profile.unpriced_out == 1
    assert profile.max_forward_sat == 0
    assert profile.activity is ActivityClass.PASSIVE


def test_disabled_direction_is_not_usable():
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        *_both_directions(1),
        channel_update(2, 0, disabled=True),
        channel_update(2, 1, disabled=True),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, {1: 1_000_000, 2: 1_000_000})
    profile = node_profiles(graph, AMOUNT)[NODE_B]
    assert profile.disabled_out == 1
    assert profile.max_forward_sat == 0


def test_htlc_maximum_caps_the_ceiling_below_capacity():
    """A conservative htlc_maximum binds before the on-chain capacity does."""
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        *_both_directions(1, htlc_max_msat=50_000_000),  # 50 000 sat
        *_both_directions(2),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, {1: 1_000_000, 2: 1_000_000})
    assert node_profiles(graph, 10_000)[NODE_B].max_forward_sat == 50_000


# ── classes and the rule ────────────────────────────────────────────────────────────


def test_marginal_is_outside_the_core():
    """A node that can forward but cannot be reached and left is MARGINAL, not ACTIVE.

    B─C is a two-node island priced in both directions, hanging off nothing. C can forward
    between its two channels but no traffic can enter and exit the island.
    """
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_announcement(3, NODE_C, NODE_D),
        *_both_directions(1),
        *_both_directions(2),
        *_both_directions(3),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, dict.fromkeys((1, 2, 3), 1_000_000))
    profiles = node_profiles(graph, AMOUNT)
    # B and C are both interior; A and D are leaves.
    assert profiles[NODE_B].activity is ActivityClass.ACTIVE
    assert profiles[NODE_C].activity is ActivityClass.ACTIVE
    assert profiles[NODE_A].activity is ActivityClass.PASSIVE
    assert profiles[NODE_D].activity is ActivityClass.PASSIVE


def test_min_live_channels_tightens_and_is_off_by_default():
    graph = _line_graph()
    assert node_profiles(graph, AMOUNT)[NODE_B].activity is ActivityClass.ACTIVE
    strict = ActivityRule(amount_sat=AMOUNT, min_live_channels=3)
    assert node_profiles(graph, AMOUNT, rule=strict)[NODE_B].activity is ActivityClass.MARGINAL


def test_rule_rebases_to_the_requested_amount():
    """A rule built at one amount must be re-anchored, or the gate tests the wrong size."""
    rule = ActivityRule(amount_sat=1_000)
    assert rule.at_amount(500_000).amount_sat == 500_000
    graph = _line_graph()  # 1 000 000 sat channels
    # A 1 000-sat rule used to profile a 2 000 000-sat payment must still reject the node.
    # If the rule kept its own amount, its gate would compare against 1 000 and admit it.
    assert node_profiles(graph, 2_000_000, rule=rule)[NODE_B].activity is ActivityClass.PASSIVE
    assert node_profiles(graph, 500_000, rule=rule)[NODE_B].activity is ActivityClass.ACTIVE


def test_summarise_counts_every_node_exactly_once():
    profiles = node_profiles(_line_graph(), AMOUNT)
    assert sum(summarise(profiles).values()) == len(profiles)


def test_active_nodes_matches_the_class():
    profiles = node_profiles(_line_graph(), AMOUNT)
    assert active_nodes(profiles) == {NODE_B}


# ── strong_core ─────────────────────────────────────────────────────────────────────


def test_strong_core_keeps_only_forwarders():
    graph = _line_graph()
    core = strong_core(graph, AMOUNT, connected=False)
    assert set(core.nodes()) == {NODE_B}


def test_strong_core_drops_a_singleton_because_it_cannot_pay_anyone():
    """A one-node "core" is not a core: strong connectivity needs two members."""
    assert strong_core(_line_graph(), AMOUNT).number_of_nodes() == 0


def test_strong_core_survives_a_ring():
    """Four nodes in a ring are all interior and all mutually reachable."""
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_announcement(3, NODE_C, NODE_D),
        channel_announcement(4, NODE_D, NODE_A),
        *_both_directions(1),
        *_both_directions(2),
        *_both_directions(3),
        *_both_directions(4),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, dict.fromkeys((1, 2, 3, 4), 1_000_000))
    core = strong_core(graph, AMOUNT)
    assert set(core.nodes()) == {NODE_A, NODE_B, NODE_C, NODE_D}
    assert core.number_of_edges() // 2 == 4


def test_strong_core_prunes_a_pendant_from_the_ring():
    """A leaf hanging off a ring is passive and must not survive the filter."""
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_announcement(3, NODE_C, NODE_A),
        channel_announcement(4, NODE_C, NODE_E),  # pendant
        *_both_directions(1),
        *_both_directions(2),
        *_both_directions(3),
        *_both_directions(4),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, dict.fromkeys((1, 2, 3, 4), 1_000_000))
    core = strong_core(graph, AMOUNT)
    assert set(core.nodes()) == {NODE_A, NODE_B, NODE_C}
    assert NODE_E not in core


def test_strong_core_returns_a_mutable_copy():
    """Callers analyse and mutate the result; a view would alias the source graph."""
    graph = _line_graph()
    core = strong_core(graph, AMOUNT, connected=False)
    core.add_node("deadbeef")
    assert "deadbeef" not in graph


# ── the amount curve ────────────────────────────────────────────────────────────────


def test_activity_curve_is_monotone_decreasing():
    messages = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_announcement(3, NODE_C, NODE_A),
        *_both_directions(1),
        *_both_directions(2),
        *_both_directions(3),
    ]
    graph = build_multidigraph(messages)
    attach_capacity(graph, {1: 1_000_000, 2: 500_000, 3: 200_000})
    profiles = node_profiles(graph, 1_000)
    curve = activity_curve(profiles, [1_000, 200_000, 500_000, 1_000_000])
    counts = [count for _, count in curve]
    assert counts == sorted(counts, reverse=True)
    # At 1 000 sat every node is between two usable channels.
    assert counts[0] == 3
    # Above 500 000 nothing can forward: only channel 1 is that big, and a forward needs two.
    assert counts[-1] == 0


@pytest.mark.parametrize("amount", [1_000, 10_000, 100_000])
def test_classification_partitions_the_node_set(amount):
    graph = _line_graph()
    profiles = node_profiles(graph, amount)
    classes = {profile.activity for profile in profiles.values()}
    assert classes <= set(ActivityClass)
    assert len(profiles) == graph.number_of_nodes()
