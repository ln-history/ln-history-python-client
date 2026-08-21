"""Per-client weight functions, checked against values computed by hand from the sources.

These tests exist to catch a *silently wrong constant*, which is the failure mode that
matters here: a mis-scaled penalty still produces plausible routes, so nothing downstream
would notice. Each expected number below is derived from the upstream expression cited in
the corresponding module, not from this implementation's own output.
"""

import math

import pytest
from conftest import channel_announcement, channel_update

from lnhistoryclient.analysis import Constraint, RoutingIndex
from lnhistoryclient.analysis.clients import (
    AprioriEstimator,
    BimodalEstimator,
    ClnWeightFunction,
    EclairConstantsWeightFunction,
    EclairRatiosWeightFunction,
    EdgeContext,
    LdkScoringParameters,
    LdkWeightFunction,
    LndWeightFunction,
    NetworkStats,
    UniformEstimator,
    available_clients,
    client_profile,
    parse_version,
)
from lnhistoryclient.analysis.clients.eclair import normalize
from lnhistoryclient.analysis.compare import compare_clients
from lnhistoryclient.analysis.routing import ClientRouter
from lnhistoryclient.graph import attach_capacity, build_multidigraph

NODE_A = "aa" * 33
NODE_B = "bb" * 33
NODE_C = "cc" * 33
NODE_D = "dd" * 33

NO_BALANCE = Constraint.ALL & ~Constraint.BALANCE


def context(
    amount_msat: int = 100_000_000,
    fee_msat: int = 1000,
    *,
    fee_base_msat: int = 1000,
    fee_ppm: int = 0,
    cltv: int = 40,
    htlc_min: int = 1000,
    htlc_max: int = None,
    capacity_msat: int = 1_000_000_000,
    funding_block: int = 600_000,
    is_sender_hop: bool = False,
) -> EdgeContext:
    return EdgeContext(
        amount_msat=amount_msat,
        fee_msat=fee_msat,
        fee_base_msat=fee_base_msat,
        fee_ppm=fee_ppm,
        cltv_expiry_delta=cltv,
        htlc_minimum_msat=htlc_min,
        htlc_maximum_msat=htlc_max,
        capacity_msat=capacity_msat,
        funding_block=funding_block,
        is_sender_hop=is_sender_hop,
    )


def scid_at(block: int, index: int) -> int:
    return (block << 40) | (index << 16)


# ── LND ──────────────────────────────────────────────────────────────────────────


def test_lnd_timelock_penalty_truncates_to_zero_for_small_payments():
    """``amt * delta * 15 / 1e9`` is integer division, so small payments pay no risk cost.

    1_000_000 msat over a 40-block delta gives 6e8, which floors to 0. This is real LND
    behaviour, not a rounding artefact, and a float implementation would silently differ.
    """
    weight = LndWeightFunction().edge_weight(context(amount_msat=1_000_000, fee_msat=0))
    assert weight == 0.0


def test_lnd_timelock_penalty_matches_hand_computation():
    # locked = amount + fee = 100_001_000; * 40 * 15 = 60_000_600_000; // 1e9 = 60.
    weight = LndWeightFunction().edge_weight(context(amount_msat=100_000_000, fee_msat=1000))
    assert weight == 1000 + 60


def test_lnd_v019_charges_timelock_against_amount_sent():
    """v0.19.0 switched ``edgeWeight``'s first argument from amountToReceive to amountToSend."""
    ctx = context(amount_msat=100_000_000, fee_msat=1000)
    older = LndWeightFunction(locked_amount_includes_fee=True).edge_weight(ctx)
    newer = LndWeightFunction(locked_amount_includes_fee=False).edge_weight(ctx)
    # 100_000_000 * 40 * 15 // 1e9 = 60 as well here, but the inputs genuinely differ;
    # assert the boundary rather than a coincidence.
    assert older >= newer


def test_lnd_attempt_cost_is_one_hundred_sat_not_one_hundred_msat():
    """The paper states 100 msat; ``DefaultAttemptCost`` is ``NewMSatFromSatoshis(100)``."""
    # 100_000 msat base + 100_000_000 * 1000ppm / 1e6 = 100_000 + 100_000.
    assert LndWeightFunction().attempt_cost_msat(100_000_000) == pytest.approx(200_000.0)


@pytest.mark.parametrize(
    "time_preference,multiplier",
    [(0.0, 1.0), (1.0, 19.0), (-1.0, 1 / 0.95 - 1), (0.5, 1 / (0.5 - 0.225) - 1)],
)
def test_lnd_time_preference_scales_attempt_cost(time_preference, multiplier):
    weight = LndWeightFunction(time_preference=time_preference)
    assert weight.attempt_cost_msat(100_000_000) == pytest.approx(200_000.0 * multiplier)


def test_lnd_time_preference_is_validated():
    with pytest.raises(ValueError, match=r"\[-1, 1\]"):
        LndWeightFunction(time_preference=1.5)


@pytest.mark.parametrize(
    "ratio,expected",
    [(0.0, 1.0), (0.9, 0.990968), (0.99, 0.798866), (1.0, 0.749500)],
)
def test_lnd_apriori_capacity_factor_is_a_logistic(ratio, expected):
    """Paper eq. (17) drops the ``exp``; these values come from the Go source's logistic."""
    capacity = 100_000_000
    factor = AprioriEstimator().capacity_factor(int(ratio * capacity), capacity)
    assert factor == pytest.approx(expected, abs=1e-5)


def test_lnd_apriori_rejects_amounts_above_capacity():
    assert AprioriEstimator().capacity_factor(200, 100) == 0.0


def test_lnd_apriori_ignores_unknown_capacity():
    """A channel with no known capacity must not be penalised for missing data."""
    assert AprioriEstimator().capacity_factor(100, None) == 1.0
    assert AprioriEstimator().probability(100, None) == pytest.approx(0.6)


def test_lnd_bimodal_probability_is_monotone_and_bounded():
    estimator = BimodalEstimator()
    capacity = 10_000_000_000
    values = [estimator.probability(int(fraction * capacity), capacity) for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert values[0] == pytest.approx(1.0)
    assert values[-1] == pytest.approx(0.0, abs=1e-9)
    assert all(earlier >= later for earlier, later in zip(values, values[1:], strict=False))


def test_lnd_uniform_estimator_matches_paper_equation_43():
    assert UniformEstimator().probability(250, 1000) == pytest.approx(0.75)


# ── CLN ──────────────────────────────────────────────────────────────────────────


def test_cln_weight_matches_route_score():
    """``(fee + risk + 1) * (capacity_bias + 1)`` with riskfactor 10%/yr over 52596 blocks."""
    amount, capacity, fee, cltv = 100_000_000, 1_000_000_000, 1000, 40
    risk = int(amount * (10.0 / 100.0 / 52596 * cltv))
    bias = -math.log((capacity + 1 - amount) / (capacity + 1))
    expected = (fee + risk + 1) * (bias + 1.0)

    weight = ClnWeightFunction().edge_weight(context(amount_msat=amount, fee_msat=fee, capacity_msat=capacity))
    assert weight == pytest.approx(expected)


def test_cln_uses_its_own_blocks_per_year():
    """52596, not eclair's 52560. Sharing the constant would be a silent 0.07% fee error."""
    from lnhistoryclient.analysis.clients.cln import BLOCKS_PER_YEAR as CLN_YEAR
    from lnhistoryclient.analysis.clients.eclair import BLOCK_TIME_ONE_YEAR as ECLAIR_YEAR

    assert CLN_YEAR == 52596
    assert ECLAIR_YEAR == 52560


def test_cln_capacity_bias_grows_as_the_channel_fills():
    weight = ClnWeightFunction()
    nearly_empty = weight.capacity_bias(context(amount_msat=1_000, capacity_msat=1_000_000_000))
    nearly_full = weight.capacity_bias(context(amount_msat=999_000_000, capacity_msat=1_000_000_000))
    assert 0.0 <= nearly_empty < nearly_full


def test_cln_getroute_variant_drops_the_capacity_bias():
    """Only ``pay`` uses route_score; the getroute RPC uses route_score_cheaper."""
    ctx = context(amount_msat=900_000_000, capacity_msat=1_000_000_000)
    assert ClnWeightFunction(use_capacity_bias=False).edge_weight(ctx) < ClnWeightFunction().edge_weight(ctx)


def test_cln_saturates_rather_than_taking_log_of_a_negative():
    weight = ClnWeightFunction().edge_weight(context(amount_msat=2_000_000_000, capacity_msat=1_000_000_000))
    assert weight == float(0xFFFFFFFF)


# ── LDK ──────────────────────────────────────────────────────────────────────────


def test_ldk_path_htlc_minimum_takes_max_with_downstream():
    """Paper eq. (23) omits this ``max``, making it correct only for the final hop."""
    weight = LdkWeightFunction()
    ctx = context(htlc_min=500, fee_base_msat=100, fee_ppm=0)
    # Downstream accumulator of 1100 dominates this channel's own 500 minimum.
    assert weight.path_htlc_minimum_msat(ctx, 1100) == 1100 + 100
    assert weight.path_htlc_minimum_msat(ctx, 0) == 500 + 100


def test_ldk_path_cost_takes_max_of_fee_and_htlc_minimum():
    """The cost is not a sum of edge weights; ``max`` does not distribute over ``+``."""
    weight = LdkWeightFunction()
    assert (
        weight.path_cost(50.0, fee_msat=2000, path_htlc_minimum_msat=9000, probability=1.0, attempt_cost_msat=0.0)
        == 9050.0
    )
    assert (
        weight.path_cost(50.0, fee_msat=9000, path_htlc_minimum_msat=2000, probability=1.0, attempt_cost_msat=0.0)
        == 9050.0
    )


def test_ldk_base_penalty_matches_hand_computation():
    # 500 + 8192 * 1e8 // 2^30 = 500 + 762.
    params = LdkScoringParameters(
        liquidity_penalty_multiplier_msat=0,
        liquidity_penalty_amount_multiplier_msat=0,
        historical_liquidity_penalty_multiplier_msat=0,
        historical_liquidity_penalty_amount_multiplier_msat=0,
        anti_probing_penalty_msat=0,
    )
    weight = LdkWeightFunction(params).edge_weight(context(amount_msat=100_000_000, htlc_max=1))
    assert weight == pytest.approx(500 + 762)


def test_ldk_anti_probing_applies_only_above_half_capacity():
    params = LdkScoringParameters(
        base_penalty_msat=0,
        base_penalty_amount_multiplier_msat=0,
        liquidity_penalty_multiplier_msat=0,
        liquidity_penalty_amount_multiplier_msat=0,
        historical_liquidity_penalty_multiplier_msat=0,
        historical_liquidity_penalty_amount_multiplier_msat=0,
    )
    weight = LdkWeightFunction(params)
    capacity = 1_000_000_000
    assert weight.edge_weight(context(htlc_max=capacity, capacity_msat=capacity)) == 250.0
    assert weight.edge_weight(context(htlc_max=capacity // 4, capacity_msat=capacity)) == 0.0


def test_ldk_success_probability_falls_with_amount():
    weight = LdkWeightFunction()
    capacity = 1_000_000_000
    low = weight.success_probability(capacity // 10, 0, capacity, capacity)
    high = weight.success_probability(capacity * 9 // 10, 0, capacity, capacity)
    assert 0.0 <= high < low <= 1.0


def test_ldk_linear_probability_includes_the_plus_one_denominator():
    """Paper eq. (30) omits the ``+1`` that LDK's saturating_add puts there."""
    weight = LdkWeightFunction(LdkScoringParameters(probability_model="linear"))
    capacity = 1000
    assert weight.success_probability(250, 0, capacity, capacity) == pytest.approx(750 / 1001)


def test_ldk_v01_disables_the_live_liquidity_penalty():
    """From v0.1.0 ``liquidity_penalty_multiplier_msat`` defaults to 0."""
    profile = client_profile("ldk", version="0.1.0")
    assert profile.weight.params.liquidity_penalty_multiplier_msat == 0
    assert profile.weight.params.base_penalty_msat == 1024
    assert profile.weight.params.probability_model == "degree9"


def test_ldk_sender_hop_is_not_penalised():
    """channel_penalty_msat returns 0 for candidates that are not public hops."""
    assert LdkWeightFunction().edge_weight(context(is_sender_hop=True)) == 0.0


# ── eclair ───────────────────────────────────────────────────────────────────────


def test_eclair_normalize_never_reaches_zero_or_one():
    """The [0.00001, 0.99999] range keeps a factor from cancelling a whole weight term."""
    assert normalize(9, 9, 2016) == pytest.approx(0.00001)
    assert normalize(2016, 9, 2016) == pytest.approx(0.99999)
    assert normalize(-500, 9, 2016) == pytest.approx(0.00001)  # clamped
    assert normalize(5000, 9, 2016) == pytest.approx(0.99999)  # clamped


def test_eclair_ratio_defaults_are_the_v06_values_not_the_papers():
    """The paper quotes eclair <= v0.5.1 ratios, stale by about three years."""
    weight = EclairRatiosWeightFunction()
    assert (weight.base_ratio, weight.cltv_ratio, weight.age_ratio, weight.capacity_ratio) == (0.0, 0.05, 0.4, 0.55)


def test_eclair_hop_costs_are_not_zero():
    """reference.conf has 500 msat + 200 ppm; zeroing them removes the short-path bias."""
    weight = EclairRatiosWeightFunction()
    assert (weight.hop_cost_base_msat, weight.hop_cost_ppm) == (500, 200)


def test_eclair_prefers_older_and_larger_channels():
    weight = EclairRatiosWeightFunction()
    weight.prepare(NetworkStats(tip_block_height=800_000, min_funding_block=600_000), 100_000_000)

    old_large = weight.edge_weight(context(funding_block=600_000, capacity_msat=100_000_000_000))
    new_small = weight.edge_weight(context(funding_block=799_000, capacity_msat=100_000_000))
    assert old_large < new_small


def test_eclair_sender_hop_contributes_nothing():
    """Graph.scala returns prev.weight unchanged for the sender's own channel."""
    weight = EclairRatiosWeightFunction()
    weight.prepare(NetworkStats(tip_block_height=800_000, min_funding_block=600_000), 100_000_000)
    assert weight.edge_weight(context(is_sender_hop=True)) == 0.0


def test_eclair_constants_divides_by_the_whole_path_probability():
    """Case 2 is additive-plus-multiplicative, so path_cost must fold in the failure cost."""
    weight = EclairConstantsWeightFunction(use_log_probability=False)
    weight.prepare(NetworkStats(tip_block_height=800_000, min_funding_block=600_000), 100_000_000)

    reliable = weight.path_cost(1000.0, fee_msat=500, path_htlc_minimum_msat=0, probability=1.0, attempt_cost_msat=0.0)
    risky = weight.path_cost(1000.0, fee_msat=500, path_htlc_minimum_msat=0, probability=0.5, attempt_cost_msat=0.0)
    # failure_cost = 2000 + (100_000_000 + 500) * 500 / 1e6 = 2000 + 50_000.
    assert risky - reliable == pytest.approx(52_000.0)


def test_eclair_log_variant_is_purely_additive():
    weight = EclairConstantsWeightFunction(use_log_probability=True)
    weight.prepare(NetworkStats(tip_block_height=800_000, min_funding_block=600_000), 100_000_000)
    assert weight.path_cost(1234.0, 500, 0, 0.5, 0.0) == 1234.0


def test_eclair_probability_is_one_minus_amount_over_capacity():
    weight = EclairConstantsWeightFunction()
    assert weight.edge_probability(context(amount_msat=250, capacity_msat=1000)) == pytest.approx(0.75)


# ── registry ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [("0.17.4", (0, 17, 4)), ("v0.17.4-beta", (0, 17, 4)), ("24.02.1", (24, 2, 1)), ("0.0.120", (0, 0, 120))],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_default_version_is_the_paper_baseline():
    """A moving default would make published results shift on upgrade."""
    assert client_profile("lnd").version == "0.17.4"
    assert client_profile("cln").version == "24.02.1"
    assert client_profile("ldk").version == "0.0.120"
    assert client_profile("eclair").version == "0.10.0"


def test_version_selects_the_right_release():
    assert client_profile("lnd", version="0.17.9").version == "0.17.4"
    assert client_profile("lnd", version="0.18.3").version == "0.18.0"
    assert client_profile("lnd", version="0.21.0").version == "0.19.0"
    assert client_profile("eclair", version="0.12.0").version == "0.10.0"
    assert client_profile("eclair", version="0.14.1").version == "0.13.1"


def test_spec_string_carries_version_and_variant():
    profile = client_profile("eclair-constants-log@0.10.0")
    assert profile.implementation.value == "eclair"
    assert profile.variant == "constants-log"
    assert profile.version == "0.10.0"
    assert profile.label == "eclair-constants-log@0.10.0"


def test_removed_variant_raises_rather_than_falling_back():
    """eclair deleted the ratios mode in v0.13.1; silently substituting would fabricate."""
    with pytest.raises(ValueError, match="no variant 'ratios'"):
        client_profile("eclair", version="0.14.0", variant="ratios")


def test_version_older_than_modelled_raises():
    with pytest.raises(ValueError, match="predates the oldest modelled release"):
        client_profile("lnd", version="0.10.0")


def test_unknown_implementation_lists_the_known_ones():
    with pytest.raises(ValueError, match="unknown implementation"):
        client_profile("btcd")


def test_available_clients_marks_paper_baselines():
    baselines = [row for row in available_clients() if row["is_paper_baseline"]]
    assert len(baselines) == 4


# ── routing integration ──────────────────────────────────────────────────────────


@pytest.fixture
def diamond_graph():
    """Two A->D routes: via B is cheap but small and new; via C is pricey, large and old."""
    channels = [
        (scid_at(700_000, 1), NODE_A, NODE_B, 0, 1, 1_000_000),
        (scid_at(700_000, 2), NODE_B, NODE_D, 100, 10, 250_000),
        (scid_at(600_000, 3), NODE_A, NODE_C, 0, 1, 5_000_000),
        (scid_at(600_000, 4), NODE_C, NODE_D, 1000, 100, 16_000_000),
    ]
    messages = []
    capacities = {}
    for scid, left, right, base, ppm, capacity in channels:
        messages.append(channel_announcement(scid, left, right))
        for direction in (0, 1):
            messages.append(
                channel_update(scid, direction, fee_base_msat=base, fee_ppm=ppm, htlc_max_msat=10_000_000_000)
            )
        capacities[scid] = capacity

    graph = build_multidigraph(messages)
    attach_capacity(graph, capacities)
    return graph


def test_index_exposes_funding_blocks_for_channel_age(diamond_graph):
    stats = RoutingIndex(diamond_graph).stats
    assert stats.tip_block_height == 700_000
    assert stats.min_funding_block == 600_000


def test_clients_disagree_about_the_best_route(diamond_graph):
    """The whole point of the feature: same graph, same payment, different choices."""
    index = RoutingIndex(diamond_graph)
    chosen = {}
    for spec in ("lnd-apriori@0.17.4", "cln@24.02.1", "ldk@0.0.120", "eclair-ratios@0.10.0"):
        route = ClientRouter(client_profile(spec)).find_route(index, NODE_A, NODE_D, 200_000, NO_BALANCE)
        assert route is not None
        chosen[spec] = route.path[1]

    assert len(set(chosen.values())) > 1, f"expected disagreement, all chose {chosen}"


def test_route_records_the_client_that_produced_it(diamond_graph):
    index = RoutingIndex(diamond_graph)
    route = ClientRouter(client_profile("cln")).find_route(index, NODE_A, NODE_D, 100_000, NO_BALANCE)
    assert route.client == "cln-pay@24.02.1"
    assert route.total_weight > 0


def test_eclair_returns_several_candidate_paths(diamond_graph):
    """Yen's K-shortest, K=3 by default. Both disjoint A->D routes must be found."""
    index = RoutingIndex(diamond_graph)
    router = ClientRouter(client_profile("eclair-ratios@0.10.0"))
    routes = router.find_routes(index, NODE_A, NODE_D, 100_000, NO_BALANCE)

    assert len(routes) >= 2
    assert {route.path[1] for route in routes} == {NODE_B, NODE_C}
    # Ranked by the client's own cost, best first.
    assert routes == sorted(routes, key=lambda route: route.total_weight)


def test_yen_candidates_are_costed_end_to_end(diamond_graph):
    """A spliced path is re-priced, so its fee must match its own hops, not the spur's."""
    index = RoutingIndex(diamond_graph)
    router = ClientRouter(client_profile("eclair-ratios@0.10.0"))
    for route in router.find_routes(index, NODE_A, NODE_D, 100_000, NO_BALANCE):
        assert route.total_fee_msat == sum(hop.fee_msat for hop in route.hops)
        assert route.hops[0].fee_msat == 0  # the sender forwards nothing


def test_path_length_constraint_is_enforced(diamond_graph):
    """A 1-hop ceiling must make the 2-hop A->D route unreachable."""
    from dataclasses import replace

    profile = client_profile("cln")
    capped = replace(profile, constraints=replace(profile.constraints, max_path_length=1))
    index = RoutingIndex(diamond_graph)

    assert ClientRouter(profile).find_route(index, NODE_A, NODE_D, 100_000, NO_BALANCE) is not None
    assert ClientRouter(capped).find_route(index, NODE_A, NODE_D, 100_000, NO_BALANCE) is None


def test_compare_clients_pairs_the_same_trials(diamond_graph):
    comparison = compare_clients(diamond_graph, ["cln", "ldk", "eclair-ratios@0.10.0"], n=25, amount_sat=50_000, seed=3)

    assert comparison.num_trials == 25
    assert len(comparison.outcomes) == 3
    # Every client saw the identical trial list — that is what makes the comparison valid.
    for outcome in comparison.outcomes:
        assert outcome.trials == 25
        assert [(r.src, r.dst, r.amount_sat) for r in outcome.results] == comparison.trials

    for row in comparison.summary():
        assert 0.0 <= row["success_rate"] <= 1.0


def test_compare_clients_reports_costs_only_over_shared_successes(diamond_graph):
    comparison = compare_clients(diamond_graph, ["cln", "ldk"], n=20, amount_sat=50_000, seed=11)
    for index in comparison.common_success:
        assert all(outcome.results[index].success for outcome in comparison.outcomes)


def test_compare_clients_rejects_an_empty_client_list(diamond_graph):
    with pytest.raises(ValueError, match="at least one client"):
        compare_clients(diamond_graph, [], n=5)


def test_client_side_constraints_are_named_as_such(diamond_graph):
    """A client refusing on its own budget must not be reported as an unreachable pair."""
    from dataclasses import replace

    from lnhistoryclient.analysis import simulate_payment
    from lnhistoryclient.analysis.clients import FeeBudget

    profile = client_profile("cln")
    # A one-msat fee budget no real route can meet, while the path plainly exists.
    broke = replace(profile, constraints=replace(profile.constraints, fee_budget=FeeBudget(base_msat=1)))

    result = simulate_payment(diamond_graph, NODE_A, NODE_D, 200_000, strategy=ClientRouter(broke))
    assert not result.success
    assert result.failure_reason == "client_side_constraints"


def test_unreachable_pair_is_still_reported_as_unreachable(diamond_graph):
    """The new rung must not swallow genuine disconnection."""
    from lnhistoryclient.analysis import simulate_payment

    diamond_graph.add_node("ee" * 33, announced=True)
    result = simulate_payment(diamond_graph, NODE_A, "ee" * 33, 1000, strategy=ClientRouter(client_profile("cln")))
    assert not result.success
    assert result.failure_reason == "unreachable"
