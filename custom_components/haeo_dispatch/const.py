"""Constants for HAEO Dispatch."""
DOMAIN = "haeo_dispatch"
PLATFORMS = ["switch", "number", "select", "sensor"]
FIELD_NAMES = {
    "grid_plan": "HAEO grid active power (kW; +import)",
    "leaf_plan": "HAEO Leaf active power (kW; +discharge)",
    "tesla_plan": "HAEO Tesla active power (kW; +discharge)",
    "grid_actual": "Actual grid power sensor",
    "leaf_actual": "Actual Solis battery power sensor",
    "tesla_actual": "Actual Fronius battery power sensor",
    "leaf_soc": "Actual Leaf SoC sensor (%)",
    "tesla_soc": "Actual Tesla SoC sensor (%)",
    "leaf_number": "Solis forced battery power number",
    "tesla_number": "Fronius forced battery power number",
    "leaf_switch": "Solis Self-Use Mode switch",
    "tesla_switch": "Fronius Self-Use Mode switch",
}
GRID_PLAN = "grid_plan"
LEAF_PLAN = "leaf_plan"
TESLA_PLAN = "tesla_plan"
