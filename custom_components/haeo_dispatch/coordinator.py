"""Execute HAEO power plans without a competing feedback controller."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta

_LOGGER = logging.getLogger(__name__)
TICK_SECONDS = 20
PLAN_MAX_AGE_SECONDS = 900
GRID_TOLERANCE_W = 100
BATTERY_TOLERANCE_W = 80

def clamp(value, low, high):
    return max(low, min(high, value))

class DispatchCoordinator:
    """Translate optimized setpoints to physical inverter commands."""

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry
        self.config = {**entry.data, **entry.options}
        self.enabled = False  # An explicit action is required before any writes.
        self.mode = "Disabled"
        self.status = "Observation only; dispatch disabled"
        self.grid_target_w = None
        self.grid_actual_w = None
        self.grid_error_w = None
        self.leaf_plan_w = None
        self.tesla_plan_w = None
        self.leaf_command_w = None
        self.tesla_command_w = None
        self._current_mode = None
        self._unsub = None
        self._running = asyncio.Lock()
        self._listeners = []
        self._safe_stop_attempted = False
        self.tesla_write_status = "No command sent"
        self.tesla_write_time = None
        self.tesla_effective_target_w = None
        self.tesla_limit_reason = "Not evaluated"
        self.leaf_limit_reason = "Not evaluated"
        self.tesla_actuator_w = None
        self.tesla_switch_state = None
        self._tesla_self_use_conflicts = 0

    @property
    def device_info(self):
        return {
            "identifiers": {("haeo_dispatch", self.entry.entry_id)},
            "name": self.entry.title,
            "manufacturer": "Homusman",
            "model": "HAEO Dispatch Controller",
        }

    @property
    def unique_prefix(self):
        return self.entry.entry_id

    def add_listener(self, entity):
        self._listeners.append(entity)

    def update(self):
        for entity in self._listeners:
            if entity.hass is not None:
                entity.async_write_ha_state()

    async def start(self):
        await self.tick()
        self._unsub = async_track_time_interval(
            self.hass, self.tick, timedelta(seconds=TICK_SECONDS)
        )

    async def stop(self):
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _raw(self, field):
        entity_id = self.config[field]
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", "none", ""):
            raise ValueError(f"{field} unavailable: {entity_id}")
        try:
            number = float(state.state)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{field} not numeric: {entity_id}") from exc
        if not (-1e8 < number < 1e8):
            raise ValueError(f"{field} nonfinite or implausibly large")
        return state, number

    def _watts(self, field):
        state, number = self._raw(field)
        unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "kw":
            return number * 1000
        if unit == "w":
            return number
        raise ValueError(f"{field} needs W or kW units, got {unit!r}")

    def _plan_watts(self, field):
        state, value = self._raw(field)
        if state.attributes.get("unit_of_measurement", "").strip().lower() != "kw":
            raise ValueError(f"{field} must be HAEO kW, not another unit")
        # last_reported tracks HA state reports even if the value is unchanged.
        timestamp = getattr(state, "last_reported", None) or state.last_updated
        if (datetime.now(timezone.utc) - timestamp).total_seconds() > PLAN_MAX_AGE_SECONDS:
            raise ValueError(f"{field} plan stale (>15 minutes)")
        return value * 1000

    def _read_inputs(self):
        grid = self._plan_watts("grid_plan")
        leaf = self._plan_watts("leaf_plan")
        tesla = self._plan_watts("tesla_plan")
        actual_grid = self._watts("grid_actual")
        actual_leaf = self._watts("leaf_actual")
        self._watts("tesla_actual")  # Require telemetry for both batteries.
        if not self.config["grid_positive_import"]:
            actual_grid *= -1
        if not self.config["leaf_positive_discharge"]:
            actual_leaf *= -1
        # Tesla telemetry sign is validated here; it is useful for diagnostics.
        actual_tesla = self._watts("tesla_actual")
        if not self.config["tesla_positive_discharge"]:
            actual_tesla *= -1
        # Battery SoC is deliberately not an interlock or required input here.
        # Inverter / BMS enforces battery constraints and calibration behavior.
        self.grid_target_w, self.grid_actual_w = grid, actual_grid
        self.grid_error_w = actual_grid - grid  # Positive -> need more discharge.
        self.leaf_plan_w, self.tesla_plan_w = leaf, tesla
        return grid, leaf, tesla, actual_leaf, actual_tesla

    def _select_mode(self, grid, leaf, tesla):
        if abs(leaf) <= BATTERY_TOLERANCE_W and abs(tesla) <= BATTERY_TOLERANCE_W:
            return "Hold"
        if abs(grid) <= GRID_TOLERANCE_W:
            return "Self-use"
        return "Arbitrage"

    def _limits(self, which, target):
        """Enforce exposed actuator bounds only; never impose SoC cutoffs."""
        entity_id = self.config[which + "_number"]
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unavailable", "unknown"):
            raise ValueError(f"{which} actuator unavailable: {entity_id}")
        low, high = state.attributes.get("min"), state.attributes.get("max")
        if low is None or high is None:
            raise ValueError(f"{which} actuator limits unavailable: {entity_id}")
        low, high = float(low), float(high)
        if not (math.isfinite(low) and math.isfinite(high) and low <= high):
            raise ValueError(f"{which} invalid actuator range [{low}, {high}]")
        requested = float(target)
        clipped = clamp(requested, low, high)
        # Round to 10 W only when this doesn't violate device constraints.
        rounded = round(clipped / 10) * 10
        target = int(rounded) if low <= rounded <= high else clipped
        reason = (
            f"Actuator range [{low:g}, {high:g}] W clipped "
            f"{requested:.0f} W to {target:.0f} W"
            if clipped != requested else "None"
        )
        if which == "tesla":
            self.tesla_effective_target_w = target
            self.tesla_limit_reason = reason
        else:
            self.leaf_limit_reason = reason
        return target

    def _clamp_summary(self):
        notes = []
        if self.leaf_limit_reason not in ("None", "Not evaluated"):
            notes.append(f"Leaf: {self.leaf_limit_reason}")
        if self.tesla_limit_reason not in ("None", "Not evaluated"):
            notes.append(f"Tesla: {self.tesla_limit_reason}")
        return f"; CLAMPED: {'; '.join(notes)}" if notes else ""

    async def _call(self, domain, service, entity_id, **data):
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True
        )

    async def _dispatch(self, which, target, forced=True, force_write=False):
        """Write only actual HAEO setpoints, never a grid-error correction."""
        number = self.config[which + "_number"]
        switch = self.config[which + "_switch"]
        state = self.hass.states.get(switch)
        if not state or state.state not in ("on", "off"):
            raise ValueError(f"{which} mode switch unavailable")
        if not forced:
            if state.state != "on":
                await self._call("switch", "turn_on", switch)
            if which == "leaf":
                self.leaf_command_w = None  # Hardware self-use, not a W setpoint.
            else:
                self.tesla_command_w = None
            return

        target = self._limits(which, target)
        if which == "tesla":
            self.tesla_switch_state = state.state
        if state.state == "on":
            # Re-assert the target once after the mode transition. Repeated
            # re-enabling by another controller is an unsafe command conflict.
            if which == "tesla":
                self._tesla_self_use_conflicts += 1
                if self._tesla_self_use_conflicts > 2:
                    raise ValueError(
                        f"Tesla Self-Use switch {switch} keeps reverting ON; "
                        "check actuator mapping / other automations"
                    )
            force_write = True
            # Only stage 0 if the number does not already report zero.
            # Switching the mode OFF must precede the requested forced target.
            if abs(self._raw_power_state(number)) >= 10:
                await self._call("number", "set_value", number, value=0)
            await self._call("switch", "turn_off", switch)
            if self.hass.states.is_state(switch, "on"):
                raise ValueError(f"{which} Self-Use switch remained ON after turn_off")
        elif which == "tesla":
            self._tesla_self_use_conflicts = 0
        current = self._raw_power_state(number)
        if which == "tesla":
            self.tesla_actuator_w = current
        should_write = force_write or abs(target - current) >= 10
        if should_write:
            await self._call("number", "set_value", number, value=target)
            if which == "tesla":
                self.tesla_write_status = (
                    f"Sent {target} W to {number}"
                    + (" (mode transition / enable)" if force_write else "")
                )
                self.tesla_write_time = datetime.now(timezone.utc).isoformat()
        elif which == "tesla":
            self.tesla_write_status = (
                f"No service call; {number} already reports {current:.0f} W"
            )
        if which == "leaf":
            self.leaf_command_w = target
        else:
            self.tesla_command_w = target
            self.tesla_actuator_w = self._raw_power_state(number)
            self.tesla_switch_state = self.hass.states.get(switch).state

    def _raw_power_state(self, entity_id):
        state = self.hass.states.get(entity_id)
        if not state:
            raise ValueError(f"Power actuator state absent: {entity_id}")
        return float(state.state)

    async def _safe_stop(self, reason):
        self.status = reason + "; requesting 0W hold"
        if self._safe_stop_attempted:
            return
        self._safe_stop_attempted = True
        for which in ("leaf", "tesla"):
            try:
                # Bypass rate limiting for fail-safe stop.
                num = self.config[which + "_number"]
                sw = self.config[which + "_switch"]
                if self.hass.states.is_state(sw, "on"):
                    await self._call("number", "set_value", num, value=0)
                    await self._call("switch", "turn_off", sw)
                else:
                    await self._call("number", "set_value", num, value=0)
            except Exception:
                _LOGGER.exception("Failed requesting %s 0W hold", which)
        self._current_mode = None

    async def tick(self, _now=None):
        if self._running.locked():
            return
        async with self._running:
            try:
                grid, leaf, tesla, actual_leaf, _actual_tesla = self._read_inputs()
                proposed = self._select_mode(grid, leaf, tesla)
                self.tesla_switch_state = self.hass.states.get(self.config["tesla_switch"]).state if self.hass.states.get(self.config["tesla_switch"]) else None
                self.tesla_actuator_w = self._raw_power_state(self.config["tesla_number"])
                self.mode = proposed if self.enabled else "Disabled"
                if not self.enabled:
                    self.tesla_effective_target_w = None
                    self.tesla_limit_reason = "Disabled"
                    self.status = f"Shadow: {proposed}; no inverter writes"
                    self._current_mode = None
                    self._safe_stop_attempted = False
                    return

                if proposed == "Hold":
                    self.tesla_effective_target_w = 0
                    self.tesla_limit_reason = "HAEO requests Hold"
                    self.leaf_limit_reason = "HAEO requests Hold"
                    # Send the hold only on transition, not every 20 seconds.
                    if self._current_mode != "Hold":
                        self._safe_stop_attempted = False
                    await self._safe_stop("HAEO planned battery hold")
                    self._current_mode = "Hold"
                    self.leaf_command_w = 0
                    self.tesla_command_w = 0
                elif proposed == "Self-use":
                    self.leaf_limit_reason = "None (native self-use)"
                    # Solis balances rapid load changes in hardware.
                    # Fronius follows its optimized HAEO Tesla baseload.
                    await self._dispatch("leaf", 0, forced=False)
                    await self._dispatch(
                        "tesla", tesla, force_write=self._current_mode != proposed
                    )
                    self._current_mode = proposed
                    self._safe_stop_attempted = False
                    self.status = (
                        f"Self-use: Solis hardware loop; Fronius tracks HAEO "
                        f"Tesla target {tesla:.0f} W; no feedback corrections"
                        + self._clamp_summary()
                    )
                else:
                    # Economic allocation belongs entirely to HAEO.
                    await self._dispatch("leaf", leaf)
                    await self._dispatch(
                        "tesla", tesla, force_write=self._current_mode != proposed
                    )
                    self._current_mode = proposed
                    self._safe_stop_attempted = False
                    self.status = (
                        f"Arbitrage: following HAEO Leaf {leaf:.0f} W / "
                        f"Tesla {tesla:.0f} W; no feedback corrections"
                        + self._clamp_summary()
                    )
            except Exception as exc:
                _LOGGER.warning("HAEO dispatch tick failed: %s", exc)
                self.mode = "Error"
                self.status = str(exc)
                self.tesla_write_status = f"Dispatch error: {exc}"
                if self.enabled:
                    await self._safe_stop("Dispatch invalid")
            finally:
                self.update()
