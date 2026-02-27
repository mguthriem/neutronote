"""
Data retrieval and reduction service.

Wraps mantid calls to load workspaces, reduce data, and extract arrays
suitable for plotting. Keep all mantid imports inside this module.

All instrument-specific path conventions are resolved via the active
``InstrumentConfig`` (see ``neutronote.instruments``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


def _get_instrument():
    """Return the active InstrumentConfig.

    Tries Flask's current_app first; falls back to the default instrument.
    """
    try:
        from flask import current_app

        return current_app.config["INSTRUMENT"]
    except (RuntimeError, KeyError):
        from ..instruments import get_instrument
        import os

        return get_instrument(os.environ.get("NEUTRONOTE_INSTRUMENT", "SNAP"))


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
    instrument = _get_instrument()
    native_path = instrument.data_root / ipts / "nexus" / instrument.nexus_filename(run_number)

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


def get_reduced_data_root(ipts: str) -> Path | None:
    """Get the reduced data output folder for an IPTS.

    Priority order:
    1. User-configured path in NotebookConfig.reduced_data_path
    2. Environment variable NEUTRONOTE_REDUCED_DATA_PATH (with {ipts} substitution)
    3. Instrument default from InstrumentConfig.reduced_data_root(ipts)

    Parameters
    ----------
    ipts : str
        The IPTS identifier (e.g., "IPTS-12345").

    Returns
    -------
    Path or None
        The reduced data root directory, or None if not available.
    """
    # 1. Try to get configured path from notebook config (highest priority)
    try:
        from flask import current_app
        from ..models import NotebookConfig

        with current_app.app_context():
            config = NotebookConfig.get_config()
            if config.has_reduced_data_path:
                return Path(config.reduced_data_path)
    except (RuntimeError, ImportError):
        pass

    # 2. Try environment variable with {ipts} substitution
    import os

    env_path = os.environ.get("NEUTRONOTE_REDUCED_DATA_PATH")
    if env_path:
        # Substitute {ipts} placeholder with actual IPTS value
        resolved_path = env_path.replace("{ipts}", ipts)
        return Path(resolved_path)

    # 3. Fall back to instrument config default
    instrument = _get_instrument()
    return instrument.reduced_data_root(ipts)


def discover_state_ids(ipts: str) -> list[str]:
    """
    Discover all state IDs with reduced data in an IPTS.

    For SNAP-like instruments: State IDs are 16-character hash strings.
    For other instruments: Returns subdirectories in the reduced data folder.
    If no subdirectories exist, returns ["_flat"] to indicate flat structure.

    Parameters
    ----------
    ipts : str
        The IPTS identifier (e.g., "IPTS-12345").

    Returns
    -------
    list[str]
        List of state ID strings or subdirectory names found.
        Returns ["_flat"] if reduced data is stored flat (no subdirectories).
    """
    reduced_root = get_reduced_data_root(ipts)

    if not reduced_root or not reduced_root.exists():
        return []

    # First, check if there are data files directly in the root
    # This indicates a flat structure (e.g., REF_L stores REFL_*.txt in root)
    instrument = _get_instrument()
    extensions = instrument.reduced_file_extensions()
    has_root_data_files = False

    for ext in extensions:
        pattern = f"*{ext}"
        if any(reduced_root.glob(pattern)):
            has_root_data_files = True
            break

    # If data files exist in root, it's a flat structure (even if subdirs exist)
    if has_root_data_files:
        return ["_flat"]

    # State IDs are 16-character hex strings (SNAP convention)
    state_pattern = re.compile(r"^[a-f0-9]{16}$", re.IGNORECASE)

    state_ids = []
    has_subdirs = False

    for item in reduced_root.iterdir():
        if item.is_dir():
            has_subdirs = True
            # Check if it's a SNAP-style state hash or just a regular subdirectory
            if state_pattern.match(item.name):
                state_ids.append(item.name)
            else:
                # For non-SNAP instruments, include any subdirectory
                state_ids.append(item.name)

    # If no subdirectories found, indicate flat structure with "_flat"
    # This allows instruments that store reduced data directly in the root
    if not has_subdirs:
        return ["_flat"]

    return sorted(state_ids)


def _discover_reduced_runs_flat(
    reduced_root: Path,
    ipts: str,
    lite: bool = True,
    latest_only: bool = True,
) -> list[ReducedRun]:
    """
    Discover reduced runs in a flat structure (no state subdirectories).

    Looks for NeXus files directly in the reduced data root.
    Common pattern: REF_L_<run>.nxs or similar.

    Parameters
    ----------
    reduced_root : Path
        The root directory containing reduced files.
    ipts : str
        The IPTS identifier.
    lite : bool
        Ignored for flat structures.
    latest_only : bool
        Ignored for flat structures (typically only one version per run).

    Returns
    -------
    list[ReducedRun]
        List of ReducedRun objects, sorted by run number.
    """
    if not reduced_root.exists():
        return []

    runs: list[ReducedRun] = {}  # Use dict to handle potential duplicates

    # Get instrument-specific file extensions (e.g., .nxs, .txt)
    instrument = _get_instrument()
    extensions = instrument.reduced_file_extensions()

    # Look for files with specified extensions in the root
    for ext in extensions:
        # Use glob pattern like "*.nxs" or "*.txt"
        pattern = f"*{ext}"
        for data_file in reduced_root.glob(pattern):
            # Try to extract run number from filename
            run_number = instrument.run_number_from_filename(data_file.name)

            if run_number is not None:
                # Use run number as key to handle duplicates
                # (keep newest if multiple files for same run)
                if run_number not in runs or (
                    data_file.stat().st_mtime > runs[run_number].reduced_file.stat().st_mtime
                ):
                    runs[run_number] = ReducedRun(
                        run_number=run_number,
                        state_id="_flat",  # No state concept for flat structures
                        timestamp="",  # No timestamp in flat structures
                        reduced_file=data_file,
                        record_file=None,
                        pixelmask_file=None,
                    )

    return sorted(runs.values(), key=lambda r: r.run_number)


def discover_reduced_runs(
    ipts: str,
    state_id: str,
    lite: bool = True,
    latest_only: bool = True,
) -> list[ReducedRun]:
    """
    Discover reduced runs for a given state ID.

    Supports both SNAP-style structured layout and flat reduced data folders.

    Parameters
    ----------
    ipts : str
        The IPTS identifier (e.g., "IPTS-12345").
    state_id : str
        The state ID (16-character hash for SNAP, subdirectory name for others,
        or "_flat" for flat structure).
    lite : bool
        If True, look in the 'lite' subfolder (default). Otherwise 'native'.
        Only applies to SNAP-style layouts.
    latest_only : bool
        If True (default), return only the latest reduction for each run number.
        If False, return all timestamped reductions.

    Returns
    -------
    list[ReducedRun]
        List of ReducedRun objects, sorted by run number.
    """
    reduced_root = get_reduced_data_root(ipts)
    if reduced_root is None:
        return []

    # Handle flat structure (state_id == "_flat")
    if state_id == "_flat":
        return _discover_reduced_runs_flat(reduced_root, ipts, lite=lite, latest_only=latest_only)

    # Handle SNAP-style structured layout
    mode = "lite" if lite else "native"
    state_root = reduced_root / state_id / mode

    if not state_root.exists():
        # Try without mode subdirectory (some instruments don't use lite/native)
        state_root = reduced_root / state_id
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

            # NOTE: We no longer load metadata here for performance!
            # Metadata is loaded lazily via get_run_metadata_lazy()

            reduced_run = ReducedRun(
                run_number=run_number,
                state_id=state_id,
                timestamp=timestamp,
                reduced_file=reduced_file,
                record_file=record_file if record_file.exists() else None,
                pixelmask_file=pixelmask_file if pixelmask_file.exists() else None,
                # Metadata fields left at defaults - loaded lazily
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


def get_run_metadata_lazy(reduced_file: Path | str) -> dict[str, Any]:
    """
    Fetch metadata for a single reduced run (called lazily from API).

    This is the function to call when you need metadata for display,
    after the initial run list has been loaded without metadata.

    Parameters
    ----------
    reduced_file : Path or str
        Path to the reduced .nxs file.

    Returns
    -------
    dict
        Contains 'title', 'duration', 'start_time' keys.
    """
    return get_metadata_from_reduced_file(Path(reduced_file))


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
    Get the state ID for a run number using the instrument plugin.

    Parameters
    ----------
    run_number : int
        The run number.

    Returns
    -------
    str or None
        The state ID string, or None if it cannot be determined.
    """
    instrument = _get_instrument()
    return instrument.get_state_id_for_run(run_number)


# =============================================================================
# Original functions (for raw/unreduced data)
# =============================================================================


def nexus_path(ipts: str, run_number: int, lite: bool = True) -> Path:
    """Return the path to a NeXus file for the active instrument."""
    instrument = _get_instrument()
    return instrument.nexus_path(ipts, run_number, lite=lite)


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


# =============================================================================
# Load reduced data with Mantid
# =============================================================================

# Optional mantid import - gracefully degrade if not available
try:
    from mantid.simpleapi import LoadNexus, mtd
    from mantid.api import WorkspaceGroup

    HAS_MANTID = True
except ImportError:
    HAS_MANTID = False
    WorkspaceGroup = None  # type: ignore


def _sanitize_array_for_json(arr: list) -> list:
    """
    Replace NaN and Inf values with None for valid JSON serialization.

    Python's json.dumps converts NaN/Inf to JavaScript literals (NaN, Infinity)
    which are NOT valid JSON and cause JSON.parse() to fail in browsers.
    """
    import math

    return [None if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else v for v in arr]


def load_reduced_workspace(reduced_file: Path | str, workspace_name: str | None = None):
    """
    Load a reduced NeXus file into a Mantid workspace.

    Parameters
    ----------
    reduced_file : Path or str
        Full path to the reduced .nxs file.
    workspace_name : str, optional
        Name for the workspace. If None, derives from filename.

    Returns
    -------
    Workspace or WorkspaceGroup
        The loaded Mantid workspace (may be a group).

    Raises
    ------
    ImportError
        If mantid is not available.
    FileNotFoundError
        If the file doesn't exist.
    """
    if not HAS_MANTID:
        raise ImportError("Mantid is not available. Install mantid to load reduced data.")

    reduced_file = Path(reduced_file)
    if not reduced_file.exists():
        raise FileNotFoundError(f"Reduced file not found: {reduced_file}")

    if workspace_name is None:
        # Derive name from filename: reduced_065886_2025-05-08T162147.nxs -> run_065886
        workspace_name = f"run_{reduced_file.stem.split('_')[1]}"

    LoadNexus(Filename=str(reduced_file), OutputWorkspace=workspace_name)
    return mtd[workspace_name]


def _extract_single_workspace_data(ws) -> dict[str, Any]:
    """Extract x/y data from a single (non-group) workspace."""
    num_spectra = ws.getNumberHistograms()

    # Get axis labels if available
    instrument = _get_instrument()
    x_label = instrument.default_x_label()
    y_label = "Intensity"

    try:
        x_unit = ws.getAxis(0).getUnit()
        if x_unit:
            x_label = x_unit.caption()
            if x_unit.symbol():
                x_label += f" ({x_unit.symbol()})"
    except Exception:
        pass

    if num_spectra == 1:
        # Single spectrum - return simple x/y arrays
        x = ws.readX(0)
        y = ws.readY(0)
        e = ws.readE(0)

        # Handle histogram vs point data (x may be 1 longer than y)
        if len(x) == len(y) + 1:
            x = (x[:-1] + x[1:]) / 2  # Convert to bin centers

        result = {
            "type": "1d",
            "name": ws.name(),
            "x": _sanitize_array_for_json(x.tolist()),
            "y": _sanitize_array_for_json(y.tolist()),
            "labels": {"x": x_label, "y": y_label},
        }
        if e is not None and e.size > 0:
            result["errors"] = _sanitize_array_for_json(e.tolist())
        return result

    else:
        # Multiple spectra - return as 2D data for heatmap or multiple traces
        all_x = []
        all_y = []
        all_e = []

        for i in range(num_spectra):
            x = ws.readX(i)
            y = ws.readY(i)
            e = ws.readE(i)

            if len(x) == len(y) + 1:
                x = (x[:-1] + x[1:]) / 2

            all_x.append(_sanitize_array_for_json(x.tolist()))
            all_y.append(_sanitize_array_for_json(y.tolist()))
            if e.size > 0:
                all_e.append(_sanitize_array_for_json(e.tolist()))

        result = {
            "type": "2d",
            "name": ws.name(),
            "num_spectra": num_spectra,
            "x": all_x,
            "y": all_y,
            "labels": {"x": x_label, "y": y_label},
        }
        if all_e:
            result["errors"] = all_e
        return result


def extract_plot_data_from_workspace(workspace_name: str) -> dict[str, Any]:
    """
    Extract x/y data from a Mantid workspace for plotting.

    Parameters
    ----------
    workspace_name : str
        Name of the workspace in Mantid's ADS.

    Returns
    -------
    dict
        Contains plot data. For WorkspaceGroups, returns a dict with 'workspaces'
        list containing data for each workspace in the group.
        For single workspaces, returns 'x', 'y' arrays plus metadata.
    """
    if not HAS_MANTID:
        raise ImportError("Mantid is not available.")

    ws = mtd[workspace_name]

    # Handle WorkspaceGroup (SNAPRed produces these)
    if isinstance(ws, WorkspaceGroup):
        workspaces = []
        for i in range(ws.getNumberOfEntries()):
            sub_ws = ws.getItem(i)
            # Skip pixel mask workspaces (they have only 1 bin)
            if sub_ws.readX(0).size <= 1:
                continue
            workspaces.append(_extract_single_workspace_data(sub_ws))

        return {
            "type": "group",
            "name": ws.name(),
            "workspaces": workspaces,
            "count": len(workspaces),
        }
    else:
        return _extract_single_workspace_data(ws)


def _load_text_data_for_plot(text_file: Path) -> dict[str, Any]:
    """
    Load reduced data from a text file (e.g., REF_L format).

    REF_L text files have format:
    - Comment lines starting with #
    - Data columns: Q[1/Angstrom] R delta_R Precision

    Parameters
    ----------
    text_file : Path
        Path to the text file.

    Returns
    -------
    dict
        Plot data with 'x', 'y', 'errors', 'labels', 'type'.
    """
    if not text_file.exists():
        raise FileNotFoundError(f"Text file not found: {text_file}")

    # Parse the file
    q_values = []
    r_values = []
    errors = []

    with open(text_file, "r") as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            # Parse data line: Q R delta_R Precision
            parts = line.split()
            if len(parts) >= 3:
                try:
                    q = float(parts[0])
                    r = float(parts[1])
                    delta_r = float(parts[2])
                    q_values.append(q)
                    r_values.append(r)
                    errors.append(delta_r)
                except ValueError:
                    # Skip lines that can't be parsed as numbers
                    continue

    # Get instrument for label customization
    instrument = _get_instrument()
    try:
        x_label = instrument.default_x_label()
    except AttributeError:
        x_label = "Q (Å⁻¹)"

    return {
        "type": "single",
        "name": text_file.stem,
        "x": q_values,
        "y": r_values,
        "errors": errors,
        "labels": {
            "x": x_label,
            "y": "Reflectivity (R)",
            "title": f"Reduced Data: {text_file.name}",
        },
        "meta": {
            "source_file": str(text_file),
            "format": "text",
        },
    }


def load_reduced_data_for_plot(
    reduced_file: Path | str,
    workspace_name: str | None = None,
    workspace_index: int | str | None = None,
) -> dict[str, Any]:
    """
    Load a reduced file and extract plot data in one call.

    Supports both NeXus (.nxs) files and text (.txt) files.

    Parameters
    ----------
    reduced_file : Path or str
        Full path to the reduced file (.nxs or .txt).
    workspace_name : str, optional
        Name for the workspace. If None, derives from filename.
    workspace_index : int or str, optional
        For WorkspaceGroups, specify which workspace to return:
        - int: index into the group (0-based)
        - str: name substring to match (e.g., "dsp_all")
        - None: return all workspaces in the group

    Returns
    -------
    dict
        Plot data with 'x', 'y', 'labels', 'type', and optionally 'errors'.
        Also includes 'meta' with source file info.
    """
    reduced_file = Path(reduced_file)

    # Check file extension to determine how to load
    if reduced_file.suffix.lower() == ".txt":
        # Handle text files (e.g., REF_L reduced data)
        return _load_text_data_for_plot(reduced_file)

    # Handle NeXus files (default)
    if workspace_name is None:
        workspace_name = f"run_{reduced_file.stem.split('_')[1]}"

    # Load the workspace
    load_reduced_workspace(reduced_file, workspace_name)

    # Extract plot data
    plot_data = extract_plot_data_from_workspace(workspace_name)

    # If a specific workspace was requested from a group, extract it
    if workspace_index is not None and plot_data.get("type") == "group":
        workspaces = plot_data["workspaces"]
        if isinstance(workspace_index, int):
            if 0 <= workspace_index < len(workspaces):
                plot_data = workspaces[workspace_index]
            else:
                raise IndexError(f"Workspace index {workspace_index} out of range")
        elif isinstance(workspace_index, str):
            # Find by name substring
            for ws_data in workspaces:
                if workspace_index in ws_data.get("name", ""):
                    plot_data = ws_data
                    break
            else:
                raise ValueError(f"No workspace matching '{workspace_index}' found")

    # Add metadata
    plot_data["meta"] = {
        "source_file": str(reduced_file),
        "workspace_name": workspace_name,
    }

    return plot_data
