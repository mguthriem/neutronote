"""
PV Log service – queries process-variable time-series from the
EPICS Channel Archiver (Oracle database).

Uses oracledb in thin mode (no Oracle client install required).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Oracle connection details (read-only reporting account)
# ---------------------------------------------------------------------------
ORACLE_DSN = "REDACTED_DSN"
ORACLE_USER = "REDACTED_USER"
ORACLE_PASS = "REDACTED"

# ---------------------------------------------------------------------------
# PV alias registry – friendly names → candidate PVs
# ---------------------------------------------------------------------------
# Each alias may define a "validity" dict with rules for filtering out
# traces that contain only junk data (e.g. unplugged pressure gauge
# reading negative bar).
#
# validity:
#   min_valid: float | None   – individual values < this are invalid
#   max_valid: float | None   – individual values > this are invalid
#
# A trace is KEPT if it has **any** valid points (device plugged in
# mid-IPTS is fine).  Invalid points within a kept trace are replaced
# with None so Plotly draws gaps.
# ---------------------------------------------------------------------------
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
            "min_valid": 0.0,   # pressures in bar must be positive
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
            "min_valid": 0.0,   # temperatures in K must be positive
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


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class PVTimeSeries:
    """Time-series data for a single process variable."""

    name: str  # Full PV name e.g. "BL3:SE:Teledyne:PressSet_RBV"
    alias: str | None = None  # Friendly name e.g. "Pressure"
    times: list[float] = field(default_factory=list)  # Unix epoch seconds
    values: list[Any] = field(default_factory=list)  # float, int, or str
    units: str = ""
    dtype: str = "float"  # "float", "int", "string", "array"

    @property
    def is_empty(self) -> bool:
        """True if there are no data points."""
        return len(self.times) == 0

    def to_plot_json(self) -> dict:
        """Return JSON-serialisable dict for Plotly traces."""
        return {
            "name": self.alias or self.name,
            "pv": self.name,
            "x": self.times,  # epoch seconds → JS will convert
            "y": self.values,
            "units": self.units,
            "dtype": self.dtype,
            "count": len(self.times),
        }


def _lttb_downsample(
    times: list[float], values: list, target: int
) -> tuple[list, list]:
    """Downsample using Largest-Triangle-Three-Buckets (LTTB).

    Preserves visual shape of the data far better than uniform sampling.
    Returns (times, values) of length *target*.
    """
    n = len(times)
    if n <= target or target < 3:
        return times, values

    out_t = [times[0]]
    out_v = [values[0]]

    bucket_size = (n - 2) / (target - 2)
    a_idx = 0  # index of the previously selected point

    for i in range(1, target - 1):
        bucket_start = int((i - 1) * bucket_size) + 1
        bucket_end = int(i * bucket_size) + 1
        next_start = int(i * bucket_size) + 1
        next_end = min(int((i + 1) * bucket_size) + 1, n)

        # Average of next bucket (the "C" point)
        avg_t = sum(times[next_start:next_end]) / max(1, next_end - next_start)
        avg_v_vals = [v for v in values[next_start:next_end] if v is not None]
        avg_v = sum(avg_v_vals) / max(1, len(avg_v_vals)) if avg_v_vals else 0

        # Find point in current bucket with max triangle area
        max_area = -1.0
        best_idx = bucket_start
        prev_v = out_v[-1] if out_v[-1] is not None else 0
        for j in range(bucket_start, min(bucket_end, n)):
            if values[j] is None:
                continue
            area = abs(
                (times[a_idx] - avg_t) * (values[j] - prev_v)
                - (times[a_idx] - times[j]) * (avg_v - prev_v)
            )
            if area > max_area:
                max_area = area
                best_idx = j

        out_t.append(times[best_idx])
        out_v.append(values[best_idx])
        a_idx = best_idx

    # Always include last point
    out_t.append(times[-1])
    out_v.append(values[-1])

    return out_t, out_v


def _apply_validity_filter(
    ts: PVTimeSeries, validity: dict | None
) -> tuple[PVTimeSeries | None, str]:
    """Apply validity rules to a PVTimeSeries.

    Invalid individual values are replaced with None (gaps in the chart).
    If the trace has ZERO valid values after filtering it is considered
    entirely invalid and ``None`` is returned (the trace should be skipped).

    For performance, the keep/skip decision is made by sampling every
    60th point (≈ 1 per minute for 1-Hz PVs).  If any sampled value is
    valid the trace is kept; invalid values in the full array are then
    replaced with None.

    Args:
        ts: The time-series to filter.
        validity: Dict with optional ``min_valid`` and ``max_valid`` keys.

    Returns:
        (filtered_ts_or_None, reason_string).
        reason is "" when the trace is kept.
    """
    if validity is None or ts.is_empty or ts.dtype == "string":
        return ts, ""

    lo = validity.get("min_valid")
    hi = validity.get("max_valid")

    if lo is None and hi is None:
        return ts, ""

    def _is_valid(v):
        """Check a single value against bounds."""
        if v is None:
            return False
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return False
        if lo is not None and fv < lo:
            return False
        if hi is not None and fv > hi:
            return False
        return True

    # --- Quick scan: sample every 60th point to decide keep/skip ---
    SAMPLE_STEP = 60
    has_any_valid = False
    for i in range(0, len(ts.values), SAMPLE_STEP):
        if _is_valid(ts.values[i]):
            has_any_valid = True
            break

    if not has_any_valid:
        bounds = []
        if lo is not None:
            bounds.append(f">= {lo}")
        if hi is not None:
            bounds.append(f"<= {hi}")
        reason = f"all values outside valid range ({', '.join(bounds)})"
        return None, reason

    # --- Trace has valid data: replace invalid values with None (gaps) ---
    new_values: list[Any] = []
    valid_count = 0
    for v in ts.values:
        if v is None:
            new_values.append(None)
        elif _is_valid(v):
            new_values.append(v)
            valid_count += 1
        else:
            new_values.append(None)

    ts.values = new_values
    logger.debug(
        "validity_filter(%s): %d/%d valid → kept",
        ts.name, valid_count, len(ts.values),
    )
    return ts, ""


# ---------------------------------------------------------------------------
# Service class (singleton, lazy connection pool)
# ---------------------------------------------------------------------------
class PVLogService:
    """Queries PV time-series from the EPICS Channel Archiver Oracle DB."""

    _instance: PVLogService | None = None
    _pool = None
    # Cache channel_id lookups: {pv_name: channel_id}
    _channel_cache: dict[str, int] = {}

    def __new__(cls) -> PVLogService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._channel_cache = {}
        return cls._instance

    # -- connection pool (lazy init) ----------------------------------------

    def _get_pool(self):
        """Create or return the oracledb connection pool (thin mode)."""
        if self._pool is None:
            import oracledb

            self._pool = oracledb.create_pool(
                user=ORACLE_USER,
                password=ORACLE_PASS,
                dsn=ORACLE_DSN,
                min=1,
                max=4,
                increment=1,
            )
            logger.info("Oracle connection pool created (%s)", ORACLE_DSN)
        return self._pool

    def _get_connection(self):
        """Acquire a connection from the pool."""
        return self._get_pool().acquire()

    def _release(self, conn):
        """Return a connection to the pool."""
        try:
            self._get_pool().release(conn)
        except Exception:
            pass

    # -- public API ---------------------------------------------------------

    def search_channels(self, pattern: str, limit: int = 50) -> list[str]:
        """Search for PV channel names matching a pattern.

        The pattern is wrapped with % for SQL LIKE matching if no
        wildcards are present.
        """
        if "%" not in pattern and "_" not in pattern:
            pattern = f"%{pattern}%"

        t0 = time.time()
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM chan_arch.channel "
                "WHERE UPPER(name) LIKE UPPER(:pat) "
                "FETCH FIRST :lim ROWS ONLY",
                {"pat": pattern, "lim": limit},
            )
            results = [row[0] for row in cur]
            logger.info(
                "search_channels(%r): %d results in %.2fs",
                pattern, len(results), time.time() - t0,
            )
            return results
        finally:
            self._release(conn)

    def get_channel_id(self, pv_name: str) -> int | None:
        """Look up the channel_id for an exact PV name (cached)."""
        if pv_name in self._channel_cache:
            return self._channel_cache[pv_name]

        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT channel_id FROM chan_arch.channel WHERE name = :n",
                {"n": pv_name},
            )
            row = cur.fetchone()
            if row:
                self._channel_cache[pv_name] = row[0]
                return row[0]
            return None
        finally:
            self._release(conn)

    def query_pv(
        self,
        pv_name: str,
        start: datetime | str,
        end: datetime | str,
        max_points: int = 5000,
    ) -> PVTimeSeries:
        """Query time-series data for a single PV in a time range.

        Args:
            pv_name: Exact PV name (e.g. "BL3:SE:Teledyne1:Pressure")
            start: Start of time range (datetime or ISO string)
            end: End of time range (datetime or ISO string)
            max_points: Downsample to this many points via LTTB.

        Returns:
            PVTimeSeries with times (epoch seconds) and values.

        Performance notes:
            - Always queries with ascending time order (indexed).
            - NEVER uses ORDER BY … DESC (190 s on this table).
            - Large results are streamed and downsampled in Python.
        """
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if isinstance(end, str):
            end = datetime.fromisoformat(end)

        cid = self.get_channel_id(pv_name)
        if cid is None:
            logger.warning("query_pv: PV %r not found in channel table", pv_name)
            return PVTimeSeries(name=pv_name)

        t0 = time.time()
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.arraysize = 10000  # fetch in large batches

            cur.execute(
                "SELECT smpl_time, float_val, num_val, str_val "
                "FROM chan_arch.sample "
                "WHERE channel_id = :cid "
                "AND smpl_time BETWEEN :t_start AND :t_end "
                "ORDER BY smpl_time",
                {"cid": cid, "t_start": start, "t_end": end},
            )

            times: list[float] = []
            values: list[Any] = []
            dtype = "float"

            for smpl_time, float_val, num_val, str_val in cur:
                epoch = smpl_time.replace(tzinfo=timezone.utc).timestamp()
                times.append(epoch)

                if float_val is not None:
                    v = float(float_val)
                    # NaN from IEEE → None (JSON has no NaN)
                    values.append(None if math.isnan(v) or math.isinf(v) else v)
                elif num_val is not None:
                    v = float(num_val)
                    values.append(None if math.isnan(v) or math.isinf(v) else v)
                    dtype = "int"
                elif str_val is not None:
                    values.append(str_val)
                    dtype = "string"
                else:
                    values.append(None)

            raw_count = len(times)
            elapsed = time.time() - t0
            logger.info(
                "query_pv(%s, %s→%s): %d samples in %.2fs",
                pv_name, start, end, raw_count, elapsed,
            )

            # Downsample if needed
            if len(times) > max_points and dtype != "string":
                times, values = _lttb_downsample(times, values, max_points)
                logger.info(
                    "query_pv(%s): LTTB %d → %d", pv_name, raw_count, len(times),
                )

            # Resolve alias name & units
            alias = None
            units = ""
            for a_info in SNAP_PV_ALIASES.values():
                if pv_name in a_info["pvs"]:
                    alias = a_info["label"]
                    units = a_info.get("units", "")
                    break

            return PVTimeSeries(
                name=pv_name,
                alias=alias,
                times=times,
                values=values,
                units=units,
                dtype=dtype,
            )
        finally:
            self._release(conn)

    def query_runs(
        self,
        start: datetime | str,
        end: datetime | str,
    ) -> list[dict]:
        """Query run-number transitions gated by acquisition state.

        Cross-references ``LastRunNumber`` with ``StateEnum`` to return
        only the time intervals where data was actually being collected
        (StateEnum == 3, "Run").

        Returns list of dicts, each:
            ``{run_number: int, start: float, end: float}``
        where start/end are epoch seconds.  The same run_number may
        appear **more than once** if acquisition was paused and resumed.
        """
        # 1. Fetch run-number transitions
        run_ts = self.query_pv(
            "BL3:CS:RunControl:LastRunNumber", start, end, max_points=50000,
        )
        # 2. Fetch state-enum transitions
        state_ts = self.query_pv(
            "BL3:CS:RunControl:StateEnum", start, end, max_points=50000,
        )

        if run_ts.is_empty:
            return []

        # --- Build run-number intervals (old-style, as a starting point) ---
        # Each interval: from the moment the run number appears until the
        # next run number (or end of range).
        if isinstance(end, str):
            end = datetime.fromisoformat(end)
        end_epoch = (
            end.replace(tzinfo=timezone.utc).timestamp()
            if hasattr(end, "timestamp")
            else float(end)
        )

        run_edges: list[dict] = []
        prev_run = None
        for t, v in zip(run_ts.times, run_ts.values):
            if v is not None and v != prev_run:
                try:
                    run_edges.append({"time": t, "run_number": int(v)})
                except (TypeError, ValueError):
                    pass
                prev_run = v

        if not run_edges:
            return []

        # Build raw run intervals
        raw_runs: list[dict] = []
        for i, edge in enumerate(run_edges):
            run_end = run_edges[i + 1]["time"] if i + 1 < len(run_edges) else end_epoch
            raw_runs.append({
                "run_number": edge["run_number"],
                "start": edge["time"],
                "end": run_end,
            })

        # If we have no state data, fall back to the raw intervals
        if state_ts.is_empty:
            logger.info("query_runs: no StateEnum data; returning raw run intervals")
            return raw_runs

        # --- Build "collecting" intervals (StateEnum == 3) ---
        STATE_RUN = 3
        collecting_intervals: list[tuple[float, float]] = []
        in_run = False
        run_start_t = 0.0
        prev_state = None

        for t, v in zip(state_ts.times, state_ts.values):
            if v is None:
                continue
            try:
                sv = int(v)
            except (TypeError, ValueError):
                continue

            if sv == STATE_RUN and prev_state != STATE_RUN:
                # Entered "Run" state
                in_run = True
                run_start_t = t
            elif sv != STATE_RUN and prev_state == STATE_RUN:
                # Left "Run" state
                if in_run:
                    collecting_intervals.append((run_start_t, t))
                in_run = False
            prev_state = sv

        # If still in "Run" state at end of query range
        if in_run:
            collecting_intervals.append((run_start_t, end_epoch))

        if not collecting_intervals:
            logger.info("query_runs: no StateEnum==3 intervals found")
            return []

        logger.info(
            "query_runs: %d raw run(s), %d collecting interval(s)",
            len(raw_runs), len(collecting_intervals),
        )

        # --- Intersect raw runs with collecting intervals ---
        # For each raw run interval, clip it to the collecting intervals.
        # This can produce multiple segments per run number.
        result: list[dict] = []
        for run in raw_runs:
            for coll_start, coll_end in collecting_intervals:
                # Intersection of [run.start, run.end) and [coll_start, coll_end)
                seg_start = max(run["start"], coll_start)
                seg_end = min(run["end"], coll_end)
                if seg_start < seg_end:
                    result.append({
                        "run_number": run["run_number"],
                        "start": seg_start,
                        "end": seg_end,
                    })

        logger.info(
            "query_runs: %d acquisition segment(s) after intersection",
            len(result),
        )
        return result

    def resolve_alias(
        self,
        alias: str,
        start: datetime | str,
        end: datetime | str,
        max_points: int = 5000,
    ) -> tuple[list[PVTimeSeries], list[dict]]:
        """Resolve a friendly alias to active PV time-series.

        Queries all candidate PVs for the alias and returns only
        those with >= 2 data points *and* at least some valid data
        (per the alias validity rules).

        Returns:
            (active_traces, skipped_info_list)
            where each skipped entry is {"pv": str, "reason": str}.
        """
        alias_lower = alias.lower()
        if alias_lower not in SNAP_PV_ALIASES:
            return [], []

        info = SNAP_PV_ALIASES[alias_lower]
        validity = info.get("validity")
        results = []
        skipped: list[dict] = []

        for pv in info["pvs"]:
            ts = self.query_pv(pv, start, end, max_points=max_points)
            if len(ts.times) < 2:
                logger.debug(
                    "resolve_alias(%s): skipped %s (%d points)",
                    alias, pv, len(ts.times),
                )
                skipped.append({"pv": pv, "reason": f"only {len(ts.times)} point(s)"})
                continue

            # Apply validity filter (e.g. pressure must be > 0)
            filtered, reason = _apply_validity_filter(ts, validity)
            if filtered is None:
                logger.info(
                    "resolve_alias(%s): skipped %s – %s",
                    alias, pv, reason,
                )
                skipped.append({"pv": pv, "reason": reason})
                continue

            # Use second-to-last colon component as the short label
            # e.g. "BL3:SE:Teledyne1:Pressure" → "Teledyne1"
            parts = pv.split(":")
            short = parts[-2] if len(parts) >= 2 else pv
            filtered.alias = short
            filtered.units = info.get("units", "")
            results.append(filtered)

        return results, skipped

    # -- utility ------------------------------------------------------------

    @staticmethod
    def list_aliases() -> dict[str, dict]:
        """Return the alias registry."""
        return SNAP_PV_ALIASES

    @staticmethod
    def is_alias(name: str) -> bool:
        """Check if a name is a known alias."""
        return name.lower() in SNAP_PV_ALIASES
