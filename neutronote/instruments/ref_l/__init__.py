"""
REF_L instrument plugin for neutroNote.

REF_L (Liquids Reflectometer) is located on beamline BL4B at the SNS.
This module registers the instrument so the rest of the application can
resolve paths, PV names, and reduced-data layouts without hard-coding
REF_L-specific details.
"""

from __future__ import annotations

from pathlib import Path

from .. import InstrumentConfig, register_instrument


@register_instrument
class REFLConfig(InstrumentConfig):
    """Configuration for the REF_L instrument (BL4B, SNS)."""

    # --- Identity -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "REF_L"

    @property
    def beamline(self) -> str:
        return "BL4B"

    # --- NeXus filenames ----------------------------------------------------

    def nexus_filename(self, run_number: int) -> str:
        return f"REF_L_{run_number}.nxs.h5"

    def lite_nexus_filename(self, run_number: int) -> str:
        return f"REF_L_{run_number}.lite.nxs.h5"

    def run_number_from_filename(self, filename: str) -> int | None:
        """Extract run number from ``REF_L_<run>.<ext>`` filenames."""
        prefix = "REF_L_"
        if filename.startswith(prefix):
            try:
                # Strip prefix, then take everything before the first dot
                return int(filename[len(prefix):].split(".")[0])
            except (IndexError, ValueError):
                pass
        return None

    # --- Reduced data -------------------------------------------------------

    def reduced_data_root(self, ipts: str) -> Path:
        """Reduced data stored under ``<IPTS>/shared/autoreduce/``."""
        return self.data_root / ipts / "shared" / "autoreduce"

    # --- PV aliases ---------------------------------------------------------

    def pv_aliases(self) -> dict[str, dict]:
        from .pv_aliases import REF_L_PV_ALIASES

        return REF_L_PV_ALIASES

    # --- Optional hooks -----------------------------------------------------

    def default_x_label(self) -> str:
        return "Q (Å⁻¹)"
