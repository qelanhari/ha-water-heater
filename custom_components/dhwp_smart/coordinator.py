"""DataUpdateCoordinator for Smart DHWP — the integration's brain."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_FORECAST_TODAY_ENTITY,
    CONF_FORECAST_TOMORROW_ENTITY,
    CONF_GARAGE_TEMP_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HEATER_BOOSTER_POWER_ENTITY,
    CONF_HEATER_ENERGY_METER_ENTITY,
    CONF_HEATER_POWER_ENTITY,
    CONF_MIN_DWELL_SECONDS,
    CONF_MORNING_DEADLINE_HOUR,
    CONF_MORNING_DEADLINE_MINUTE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_ROUGE_HP_BLENDED_CAP,
    CONF_SIGNAL_SWITCH_ENTITY,
    CONF_SOLAR_SMOOTH_ALPHA,
    CONF_SURPLUS_SAFETY_MARGIN_W,
    CONF_TANK_CAPACITY_L,
    CONF_TANK_MIDDLE_TEMP_ENTITY,
    CONF_TANK_MORNING_FLOOR_C,
    CONF_TANK_TARGET_TOP_C,
    CONF_TANK_TOP_TEMP_ENTITY,
    CONF_TEMPO_COLOR_ENTITY,
    CONF_TEMPO_IS_HC_ENTITY,
    CONF_TEMPO_NEXT_COLOR_ENTITY,
    DEFAULT_MIN_DWELL_SECONDS,
    DEFAULT_MORNING_DEADLINE_HOUR,
    DEFAULT_MORNING_DEADLINE_MINUTE,
    DEFAULT_ROUGE_HP_BLENDED_CAP,
    DEFAULT_SOLAR_SMOOTH_ALPHA,
    DEFAULT_SURPLUS_SAFETY_MARGIN_W,
    DEFAULT_TANK_CAPACITY_L,
    DEFAULT_TANK_MORNING_FLOOR_C,
    DEFAULT_TANK_TARGET_TOP_C,
    DOMAIN,
    MANUAL_MODES,
    MODE_AUTO,
    UPDATE_INTERVAL_SECONDS,
)
from .cost import CycleAccumulator, accumulate, blended_cost_eur_per_kwh
from .decision import Decision, Inputs, Thresholds, decide
from .pattern import PatternState, expected_kwh_for_day, update_pattern
from .thermo import energy_budget_until_morning_kwh

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


def _storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"


class SmartDhwpCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.config_entry = entry
        self._store: Store = Store(hass, STORAGE_VERSION, _storage_key(entry.entry_id))
        self._unsub: list[Any] = []

        # State (persisted)
        self._mode: str = MODE_AUTO
        self._pattern: PatternState = PatternState()
        self._cycle: CycleAccumulator = CycleAccumulator()
        self._cycle_started_at: datetime | None = None
        self._last_cycle_summary: dict[str, Any] | None = None
        self._last_energy_meter_wh: float | None = None
        self._grid_smooth_w: float | None = None
        self._last_switch_change_at: datetime | None = None
        self._last_known_switch_state: bool = False
        self._today_kwh: float = 0.0
        self._today_date: date | None = None
        self._daily_outdoor_sum: float = 0.0
        self._daily_outdoor_count: int = 0
        self._force_skim: bool = False     # one-shot "force heat now" button

        # Last computed decision
        self._last_decision: Decision = Decision(
            heater_on=False, reason="initial", action="wait"
        )

    # ------------------------------------------------------------------
    # Public properties for entity classes
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def last_decision(self) -> Decision:
        return self._last_decision

    @property
    def cycle(self) -> CycleAccumulator:
        return self._cycle

    @property
    def pattern(self) -> PatternState:
        return self._pattern

    @property
    def thresholds(self) -> Thresholds:
        return Thresholds(
            hard_floor_c=float(self._cfg(CONF_TANK_MORNING_FLOOR_C, DEFAULT_TANK_MORNING_FLOOR_C)),
            target_top_c=float(self._cfg(CONF_TANK_TARGET_TOP_C, DEFAULT_TANK_TARGET_TOP_C)),
            morning_window_start=time(4, 0),
            morning_window_end=time(
                int(self._cfg(CONF_MORNING_DEADLINE_HOUR, DEFAULT_MORNING_DEADLINE_HOUR)),
                int(self._cfg(CONF_MORNING_DEADLINE_MINUTE, DEFAULT_MORNING_DEADLINE_MINUTE)),
            ),
            surplus_safety_margin_w=float(
                self._cfg(CONF_SURPLUS_SAFETY_MARGIN_W, DEFAULT_SURPLUS_SAFETY_MARGIN_W)
            ),
            rouge_hp_blended_cap_eur_per_kwh=float(
                self._cfg(CONF_ROUGE_HP_BLENDED_CAP, DEFAULT_ROUGE_HP_BLENDED_CAP)
            ),
        )

    # ------------------------------------------------------------------
    # Entry points called by entities
    # ------------------------------------------------------------------

    async def async_set_mode(self, mode: str) -> None:
        self._mode = mode
        await self._save_state()
        await self.async_request_refresh()

    async def async_force_heat(self) -> None:
        self._force_skim = True
        await self.async_request_refresh()

    async def async_reset_patterns(self) -> None:
        self._pattern = PatternState()
        await self._save_state()
        _LOGGER.info("DHWP Smart: weekday patterns reset")
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        await self._load_state()
        self._register_listeners()

    async def async_teardown(self) -> None:
        for u in self._unsub:
            u()
        self._unsub.clear()

    def _register_listeners(self) -> None:
        watched = [
            self._entity(k)
            for k in (
                CONF_TEMPO_COLOR_ENTITY,
                CONF_TEMPO_IS_HC_ENTITY,
                CONF_TANK_MIDDLE_TEMP_ENTITY,
                CONF_GRID_POWER_ENTITY,
            )
        ]
        watched = [e for e in watched if e]
        if watched:
            self._unsub.append(
                async_track_state_change_event(self.hass, watched, self._on_change)
            )

    @callback
    def _on_change(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _load_state(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        self._mode = data.get("mode", MODE_AUTO)
        if pat := data.get("pattern"):
            self._pattern = PatternState(
                daily_kwh_ema=tuple(pat.get("daily_kwh_ema", PatternState().daily_kwh_ema)),
                outdoor_coef=float(pat.get("outdoor_coef", 0.1)),
                samples_n=int(pat.get("samples_n", 0)),
            )
        if cyc := data.get("cycle"):
            self._cycle = CycleAccumulator(
                kwh_solar=float(cyc.get("kwh_solar", 0.0)),
                kwh_hc=float(cyc.get("kwh_hc", 0.0)),
                kwh_hp=float(cyc.get("kwh_hp", 0.0)),
                cost_eur=float(cyc.get("cost_eur", 0.0)),
            )
        self._cycle_started_at = _parse_iso(data.get("cycle_started_at"))
        self._last_cycle_summary = data.get("last_cycle_summary")
        self._last_energy_meter_wh = data.get("last_energy_meter_wh")
        self._grid_smooth_w = data.get("grid_smooth_w")
        self._last_switch_change_at = _parse_iso(data.get("last_switch_change_at"))
        self._today_kwh = float(data.get("today_kwh", 0.0))
        td = data.get("today_date")
        if td:
            try:
                self._today_date = date.fromisoformat(td)
            except ValueError:
                self._today_date = None
        self._daily_outdoor_sum = float(data.get("daily_outdoor_sum", 0.0))
        self._daily_outdoor_count = int(data.get("daily_outdoor_count", 0))

    async def _save_state(self) -> None:
        await self._store.async_save(
            {
                "mode": self._mode,
                "pattern": {
                    "daily_kwh_ema": list(self._pattern.daily_kwh_ema),
                    "outdoor_coef": self._pattern.outdoor_coef,
                    "samples_n": self._pattern.samples_n,
                },
                "cycle": asdict(self._cycle),
                "cycle_started_at": _to_iso(self._cycle_started_at),
                "last_cycle_summary": self._last_cycle_summary,
                "last_energy_meter_wh": self._last_energy_meter_wh,
                "grid_smooth_w": self._grid_smooth_w,
                "last_switch_change_at": _to_iso(self._last_switch_change_at),
                "today_kwh": self._today_kwh,
                "today_date": self._today_date.isoformat() if self._today_date else None,
                "daily_outdoor_sum": self._daily_outdoor_sum,
                "daily_outdoor_count": self._daily_outdoor_count,
            }
        )

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self.config_entry.options.get(key, self.config_entry.data.get(key, default))

    def _entity(self, key: str) -> str | None:
        v = self._cfg(key)
        return v if v else None

    def _get_float(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        s = self.hass.states.get(entity_id)
        if s is None or s.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            return float(s.state)
        except (ValueError, TypeError):
            return None

    def _get_state(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        s = self.hass.states.get(entity_id)
        if s is None:
            return None
        return s.state

    # ------------------------------------------------------------------
    # Main control loop
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        local_now = datetime.now()

        # Daily rollover: if the date changed, close yesterday's pattern.
        today = local_now.date()
        if self._today_date is not None and today != self._today_date:
            await self._finalise_yesterday()
        if self._today_date is None:
            self._today_date = today

        # Read sensors.
        grid_raw = self._get_float(self._entity(CONF_GRID_POWER_ENTITY)) or 0.0
        pv = self._get_float(self._entity(CONF_PV_POWER_ENTITY)) or 0.0
        heater_w = self._get_float(self._entity(CONF_HEATER_POWER_ENTITY)) or 0.0
        booster_w = self._get_float(self._entity(CONF_HEATER_BOOSTER_POWER_ENTITY)) or 0.0
        heater_total_w = heater_w + booster_w
        tank_top = self._get_float(self._entity(CONF_TANK_TOP_TEMP_ENTITY)) or 0.0
        tank_mid = self._get_float(self._entity(CONF_TANK_MIDDLE_TEMP_ENTITY)) or 0.0
        garage_c = self._get_float(self._entity(CONF_GARAGE_TEMP_ENTITY)) or 18.0
        outdoor_c = self._get_float(self._entity(CONF_OUTDOOR_TEMP_ENTITY))
        tempo_color = self._get_state(self._entity(CONF_TEMPO_COLOR_ENTITY)) or "Bleu"
        tempo_next = self._get_state(self._entity(CONF_TEMPO_NEXT_COLOR_ENTITY))
        is_hc_raw = self._get_state(self._entity(CONF_TEMPO_IS_HC_ENTITY))
        is_hc = is_hc_raw == STATE_ON
        forecast_today_wh = self._get_float(self._entity(CONF_FORECAST_TODAY_ENTITY))
        forecast_tomorrow_wh = self._get_float(self._entity(CONF_FORECAST_TOMORROW_ENTITY))
        forecast_today_kwh = forecast_today_wh / 1000.0 if forecast_today_wh else None
        forecast_tomorrow_kwh = forecast_tomorrow_wh / 1000.0 if forecast_tomorrow_wh else None
        energy_meter_wh = self._get_float(self._entity(CONF_HEATER_ENERGY_METER_ENTITY))

        # EMA-smooth grid.
        alpha = float(self._cfg(CONF_SOLAR_SMOOTH_ALPHA, DEFAULT_SOLAR_SMOOTH_ALPHA))
        if self._grid_smooth_w is None:
            self._grid_smooth_w = grid_raw
        else:
            self._grid_smooth_w = alpha * grid_raw + (1.0 - alpha) * self._grid_smooth_w

        # Accumulate outdoor temp for the daily average.
        if outdoor_c is not None:
            self._daily_outdoor_sum += outdoor_c
            self._daily_outdoor_count += 1

        # Energy budget for the next morning.
        hours_to_morning = self._hours_to_next_morning(local_now)
        weekday = local_now.weekday()
        expected_usage_kwh = expected_kwh_for_day(
            self._pattern, weekday, outdoor_c if outdoor_c is not None else 15.0
        )
        capacity_l = int(self._cfg(CONF_TANK_CAPACITY_L, DEFAULT_TANK_CAPACITY_L))
        target_top = float(self._cfg(CONF_TANK_TARGET_TOP_C, DEFAULT_TANK_TARGET_TOP_C))
        floor_c = float(self._cfg(CONF_TANK_MORNING_FLOOR_C, DEFAULT_TANK_MORNING_FLOOR_C))
        energy_needed = energy_budget_until_morning_kwh(
            current_top_c=tank_top,
            target_top_c=target_top,
            floor_c=floor_c,
            garage_c=garage_c,
            hours_to_morning=hours_to_morning,
            expected_usage_kwh=expected_usage_kwh,
            capacity_l=capacity_l,
        )

        # Accumulate cycle energy + outdoor sample BEFORE the decision (so
        # the cycle reflects what already happened before this tick).
        if energy_meter_wh is not None and self._last_energy_meter_wh is not None:
            delta_wh = max(0.0, energy_meter_wh - self._last_energy_meter_wh)
            delta_kwh = delta_wh / 1000.0
            if delta_kwh > 0 and heater_total_w > 0:
                # Source attribution: how much of heater's draw came from solar?
                solar_share = self._solar_share(grid_raw, heater_total_w)
                self._cycle = accumulate(
                    self._cycle, delta_kwh, solar_share, tempo_color, is_hc
                )
                self._today_kwh += delta_kwh
        self._last_energy_meter_wh = energy_meter_wh

        # Honour force-heat: equivalent to a one-shot boost.
        effective_mode = self._mode
        if self._force_skim:
            effective_mode = "boost"
            self._force_skim = False

        inputs = Inputs(
            now=now,
            mode=effective_mode,
            tank_top_c=tank_top,
            tank_middle_c=tank_mid,
            garage_c=garage_c,
            outdoor_c=outdoor_c,
            grid_smooth_w=self._grid_smooth_w,
            pv_power_w=pv,
            heater_power_w=heater_total_w,
            tempo_color=tempo_color,
            tempo_next_color=tempo_next,
            is_hc=is_hc,
            forecast_today_kwh=forecast_today_kwh,
            forecast_tomorrow_kwh=forecast_tomorrow_kwh,
            energy_needed_kwh=energy_needed,
            cycle=self._cycle,
        )
        decision = decide(inputs, self.thresholds)
        self._last_decision = decision

        # Apply, respecting min-dwell rate limit (heater dislikes rapid cycling).
        await self._apply_switch(decision.heater_on, now)

        await self._save_state()
        return self._build_data(
            decision=decision,
            energy_needed=energy_needed,
            expected_usage=expected_usage_kwh,
            grid_smooth=self._grid_smooth_w,
            heater_w=heater_total_w,
            forecast_today_kwh=forecast_today_kwh,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _hours_to_next_morning(self, local_now: datetime) -> float:
        hh = int(self._cfg(CONF_MORNING_DEADLINE_HOUR, DEFAULT_MORNING_DEADLINE_HOUR))
        mm = int(self._cfg(CONF_MORNING_DEADLINE_MINUTE, DEFAULT_MORNING_DEADLINE_MINUTE))
        deadline = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if deadline <= local_now:
            deadline = deadline + timedelta(days=1)
        return (deadline - local_now).total_seconds() / 3600.0

    @staticmethod
    def _solar_share(grid_raw_w: float, heater_w: float) -> float:
        """Fraction of the heater's current draw covered by solar.

        grid_raw_w negative means exporting (heater draw fully covered by
        solar and there's more left). 0 or positive means we're pulling
        from grid; the share is `(heater - grid)/heater` (clamped).
        """
        if heater_w <= 0:
            return 0.0
        if grid_raw_w <= 0:
            return 1.0
        return max(0.0, min(1.0, (heater_w - grid_raw_w) / heater_w))

    async def _apply_switch(self, desired_on: bool, now: datetime) -> None:
        switch_id = self._entity(CONF_SIGNAL_SWITCH_ENTITY)
        if not switch_id:
            return

        # Read current switch state.
        actual = self._get_state(switch_id)
        actual_on = actual == STATE_ON
        if actual_on == desired_on:
            self._last_known_switch_state = actual_on
            return

        # Rate limit. Manual mode bypass.
        dwell = float(self._cfg(CONF_MIN_DWELL_SECONDS, DEFAULT_MIN_DWELL_SECONDS))
        bypass = self._mode in MANUAL_MODES
        elapsed = (
            (now - self._last_switch_change_at).total_seconds()
            if self._last_switch_change_at
            else float("inf")
        )
        if not bypass and elapsed < dwell:
            _LOGGER.debug(
                "DHWP Smart: rate-limited (%.0fs < %.0fs), holding switch=%s",
                elapsed, dwell, actual_on,
            )
            return

        service = "turn_on" if desired_on else "turn_off"
        _LOGGER.info(
            "DHWP Smart: switch.%s on %s — %s",
            service, switch_id, self._last_decision.reason,
        )
        await self.hass.services.async_call(
            "switch", service, {"entity_id": switch_id}, blocking=True
        )
        self._last_switch_change_at = now

        # Cycle bookkeeping: opening on edge → reset cycle.
        if desired_on and not self._last_known_switch_state:
            self._cycle = CycleAccumulator()
            self._cycle_started_at = now
            _LOGGER.info("DHWP Smart: cycle started")
        elif not desired_on and self._last_known_switch_state and self._cycle_started_at:
            self._last_cycle_summary = {
                "ended_at": now.isoformat(),
                "duration_min": (now - self._cycle_started_at).total_seconds() / 60.0,
                "kwh_solar": self._cycle.kwh_solar,
                "kwh_hc": self._cycle.kwh_hc,
                "kwh_hp": self._cycle.kwh_hp,
                "cost_eur": self._cycle.cost_eur,
                "blended_eur_per_kwh": blended_cost_eur_per_kwh(self._cycle),
            }
            _LOGGER.info(
                "DHWP Smart: cycle ended — %.2f kWh (solar %.2f, hc %.2f, hp %.2f), "
                "cost %.3f € (blended %.4f €/kWh)",
                self._cycle.kwh_solar + self._cycle.kwh_hc + self._cycle.kwh_hp,
                self._cycle.kwh_solar, self._cycle.kwh_hc, self._cycle.kwh_hp,
                self._cycle.cost_eur, blended_cost_eur_per_kwh(self._cycle),
            )
            self._cycle_started_at = None

        self._last_known_switch_state = desired_on

    async def _finalise_yesterday(self) -> None:
        """Close yesterday's daily aggregation and fold it into the weekday pattern."""
        if self._today_date is None:
            return
        weekday = self._today_date.weekday()
        outdoor_avg = (
            self._daily_outdoor_sum / self._daily_outdoor_count
            if self._daily_outdoor_count > 0
            else 15.0
        )
        self._pattern = update_pattern(
            self._pattern, weekday=weekday,
            observed_kwh=self._today_kwh, outdoor_avg_c=outdoor_avg,
        )
        _LOGGER.info(
            "DHWP Smart: weekday %d closed — %.2f kWh @ avg outdoor %.1f°C",
            weekday, self._today_kwh, outdoor_avg,
        )
        self._today_kwh = 0.0
        self._today_date = None
        self._daily_outdoor_sum = 0.0
        self._daily_outdoor_count = 0

    # ------------------------------------------------------------------
    # Data payload for entities
    # ------------------------------------------------------------------

    def _build_data(
        self,
        decision: Decision,
        energy_needed: float,
        expected_usage: float,
        grid_smooth: float | None,
        heater_w: float,
        forecast_today_kwh: float | None,
    ) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "decision_action": decision.action,
            "decision_reason": decision.reason,
            "heater_on": decision.heater_on,
            "energy_needed_kwh": round(energy_needed, 2),
            "expected_usage_today_kwh": round(expected_usage, 2),
            "cycle_kwh_today": round(self._today_kwh, 2),
            "cycle_blended_cost_eur_per_kwh": round(
                blended_cost_eur_per_kwh(self._cycle), 4
            ),
            "grid_smooth_w": round(grid_smooth, 0) if grid_smooth is not None else None,
            "heater_power_w": round(heater_w, 0),
            "forecast_today_kwh": (
                round(forecast_today_kwh, 2) if forecast_today_kwh is not None else None
            ),
            "pattern_samples": self._pattern.samples_n,
            "outdoor_coef": round(self._pattern.outdoor_coef, 3),
        }


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _to_iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None
