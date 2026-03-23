"""
neutroNote – Flask application factory.
"""

import os

from dotenv import load_dotenv
from flask import Flask

from .instruments import get_instrument, available_instruments, InstrumentConfig

# Load .env file (if present) so os.environ.get() picks up secrets.
# In production, set real environment variables instead.
load_dotenv()

# Allowed image extensions
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

# Default instrument (can be overridden via env var or CLI arg)
DEFAULT_INSTRUMENT = "SNAP"


def allowed_file(filename):
    """Check if the file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_ipts_notebook_path(ipts: str, instrument: InstrumentConfig | None = None) -> str:
    """Get the notebook storage path for an IPTS.

    Args:
        ipts: IPTS number (e.g., "33219")
        instrument: InstrumentConfig instance (defaults to current default)

    Returns:
        Path to notebook folder, e.g. /SNS/SNAP/IPTS-{ipts}/shared/neutronote/
    """
    if instrument is None:
        instrument = get_instrument(os.environ.get("NEUTRONOTE_INSTRUMENT", DEFAULT_INSTRUMENT))
    return instrument.notebook_path(f"IPTS-{ipts}")


def _migrate_db(app):
    """Run lightweight schema migrations for SQLite.

    Adds new columns that may not exist in older databases.
    Safe to call multiple times – skips columns that already exist.
    """
    import sqlite3

    from .models import db as _db

    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    # Only handle sqlite URIs
    if not uri.startswith("sqlite"):
        return

    db_path = uri.replace("sqlite:///", "")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check existing columns in notebook_config
        cursor.execute("PRAGMA table_info(notebook_config)")
        existing = {row[1] for row in cursor.fetchall()}

        if "experiment_start" not in existing:
            cursor.execute("ALTER TABLE notebook_config ADD COLUMN experiment_start DATETIME")
            app.logger.info("Migration: added notebook_config.experiment_start")
        if "experiment_end" not in existing:
            cursor.execute("ALTER TABLE notebook_config ADD COLUMN experiment_end DATETIME")
            app.logger.info("Migration: added notebook_config.experiment_end")
        if "reduced_data_path" not in existing:
            cursor.execute("ALTER TABLE notebook_config ADD COLUMN reduced_data_path VARCHAR(500)")
            app.logger.info("Migration: added notebook_config.reduced_data_path")

        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning("Migration check failed (non-fatal): %s", e)


def create_app(test_config=None, ipts=None, instrument_name=None):
    """Application factory.

    Args:
        test_config: Optional test configuration dict
        ipts: IPTS number for notebook storage. If not provided, uses
              NEUTRONOTE_IPTS environment variable. Falls back to local
              instance/ folder for development.
        instrument_name: Instrument name (e.g. "SNAP"). If not provided,
              uses NEUTRONOTE_INSTRUMENT env var, then DEFAULT_INSTRUMENT.
    """
    app = Flask(__name__, instance_relative_config=True)

    # Resolve instrument
    instrument_name = (
        instrument_name or os.environ.get("NEUTRONOTE_INSTRUMENT") or DEFAULT_INSTRUMENT
    )
    instrument = get_instrument(instrument_name)
    app.config["INSTRUMENT"] = instrument
    app.config["INSTRUMENT_NAME"] = instrument.name

    # Determine IPTS and storage location
    ipts = ipts or os.environ.get("NEUTRONOTE_IPTS")

    if ipts and not test_config:
        # Production: use IPTS shared folder
        notebook_path = get_ipts_notebook_path(ipts, instrument=instrument)
        os.makedirs(notebook_path, exist_ok=True)
        upload_folder = os.path.join(notebook_path, "uploads")
        os.makedirs(upload_folder, exist_ok=True)
        db_path = os.path.join(notebook_path, "neutronote.db")
        app.config["IPTS"] = ipts
    else:
        # Development/testing: use local instance folder
        os.makedirs(app.instance_path, exist_ok=True)
        upload_folder = os.path.join(app.instance_path, "uploads")
        os.makedirs(upload_folder, exist_ok=True)
        db_path = os.path.join(app.instance_path, "neutronote.db")
        app.config["IPTS"] = None

    # Default configuration
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-in-production"),
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # SQLite settings for shared filesystem (GPFS) with multiple users
        SQLALCHEMY_ENGINE_OPTIONS={
            "connect_args": {
                "timeout": 30,  # Wait up to 30s for locks
                "check_same_thread": False,
            },
            "pool_pre_ping": True,  # Check connection is alive before using
        },
        UPLOAD_FOLDER=upload_folder,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16 MB max upload
    )

    if test_config:
        app.config.update(test_config)

    # ---- Extensions ----
    from flask_wtf.csrf import CSRFProtect
    from .models import db
    from sqlalchemy import event

    csrf = CSRFProtect(app)
    db.init_app(app)

    # Set SQLite pragmas for better multi-user support on shared filesystem
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        # WAL mode allows concurrent reads while writing
        cursor.execute("PRAGMA journal_mode=WAL")
        # Synchronous=NORMAL is a good balance of safety and speed
        cursor.execute("PRAGMA synchronous=NORMAL")
        # Busy timeout in milliseconds
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    with app.app_context():
        # Register the pragma listener
        event.listen(db.engine, "connect", set_sqlite_pragma)
        db.create_all()
        # Run lightweight migrations for new columns
        _migrate_db(app)

        # Auto-populate NotebookConfig from CLI args so users don't have to
        # re-enter IPTS in the settings modal.
        if ipts and not test_config:
            from .models import NotebookConfig

            config = NotebookConfig.get_config()
            normalised_ipts = f"IPTS-{ipts}" if not str(ipts).upper().startswith("IPTS-") else str(ipts).upper()
            if not config.ipts or config.ipts != normalised_ipts:
                config.ipts = normalised_ipts
                config.instrument = instrument.name
                # Set default reduced data path from instrument if not already set
                if not config.has_reduced_data_path:
                    root = instrument.reduced_data_root(normalised_ipts)
                    if root:
                        config.reduced_data_path = str(root)
                db.session.commit()

    # Expire all objects before each request to ensure fresh data
    # This is important for multi-user scenarios on shared storage
    @app.before_request
    def expire_session():
        db.session.expire_all()

    # ---- Blueprints ----
    from .routes import entries

    app.register_blueprint(entries.bp)

    # ---- Template filters ----
    import json

    import markdown as md
    from markupsafe import Markup

    @app.template_filter("markdown")
    def markdown_filter(text):
        """Convert Markdown text to HTML."""
        if not text:
            return ""
        # Enable common extensions: fenced code blocks, tables, etc.
        html = md.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
        return Markup(html)

    @app.template_filter("fromjson")
    def fromjson_filter(text):
        """Parse a JSON string into a Python object."""
        if not text:
            return {}
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"error": "Invalid JSON data"}

    # Redirect root to entries
    @app.route("/")
    def index():
        from flask import redirect, url_for

        return redirect(url_for("entries.index"))

    # Make IPTS and instrument available in templates
    @app.context_processor
    def inject_globals():
        inst = app.config.get("INSTRUMENT")
        return {
            "current_ipts": app.config.get("IPTS"),
            "instrument_name": inst.name if inst else "SNAP",
            "instrument": inst,
        }

    return app


def _find_free_port(start=6100, end=6999):
    """Find a free TCP port in the given range."""
    import socket

    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return None


def main():
    """CLI entry-point for `neutronote` command."""
    import argparse

    parser = argparse.ArgumentParser(description="Run neutroNote server")
    parser.add_argument("--ipts", "-i", help="IPTS number for notebook storage (e.g., 33219)")
    parser.add_argument(
        "--instrument",
        default=None,
        help=f"Instrument name (default: {DEFAULT_INSTRUMENT}). "
        f"Available: {', '.join(available_instruments())}",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=None,
        help="Port to run server on (default: auto-allocate in 6100-6999)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="User-friendly mode: clean banner, suppress Flask/Werkzeug noise",
    )
    args = parser.parse_args()

    ipts = args.ipts or os.environ.get("NEUTRONOTE_IPTS")
    instrument_name = (
        args.instrument or os.environ.get("NEUTRONOTE_INSTRUMENT") or DEFAULT_INSTRUMENT
    )

    # Auto-select a free port in the 6100-6999 range when none specified.
    port = args.port
    if port is None:
        port = _find_free_port()
        if port is None:
            print("No free port found in 6100-6999; defaulting to 6100")
            port = 6100

    # Try to clear any process listening on the port (best-effort)
    try:
        import subprocess

        subprocess.run(["fuser", "-k", f"{port}/tcp"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # Build the URL for the local machine.
    # Use 127.0.0.1 (not "localhost") because on some systems localhost
    # resolves to ::1 (IPv6) first and the browser fails to connect.
    url = f"http://127.0.0.1:{port}"

    if args.quiet:
        # ---- User-friendly mode: clean output, suppress Werkzeug logs ----
        import logging
        import sys

        # Suppress Werkzeug request logs
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

        # Suppress Flask/Werkzeug startup banner ("Serving Flask app", "Debug mode")
        # by temporarily silencing stderr during app.run() init, then restoring it.
        class _QuietStderr:
            """Swallows write() calls that contain Flask/Werkzeug banner text."""
            def __init__(self, real):
                self._real = real
                self._suppress = {" * Serving", " * Debug mode", " * Running on",
                                  "WARNING: This is a development server",
                                  "Press CTRL+C", "Use a production"}
            def write(self, s):
                if any(tok in s for tok in self._suppress):
                    return
                self._real.write(s)
            def flush(self):
                self._real.flush()
            def __getattr__(self, name):
                return getattr(self._real, name)

        print()
        if ipts:
            inst = get_instrument(instrument_name)
            storage = get_ipts_notebook_path(ipts, instrument=inst)
            print(f"  📓 neutroNote starting for IPTS-{ipts} ({instrument_name})")
            print(f"     Storage: {storage}")
        else:
            print(f"  📓 neutroNote starting in development mode ({instrument_name})")
        print()
        print(f"  To open neutroNote, open this link in a browser:")
        print(f"  👉 {url}")
        print()
        print(f"  Press Ctrl+C to stop the server.")
        print()

        app = create_app(ipts=ipts, instrument_name=instrument_name)
        sys.stderr = _QuietStderr(sys.stderr)
        sys.stdout = _QuietStderr(sys.stdout)
        # Bind to localhost only (users access from the same machine)
        app.run(host="127.0.0.1", port=port, debug=False)
    else:
        # ---- Developer mode: full Flask debug output ----
        if ipts:
            print(f"📓 neutroNote starting for IPTS-{ipts} ({instrument_name})")
            inst = get_instrument(instrument_name)
            print(f"   Storage: {get_ipts_notebook_path(ipts, instrument=inst)}")
        else:
            print(f"📓 neutroNote starting in development mode ({instrument_name})")

        app = create_app(ipts=ipts, instrument_name=instrument_name)
        print(f" * Running on {url}")
        # Bind to localhost only (users access from the same machine)
        app.run(host="127.0.0.1", debug=True, port=port)


if __name__ == "__main__":
    main()
