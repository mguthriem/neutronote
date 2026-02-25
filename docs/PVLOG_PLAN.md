# PV Log Entry Type – Design & Implementation Plan

## Overview

Add a **PV Log** entry type that lets users browse, retrieve, and plot
process-variable (PV) time-series from the EPICS Channel Archiver database
alongside neutron run information on a shared timeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (Plotly.js)                                            │
│  Multi-axis time-series chart: PVs (left/right Y) + run bars   │
└─────────────────┬───────────────────────────────────────────────┘
                  │ JSON (time[], value[], meta)
                  │
┌─────────────────▼───────────────────────────────────────────────┐
│  Flask API endpoints (routes/entries.py)                         │
│  GET  /api/pvlog/query?pv=...&start=...&end=...                 │
│  GET  /api/pvlog/search?pattern=...                             │
│  GET  /api/pvlog/aliases                                        │
│  GET  /api/pvlog/runs?start=...&end=...                         │
│  POST /api/create/pvlog                                         │
└─────────────────┬───────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────────┐
│  services/pvlog.py                                               │
│  PVLogService  (Oracle connection pool, query, type dispatch)    │
│  AliasRegistry (YAML/dict of friendly names → PV patterns)      │
└─────────────────┬───────────────────────────────────────────────┘
                  │ SQL (oracledb)
                  │
┌─────────────────▼───────────────────────────────────────────────┐
│  Oracle: snsoroda-scan.sns.gov:1521/scprod_controls              │
│  Tables: chan_arch.channel, chan_arch.sample                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phased Implementation

### Phase PV-0: Date range configuration & model changes

**Goal**: Let users set experiment start/end dates; store them in
`NotebookConfig`. These become the default time range for PV queries.

| Task | Details |
|------|---------|
| Add `start_date`, `end_date` to `NotebookConfig` model | `db.Column(db.DateTime)` |
| Update IPTS setup form in `index.html` | Date pickers for experiment start/end |
| Default time window | 08:00 local on start date → 08:00 local on end date |
| Add `Entry.TYPE_PVLOG = "pvlog"` constant | New entry type |
| Add PVLog tab to entry-type tabs | Disabled until IPTS + dates configured |

**Checkpoint PV-0**
```bash
# Configure IPTS with dates
# Verify dates are stored and displayed
pixi run test
```

---

### Phase PV-1: Oracle service layer (`services/pvlog.py`)

**Goal**: Reliable service to query PV data from the EPICS archiver.

| Task | Details |
|------|---------|
| Add `oracledb` to pixi dependencies | `pip` or `conda-forge` |
| Create `services/pvlog.py` | Connection pool, query functions |
| `PVLogService` class | Singleton with lazy Oracle connection pool |
| `search_channels(pattern)` | `SELECT name FROM chan_arch.channel WHERE name LIKE :pat` |
| `get_channel_id(pv_name)` → `int` | Lookup channel_id by exact name |
| `query_pv(pv, start, end)` → `PVTimeSeries` | Main query: returns time + value arrays |
| Handle value types | `num_val`, `float_val`, `str_val`, `array_val` dispatch |
| `PVTimeSeries` dataclass | `name, times[], values[], units, dtype` |
| `query_runs(start, end)` | Query `BL3:CS:RunControl:LastRunNumber` for run boundaries |
| Connection error handling | Graceful fallback if Oracle unreachable |

#### Key data class

```python
@dataclass
class PVTimeSeries:
    """Time-series data for a single PV."""
    name: str                    # Full PV name e.g. "BL3:SE:Teledyne:PressSet_RBV"
    alias: str | None = None     # Friendly name e.g. "Pressure"
    times: list[float] = field(default_factory=list)   # Unix epoch ms
    values: list[Any] = field(default_factory=list)     # float, int, or str
    units: str = ""
    dtype: str = "float"         # "float", "int", "string", "array"
    is_empty: bool = True        # True if no non-null values in range

    def to_plot_json(self) -> dict:
        """Return JSON-serialisable dict for Plotly."""
        ...
```

#### SQL query pattern (from test.py, generalised)

```sql
SELECT smpl_time, datatype, num_val, float_val, str_val, array_val
FROM chan_arch.sample
WHERE channel_id = :cid
  AND smpl_time BETWEEN :t_start AND :t_end
ORDER BY smpl_time ASC
```

**Checkpoint PV-1**
```bash
pixi run python -c "
from neutronote.services.pvlog import PVLogService
svc = PVLogService()
ts = svc.query_pv('BL3:CS:ITEMS', '2025-06-01', '2025-06-02')
print(f'{ts.name}: {len(ts.times)} samples, dtype={ts.dtype}')
"
```

---

### Phase PV-2: Alias registry

**Goal**: Map user-friendly names ("pressure", "temperature") to lists
of candidate PVs; auto-discover which are active in the time range.

| Task | Details |
|------|---------|
| Create `SNAP_PV_ALIASES` dict in `pvlog.py` | Curated per-instrument mapping |
| `resolve_alias(alias, start, end)` → `list[PVTimeSeries]` | Query all candidates, filter empty |
| Allow user-defined aliases | Store in `NotebookConfig` or separate JSON file in IPTS folder |

#### Initial alias table (SNAP / BL3)

```python
SNAP_PV_ALIASES = {
    "pressure": {
        "label": "Pressure",
        "units": "bar",
        "pvs": [
            "BL3:SE:Teledyne:PressSet_RBV",
            "BL3:SE:Teledyne:PressRead",
            "BL3:SE:GP1:Pressure",
            "BL3:SE:GP2:Pressure",
        ],
    },
    "temperature": {
        "label": "Temperature",
        "units": "K",
        "pvs": [
            "BL3:SE:LS336:TC1:RBV",
            "BL3:SE:LS336:TC2:RBV",
            "BL3:SE:LS340:Input1",
            "BL3:SE:LS340:Input2",
            "BL3:SE:CryoCon:In1",
            "BL3:SE:CryoCon:In2",
        ],
    },
    "run_number": {
        "label": "Run Number",
        "units": "",
        "pvs": [
            "BL3:CS:RunControl:LastRunNumber",
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
```

> **Note**: These PV names need to be verified/expanded with instrument
> scientists. The alias dict should be easy to edit – keep it in a dedicated
> section of `pvlog.py` or a YAML file.

**Checkpoint PV-2**
```bash
pixi run python -c "
from neutronote.services.pvlog import PVLogService
svc = PVLogService()
results = svc.resolve_alias('pressure', '2025-06-01', '2025-06-15')
for ts in results:
    status = 'ACTIVE' if not ts.is_empty else 'empty'
    print(f'  {ts.name}: {status} ({len(ts.times)} pts)')
"
```

---

### Phase PV-3: API endpoints

**Goal**: REST endpoints for the frontend to search/query/plot PV data.

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/pvlog/aliases` | GET | List of alias names with labels and units |
| `/api/pvlog/search?pattern=BL3:SE:%` | GET | Matching PV channel names |
| `/api/pvlog/query` | GET | Time-series JSON for one or more PVs |
| `/api/pvlog/runs` | GET | Run-number time-series for annotation |
| `/api/create/pvlog` | POST | Create a PVLog entry in timeline |

#### `/api/pvlog/query` parameters

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `pv` | string (repeatable) | required | PV name or alias |
| `start` | ISO datetime | Config start_date 08:00 | |
| `end` | ISO datetime | Config end_date 08:00 | |
| `max_points` | int | 5000 | Downsample if more points |

#### Response shape

```json
{
  "series": [
    {
      "name": "BL3:SE:Teledyne:PressSet_RBV",
      "alias": "Pressure",
      "units": "bar",
      "dtype": "float",
      "times": [1717200000000, ...],
      "values": [1.013, ...],
      "num_points": 4823
    }
  ],
  "runs": [
    {"run_number": 65890, "start": 1717200000000, "end": 1717203600000},
    {"run_number": 65891, "start": 1717203600000, "end": 1717207200000}
  ],
  "time_range": {
    "start": "2025-06-01T08:00:00-04:00",
    "end": "2025-06-15T08:00:00-04:00"
  }
}
```

**Checkpoint PV-3**
```bash
curl -s "http://127.0.0.1:5000/entries/api/pvlog/aliases" | python -m json.tool
curl -s "http://127.0.0.1:5000/entries/api/pvlog/query?pv=pressure&start=2025-06-01&end=2025-06-15"
```

---

### Phase PV-4: UI – PVLog tab & interactive plot

**Goal**: Full browser interface for querying and visualising PV data.

#### Left panel (PVLog tab)

```
┌──────────────────────────────────────┐
│  PV Log                              │
│  ─────                               │
│  Time Range:                         │
│  [2025-06-01 08:00] → [2025-06-15]  │
│                                      │
│  Quick aliases:                      │
│  [Pressure ▼] [Temperature ▼]       │
│  [+ Add custom PV]                   │
│                                      │
│  Selected PVs:              Axis     │
│  ┌────────────────────────┬──────┐   │
│  │ Pressure (Teledyne)    │ Left │ ✕ │
│  │ Temperature (LS336)    │Right │ ✕ │
│  └────────────────────────┴──────┘   │
│                                      │
│  ☑ Show run numbers                  │
│  ☑ Auto-hide empty PVs              │
│                                      │
│  [🔍 Query & Plot]                   │
│  [📝 Add to Timeline]               │
└──────────────────────────────────────┘
```

#### Plot (modal, like data viewer)

- **Plotly.js** multi-axis chart
- Shared X-axis: time (with timezone display)
- Left Y-axis: first unit group (e.g. bar)
- Right Y-axis: second unit group (e.g. K)
- Additional axes for more unit groups (stacked or offset)
- **Run annotations**: vertical shaded bands or tick marks on X-axis
  labelled with run number
- Zoom/pan/hover with Plotly built-in tools
- Legend toggle per trace

#### Timeline card (`_pvlog_content.html`)

```
┌──────────────────────────────────────────────────────┐
│  📊 PV Log  ·  66j  ·  Feb 4, 3:42 PM              │
│  Pressure, Temperature  ·  Jun 1–15 2025            │
│  ┌──────────────────────────────────────────────┐    │
│  │  [Snapshot image of the plot]                │    │
│  └──────────────────────────────────────────────┘    │
│  Note: Checking pressure stability during run 65890  │
└──────────────────────────────────────────────────────┘
```

**Checkpoint PV-4**
```bash
# Select "PV Log" tab
# Pick "pressure" alias → active PVs discovered
# Click "Query & Plot" → multi-axis chart with run annotations
# Click "Add to Timeline" → snapshot saved
```

---

## Data model for PVLog entries

The `Entry.body` stores JSON:

```json
{
  "pvs": [
    {"name": "BL3:SE:Teledyne:PressSet_RBV", "alias": "Pressure", "axis": "left"},
    {"name": "BL3:SE:LS336:TC1:RBV", "alias": "Temperature", "axis": "right"}
  ],
  "time_range": {
    "start": "2025-06-01T08:00:00-04:00",
    "end": "2025-06-15T08:00:00-04:00"
  },
  "show_runs": true,
  "snapshot": "snapshot_abc123.png",
  "note": "Pressure stable at 2.1 bar during run 65890"
}
```

---

## Dependencies to add

| Package | Source | Purpose |
|---------|--------|---------|
| `oracledb` | pip (pypi) | Oracle DB driver (thin mode, no client needed) |

> `oracledb` in **thin mode** requires no Oracle client install – just
> `pip install oracledb`. This is much simpler than the old `cx_Oracle`.

---

## TODO (deferred)

- [ ] **Auto-port selection**: Query existing ports, pick next available,
  print URL for user. (Multi-user convenience.)
- [ ] **PV alias editor UI**: Let users add/edit aliases from the browser.
- [ ] **PV data caching**: Cache recent queries to avoid repeated Oracle hits.
- [ ] **Downsampling**: Largest-triangle-three-buckets (LTTB) for large
  time ranges with millions of samples.
- [ ] **Array-valued PVs**: Handle PVs that return waveform arrays
  (e.g. detector images). Display as heatmap or table.
- [ ] **PV search with autocomplete**: Type-ahead search against
  `chan_arch.channel` table.

---

## Implementation order summary

```
PV-0  Model + date config           (~ 1 session)
PV-1  Oracle service layer          (~ 1–2 sessions)
PV-2  Alias registry                (~ 0.5 session)
PV-3  API endpoints                 (~ 1 session)
PV-4  UI + Plotly multi-axis plot   (~ 2 sessions)
```

Total estimate: **5–7 working sessions**

---

*Reference: `test.py` in `/SNS/users/66j/code/scanView/` for Oracle
connection pattern, channel lookup, and value extraction.*
