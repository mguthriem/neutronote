"""
SNAP instrument plugin for neutroNote.

SNAP (Spallation Neutrons and Pressure diffractometer) is located on
beamline BL3 at the SNS.  This module registers the instrument so the
rest of the application can resolve paths, PV names, and reduced-data
layouts without hard-coding SNAP-specific details.
"""

from __future__ import annotations

from pathlib import Path

from .. import InstrumentConfig, register_instrument


@register_instrument
class SNAPConfig(InstrumentConfig):
    """Configuration for the SNAP instrument (BL3, SNS)."""

    # --- Identity -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "SNAP"

    @property
    def beamline(self) -> str:
        return "BL3"

    # --- NeXus filenames ----------------------------------------------------

    def nexus_filename(self, run_number: int) -> str:
        return f"SNAP_{run_number}.nxs.h5"

    def lite_nexus_filename(self, run_number: int) -> str:
        return f"SNAP_{run_number}.lite.nxs.h5"

    # --- Reduced data -------------------------------------------------------

    def reduced_data_root(self, ipts: str) -> Path:
        """SNAPRed stores reduced data under ``<IPTS>/shared/SNAPRed/``."""
        return self.data_root / ipts / "shared" / "SNAPRed"

    # --- PV aliases ---------------------------------------------------------

    def pv_aliases(self) -> dict[str, dict]:
        from .pv_aliases import SNAP_PV_ALIASES

        return SNAP_PV_ALIASES

    # --- Optional hooks -----------------------------------------------------

    def get_state_id_for_run(self, run_number: int) -> str | None:
        """Use SNAPWrap's ``stateDef`` to look up the instrument state."""
        try:
            from snapwrap.snapStateMgr import stateDef

            result = stateDef(run_number)
            if result and len(result) > 0:
                return result[0]
        except (ImportError, Exception):
            pass
        return None

    def default_x_label(self) -> str:
        return "d-spacing (Å)"
