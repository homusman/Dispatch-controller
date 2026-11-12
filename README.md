# HAEO Dispatch Controller

**v0.2.5 — HAEO owns the economic decisions.** This custom HACS integration translates HAEO battery power targets into Solis and Fronius commands. It does **not** implement proportional grid feedback, battery reallocation, or a second optimization loop.

## How it works

HAEO publishes its current optimized active-power targets for the grid, Leaf battery and Tesla battery. The dispatcher checks them every **20 seconds**, translates signs and units, and issues commands when the requested inverter setpoints change. This is a command reconciliation interval, **not** an independent control loop.

| Mode | Conditions | Solis / Leaf | Fronius / Tesla |
| --- | --- | --- | --- |
| **Hold** | Both HAEO battery targets near 0 W | Forced 0 W | Forced 0 W |
| **Self-use** | Grid target within the configured switching threshold | Native Solis hardware self-use | HAEO's **Tesla** signed power target |
| **Arbitrage** | Grid target beyond the configured switching threshold | HAEO's **Leaf** signed target | HAEO's **Tesla** signed target |

Hold has priority when both individual battery targets are near zero. HAEO grid active power is **positive import / negative export**; HAEO battery active power is **positive discharge / negative charge**, in kW. Inverter actuator numbers use the same battery power sign, in W.

**Note:** In self-use mode, Solis operates autonomously and therefore may deviate from the optimized Leaf SoC trajectory. This is an explicitly chosen tradeoff; the dispatcher **does not** independently compensate for grid error. More accurate real-time load inputs and frequent HAEO reoptimization should reduce deviations. Fronius still follows the HAEO Tesla power target.

## Installation

In HACS, add `https://github.com/homusman/Dispatch-controller` as a custom Integration repository, download, restart Home Assistant, and add **HAEO Dispatch Controller** from Settings → Devices & services.

Configure these 12 entity mappings:

- HAEO planned grid active power, Leaf active power, Tesla active power (**kW**).
- Measured grid power, Leaf battery power, Tesla battery power (**W** or **kW**); set measured-sign selectors accurately.
- Actual Leaf and Tesla SoC (**%**, legacy mappings retained but not used for command limiting).
- Solis and Fronius signed-power Number actuator entities and Self-Use Mode Switch actuator entities.

Example HAEO sensor IDs (assuming the elements are named Grid, Leaf and Tesla): `sensor.grid_active_power`, `sensor.leaf_active_power`, `sensor.tesla_active_power`. Choose actual IDs rather than guessing.

## Editing configuration

Home Assistant → Settings → Devices & services → HAEO Dispatch Controller → **Configure**. The options flow lets you change all sensor mappings, sign conventions and actuator entities after installation. Settings changes reload the integration in shadow mode; **Dispatch enabled** must be turned on again explicitly. Do not confuse the hub/device screen with the integration entry's Configure button.

## v0.2.5 reload and mode-state fixes

- Fixed integration reload/unload on Home Assistant versions where `ConfigEntry.async_unload_callbacks` does not exist. Registered unload callbacks are managed by Home Assistant.
- In **Shadow**, the command sensors now preview the calculated setpoints so that Fronius/Tesla no longer appears unavailable merely because the dispatcher has not written a command. **Solis/Leaf has no numeric command in Self-use**, since its hardware loop is active; an unavailable command reading there is intentional.
- Status appends a concise switch-state mismatch when either actual Self-use switch differs from the planned mode, including when manually changed. The dispatcher subscribes to switch-state changes, so it can update the Shadow status or reconcile the live mode without waiting for its next regular 20-second tick.
- When Dispatch is enabled, a manually changed inverter mode is subject to automatic reconciliation; **switch Dispatch OFF before making intentional manual adjustments**. The disable action still requests 0 W hold by design.

## Controls and diagnostics

- **Dispatch enabled** switch: OFF at startup. When disabled, the controller uses **Shadow** status to show the exact decision it would make, without writing to the inverters.
- **Dispatch mode**: Disabled, Hold, Self-use, Arbitrage, or Error.
- **Dispatch status**: A concise human-readable summary including operating mode and intended power commands, any actuator clamps, or faults.
- **HAEO grid / Leaf / Tesla power**, measured grid power, and **Solis / Fronius command** sensors remain available for numeric comparisons.
- The six extra Fronius troubleshooting sensors from v0.2.1–v0.2.3 are no longer created. HA's entity registry may retain old unavailable entries; remove those through the entity registry if desired.

### Status examples

```text
Shadow | Self-use | Solis auto | Tesla +2200 W
Self-use | Solis auto | Tesla +2200 W
Arbitrage | Leaf +1800 W | Tesla -1000 W
Shadow | Arbitrage | Leaf +5000 W | Tesla +3000 W | CLAMP Leaf +6500→+5000 W
Hold | Leaf 0 W | Tesla 0 W
Error | tesla actuator unavailable: number.xxx | 0 W hold requested
```

Shadow mode performs the same actuator min/max evaluation as live mode, but makes **no inverter service calls**. The command sensor readings in Shadow are predictions, not evidence of a dispatched command. The clamp summaries show the actual requested setpoint when actuator limits apply. No SoC-based clipping is performed by the dispatcher; inverter/BMS protections remain authoritative.

## Mode transitions

The Configure page exposes **Grid deadband** and **Grid hysteresis** in watts.
Deadband defaults to 100 W and hysteresis defaults to 100 W, including for existing entries.
From startup, Hold or Self-use, Arbitrage requires planned import or export above
deadband plus hysteresis. Once in Arbitrage, the controller returns to Self-use at
deadband or below. At the defaults, a 150 W target stays in Self-use, 201 W enters
Arbitrage, and 100 W returns to Self-use. Setting hysteresis to zero restores a
single threshold. Both battery targets within 80 W still select Hold first.

Mode selection uses the same hysteresis in Shadow and live operation. Stopping
dispatch or encountering an input or actuator error resets its mode memory.

On entry to Self-use or Arbitrage, the controller reasserts the Fronius/Tesla requested power target even if the Home Assistant Number already displays that setpoint. It avoids repeated writes when the displayed setpoint is unchanged. A successful HA service call doesn't prove that the inverter physically applied the request; verify actual battery power during commissioning.

## Safety and commissioning

This is experimental software, not an export limiter or a BMS safety controller.

- Initial state is **shadow mode** (Dispatch enabled OFF). Inspect the planned mode, power targets, units, and sensor sign conventions.
- Disable the old Battery Aggregator and any other automations writing to the same inverter entities.
- Confirm small power requests and all three mode transitions under observation. **Setpoint changes are no longer rate-limited by this dispatcher**; inverter/BMS hardware must enforce safe electrical limits.
- Dispatch values are clamped only to the Number actuator's exposed min/max. **No SoC interlocks are enforced here**: battery/inverter firmware must prevent unsafe charging or discharging and may allow 0%/99.9% recalibration. Configure battery operating reserves in HAEO or physical battery controls as appropriate.
- If an input becomes unavailable or a HAEO plan has not reported for over 15 minutes, the controller requests zero-power hold while enabled. Check actual inverter state: network/service errors may prevent these requests.
- Explicitly switching dispatch OFF requests zero-power hold on both batteries. Starting/restarting Home Assistant leaves dispatch OFF **without** transmitting a new inverter command.
- Measured battery/grid power inputs remain available for diagnostics and sanity checks, but the dispatcher does not use their power errors to tune commands.

## Faster HAEO updates

HAEO is **event-driven**, not fixed to a five-minute interval: it optimizes when an input entity changes or the horizon crosses a time boundary, with debounce to combine nearby updates. Feed HAEO accurate, current load inputs and verify new optimization results are published often enough. A manual `haeo.optimize` service/action can request an immediate solve when needed.

Reference: [HAEO data-update documentation](https://github.com/hass-energy/haeo/blob/main/docs/user-guide/data-updates.md).

## Next mini-project

Improve the HAEO load forecast: separate baseline household load from unpredictable loads such as the EV charger; give HAEO the live changes to trigger economically informed replanning. Keep grid-error feedback in this integration **disabled/removed** unless a concrete control gap is demonstrated.

