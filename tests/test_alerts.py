"""AlertEvaluator behavior tests against a mocked DynamoDB table."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydynantic import ConditionCheckFailedError

from hodlbook.alerts import AlertEvaluator, FiredAlert
from hodlbook.prices import MockPriceProvider, PriceCache
from hodlbook.repository import Repository
from hodlbook.storage import Direction


def _cache(repo: Repository, prices: dict[str, Decimal]) -> PriceCache:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return PriceCache(repo, MockPriceProvider(prices), clock=lambda: now)


def test_above_and_below_fire_only_when_crossed(repo: Repository) -> None:
    repo.create_alert("p1", "a-btc", "bitcoin", Direction.ABOVE, Decimal("40000"))
    repo.create_alert("p1", "a-eth", "ethereum", Direction.BELOW, Decimal("2000"))

    prices = {"bitcoin": Decimal("50000"), "ethereum": Decimal("1500")}
    evaluator = AlertEvaluator(repo, _cache(repo, prices))

    fired = evaluator.evaluate(["bitcoin", "ethereum"])

    fired_ids = {f.alert.alert_id for f in fired}
    assert fired_ids == {"a-btc", "a-eth"}
    assert all(isinstance(f, FiredAlert) for f in fired)
    assert all(f.price == prices[f.alert.symbol] for f in fired)


def test_direction_correctness(repo: Repository) -> None:
    # ABOVE should NOT fire when price is below threshold; BELOW should NOT fire
    # when price is above threshold -- direction must not be inverted.
    repo.create_alert("p1", "above", "bitcoin", Direction.ABOVE, Decimal("60000"))
    repo.create_alert("p1", "below", "bitcoin", Direction.BELOW, Decimal("40000"))

    evaluator = AlertEvaluator(repo, _cache(repo, {"bitcoin": Decimal("50000")}))
    fired = evaluator.evaluate(["bitcoin"])

    assert fired == []
    assert repo.get_alert("p1", "above").triggered is False
    assert repo.get_alert("p1", "below").triggered is False


def test_non_crossing_alert_stays_armed(repo: Repository) -> None:
    repo.create_alert("p1", "a1", "bitcoin", Direction.ABOVE, Decimal("60000"))

    evaluator = AlertEvaluator(repo, _cache(repo, {"bitcoin": Decimal("50000")}))
    assert evaluator.evaluate(["bitcoin"]) == []
    assert repo.get_alert("p1", "a1").triggered is False


def test_crossing_alert_is_marked_triggered(repo: Repository) -> None:
    repo.create_alert("p1", "a1", "bitcoin", Direction.ABOVE, Decimal("40000"))

    evaluator = AlertEvaluator(repo, _cache(repo, {"bitcoin": Decimal("50000")}))
    fired = evaluator.evaluate(["bitcoin"])

    assert len(fired) == 1
    assert repo.get_alert("p1", "a1").triggered is True


def test_double_evaluate_is_idempotent(repo: Repository) -> None:
    repo.create_alert("p1", "a1", "bitcoin", Direction.ABOVE, Decimal("40000"))

    evaluator = AlertEvaluator(repo, _cache(repo, {"bitcoin": Decimal("50000")}))

    first = evaluator.evaluate(["bitcoin"])
    assert len(first) == 1

    # Same prices, second pass: the alert is already triggered -> nothing fires.
    second = evaluator.evaluate(["bitcoin"])
    assert second == []


def test_boundary_price_equals_threshold_fires(repo: Repository) -> None:
    # price == threshold must fire for both directions (>= / <=).
    repo.create_alert("p1", "above", "bitcoin", Direction.ABOVE, Decimal("50000"))
    repo.create_alert("p1", "below", "ethereum", Direction.BELOW, Decimal("3000"))

    prices = {"bitcoin": Decimal("50000"), "ethereum": Decimal("3000")}
    evaluator = AlertEvaluator(repo, _cache(repo, prices))
    fired = evaluator.evaluate(["bitcoin", "ethereum"])

    assert {f.alert.alert_id for f in fired} == {"above", "below"}


def test_lost_race_is_skipped(repo: Repository) -> None:
    # If mark_alert_triggered loses the conditional put (another evaluator fired
    # it first), the alert is skipped rather than appended to the result.
    repo.create_alert("p1", "a1", "bitcoin", Direction.ABOVE, Decimal("40000"))

    def race(_portfolio_id: str, _alert_id: str) -> None:
        raise ConditionCheckFailedError("lost race")

    repo.mark_alert_triggered = race  # type: ignore[method-assign]

    evaluator = AlertEvaluator(repo, _cache(repo, {"bitcoin": Decimal("50000")}))
    assert evaluator.evaluate(["bitcoin"]) == []


def test_only_crossing_thresholds_fire_across_symbols(repo: Repository) -> None:
    repo.create_alert("p1", "btc-hit", "bitcoin", Direction.ABOVE, Decimal("45000"))
    repo.create_alert("p1", "btc-miss", "bitcoin", Direction.ABOVE, Decimal("99000"))
    repo.create_alert("p2", "eth-hit", "ethereum", Direction.BELOW, Decimal("3500"))
    repo.create_alert("p2", "eth-miss", "ethereum", Direction.BELOW, Decimal("1000"))

    prices = {"bitcoin": Decimal("50000"), "ethereum": Decimal("3000")}
    evaluator = AlertEvaluator(repo, _cache(repo, prices))
    fired = evaluator.evaluate(["bitcoin", "ethereum"])

    assert {f.alert.alert_id for f in fired} == {"btc-hit", "eth-hit"}
