"""Execute HAEO power plans without a competing feedback controller."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from datetime import timedelta
from .mode import (
    DEFAULT_GRID_DEADBAND_W,
    DEFAULT_GRID_HYSTERESIS_W,
    nonnegative_watts,
    select_mode,
)

_LOGGER = logging.getLogger(__name__)
TICK_SECONDS = 20
PLAN_MAX_AGE_SECONDS = 900

class PlanInputError(ValueError):
    """HAEO input is invalid; eligible for the configured fallback."""


def clamp(value, low, high):
    return max(low, min(high, value))

class DispatchCoordinator:
    """Translate optimized setpoints to physical inverter commands."""

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry
        self.config = {**entry.data, **entry.options}
        self.grid_deadband_w = nonnegative_watts(
            self.config.get("grid_deadband_w", DEFAULT_GRID_DEADBAND_W)
        )
        self.grid_hysteresis_w = nonnegative_watts(
            self.config.get("grid_hysteresis_w", DEFAULT_GRID_HYSTERESIS_W)
        )
        self._selected_mode = None
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
        self._unsub_switches = None
        self._running = asyncio.Lock()
        self._listeners = []
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
        self._unsub_switches = async_track_state_change_event(
            self.hass,
            [self.config["leaf_switch"], self.config["tesla_switch"]],
            self._on_switch_changed,
        )

    async def stop(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_switches:
            self._unsub_switches()
            self._unsub_switches = None

    @callback
    def _on_switch_changed(self, _event):
        # The physical inverter mode can change outside the dispatcher.
        # Reconcile or update the shadow preview without waiting 20 seconds.
        self.hass.async_create_task(self.tick())

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
        self._selected_mode = select_mode(
            grid, leaf, tesla, self._selected_mode,
            self.grid_deadband_w, self.grid_hysteresis_w,
        )
        return self._selected_mode

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

    def _decision_summary(self, mode, leaf, tesla):
        """Concise plan/status shared by shadow and actual dispatch."""
        if mode == "Hold":
            self.leaf_limit_reason = "None"
            self.tesla_limit_reason = "None"
            self.tesla_effective_target_w = 0
            return "Hold | Leaf 0 W | Tesla 0 W"
        if mode == "Self-use":
            parts = ["Self-use", "Solis auto"]
            self.leaf_limit_reason = "None"
        else:
            parts = ["Arbitrage"]
            leaf_target = self._limits("leaf", leaf)
            parts.append(f"Leaf {leaf_target:+.0f} W")
        tesla_target = self._limits("tesla", tesla)
        parts.append(f"Tesla {tesla_target:+.0f} W")
        if mode == "Arbitrage" and self.leaf_limit_reason != "None":
            parts.append(
                f"CLAMP Leaf {leaf:+.0f}→{leaf_target:+.0f} W"
            )
        if self.tesla_limit_reason != "None":
            parts.append(
                f"CLAMP Tesla {tesla:+.0f}→{tesla_target:+.0f} W"
            )
        return " | ".join(parts)

    def _switch_summary(self, mode):
        """Compare requested and actual switch states in the status line."""
        expected = {
            "leaf": "on" if mode == "Self-use" else "off",
            "tesla": "off",
        }
        parts = []
        for which, label in (("leaf", "Solis"), ("tesla", "Fronius")):
            entity_id = self.config[which + "_switch"]
            state = self.hass.states.get(entity_id)
            actual = state.state if state else "missing"
            if actual not in ("on", "off"):
                parts.append(f"{label} switch {actual}")
            elif actual != expected[which]:
                parts.append(
                    f"{label} switch {actual} (expected {expected[which]})"
                )
        return (" | " + "; ".join(parts)) if parts else ""

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
        """Reconcile both batteries to forced 0 W on every stop tick.

        Retry failed commands after an actuator recovers rather than treating
        a previous service-call attempt as a completed safe stop.
        """
        self._selected_mode = None
        pending = []
        for which in ("leaf", "tesla"):
            num = self.config[which + "_number"]
            sw = self.config[which + "_switch"]
            number_state = self.hass.states.get(num)
            switch_state = self.hass.states.get(sw)
            try:
                # Unknown/unavailable state must never count as verified zero.
                number_zero = (
                    number_state is not None
                    and number_state.state not in ("unavailable", "unknown")
                    and math.isfinite(float(number_state.state))
                    and abs(float(number_state.state)) < 10
                )
                switch_off = switch_state is not None and switch_state.state == "off"

                if not number_zero:
                    await self._call("number", "set_value", num, value=0)
                if not switch_off:
                    await self._call("switch", "turn_off", sw)

                # HA state is only a proxy for actual inverter execution.
                # Verify physical battery power separately.
                if not (number_zero and switch_off):
                    pending.append(which)
            except Exception:
                pending.append(which)
                _LOGGER.exception("Failed requesting %s 0W hold; will retry", which)

        self.status = (
            reason + ("; 0 W hold awaiting actuator-state verification: "
                      + ", ".join(pending) if pending
                      else "; actuator states report 0 W hold")
        )
        self._current_mode = None

    async def _fallback(self, reason):
        """Apply the configured fallback when the HAEO plan is invalid.

        Fallback is attempted again on every tick. Failed actuator commands
        remain visible; an attempted command is never reported as verified.
        """
        self._selected_mode = None
        fallback_mode = self.config.get("fallback_mode", "hold")
        if fallback_mode != "self_use":
            await self._safe_stop(reason + " | fallback Hold")
            return

        pending = []
        for which in ("leaf", "tesla"):
            switch = self.config[which + "_switch"]
            state = self.hass.states.get(switch)
            try:
                if state is None or state.state != "on":
                    await self._call("switch", "turn_on", switch)
                if not self.hass.states.is_state(switch, "on"):
                    pending.append(which)
            except Exception:
                pending.append(which)
                _LOGGER.exception("Failed requesting %s self-use fallback; will retry", which)

        self.leaf_command_w = None
        self.tesla_command_w = None
        self._current_mode = None
        self.status = (
            reason + " | fallback Self-use"
            + (" | awaiting actuator-state verification: " + ", ".join(pending)
               if pending else " | both switches report ON")
        )

    async def tick(self, _now=None):
        if self._running.locked():
            return
        async with self._running:
            try:
                try:
                    grid, leaf, tesla, actual_leaf, _actual_tesla = self._read_inputs()
                except (ValueError, TypeError) as input_error:
                    raise PlanInputError(str(input_error)) from input_error
                proposed = self._select_mode(grid, leaf, tesla)
                summary = self._decision_summary(proposed, leaf, tesla)
                self.mode = proposed if self.enabled else "Disabled"
                if not self.enabled:
                    # Preview the intended commands, never imply they were sent.
                    self.leaf_command_w = (
                        None if proposed == "Self-use" else
                        (0 if proposed == "Hold" else self._limits("leaf", leaf))
                    )
                    self.tesla_command_w = (
                        0 if proposed == "Hold" else self._limits("tesla", tesla)
                    )
                    self.status = f"Shadow | {summary}{self._switch_summary(proposed)}"
                    self._current_mode = None
                    return

                if proposed == "Hold":
                    await self._safe_stop("HAEO planned battery hold")
                    self._current_mode = "Hold"
                    self.leaf_command_w = 0
                    self.tesla_command_w = 0
                elif proposed == "Self-use":
                    await self._dispatch("leaf", 0, forced=False)
                    await self._dispatch(
                        "tesla", tesla, force_write=self._current_mode != proposed
                    )
                    self._current_mode = proposed
                else:
                    await self._dispatch("leaf", leaf)
                    await self._dispatch(
                        "tesla", tesla, force_write=self._current_mode != proposed
                    )
                    self._current_mode = proposed

                # Report the same decision in either mode; extra details only
                # when they explain an actual actuator write/skip.
                if proposed != "Hold":
                    self.status = summary + self._switch_summary(proposed)
            except Exception as exc:
                self._selected_mode = None
                _LOGGER.warning("HAEO dispatch tick failed: %s", exc)
                self.mode = "Error"
                self.status = f"Error | {exc}"
                if self.enabled:
                    # Only bad HAEO inputs/plans use the configured fallback.
                    # Hardware command failures retain forced 0 W Hold.
                    # This avoids switching a battery to auto on a write error.
                    if isinstance(exc, PlanInputError):
                        await self._fallback(f"HAEO inputs invalid: {exc}")
                    else:
                        await self._safe_stop(f"Dispatch fault: {exc}")
                    self.status = f"Error | {self.status}"
            finally:
                self.update()

