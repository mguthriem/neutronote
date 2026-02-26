"""
REF_L PV alias registry.

Maps friendly alias names to candidate EPICS PV names on beamline BL4B.
"""

REF_L_PV_ALIASES: dict[str, dict] = {
    "temperature": {
        "label": "Temperature",
        "units": "K",
        "pvs": [
            "BL4B:SE:Lakeshore:KRDG0",
            "BL4B:SE:Lakeshore:KRDG2",
        ],
        "validity": {
            "min_valid": 0.0,
            "max_valid": None,
        },
    },
    "run_number": {
        "label": "Run Number",
        "units": "",
        "pvs": [
            "BL4B:CS:RunControl:LastRunNumber",
        ],
    },
    "run_state": {
        "label": "Run State",
        "units": "",
        "pvs": [
            "BL4B:CS:RunControl:StateEnum",
        ],
    },
    "items": {
        "label": "ITEMS Proposal",
        "units": "",
        "pvs": [
            "BL4B:CS:ITEMS",
        ],
    },
}
