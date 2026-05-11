"""Shared fixtures for dhwp_smart tests."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "custom_components" / "dhwp_smart"),
)

from cost import CycleAccumulator  # noqa: E402
from decision import Inputs, Thresholds  # noqa: E402


@pytest.fixture
def t0() -> datetime:
    """Reference time: Tuesday 2026-06-16 14:00 UTC (HP window, summer)."""
    return datetime(2026, 6, 16, 14, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def thr() -> Thresholds:
    return Thresholds()


@pytest.fixture
def base_inputs(t0: datetime) -> Inputs:
    """Mid-afternoon summer day: warm-ish tank, neutral grid, no Tempo Rouge."""
    return Inputs(
        now=t0,
        mode="auto",
        tank_top_c=52.0,
        tank_middle_c=50.0,
        garage_c=18.0,
        outdoor_c=25.0,
        grid_smooth_w=0.0,
        pv_power_w=2000.0,
        heater_power_w=0.0,
        tempo_color="Bleu",
        tempo_next_color="Bleu",
        is_hc=False,
        forecast_today_kwh=15.0,           # ample sun
        forecast_tomorrow_kwh=15.0,
        energy_needed_kwh=1.5,             # small top-up
        cycle=CycleAccumulator(),
        signal_on_at=None,
        signal_currently_on=False,
    )
