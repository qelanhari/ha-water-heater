"""Tempo pricing and cycle blended-cost math.

Pure functions only — no `homeassistant` imports.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Tempo prices (€/kWh) as configured by the user. These match HA helpers
# `input_number.tempo_prix_<color>_<window>` and are also kept here so the
# tests can run standalone.
TEMPO_PRICE: dict[tuple[str, str], float] = {
    ("Bleu", "HC"): 0.1325,  ("Bleu", "HP"): 0.1612,
    ("Blanc", "HC"): 0.1499, ("Blanc", "HP"): 0.1871,
    ("Rouge", "HC"): 0.1575, ("Rouge", "HP"): 0.7060,
}

TEMPO_COLORS: tuple[str, ...] = ("Bleu", "Blanc", "Rouge")


def price_per_kwh(color: str, is_hc: bool) -> float:
    """€/kWh for the given Tempo color and HC/HP window.

    Unknown colors default to Bleu (cheapest) so a missing sensor doesn't
    cause us to over-cost a cycle.
    """
    if color not in TEMPO_COLORS:
        color = "Bleu"
    window = "HC" if is_hc else "HP"
    return TEMPO_PRICE[(color, window)]


@dataclass(frozen=True)
class CycleAccumulator:
    """Running tally of a single heating cycle.

    A cycle starts when the contactor turns on, ends when it turns off (or
    the heat-pump stops drawing power for 5 min — same threshold as the
    'fin de cycle' safety automation we keep alongside).
    """

    kwh_solar: float = 0.0  # free
    kwh_hc: float = 0.0
    kwh_hp: float = 0.0
    cost_eur: float = 0.0


def total_kwh(c: CycleAccumulator) -> float:
    return c.kwh_solar + c.kwh_hc + c.kwh_hp


def blended_cost_eur_per_kwh(c: CycleAccumulator) -> float:
    """Cost per kWh of energy actually stored — the user's optimisation target.

    Returns 0.0 for an empty cycle (no division by zero, no NaN).
    """
    t = total_kwh(c)
    return c.cost_eur / t if t > 0 else 0.0


def accumulate(
    c: CycleAccumulator,
    delta_kwh: float,
    solar_share: float,
    color: str,
    is_hc: bool,
) -> CycleAccumulator:
    """Add `delta_kwh` of consumption split by `solar_share` (0..1).

    The non-solar portion is priced at the current Tempo (color, HC/HP).
    """
    if delta_kwh <= 0:
        return c
    solar_share = max(0.0, min(1.0, solar_share))
    s = delta_kwh * solar_share
    g = delta_kwh - s
    price = price_per_kwh(color, is_hc)
    if is_hc:
        return replace(
            c,
            kwh_solar=c.kwh_solar + s,
            kwh_hc=c.kwh_hc + g,
            cost_eur=c.cost_eur + g * price,
        )
    return replace(
        c,
        kwh_solar=c.kwh_solar + s,
        kwh_hp=c.kwh_hp + g,
        cost_eur=c.cost_eur + g * price,
    )


def can_still_meet_blended_cap(
    c: CycleAccumulator,
    extra_kwh: float,
    extra_eur_per_kwh: float,
    cap_eur_per_kwh: float,
) -> bool:
    """If we added `extra_kwh` at `extra_eur_per_kwh`, would blended cost stay below cap?

    Used by the Rouge-HP guard: before letting solar mode authorise heating
    on Rouge HP, project the next 15 min at full Tempo Rouge HP price and
    verify the *worst case* still respects the cap.
    """
    if extra_kwh <= 0:
        return blended_cost_eur_per_kwh(c) <= cap_eur_per_kwh
    new_total = total_kwh(c) + extra_kwh
    new_cost = c.cost_eur + extra_kwh * extra_eur_per_kwh
    if new_total <= 0:
        return True
    return (new_cost / new_total) <= cap_eur_per_kwh


def rouge_hp_blended_cap(rouge_hc_price: float, discount_pct: float) -> float:
    """The blended cap for the Rouge-HP solar-pulling rule.

    Default `discount_pct = 0.25` (25% cheaper than Rouge HC):
        cap = 0.75 * 0.1575 = 0.118125 €/kWh
    """
    return (1.0 - discount_pct) * rouge_hc_price
