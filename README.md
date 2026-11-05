# HAEO Dispatch Controller

**v0.2.0 — HAEO owns the economic decisions.** This custom HACS integration translates HAEO battery power targets into Solis and Fronius commands. It does **not** implement proportional grid feedback, battery reallocation, or a second optimization loop.

## How it works

HAEO publishes its current optimized active-power targets for the grid, Leaf battery and Tesla battery. The dispatcher checks them every **20 seconds**, translates signs and units, and issues commands when the requested inverter setpoints change. This is a command reconciliation interval, **not** an independent control loop.

| Mode | Conditions | Solis / Leaf | Fronius / Tesla |
| --- | --- | --- | --- |
| **Hold** | Both HAEO battery targets near 0 W | Forced 0 W | Forced 0 W |
| **Self-use** | Otherwise HAEO grid net target near 0 W | Native Solis hardware self-use | HAEO's **Tesla** signed power target |
| **Arbitrage** | Otherwise (nonzero HAEO grid target) | HAEO's **Leaf** signed target | HAEO's **Tesla** signed target |

Hold has priority when both individual battery targets are near zero. HAEO grid active power is **positive import / negative export**; HAEO battery active power is **positive discharge / negative charge**, in kW. Inverter actuator numbers use the same battery power sign, in W.

**Note:** In self-use mode, Solis operates autonomously and therefore may deviate from the optimized Leaf SoC trajectory. This is an explicitly chosen tradeoff; the dispatcher **does not** independently compensate for grid error. More accurate real-time load inputs and frequent HAEO reoptimization should reduce deviations. Fronius still follows the HAEO Tesla power target.

## Installation

In HACS, add `https://github.com/homusman/Dispatch-controller` as a custom Integration repository, download, restart Home Assistant, and add **HAEO Dispatch Controller** from Settings → Devices & services.

Configure these 12 entity mappings:

- HAEO planned grid active power, Leaf active power, Tesla active power (**kW**).
- Measured grid power, Leaf battery power, Tesla battery power (**W** or **kW**); set measured-sign selectors accurately.
- Actual Leaf and Tesla SoC (**%**).
- Solis and Fronius signed-power Number actuator entities and Self-Use Mode Switch actuator entities.

Example HAEO sensor IDs (assuming the elements are named Grid, Leaf and Tesla): `sensor.grid_active_power`, `sensor.leaf_active_power`, `sensor.tesla_active_power`. Choose actual IDs rather than guessing.

## Controls and diagnostics

- **Dispatch enabled** switch: **OFF on startup**; no inverter commands until explicitly enabled.
- **Dispatch mode** sensor: Disabled, Hold, Self-use, Arbitrage or Error.
- **Dispatch status** sensor: recent controller decision/error.
- **HAEO grid target / Leaf power / Tesla power** sensors: plan inputs in W.
- **Measured grid power / Grid tracking error**: **diagnostic only**. Grid error never modifies commands.
- **Solis command / Fronius command**: last dispatch requested; Unknown in shadow mode until a command is issued.

A 20-second check is retained to catch mode changes and recover from missed state events; it does not calculate a grid-error power correction. The older gain, max-feedback-adjustment, max-command-step, and battery-priority entities are removed in v0.2.0. Home Assistant may retain their entity-registry entries marked unavailable; they can be deleted manually after updating.

## Safety and commissioning

This is experimental software, not an export limiter or a BMS safety controller.

- Initial state is **shadow mode** (Dispatch enabled OFF). Inspect the planned mode, power targets, units, and sensor sign conventions.
- Disable the old Battery Aggregator and any other automations writing to the same inverter entities.
- Confirm small power requests and all three mode transitions under observation. **Setpoint changes are no longer rate-limited by this dispatcher**; inverter/BMS hardware must enforce safe electrical limits.
- Dispatch values are clamped to actuator limits, with basic fixed SoC cutoffs (no discharge below 10%, no charge above 95%). These are not HAEO dynamic reserves.
- If an input becomes unavailable or a HAEO plan has not reported for over 15 minutes, the controller requests zero-power hold while enabled. Check actual inverter state: network/service errors may prevent these requests.
- Explicitly switching dispatch OFF requests zero-power hold on both batteries. Starting/restarting Home Assistant leaves dispatch OFF **without** transmitting a new inverter command.
- Measured battery/grid power inputs remain available for diagnostics and sanity checks, but the dispatcher does not use their power errors to tune commands.

## Faster HAEO updates

HAEO is **event-driven**, not fixed to a five-minute interval: it optimizes when an input entity changes or the horizon crosses a time boundary, with debounce to combine nearby updates. Feed HAEO accurate, current load inputs and verify new optimization results are published often enough. A manual `haeo.optimize` service/action can request an immediate solve when needed.

Reference: [HAEO data-update documentation](https://github.com/hass-energy/haeo/blob/main/docs/user-guide/data-updates.md).

## Next mini-project

Improve the HAEO load forecast: separate baseline household load from unpredictable loads such as the EV charger; give HAEO the live changes to trigger economically informed replanning. Keep grid-error feedback in this integration **disabled/removed** unless a concrete control gap is demonstrated.
