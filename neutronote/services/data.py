"""
Data retrieval and reduction service.

Wraps snapwrap / mantid calls to load workspaces, reduce data, and extract arrays
suitable for plotting. Keep all mantid imports inside this module.

File paths
----------
- Full NeXus: /SNS/SNAP/<IPTS>/nexus/SNAP_<run>.nxs.h5
- Lite NeXus: /SNS/SNAP/<IPTS>/shared/lite/SNAP_<run>.lite.nxs.h5  (commonly used)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

# Default to Lite files for faster loading
SNAP_DATA_ROOT = Path("/SNS/SNAP")


def nexus_path(ipts: str, run_number: int, lite: bool = True) -> Path:
    """Return the path to a SNAP NeXus file."""
    if lite:
        return SNAP_DATA_ROOT / ipts / "shared" / "lite" / f"SNAP_{run_number}.lite.nxs.h5"
    return SNAP_DATA_ROOT / ipts / "nexus" / f"SNAP_{run_number}.nxs.h5"


def get_reduced_data(
    ipts: str,
    run_number: int,
    reduction_params: dict[str, Any] | None = None,
    lite: bool = True,
) -> dict[str, Any]:
    """
    Load and reduce neutron data for a run, returning plot-ready arrays.

    Parameters
    ----------
    ipts : str
        The IPTS identifier (e.g., "IPTS-12345").
    run_number : int
        The run number.
    reduction_params : dict, optional
        Optional parameters to pass to the reduction workflow.
    lite : bool
        If True (default), load from the Lite NeXus path.

    Returns
    -------
    dict
        Contains 'x', 'y' (and optionally 'z') numpy arrays, plus 'labels'.

    Notes
    -----
    This is a stub. Replace with actual mantid/snapwrap reduction, e.g.:

        from mantid.simpleapi import Load, Rebin
        from snapwrap.spectralTools import some_algorithm

        filepath = nexus_path(ipts, run_number, lite=lite)
        ws = Load(str(filepath))
        ws = Rebin(ws, Params="0.5,0.01,10")
        x = ws.readX(0)
        y = ws.readY(0)
        return {"x": x.tolist(), "y": y.tolist(), ...}
    """
    # --- STUB: generate synthetic data for development ---
    x = np.linspace(0.5, 10, 500)
    y = np.sin(2 * np.pi * x) * np.exp(-0.1 * x) + np.random.normal(0, 0.05, x.shape)

    return {
        "x": x.tolist(),
        "y": y.tolist(),
        "labels": {
            "x": "d-spacing (Å)",
            "y": "Intensity (arb. units)",
        },
        "meta": {
            "ipts": ipts,
            "run_number": run_number,
            "source_file": str(nexus_path(ipts, run_number, lite=lite)),
        },
    }
