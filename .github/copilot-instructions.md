# Copilot instructions – neutroNote

> E-lab notebook web app for time-sequenced experiment entries (Flask + SQLite + mantid/snapwrap).

## Quick reference

| Action | Command |
|--------|---------|
| Install (Pixi) | `pixi install` |
| Run dev server | `pixi run dev` (or `flask --app neutronote.app run --debug`) |
| Run tests | `pixi run test` (or `pytest -q tests/`) |
| Lint | `pixi run lint` |
| Format | `pixi run fmt` |

## Project layout

```
neutronote/
  app.py            # create_app() factory, blueprints registered here
  models.py         # SQLAlchemy models: Entry, Tag (User added later)
  routes/           # Blueprints (entries, auth, api…)
  services/         # metadata.py, data.py – wrappers around snapwrap/mantid
  templates/        # Jinja2 HTML (base.html, entries/, auth/)
  static/           # CSS, JS, images
tests/              # pytest suite (mirrors neutronote/ structure)
docs/PLAN.md        # Phased development roadmap with checkpoints
pyproject.toml      # Pixi + pip config, tasks, tool settings
```

## Architecture at a glance

- **Flask app factory** in `neutronote/app.py` – loads config, registers
  extensions (Flask-SQLAlchemy) and blueprints.
- **SQLite** database at `instance/neutronote.db`; models in `models.py`.
- **Entry types** (text, header, neutron-data, code) share a single `Entry`
  model with a `type` discriminator; rendering handled in Jinja partials.
- **Data backend**: `snapwrap` + `mantid` (server-side) for loading/reducing
  neutron data; results sent as JSON to browser.
- **Interactive plots**: Plotly (server builds JSON config, Plotly.js renders).
- **Code cells**: Pyodide (browser-side Python/WebAssembly) for lightweight
  scripting; heavy computation stays on server via API.

### UI layout: split view

The main interface uses a **split-view design**:
- **Left panel**: entry creation – tabs/buttons to select entry type (Text,
  Header, Data, Code), plus the form/editor for that type.
- **Right panel**: scrollable timeline of all previous entries (newest at
  bottom, chat-style). Users can review past entries while composing new ones.

### Data file paths

| Type | Path template |
|------|---------------|
| Full NeXus | `/SNS/SNAP/<IPTS>/nexus/SNAP_<run>.nxs.h5` |
| Lite NeXus | `/SNS/SNAP/<IPTS>/shared/lite/SNAP_<run>.lite.nxs.h5` *(preferred)* |

## Conventions

- **Blueprints**: one module per concern under `neutronote/routes/`; register in
  `app.py`.
- **Templates**: extend `templates/base.html`; entry-type partials live in
  `templates/entries/_<type>.html`.
- **Services**: `services/metadata.py` and `services/data.py` wrap snapwrap
  calls; keep mantid imports isolated here.
- **Tests**: mirror source tree; use the `client` fixture from `conftest.py`.
- **Config**: Flask config via `app.config`; secrets in env vars or `.env`
  (loaded by `python-dotenv`). Never commit secrets.

## Adding a new entry type

1. Add a constant to `Entry.TYPE_*` in `models.py`.
2. Create a form/route in `routes/entries.py`.
3. Add a Jinja partial `templates/entries/_<type>_content.html`.
4. Update `templates/entries/_entry_card.html` to render the new type.
5. Update `templates/entries/index.html` to enable the tab and add the form.
6. Write tests in `tests/test_app.py`.

### Header entries (Run Headers)

Header entries fetch metadata from NeXus files and display run information.
- **Non-editable**: header entries cannot be edited (data comes from files).
- **Body is JSON**: the entry body stores `RunMetadata.to_dict()` as JSON.
- **Rendering**: `_header_content.html` parses JSON via `|fromjson` filter.
- **Metadata service**: `services/metadata.py` contains `get_run_metadata(run_number)`.
- **File lookup**: prefers lite NeXus files, falls back to native files.
- **Error handling**: if file not found, stores error in the entry body.

## External integrations

| Integration | Location | Notes |
|-------------|----------|-------|
| **snapwrap** | `services/metadata.py`, `services/data.py` | SNAP-specific mantid algorithms; see [neutrons/SNAPWrap](https://github.com/neutrons/SNAPWrap) |
| **mantid** | imported inside services | Load workspaces, reduce data, extract arrays |
| **Pyodide** | `static/js/pyodide-loader.js` | Browser Python for code cells |

### snapwrap import convention

```python
from snapwrap.spectralTools import some_function
from snapwrap.SEEMeta import SomeClass
```

Keep all mantid/snapwrap imports isolated inside `neutronote/services/` so the
app can run (with stubs) in environments without mantid installed.

## Tips for AI agents

- Run `pixi run test` after changes; check for regressions.
- Use `ruff` and `black` before committing (`pixi run lint && pixi run fmt`).
- Consult `docs/PLAN.md` to see what phase the project is in and what's next.
- Keep mantid/snapwrap imports inside `services/` to avoid import errors in
  environments without mantid installed.
