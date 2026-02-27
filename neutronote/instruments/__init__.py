"""
Instrument plugin system for neutroNote.

Each supported instrument provides an InstrumentConfig subclass that
describes file-path conventions, PV aliases, reduced-data layout, and
any instrument-specific helpers.

Register a new instrument by subclassing InstrumentConfig and calling
``register_instrument(MyInstrument)`` (typically in the submodule's
``__init__.py``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class InstrumentConfig(ABC):
    """Interface every instrument plugin must implement."""

    # --- Identity -----------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Short uppercase instrument name, e.g. ``'SNAP'``."""

    @property
    @abstractmethod
    def beamline(self) -> str:
        """Beamline identifier used in EPICS PV prefixes, e.g. ``'BL3'``."""

    @property
    def facility(self) -> str:
        """Facility name.  Defaults to ``'SNS'``."""
        return "SNS"

    # --- File paths ---------------------------------------------------------

    @property
    def data_root(self) -> Path:
        """Root data directory, e.g. ``/SNS/SNAP``."""
        return Path(f"/{self.facility}/{self.name}")

    @abstractmethod
    def nexus_filename(self, run_number: int) -> str:
        """Full NeXus filename, e.g. ``'SNAP_65432.nxs.h5'``."""

    @abstractmethod
    def lite_nexus_filename(self, run_number: int) -> str:
        """Lite NeXus filename, e.g. ``'SNAP_65432.lite.nxs.h5'``."""

    def nexus_path(self, ipts: str, run_number: int, lite: bool = True) -> Path:
        """Return the full path to a NeXus file for *run_number*."""
        if lite:
            return self.data_root / ipts / "shared" / "lite" / self.lite_nexus_filename(run_number)
        return self.data_root / ipts / "nexus" / self.nexus_filename(run_number)

    def ipts_path(self, ipts: str) -> Path:
        """Return the IPTS directory, e.g. ``/SNS/SNAP/IPTS-12345``."""
        return self.data_root / ipts

    def notebook_path(self, ipts: str) -> str:
        """Return the neutroNote storage directory for an IPTS."""
        return str(self.data_root / ipts / "shared" / "neutronote")

    def run_number_from_filename(self, filename: str) -> int | None:
        """Extract a run number from a NeXus filename.

        Default implementation looks for ``<NAME>_<run>.<ext>`` where
        ``<NAME>`` matches :pyattr:`name`.
        """
        prefix = f"{self.name}_"
        if filename.startswith(prefix):
            try:
                return int(filename.split("_")[1].split(".")[0])
            except (IndexError, ValueError):
                pass
        return None

    # --- Reduced data -------------------------------------------------------

    def reduced_data_root(self, ipts: str) -> Path | None:
        """Root directory for reduced data, or ``None`` if not applicable."""
        return None

    def reduced_file_extensions(self) -> list[str]:
        """File extensions for reduced data files (e.g., [".nxs", ".txt"]).

        Used to discover reduced data files in flat directory structures.
        Default is [".nxs"] for NeXus files.
        """
        return [".nxs"]

    # --- PV aliases ---------------------------------------------------------

    @abstractmethod
    def pv_aliases(self) -> dict[str, dict]:
        """Return the PV alias dictionary for this beamline.

        Each key is a lowercase alias (e.g. ``"pressure"``), and the
        value is a dict with at least ``label``, ``units``, and ``pvs``
        keys.
        """

    # --- Run-number / state PV names ----------------------------------------

    def run_number_pv(self) -> str:
        """PV that holds the latest run number."""
        return f"{self.beamline}:CS:RunControl:LastRunNumber"

    def run_state_pv(self) -> str:
        """PV that holds the DAQ state enum."""
        return f"{self.beamline}:CS:RunControl:StateEnum"

    # --- Optional hooks (override in subclass) ------------------------------

    def get_state_id_for_run(self, run_number: int) -> str | None:
        """Return an instrument-state hash for *run_number*, or ``None``.

        SNAP uses this to map runs to 16-character state IDs via snapwrap.
        Other instruments may not have a state concept and should return None.
        If None, reduced data discovery will look for flat file structures or
        use user-configured paths.
        """
        return None

    def default_x_label(self) -> str:
        """Default x-axis label for reduced data plots."""
        return "x"

    def finddata_args(self) -> tuple[str, str]:
        """Arguments for the ``finddata`` CLI: ``(facility, instrument)``."""
        return self.facility, self.name


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[InstrumentConfig]] = {}


def register_instrument(cls: type[InstrumentConfig]) -> type[InstrumentConfig]:
    """Register an InstrumentConfig subclass by its ``name``.

    Can be used as a decorator::

        @register_instrument
        class MyInstrument(InstrumentConfig):
            ...
    """
    # Instantiate temporarily to read the name property
    instance = cls()
    _REGISTRY[instance.name.upper()] = cls
    return cls


def get_instrument(name: str) -> InstrumentConfig:
    """Return an instance of the registered instrument config for *name*.

    Raises ``ValueError`` if the instrument is unknown.
    """
    cls = _REGISTRY.get(name.upper())
    if cls is None:
        raise ValueError(
            f"Unknown instrument: {name!r}. " f"Available: {', '.join(sorted(_REGISTRY))}"
        )
    return cls()


def available_instruments() -> list[str]:
    """Return a sorted list of registered instrument names."""
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# Auto-discover built-in instruments
# ---------------------------------------------------------------------------
def _auto_discover():
    """Import built-in instrument sub-packages so they self-register."""
    from . import ref_l  # noqa: F401
    from . import snap  # noqa: F401


_auto_discover()
