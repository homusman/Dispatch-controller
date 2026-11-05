"""Translate HAEO current power plans into bounded inverter dispatch commands."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
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
    """Read the current HAEO setpoints and apply a bounded feedback adjustment."""

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        self.enabled = False  # An explicit action is required before any writes.
        self.max_adjust_w = 500
        self.gain = 0.30
        self.max_step_w = 500
        self.priority = "Leaf first"
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
        self._raw("leaf_soc")
        self._raw("tesla_soc")
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
        entity_id = self.config[which + "_number"]
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unavailable", "unknown"):
            raise ValueError(f"{which} actuator unavailable")
        low, high = state.attributes.get("min"), state.attributes.get("max")
        if low is None or high is None:
            raise ValueError(f"{which} actuator limits unavailable")
        target = clamp(target, float(low), float(high))
        _, soc = self._raw(which + "_soc")
        # Conservative interlock; actual BMS protections remain authoritative.
        if soc <= 10 and target > 0:
            target = 0
        if soc >= 95 and target < 0:
            target = 0
        return int(round(target / 10) * 10)

    def _add_feedback(self, leaf, tesla, correction):
        if self.priority == "Tesla first":
            order = ["tesla", "leaf"]
        elif self.priority == "SoC headroom":
            leaf_soc = self._raw("leaf_soc")[1]
            tesla_soc = self._raw("tesla_soc")[1]
            if correction >= 0:
                order = ["leaf", "tesla"] if leaf_soc >= tesla_soc else ["tesla", "leaf"]
            else:
                order = ["leaf", "tesla"] if leaf_soc <= tesla_soc else ["tesla", "leaf"]
        else:
            order = ["leaf", "tesla"]
        targets = {"leaf": leaf, "tesla": tesla}
        remaining = correction
        for which in order:
            original = targets[which]
            candidate = self._limits(which, original + remaining)
            targets[which] = candidate
            remaining -= candidate - original
        return targets["leaf"], targets["tesla"]

    async def _call(self, domain, service, entity_id, **data):
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True
        )

    async def _dispatch(self, which, target, forced=True):
        number = self.config[which + "_number"]
        switch = self.config[which + "_switch"]
        state = self.hass.states.get(switch)
        if not state or state.state not in ("on", "off"):
            raise ValueError(f"{which} mode switch unavailable")
        if not forced:
            if state.state != "on":
                await self._call("switch", "turn_on", switch)
            return
        target = self._limits(which, target)
        if state.state == "on":
            # Stage a safe 0W before disabling hardware self-use, then ramp.
            await self._call("number", "set_value", number, value=0)
            await self._call("switch", "turn_off", switch)
            previous = 0
        else:
            previous = self.leaf_command_w if which == "leaf" else self.tesla_command_w
            if previous is None:
                previous = self._raw_power_state(number)
        adjusted = clamp(target, previous - self.max_step_w, previous + self.max_step_w)
        adjusted = int(round(adjusted / 10) * 10)
        if abs(adjusted - self._raw_power_state(number)) >= 10:
            await self._call("number", "set_value", number, value=adjusted)
        if which == "leaf":
            self.leaf_command_w = adjusted
        else:
            self.tesla_command_w = adjusted

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
                self.mode = proposed if self.enabled else "Disabled"
                if not self.enabled:
                    self.status = f"Shadow: {proposed}; no inverter writes"
                    self._current_mode = None
                    self._safe_stop_attempted = False
                    return

                if proposed == "Hold":
                    # Send the hold only on transition, not every 20 seconds.
                    if self._current_mode != "Hold":
                        self._safe_stop_attempted = False
                    await self._safe_stop("HAEO planned battery hold")
                    self._current_mode = "Hold"
                    self.leaf_command_w = 0
                    self.tesla_command_w = 0
                elif proposed == "Self-use":
                    self._safe_stop_attempted = False
                    # Solis responds to load instantly. Fronius baseload follows
                    # Tesla's HAEO target, corrected only for residual GRID error.
                    # Positive grid error (+actual import vs HAEO target)
                    # means increase Fronius discharge / reduce charging.
                    # Do not chase Solis power: it can deviate intentionally.
                    correction = clamp(
                        self.gain * self.grid_error_w,
                        -self.max_adjust_w, self.max_adjust_w
                    )
                    target = self._limits("tesla", tesla + correction)
                    await self._dispatch("leaf", 0, forced=False)
                    await self._dispatch("tesla", target)
                    self._current_mode = proposed
                    self.status = (
                        f"Self-use: Solis native; Fronius follows {tesla:.0f}W "
                        f"Tesla plan; grid correction {correction:+.0f}W"
                    )
                else:
                    self._safe_stop_attempted = False
                    correction = clamp(
                        self.gain * self.grid_error_w,
                        -self.max_adjust_w, self.max_adjust_w
                    )
                    target_leaf, target_tesla = self._add_feedback(
                        leaf, tesla, correction
                    )
                    await self._dispatch("leaf", target_leaf)
                    await self._dispatch("tesla", target_tesla)
                    self._current_mode = proposed
                    self.status = (
                        f"Arbitrage: HAEO plans {leaf:.0f}/{tesla:.0f}W; "
                        f"grid correction {correction:+.0f}W; priority {self.priority}"
                    )
            except Exception as exc:
                _LOGGER.warning("HAEO dispatch tick failed: %s", exc)
                self.mode = "Error"
                self.status = str(exc)
                if self.enabled:
                    await self._safe_stop("Dispatch invalid")
            finally:
                self.update()
