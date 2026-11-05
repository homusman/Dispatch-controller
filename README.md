# HAEO Dispatch Controller

A HACS-compatible Home Assistant integration that executes **HAEO** per-battery plans through the signed-power Solis and Fronius actuators. This integration is **experimental**; validate in shadow mode first.

## Architecture

HAEO optimizes Leaf and Tesla separately; this integration never chooses the economic battery allocation.

- **HOLD**: if *both* planned battery powers are approximately zero, request 0 W forced hold on Solis and Fronius (even when HAEO's planned grid power is zero).
- **SELF_USE**: otherwise, if planned grid active power is near zero, enable Solis hardware self-use; command Fronius to follow the HAEO Tesla target, adjusting it by a **bounded** correction toward HAEO's Leaf target based on measured Solis battery power.
- **ARBITRAGE**: otherwise, send HAEO's individual battery plans as feedforward targets and apply a slow, bounded correction toward HAEO's planned **grid active power**, prioritized across batteries using selectable policy (Leaf first / Tesla first / SoC headroom).

### Sign conventions

- HAEO grid active power: positive **import**, negative **export**; unit kW.
- Measured grid meter: configurable polarity; internally positive import.
- HAEO battery: nonnegative charge/discharge sensors, kW; signed dispatch = discharge − charge.
- Both actuators: positive **discharge**, negative **charge**; unit W.
- Measured battery power sign is configurable per battery.

## Install

HACS → Custom repositories → `https://github.com/homusman/Dispatch-controller` (Integration) → Download → restart HA → Add integration → **HAEO Dispatch Controller**.

Configure the HAEO current grid **active power** sensor, four HAEO battery charge/discharge sensors, measured grid power, measured battery power sensors, both actuator Number entities and both self-use Switch entities, and two SoC sensors. Entity IDs are selected during setup rather than hardcoded.

The controller starts with **Dispatch Enabled OFF** and only observes the plan. Check the Mode, Targets, Grid Error and Status sensors before enabling any commands. The previous Battery Aggregator or other dispatch automation must be disabled to avoid competing writes.

## Controls

- `switch.*_dispatch_enabled`: **OFF by default**; enabling controls the inverters.
- `number.*_max_feedback_adjustment`: maximum total instantaneous feedback correction (W); default 500 W.
- `number.*_feedback_gain`: grid error proportional gain; default 0.3.
- `select.*_battery_priority`: Leaf first, Tesla first or SoC headroom.
- `number.*_max_command_step`: maximum step in commanded battery target per tick (W); default 500 W.
- `sensor.*_mode`, `sensor.*_leaf_target`, `sensor.*_tesla_target`, `sensor.*_grid_error`, `sensor.*_status` for diagnostics.

The loop runs every 20 seconds; a stale HAEO input (>10 minutes without update) or invalid values triggers a one-time zero-hold attempt, then suppresses automatic commands until valid again. An explicit OFF disables new dispatch commands; it does **not** change existing inverter modes. Use inverter manual controls to release control if needed. Maximum observed grid import/export and SOC ceilings are **not** physical interlocks; configure hardware export limits separately.

## V0.1 limitations

- Physical measurements and inverter response times vary. Confirm polarity on your measured grid and battery sensors.
- Self-use mode correction follows the *Leaf target* using measured Leaf battery power, rather than forcibly driving Solis to zero.
- SoC-based prioritization uses available percentage headroom, not HAEO's future shadow prices.
- HAEO emits average power over each optimization interval. The feedback loop is intentionally bounded so it does not replace HAEO's 5-minute economic replanning.
- No complete battery energy-budget tracking or hard export-limit enforcement yet.
- The integration is not a safety-certified device controller. Keep hardware/BMS safety limits and export protections in place.

## Next

Improve load forecasting; assess predicted vs actual loads, separate uncontrollable base load from EV/other controllable loads, and feed corrected load forecasts to HAEO.
