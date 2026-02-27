# neutroNote – E-Lab Notebook

A web-based electronic lab notebook that helps experimental teams create time-sequenced entries during neutron-science experiments at SNAP.

## Features (planned)

| Entry type | Description |
|------------|-------------|
| **Header** | Auto-populated metadata from IPTS/run via snapwrap |
| **Neutron data** | Interactive Plotly 2-D/3-D plots of reduced run data |
| **Code** | Browser-side Python (Pyodide) to manipulate/visualise data |
| **Text** | Rich narrative with embedded images, `#tags`, and `@mentions` |

Additional capabilities:
- Scroll-through timeline view (chat-style)
- Multi-user editing (auth deferred to Phase 6)
- Tagging & search

## Data backend

This app uses **mantid** and **snapwrap** ([neutrons/SNAPWrap](https://github.com/neutrons/SNAPWrap)) to:
- Fetch experiment metadata given IPTS / run numbers
- Load and reduce neutron data
- Return plot-ready arrays to the browser

Heavy computation runs server-side; code cells in the browser (Pyodide) operate on JSON arrays.

## Quick Start (Pixi)

```bash
# Install Pixi if needed: https://prefix.dev/docs/pixi/overview
pixi install            # create env & install deps

# Optional: Configure environment (copy .env.example to .env and customize)
cp .env.example .env    # set ORACLE_DSN, NEUTRONOTE_REDUCED_DATA_PATH, etc.

pixi run dev            # start Flask dev server at http://127.0.0.1:5000
```

## Quick Start (pip)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Optional: Configure environment
cp .env.example .env    # customize as needed

flask --app neutronote.app run --debug
```

## Configuration

Copy `.env.example` to `.env` and customize:

- `NEUTRONOTE_INSTRUMENT` - Default instrument (SNAP, REF_L, etc.)
- `NEUTRONOTE_REDUCED_DATA_PATH` - Default reduced data location (supports `{ipts}` placeholder)
- `ORACLE_DSN`, `ORACLE_USER`, `ORACLE_PASS` - PV Log database credentials
- `SECRET_KEY` - Flask secret (change in production!)

See `.env.example` for full list and documentation.

## Running tests

```bash
pixi run test
# or
pytest -q tests/
```

## Project layout

```
neutronote/
├── app.py              # Flask application factory
├── models.py           # SQLAlchemy models (Entry, Tag, User…)
├── routes/             # Blueprints (entries, auth, api)
├── services/           # metadata.py, data.py (snapwrap wrappers)
├── templates/          # Jinja2 HTML
└── static/             # CSS, JS, images
tests/
└── ...
```

## Development roadmap

See `docs/PLAN.md` for the phased development plan with test checkpoints.

## License

MIT
