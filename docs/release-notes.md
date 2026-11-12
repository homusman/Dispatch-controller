# Configurable grid deadband and hysteresis

The controller's Configure page now exposes two grid mode settings in watts.

- **Grid deadband** defaults to **100 W**. Arbitrage returns to Self-use when the absolute planned grid target reaches this value or lower.
- **Grid hysteresis** defaults to **100 W**. From Self-use, Hold, or startup, Arbitrage requires an absolute planned grid target greater than deadband plus hysteresis. At the defaults, the entry threshold is 200 W.

Both import and export use these thresholds. Setting hysteresis to zero gives a single switching threshold. Existing entries receive the defaults automatically. Hold still applies when both planned battery powers are within 80 W of zero.

Update the HAEO Dispatch Controller in HACS, restart Home Assistant, and open **Settings → Devices & services → HAEO Dispatch Controller → Configure** to adjust the values. Configuration reloads the controller into its disabled state. Check its shadow preview and enable dispatch when ready.
