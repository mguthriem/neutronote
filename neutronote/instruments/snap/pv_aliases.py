"""
SNAP PV alias registry.

Maps friendly alias names to candidate EPICS PV names on beamline BL3.
Moved here from ``services/pvlog.py`` so it can be loaded via the
instrument plugin interface.
"""

SNAP_PV_ALIASES: dict[str, dict] = {
    "pressure": {
        "label": "Pressure",
        "units": "bar",
        "pvs": [
            "BL3:SE:Teledyne1:Pressure",
            "BL3:SE:Teledyne2:PressSet",
            "BL3:SE:PACE1:Pressure",
        ],
        "validity": {
            "min_valid": 0.0,  # pressures in bar must be positive
            "max_valid": None,
        },
    },
    "temperature": {
        "label": "Temperature",
        "units": "K",
        "pvs": [
            "BL3:SE:Lakeshore:KRDG0",
            "BL3:SE:Lakeshore:KRDG2",
        ],
        "validity": {
            "min_valid": 0.0,  # temperatures in K must be positive
            "max_valid": None,
        },
    },
    "run_number": {
        "label": "Run Number",
        "units": "",
        "pvs": [
            "BL3:CS:RunControl:LastRunNumber",
        ],
    },
    "run_state": {
        "label": "Run State",
        "units": "",
        "pvs": [
            "BL3:CS:RunControl:StateEnum",
        ],
    },
    "items": {
        "label": "ITEMS Proposal",
        "units": "",
        "pvs": [
            "BL3:CS:ITEMS",
        ],
    },
}
