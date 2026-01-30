"""
Metadata retrieval service.

Wraps snapwrap / mantid calls to fetch experiment info (IPTS, run number, sample, etc.).
Keep all mantid imports inside this module to avoid import errors in environments
without mantid installed.

Import style for snapwrap sub-modules:
    from snapwrap.spectralTools import ...
    from snapwrap.SEEMeta import ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Optional h5py import – gracefully degrade if not available
try:
    import h5py

    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False


@dataclass
class RunMetadata:
    """Structured metadata for a single neutron run."""

    run_number: int
    title: str = ""
    start_time: str = ""
    end_time: str = ""
    duration: float = 0.0  # seconds
    total_counts: int = 0
    file_size_bytes: int = 0
    file_path: str = ""
    error: str | None = None

    # Additional fields (can be extended)
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def file_size_display(self) -> str:
        """Human-readable file size."""
        size = self.file_size_bytes
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"

    @property
    def duration_display(self) -> str:
        """Human-readable duration."""
        sec = self.duration
        if sec < 60:
            return f"{sec:.0f} sec"
        elif sec < 3600:
            return f"{sec / 60:.1f} min"
        else:
            return f"{sec / 3600:.1f} hours"

    @property
    def count_rate_display(self) -> str:
        """Count rate in ME/s (million events per second)."""
        if self.duration > 0:
            rate = self.total_counts / self.duration / 1e6
            return f"{rate:.3f} ME/s"
        return "N/A"

    @property
    def start_time_formatted(self) -> str:
        """Format start time for display."""
        return self._format_timestamp(self.start_time)

    @property
    def end_time_formatted(self) -> str:
        """Format end time for display."""
        return self._format_timestamp(self.end_time)

    def _format_timestamp(self, ts: str) -> str:
        """Parse and format an ISO timestamp."""
        if not ts:
            return "N/A"
        # Handle byte strings
        if isinstance(ts, bytes):
            ts = ts.decode("utf-8")
        # Strip timezone suffix if present (e.g., "-05:00:00")
        # Format: "2026-01-30T10:00:00-05:00:00"
        try:
            # Try parsing just the date and time portion
            dt_str = ts[:19]  # "YYYY-MM-DDTHH:MM:SS"
            dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, IndexError):
            return ts

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization or template rendering."""
        return {
            "run_number": self.run_number,
            "title": self.title,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "start_time_formatted": self.start_time_formatted,
            "end_time_formatted": self.end_time_formatted,
            "duration": self.duration,
            "duration_display": self.duration_display,
            "total_counts": self.total_counts,
            "count_rate_display": self.count_rate_display,
            "file_size_bytes": self.file_size_bytes,
            "file_size_display": self.file_size_display,
            "file_path": self.file_path,
            "error": self.error,
            **self.extras,
        }


def find_nexus_file(run_number: int, ipts: str | None = None) -> Path | None:
    """
    Locate the NeXus file for a given run number.

    Parameters
    ----------
    run_number : int
        The SNAP run number.
    ipts : str, optional
        The IPTS folder name (e.g., "IPTS-12345"). If provided, searches
        only that IPTS folder first for faster lookup.

    Search order (prefers native files for complete metadata):
    1. Full NeXus: /SNS/SNAP/<IPTS>/nexus/SNAP_<run>.nxs.h5
    2. Lite NeXus: /SNS/SNAP/<IPTS>/shared/lite/SNAP_<run>.lite.nxs.h5 (fallback)

    Uses finddata CLI if available, otherwise scans known IPTS folders.
    """
    base = Path("/SNS/SNAP")

    # If IPTS is specified, check that folder first
    if ipts:
        ipts_dir = base / ipts
        if ipts_dir.exists():
            # Try native first (has complete metadata)
            native_path = ipts_dir / "nexus" / f"SNAP_{run_number}.nxs.h5"
            if native_path.exists():
                return native_path
            # Then lite as fallback
            lite_path = ipts_dir / "shared" / "lite" / f"SNAP_{run_number}.lite.nxs.h5"
            if lite_path.exists():
                return lite_path

    # Try using finddata CLI (same approach as stateFromRun.py)
    try:
        from finddata import cli

        record = cli.getFileLoc("SNS", "SNAP", [run_number])
        native_path = Path(record["location"])
        if native_path.exists():
            return native_path
    except Exception:
        pass

    # Fallback: scan common IPTS locations
    if not base.exists():
        return None

    # Look through IPTS folders
    for ipts_dir in sorted(base.glob("IPTS-*"), reverse=True):
        # Try native first (has complete metadata)
        native_path = ipts_dir / "nexus" / f"SNAP_{run_number}.nxs.h5"
        if native_path.exists():
            return native_path
        # Then lite as fallback
        lite_path = ipts_dir / "shared" / "lite" / f"SNAP_{run_number}.lite.nxs.h5"
        if lite_path.exists():
            return lite_path

    return None


def get_run_metadata_from_file(file_path: str | Path) -> RunMetadata:
    """
    Extract metadata directly from a NeXus HDF5 file.

    Parameters
    ----------
    file_path : str or Path
        Path to the .nxs.h5 file.

    Returns
    -------
    RunMetadata
        Populated metadata object.
    """
    path = Path(file_path)

    if not path.exists():
        return RunMetadata(run_number=0, error=f"File not found: {file_path}")

    if not HAS_H5PY:
        return RunMetadata(run_number=0, error="h5py not installed")

    # Extract run number from filename (SNAP_12345.nxs.h5 or SNAP_12345.lite.nxs.h5)
    name = path.name
    run_number = 0
    if name.startswith("SNAP_"):
        try:
            run_number = int(name.split("_")[1].split(".")[0])
        except (IndexError, ValueError):
            pass

    try:
        with h5py.File(path, "r") as f:
            # Helper to safely read HDF5 datasets
            # NeXus files store values as 1-element numpy arrays
            def read_value(key: str, default=None):
                try:
                    ds = f.get(key)
                    if ds is None:
                        return default
                    # Get the raw value
                    raw = ds[()]
                    # Handle numpy arrays (common in NeXus: shape (1,))
                    if hasattr(raw, "__len__") and len(raw) > 0:
                        v = raw[0]
                    else:
                        v = raw
                    # Decode bytes to string
                    if isinstance(v, bytes):
                        return v.decode("utf-8")
                    # Convert numpy types to Python types
                    if hasattr(v, "item"):
                        return v.item()
                    return v
                except Exception:
                    pass
                return default

            title = read_value("entry/title", "")
            start_time = read_value("entry/start_time", "")
            end_time = read_value("entry/end_time", "")
            duration = read_value("entry/duration", 0.0)
            total_counts = read_value("entry/total_counts", 0)

            file_size = path.stat().st_size

            return RunMetadata(
                run_number=run_number,
                title=title,
                start_time=start_time,
                end_time=end_time,
                duration=float(duration),
                total_counts=int(total_counts),
                file_size_bytes=file_size,
                file_path=str(path),
            )

    except Exception as e:
        return RunMetadata(run_number=run_number, error=f"Error reading file: {e}")


def get_run_metadata(run_number: int, ipts: str | None = None) -> RunMetadata:
    """
    Retrieve metadata for a given run number.

    Automatically locates the NeXus file (preferring lite over native).

    Parameters
    ----------
    run_number : int
        The SNAP run number.
    ipts : str, optional
        The IPTS folder name (e.g., "IPTS-12345"). If provided, searches
        that IPTS folder first for faster lookup.

    Returns
    -------
    RunMetadata
        Metadata object with run information or error details.
    """
    file_path = find_nexus_file(run_number, ipts=ipts)

    if file_path is None:
        if ipts:
            return RunMetadata(
                run_number=run_number,
                error=f"Could not locate file for run {run_number} in {ipts}",
            )
        return RunMetadata(
            run_number=run_number, error=f"Could not locate file for run {run_number}"
        )

    return get_run_metadata_from_file(file_path)


# Legacy function for backwards compatibility
def get_run_metadata_legacy(ipts: str, run_number: int) -> dict[str, Any]:
    """
    Retrieve metadata for a given IPTS and run number.

    DEPRECATED: Use get_run_metadata(run_number) instead.
    """
    meta = get_run_metadata(run_number)
    result = meta.to_dict()
    result["ipts"] = ipts
    return result
