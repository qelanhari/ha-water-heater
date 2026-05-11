"""Tests for cost.py — Tempo pricing + blended-cost math."""

import math

from cost import (  # type: ignore[import-not-found]
    CycleAccumulator,
    accumulate,
    blended_cost_eur_per_kwh,
    can_still_meet_blended_cap,
    price_per_kwh,
    rouge_hp_blended_cap,
    total_kwh,
)


class TestPricePerKwh:
    def test_bleu_hc(self):
        assert price_per_kwh("Bleu", True) == 0.1325

    def test_bleu_hp(self):
        assert price_per_kwh("Bleu", False) == 0.1612

    def test_blanc_hp(self):
        assert price_per_kwh("Blanc", False) == 0.1871

    def test_rouge_hc(self):
        assert price_per_kwh("Rouge", True) == 0.1575

    def test_rouge_hp_is_expensive(self):
        assert price_per_kwh("Rouge", False) == 0.706

    def test_unknown_color_defaults_to_bleu(self):
        assert price_per_kwh("Verte", True) == price_per_kwh("Bleu", True)


class TestBlended:
    def test_empty_cycle_is_zero(self):
        assert blended_cost_eur_per_kwh(CycleAccumulator()) == 0.0

    def test_pure_solar_is_free(self):
        c = CycleAccumulator(kwh_solar=2.0, cost_eur=0.0)
        assert blended_cost_eur_per_kwh(c) == 0.0
        assert total_kwh(c) == 2.0

    def test_pure_hc_at_bleu(self):
        c = accumulate(CycleAccumulator(), 2.0, solar_share=0.0, color="Bleu", is_hc=True)
        # 2 kWh × 0.1325 = 0.265 €; blended = 0.1325
        assert math.isclose(c.cost_eur, 0.265, abs_tol=1e-6)
        assert math.isclose(blended_cost_eur_per_kwh(c), 0.1325, abs_tol=1e-6)

    def test_mixed_solar_and_hc(self):
        c = CycleAccumulator()
        c = accumulate(c, 1.0, solar_share=0.5, color="Bleu", is_hc=True)
        # 0.5 solar + 0.5 HC at 0.1325 = 0.0663 €
        # total kWh = 1, blended = 0.0663
        assert math.isclose(c.kwh_solar, 0.5)
        assert math.isclose(c.kwh_hc, 0.5)
        assert math.isclose(blended_cost_eur_per_kwh(c), 0.5 * 0.1325, abs_tol=1e-6)


class TestRougeHpCap:
    def test_default_cap_is_75pct_of_rouge_hc(self):
        cap = rouge_hp_blended_cap(0.1575, 0.25)
        assert math.isclose(cap, 0.118125, abs_tol=1e-6)


class TestCanStillMeetCap:
    def test_pure_solar_topped_with_tiny_hp_is_ok(self):
        # Cycle so far: 5 kWh free solar. Adding 0.3 kWh HP at 0.706 = 0.212 €.
        # Total: 5.3 kWh, 0.212 €, blended = 0.0400 €/kWh — well under 0.118.
        c = CycleAccumulator(kwh_solar=5.0, cost_eur=0.0)
        assert can_still_meet_blended_cap(c, 0.3, 0.706, 0.118125) is True

    def test_too_much_hp_topping_breaks_cap(self):
        # 5 kWh solar + 1.5 kWh HP at 0.706 = 1.059 €. blended = 0.163 €/kWh > 0.118.
        c = CycleAccumulator(kwh_solar=5.0, cost_eur=0.0)
        assert can_still_meet_blended_cap(c, 1.5, 0.706, 0.118125) is False

    def test_no_solar_buffer_no_hp_allowed(self):
        # Empty cycle. Any HP at 0.706 immediately violates the cap.
        c = CycleAccumulator()
        assert can_still_meet_blended_cap(c, 0.1, 0.706, 0.118125) is False

    def test_zero_extra_just_checks_current(self):
        # Current cycle blended is 0 (empty), 0 ≤ cap → OK.
        c = CycleAccumulator()
        assert can_still_meet_blended_cap(c, 0.0, 0.706, 0.118125) is True
