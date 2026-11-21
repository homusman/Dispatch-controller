# HAEO Dispatch Controller v0.2.12

Fixes dispatch interruptions caused by unavailable diagnostic-only grid/battery
power sensors. Valid HAEO targets continue to execute; missing telemetry is
reported as unknown with reasons in Dispatch status attributes.

- Measured grid, Solis and Fronius power mappings are optional and removable.
- No stale or zero substitutes are used for missing measured grid power.
- Dispatch status retains the last plan/actuator fault after recovery.
- Plan availability/freshness checks, fallback policy and actuator fault handling
  remain active. Upstream HAEO plan outages still trigger the configured fallback.

Update through HACS and restart Home Assistant. Dispatch starts disabled; inspect
Shadow mode and explicitly enable it when ready.
