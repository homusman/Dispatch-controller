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

The loop runs every 20 seconds; a stale HAEO input (>15 minutes without a state report) or invalid values triggers a zero-hold request, then suppresses automatic commands until valid again. An explicit OFF requests zero-power forced hold on both inverters; it does not automatically switch Solis back to self-use. The controller starts OFF after every HA restart and sends no commands until enabled. If an actuator is unavailable, check the inverter's physical state because a hold request may fail. Maximum observed grid import/export and SOC ceilings are **not** physical interlocks; configure hardware export limits separately.

## V0.1 limitations

- Physical measurements and inverter response times vary. Confirm polarity on your measured grid and battery sensors.
- Self-use mode correction follows the *Leaf target* using measured Leaf battery power, rather than forcibly driving Solis to zero.
- SoC-based prioritization uses available percentage headroom, not HAEO's future shadow prices.
- HAEO emits average power over each optimization interval. The feedback loop is intentionally bounded so it does not replace HAEO's 5-minute economic replanning.
- No complete battery energy-budget tracking or hard export-limit enforcement yet.
- The integration is not a safety-certified device controller. Keep hardware/BMS safety limits and export protections in place.

## Next

Improve load forecasting; assess predicted vs actual loads, separate uncontrollable base load from EV/other controllable loads, and feed corrected load forecasts to HAEO.

## Entity selection example

The HAEO sensor names in the current HAEO documentation are:

```text
sensor.grid_active_power       # HAEO grid target, +kW means import
sensor.leaf_active_power       # HAEO Leaf target, +kW means discharge
sensor.tesla_active_power      # HAEO Tesla target, +kW means discharge
```

Names above are examples for elements literally called Grid, Leaf and Tesla. Select your actual HAEO entities in the config flow. Do not use actual measured grid sensors as HAEO plan inputs.

For physical measurements, choose actual grid meter power, Solis/Leaf battery power, Fronius/Tesla battery power, and actual Leaf and Tesla SoC sensors. Set the grid/battery polarity toggles to match **your measured sensors**, not the HAEO plan.

## Shadow-mode commissioning

1. Install, restart and configure the 12 entity selectors. Keep **Dispatch enabled OFF**.
2. Check `Dispatch mode`, `HAEO grid target`, `HAEO Leaf power`, `HAEO Tesla power`, `Measured grid power` and `Grid tracking error` against HAEO's own chart. Confirm W/kW and polarity.
3. Disconnect or disable any existing Battery Aggregator, manual scheduled discharge automation, or other controller targeting the same two actuators.
4. With personnel watching inverter power, enable dispatch for a short low-power test; begin with feedback adjustment set to **0 W**. Test Hold, Self-use and small signed power commands. Validate both Solis/Leaf and Fronius/Tesla measurements.
5. Only after validating mode changes and actuator direction, increase feedback gradually (e.g. 100–300 W), and then test a household load step. The default 500 W is not a commissioning requirement.
6. If HAEO reports stale or invalid data while enabled, the integration attempts an immediate 0 W hold. Do not rely on this as a grid export safety interlock.

**Known tradeoff:** With Solis in self-use, its physical response may deviate from HAEO's optimized Leaf energy schedule. Fronius only compensates a bounded share of this difference and the optimizer must replan using actual SoCs. For major unforecast EV demand, do not use unlimited grid-error correction.

**Not currently provided:** realtime feedback of physical command acceptance, hard export-limit interlock, schedule-integrated correction energy budget, and dynamic SoC minimum reserves from HAEO. Treat this as experimental v0.1 software.
