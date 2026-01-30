"""
Data retrieval and reduction service.

Wraps snapwrap / mantid calls to load workspaces, reduce data, and extract arrays
suitable for plotting. Keep all mantid imports inside this module.

File paths
----------
- Full NeXus: /SNS/SNAP/<IPTS>/nexus/SNAP_<run>.nxs.h5
- Lite NeXus: /SNS/SNAP/<IPTS>/shared/lite/SNAP_<run>.lite.nxs.h5  (commonly used)
- Reduced (SNAPRed): /SNS/SNAP/<IPTS>/shared/SNAPRed/<stateID>/lite/<run>/<timestamp>/
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

# Default to Lite files for faster loading
SNAP_DATA_ROOT = Path("/SNS/SNAP")


# =============================================================================
# Data classes for reduced data discovery
# =============================================================================


@dataclass
class ReducedRun:
    """Represents a single reduced run with its file paths and metadata."""

    run_number: int
    state_id: str
    timestamp: str  # Format: YYYY-MM-DDTHHMMSS
    reduced_file: Path
    record_file: Path | None = None
    pixelmask_file: Path | None = None
    
    # Run metadata (from native NeXus file)
    title: str = ""
    duration: float = 0.0  # seconds
    start_time: str = ""

    @property
    def timestamp_datetime(self) -> datetime | None:
        """Parse timestamp to datetime object."""
        try:
            # Format: YYYY-MM-DDTHHMMSS -> 2025-05-08T162147
            return datetime.strptime(self.timestamp, "%Y-%m-%dT%H%M%S")
        except ValueError:
            return None

    @property
    def timestamp_display(self) -> str:
        """Human-readable timestamp."""
        dt = self.timestamp_datetime
        if dt:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return self.timestamp

    @property
    def duration_display(self) -> str:
        """Human-readable duration."""
        sec = self.duration
        if sec < 60:
            return f"{sec:.0f} sec"
        elif sec < 3600:
            return f"{sec / 60:.1f} min"
        else:
            return f"{sec / 3600:.1f} hr"

    @property
    def start_time_display(self) -> str:
        """Format start time for display."""
        if not self.start_time:
            return "N/A"
        # Handle byte strings
        ts = self.start_time
        if isinstance(ts, bytes):
            ts = ts.decode("utf-8")
        try:
            # Parse ISO format and reformat
            dt_str = ts[:19]  # "YYYY-MM-DDTHH:MM:SS"
            dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, IndexError):
            return ts

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_number": self.run_number,
            "state_id": self.state_id,
            "timestamp": self.timestamp,
            "timestamp_display": self.timestamp_display,
            "reduced_file": str(self.reduced_file),
            "record_file": str(self.record_file) if self.record_file else None,
            "pixelmask_file": str(self.pixelmask_file) if self.pixelmask_file else None,
            "title": self.title,
            "duration": self.duration,
            "duration_display": self.duration_display,
            "start_time": self.start_time,
            "start_time_display": self.start_time_display,
        }


@dataclass
class StateInfo:
    """Information about an instrument state and its reduced runs."""

    state_id: str
    reduced_runs: list[ReducedRun] = field(default_factory=list)

    @property
    def run_count(self) -> int:
        return len(self.reduced_runs)

    @property
    def run_numbers(self) -> list[int]:
        return sorted(r.run_number for r in self.reduced_runs)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "state_id": self.state_id,
            "run_count": self.run_count,
            "run_numbers": self.run_numbers,
            "reduced_runs": [r.to_dict() for r in self.reduced_runs],
        }


# =============================================================================
# Run metadata from NeXus files
# =============================================================================

# Optional h5py import – gracefully degrade if not available
try:
    import h5py
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False


def get_metadata_from_reduced_file(reduced_file: Path) -> dict[str, Any]:
    """
    Extract run metadata (title, duration, start_time) from a reduced NeXus file.
    
    The reduced file stores metadata in mantid_workspace_1/logs/.
    
    Parameters
    ----------
    reduced_file : Path
        Path to the reduced .nxs file.
    
    Returns
    -------
    dict
        Contains 'title', 'duration', 'start_time' keys.
    """
    if not HAS_H5PY or not reduced_file.exists():
        return {"title": "", "duration": 0.0, "start_time": ""}
    
    try:
        with h5py.File(reduced_file, "r") as f:
            def read_log_value(log_name, default=None):
                """Read a value from mantid_workspace_1/logs/<name>/value."""
                try:
                    path = f"mantid_workspace_1/logs/{log_name}/value"
                    ds = f.get(path)
                    if ds is None:
                        return default
                    raw = ds[()]
                    if hasattr(raw, "__len__") and len(raw) > 0:
                        v = raw[0] if raw.ndim == 1 else raw
                    else:
                        v = raw
                    if isinstance(v, bytes):
                        return v.decode("utf-8")
                    if hasattr(v, "item"):
                        return v.item()
                    return v
                except Exception:
                    return default
            
            # Also check the title dataset directly
            def read_title():
                try:
                    ds = f.get("mantid_workspace_1/title")
                    if ds is not None:
                        val = ds[()]
                        if hasattr(val, "__len__") and len(val) > 0:
                            val = val[0]
                        if isinstance(val, bytes):
                            return val.decode("utf-8")
                        return str(val)
                except Exception:
                    pass
                return read_log_value("run_title", "")
            
            return {
                "title": read_title() or "",
                "duration": float(read_log_value("duration", 0.0) or 0.0),
                "start_time": read_log_value("start_time", "") or "",
            }
    except Exception:
        return {"title": "", "duration": 0.0, "start_time": ""}


def get_run_metadata_quick(ipts: str, run_number: int) -> dict[str, Any]:
    """
    Quickly fetch basic metadata (title, duration, start_time) for a run.
    
    Reads from the native NeXus file which contains the full metadata.
    
    Parameters
    ----------
    ipts : str
        The IPTS identifier.
    run_number : int
        The run number.
    
    Returns
    -------
    dict
        Contains 'title', 'duration', 'start_time' keys.
    """
    if not HAS_H5PY:
        return {"title": "", "duration": 0.0, "start_time": ""}
    
    # Try native file first (has complete metadata)
    native_path = SNAP_DATA_ROOT / ipts / "nexus" / f"SNAP_{run_number}.nxs.h5"
    
    if not native_path.exists():
        return {"title": "", "duration": 0.0, "start_time": ""}
    
    try:
        with h5py.File(native_path, "r") as f:
            def read_value(key, default=None):
                try:
                    ds = f.get(key)
                    if ds is None:
                        return default
                    raw = ds[()]
                    if hasattr(raw, "__len__") and len(raw) > 0:
                        v = raw[0]
                    else:
                        v = raw
                    if isinstance(v, bytes):
                        return v.decode("utf-8")
                    if hasattr(v, "item"):
                        return v.item()
                    return v
                except Exception:
                    return default
            
            return {
                "title": read_value("entry/title", "") or "",
                "duration": float(read_value("entry/duration", 0.0) or 0.0),
                "start_time": read_value("entry/start_time", "") or "",
            }
    except Exception:
        return {"title": "", "duration": 0.0, "start_time": ""}


# =============================================================================
# Reduced data discovery functions
# =============================================================================


def get_snapred_root(ipts: str) -> Path:
    """Get the SNAPRed output folder for an IPTS."""
    return SNAP_DATA_ROOT / ipts / "shared" / "SNAPRed"


def discover_state_ids(ipts: str) -> list[str]:
    """
    Discover all state IDs with reduced data in an IPTS.

    State IDs are 16-character hash strings representing instrument configurations.

    Parameters
    ----------
    ipts : str
        The IPTS identifier (e.g., "IPTS-12345").

    Returns
    -------
    list[str]
        List of state ID strings found in the SNAPRed folder.
    """
    snapred_root = get_snapred_root(ipts)

    if not snapred_root.exists():
        return []

    state_ids = []
    # State IDs are 16-character hex strings
    state_pattern = re.compile(r"^[a-f0-9]{16}$", re.IGNORECASE)

    for item in snapred_root.iterdir():
        if item.is_dir() and state_pattern.match(item.name):
            state_ids.append(item.name)

    return sorted(state_ids)


def discover_reduced_runs(
    ipts: str,
    state_id: str,
    lite: bool = True,
    latest_only: bool = True,
) -> list[ReducedRun]:
    """
    Discover reduced runs for a given state ID.

    Parameters
    ----------
    ipts : str
        The IPTS identifier (e.g., "IPTS-12345").
    state_id : str
        The state ID (16-character hash).
    lite : bool
        If True, look in the 'lite' subfolder (default). Otherwise 'native'.
    latest_only : bool
        If True (default), return only the latest reduction for each run number.
        If False, return all timestamped reductions.

    Returns
    -------
    list[ReducedRun]
        List of ReducedRun objects, sorted by run number.
    """
    mode = "lite" if lite else "native"
    state_root = get_snapred_root(ipts) / state_id / mode

    if not state_root.exists():
        return []

    # Collect all reductions, keyed by run number
    runs_by_number: dict[int, list[ReducedRun]] = {}

    # Pattern for timestamp folders: YYYY-MM-DDTHHMMSS
    timestamp_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}$")

    for run_dir in state_root.iterdir():
        if not run_dir.is_dir():
            continue

        # Run folder should be numeric
        try:
            run_number = int(run_dir.name)
        except ValueError:
            continue

        # Look for timestamp folders inside the run folder
        for ts_dir in run_dir.iterdir():
            if not ts_dir.is_dir():
                continue
            if not timestamp_pattern.match(ts_dir.name):
                continue

            timestamp = ts_dir.name

            # Look for the reduced file: reduced_<run.zfill(6)>_<timestamp>.nxs
            reduced_pattern = f"reduced_{run_number:06d}_{timestamp}.nxs"
            reduced_file = ts_dir / reduced_pattern

            if not reduced_file.exists():
                # Try glob in case naming varies slightly
                reduced_files = list(ts_dir.glob("reduced_*.nxs"))
                if reduced_files:
                    reduced_file = reduced_files[0]
                else:
                    continue  # No reduced file found

            # Optional files
            record_file = ts_dir / "ReductionRecord.json"
            pixelmask_pattern = f"pixelmask_{run_number:06d}_{timestamp}.h5"
            pixelmask_file = ts_dir / pixelmask_pattern

            # Extract metadata from the reduced file
            metadata = get_metadata_from_reduced_file(reduced_file)

            reduced_run = ReducedRun(
                run_number=run_number,
                state_id=state_id,
                timestamp=timestamp,
                reduced_file=reduced_file,
                record_file=record_file if record_file.exists() else None,
                pixelmask_file=pixelmask_file if pixelmask_file.exists() else None,
                title=metadata.get("title"),
                duration=metadata.get("duration"),
                start_time=metadata.get("start_time"),
            )

            if run_number not in runs_by_number:
                runs_by_number[run_number] = []
            runs_by_number[run_number].append(reduced_run)

    # Process results
    result: list[ReducedRun] = []

    for run_number in sorted(runs_by_number.keys()):
        reductions = runs_by_number[run_number]

        if latest_only and len(reductions) > 1:
            # Sort by timestamp descending and take the latest
            reductions.sort(key=lambda r: r.timestamp, reverse=True)
            result.append(reductions[0])
        else:
            result.extend(sorted(reductions, key=lambda r: r.timestamp))

    return result


def discover_all_reduced_data(ipts: str, lite: bool = True) -> list[StateInfo]:
    """
    Discover all reduced data in an IPTS, organized by state.

    Parameters
    ----------
    ipts : str
        The IPTS identifier (e.g., "IPTS-12345").
    lite : bool
        If True (default), look in 'lite' subfolders.

    Returns
    -------
    list[StateInfo]
        List of StateInfo objects, one per state ID found.
    """
    state_ids = discover_state_ids(ipts)
    states = []

    for state_id in state_ids:
        runs = discover_reduced_runs(ipts, state_id, lite=lite, latest_only=True)
        if runs:  # Only include states with actual reduced data
            states.append(StateInfo(state_id=state_id, reduced_runs=runs))

    return states


def get_state_id_for_run(run_number: int) -> str | None:
    """
    Get the state ID for a run number using SNAPWrap's stateDef.

    Parameters
    ----------
    run_number : int
        The run number.

    Returns
    -------
    str or None
        The state ID string, or None if it cannot be determined.
    """
    try:
        from snapwrap.snapStateMgr import stateDef

        result = stateDef(run_number)
        if result and len(result) > 0:
            return result[0]
    except ImportError:
        pass
    except Exception:
        pass

    return None


# =============================================================================
# Original functions (for raw/unreduced data)
# =============================================================================


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
