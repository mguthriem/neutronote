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
        """Extract run number from REF_L filenames.

        Supports multiple patterns:
        - Raw data: ``REF_L_<run>.nxs.h5``
        - Reduced data: ``REFL_<run>_combined_data_auto.txt``
          (note: no underscore between REF and L)
        """
        # Try raw data pattern first: REF_L_<run>.<ext>
        if filename.startswith("REF_L_"):
            try:
                # Strip prefix, then take everything before the first dot
                return int(filename[6:].split(".")[0])  # len("REF_L_") = 6
            except (IndexError, ValueError):
                pass

        # Try reduced data pattern: REFL_<run>_*
        if filename.startswith("REFL_"):
            try:
                # Strip "REFL_" prefix, take digits before next non-digit
                run_str = filename[5:]  # len("REFL_") = 5
                # Extract leading digits
                run_number = ""
                for char in run_str:
                    if char.isdigit():
                        run_number += char
                    else:
                        break
                if run_number:
                    return int(run_number)
            except (IndexError, ValueError):
                pass

        return None

    # --- Reduced data -------------------------------------------------------

    def reduced_data_root(self, ipts: str) -> Path:
        """Reduced data stored under ``<IPTS>/shared/autoreduce/``."""
        return self.data_root / ipts / "shared" / "autoreduce"

    def reduced_file_extensions(self) -> list[str]:
        """REF_L reduced data uses .txt files (pattern: REFL_<run>_combined_data_auto.txt).

        The .nxs files in the autoreduce folder are companion files for processing,
        not the actual reduced data that users want to browse.
        """
        return [".txt"]

    # --- PV aliases ---------------------------------------------------------

    def pv_aliases(self) -> dict[str, dict]:
        from .pv_aliases import REF_L_PV_ALIASES

        return REF_L_PV_ALIASES

    # --- Optional hooks -----------------------------------------------------

    def enabled_entry_types(self) -> list[str]:
        """REF_L does not have a state-based reduced data browser yet."""
        return ["text", "header", "image", "code", "pvlog"]

    def default_x_label(self) -> str:
        return "Q (Å⁻¹)"
