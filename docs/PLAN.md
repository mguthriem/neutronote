# Development Plan – neutroNote E-Lab Notebook

Below is a phased roadmap. Each phase ends with a **checkpoint** you can run to confirm everything works before moving on.

---

## Phase 0 – Skeleton & CI hygiene ✅
| Deliverable | Notes |
|-------------|-------|
| `pyproject.toml` | Pixi + pip installable, dev deps |
| `.gitignore` | Python, Flask, Pixi, IDE |
| `README.md` | Quick-start instructions |
| Minimal Flask app (`neutronote/app.py`) | Returns "Hello, neutroNote" |
| `tests/test_app.py` | One smoke test |

**Checkpoint 0**
```bash
pixi install && pixi run dev   # should serve on :5000
pixi run test                  # 1 test passes
```

---

## Phase 1 – Database models & simple text entries
| Deliverable | Notes |
|-------------|-------|
| SQLite via Flask-SQLAlchemy | `instance/neutronote.db` |
| Models: `Entry`, `Tag` | Entry has `type`, `body`, `created_at` (no user yet) |
| **Split-view layout** | Left: entry creation panel; Right: scrollable timeline |
| Entry-type selector | Buttons/tabs to choose Text, Header, Data, Code |
| `/entries` main view | Renders split layout |
| Create **Text** entry | Form on left, appears on right after submit |
| Basic Jinja templates | `base.html`, `entries/index.html` (split view) |

### UI: Split-view design

```
┌─────────────────────────────────────────────────────────────┐
│  neutroNote                                        [User ▾]  │
├────────────────────────┬────────────────────────────────────┤
│  CREATE ENTRY          │  TIMELINE (scrollable)             │
│  ───────────────────   │  ────────────────────────────────  │
│  [Text] [Header]       │  ┌──────────────────────────────┐  │
│  [Data] [Code]         │  │ 10:32 AM  Text entry         │  │
│                        │  │ Started calibration run...   │  │
│  ┌──────────────────┐  │  └──────────────────────────────┘  │
│  │                  │  │  ┌──────────────────────────────┐  │
│  │  [Text area or   │  │  │ 10:45 AM  Header #run-123    │  │
│  │   form fields]   │  │  │ IPTS-12345 | Sample: ...     │  │
│  │                  │  │  └──────────────────────────────┘  │
│  └──────────────────┘  │  ┌──────────────────────────────┐  │
│                        │  │ 11:02 AM  Data plot          │  │
│  [Submit Entry]        │  │ [Interactive Plotly chart]   │  │
│                        │  └──────────────────────────────┘  │
│                        │           ↓ older entries ↓        │
└────────────────────────┴────────────────────────────────────┘
```

**Checkpoint 1**
```bash
pixi run dev
# manually create a few text entries via the left panel
# verify they appear in chronological order on the right
pixi run test   # add integration tests for create/list
```

---

## Phase 2 – Header entry type (experiment metadata via h5py) ✅
| Deliverable | Notes |
|-------------|-------|
| Add `h5py` dependency | Read NeXus files directly |
| `neutronote/services/metadata.py` | `RunMetadata` dataclass + file lookup |
| `/entries/create/header` route | Input: run number |
| Auto-populate title, times, counts, file size | From native NeXus file |
| Render header card in timeline | Styled with green accent, non-editable |
| Compact single-row layout | Author · badge · timestamp · title on one line |
| Error handling | Flash message in left panel if file not found |

**Checkpoint 2**
```bash
# create header entry with run number (e.g. 67890)
# verify metadata auto-fills and displays
pixi run test  # tests pass
```

---

## Phase 2.5 – Image entry type ✅
| Deliverable | Notes |
|-------------|-------|
| Image upload support | 16 MB max, stored in `instance/uploads/` |
| `/entries/create/image` route | File picker + optional caption |
| `/entries/uploads/<filename>` route | Serve uploaded images |
| Image tab in entry form | Select file, add caption, upload |
| Image card in timeline | Responsive image display, purple accent |
| Supported formats | PNG, JPG, JPEG, GIF, WebP, SVG |

**Checkpoint 2.5**
```bash
# upload an image with caption
# verify it displays in timeline
pixi run test  # 25 tests pass
```

---

## Phase 3 – Neutron data entry (interactive plots via snapwrap/mantid)
| Deliverable | Notes |
|-------------|-------|
| `neutronote/services/data.py` | Uses mantid/snapwrap to load & reduce data |
| `/entries/new/data` form | Input: run number, optional reduction params |
| Server returns x/y (2-D) or x/y/z (3-D) JSON | Reduced workspace → arrays |
| Plotly.js integration | Render interactive line / heatmap / surface |
| Store plot config JSON in `Entry.body` | Re-render on view |

**Checkpoint 3**
```bash
# create neutron-data entry for a real run
# interactive Plotly chart renders in browser
```

---

## Phase 4 – Code entry (browser-side Python via Pyodide)
| Deliverable | Notes |
|-------------|-------|
| Integrate Pyodide (WebAssembly Python) | Runs in browser, no server exec |
| CodeMirror editor widget | Syntax highlighting |
| Pre-load numpy, pandas, plotly in Pyodide | Common data-science stack |
| Display stdout + embedded Plotly below cell | Results inline |
| Server endpoint to fetch reduced data as JSON | Code cell can `fetch()` arrays |

**Checkpoint 4**
```bash
# create code entry: `import numpy as np; print(np.arange(5))`
# output "[0 1 2 3 4]" appears below cell
# fetch server data, plot with plotly – chart renders
```

> ⚠️ Pyodide cannot import mantid (native C++). Heavy processing stays server-side; code cells work with JSON arrays sent to browser.

---

## Phase 5 – Rich text & tagging
| Deliverable | Notes |
|-------------|-------|
| Markdown editor (EasyMDE or SimpleMDE) | WYSIWYG-ish |
| Embed images in text entries | `![alt](/entries/uploads/abc.png)` (inline) |
| `#tag` parsing in entry body | Auto-create `Tag` records |
| `/tags/<name>` filter view | Show entries with that tag |
| Basic search bar | Tag or full-text |

**Checkpoint 5**
```bash
# add "#calibration" -> click tag -> filtered view
```

---

## Phase 6 – User authentication & mentions
| Deliverable | Notes |
|-------------|-------|
| Flask-Login integration | Session-based auth |
| `/auth/register`, `/auth/login`, `/auth/logout` | Simple credential flow |
| `User` model; entries linked to `author_id` | Display author name |
| Protected routes | Must be logged in to create entries |
| `@username` parsing | Link to user, future notification hook |

**Checkpoint 6**
```bash
# register two users, each posts entries
# verify authorship shown on timeline
# @mention highlighted
pixi run test   # auth + permission tests
```

> 🗓️ **Deferred**: integrate with facility SSO (LDAP/OAuth) post-MVP.

---

## Phase 7 – Polish & deployment
| Deliverable | Notes |
|-------------|-------|
| Production config (`gunicorn`, env vars) | `SECRET_KEY`, `DATABASE_URL` |
| Dockerfile + compose (optional) | Easy deploy on analysis cluster |
| Basic RBAC (admin / member roles) | Future extensibility |
| UI/UX pass | Responsive CSS, dark mode toggle |

**Checkpoint 7**
```bash
docker compose up   # or gunicorn on analysis machine
# full flow: login, create all entry types, search by tag
```

---

## Future enhancements (out of scope for MVP)
- Real-time collaboration (WebSockets)
- PDF / HTML export of notebook
- Cloud-hosted Pyodide worker for large-scale code cells
- Notifications for `@mentions`
- Integration with SNAPRed desktop app workflows

---

*Update this plan as features land or requirements shift.*
