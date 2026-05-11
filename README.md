# Smart DHWP — Hot water heating optimised on solar + Tempo

A Home Assistant custom integration for an Atlantic heat-pump water heater
(or any similar 230 V tank with a contactor in front of it). Decides each
minute whether the contactor should be on or off to keep the tank at a
healthy temperature **at the lowest possible cost**, using:

- **Solar excess** from a Shelly Pro EM (or any grid + PV power sensors).
- **Tempo tariff** awareness — heats freely on cheap days/hours, refuses
  to touch grid on Tempo Rouge HP unless the cycle's *blended cost* still
  stays ≥ 25 % cheaper than Rouge HC.
- **Forecast.Solar** to decide whether to chase scarce solar today or
  top up tonight on HC.
- **Per-weekday usage patterns** learnt from observed energy consumption,
  with an outdoor-temperature coefficient (cold days ⇒ more hot water).
- **Garage temperature** to model standing heat loss between cycles.
- A **hard floor override** (default 48 °C in the middle of the tank by
  06:30) — overrides everything to make sure you don't get a cold shower.

## Design

The decision lives in `decision.py`, a pure-Python module with no
`homeassistant` imports — 30+ unit tests run it in ~50 ms.

Coordinator (`coordinator.py`) is responsible for:
- reading sensors, EMA-smoothing grid power, computing the energy budget
  for the next morning, dispatching to `decide()`,
- applying the on/off command on the contactor with a min-dwell rate limit,
- attributing each consumed kWh to either *solar* or *grid* (split by
  Tempo color × HC/HP) for the cycle accumulator,
- persisting per-weekday EMA + outdoor coefficient across restarts.

## Entities

Under a single device "Smart DHWP":

- `select.dhwp_smart_mode` — `auto / off / boost / hc_only / solar_only`.
- `sensor.dhwp_smart_action` — one of `wait / heat_solar / heat_hc / boost / hard_floor`.
- `sensor.dhwp_smart_reason` — human-readable explanation of the current state.
- `sensor.dhwp_smart_energy_needed` — kWh required to hit the morning floor.
- `sensor.dhwp_smart_cycle_kwh_today` — heater energy consumed today.
- `sensor.dhwp_smart_blended_cost` — €/kWh of the current/last cycle.
- `sensor.dhwp_smart_grid_smooth` — EMA-smoothed grid power (W).
- `sensor.dhwp_smart_expected_usage` — pattern-model prediction for today.
- `sensor.dhwp_smart_forecast_today` — Forecast.Solar pass-through.
- `sensor.dhwp_smart_pattern_samples` — observations folded into the model.
- `binary_sensor.dhwp_smart_heating_now` — contactor on.
- `binary_sensor.dhwp_smart_hard_floor_breach` — currently below the floor in the morning window.
- `button.dhwp_smart_force_heat` — fire a one-shot boost.
- `button.dhwp_smart_reset_patterns` — clear learned weekday EMAs.

## Decision priority

1. **Manual mode** wins (off / boost / hc_only / solar_only).
2. **Hard floor breach** in the 04:00–06:30 window → force on.
3. **Tempo Rouge HP**: solar excess is allowed *only* if the cycle's
   worst-case blended cost (next 15 min projected at full HP price) still
   stays under 0.118 €/kWh.
4. **HC window**: on if any energy is still needed before morning.
5. **HP window, non-Rouge**: solar surplus + sunny forecast ⇒ on.
   Surplus + cloudy forecast ⇒ wait for HC.
6. Otherwise off.

## Setup

1. HACS → Add custom repository → `https://github.com/qelanhari/ha-water-heater` (Integration).
2. Download the integration, restart HA.
3. Settings → Devices & Services → Add Integration → "Smart DHWP".
4. Pick your contactor, sensors, Tempo entities, and (optional) Forecast.Solar.
5. Default tank settings: 270 L, 54 °C target, 48 °C floor by 06:30.
6. Disable any older HC/solar automations that toggled the contactor.

## Tests

```bash
cd ha-water-heater
pytest -q                                       # 60+ cases
pytest tests/test_decision.py::TestRougeHp     # one class
```

## License

MIT.
