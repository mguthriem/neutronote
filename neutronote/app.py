"""
neutroNote – Flask application factory.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from .instruments import get_instrument, available_instruments, InstrumentConfig

# Load .env file from the project root (parent of this package directory)
# so credentials are found regardless of the user's working directory.
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

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

    # ---- Global error handler ----
    # Log unhandled exceptions so we can diagnose 500 errors even when
    # running in --quiet mode (no debug traceback shown to user).
    # Only register in non-testing mode so the test client sees real errors.
    if not app.testing:

        @app.errorhandler(Exception)
        def unhandled_exception(error):
            import traceback

            app.logger.error(
                "Unhandled exception: %s\n%s", error, traceback.format_exc()
            )
            try:
                db.session.rollback()
            except Exception:
                pass
            return (
                "<h1>Internal Server Error</h1>"
                "<p>Something went wrong. The error has been logged.</p>"
                "<p><a href='/entries/'>← Back to notebook</a></p>",
                500,
            )

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
    """Find a free TCP port in the given range.

    Binds to 127.0.0.1 (matching the actual server bind address) so
    that we correctly detect ports already in use by other neutroNote
    instances on the same machine.
    """
    import socket

    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
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
            print("ERROR: No free port found in 6100-6999 range.")
            print("       Other neutroNote instances may be using all available ports.")
            print("       You can specify a port manually with --port <number>")
            import sys
            sys.exit(1)


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
        print(f"  Starting server — please wait...")
        print()
        print(f"  Press Ctrl+C to stop the server.")
        print()

        # Create the Flask app
        app = create_app(ipts=ipts, instrument_name=instrument_name)

        # Set up file-based error logging so 500 errors are captured even
        # when the console output is suppressed in quiet mode.
        log_path = os.path.join(
            app.config.get("UPLOAD_FOLDER", "."), "..", "neutronote.log"
        )
        log_path = os.path.normpath(log_path)
        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
        )
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.WARNING)

        # Quiet the noisy startup banner and werkzeug logs
        sys.stderr = _QuietStderr(sys.stderr)
        sys.stdout = _QuietStderr(sys.stdout)

        # Start the server in a background thread so we can poll it and only
        # print the clickable URL once it's actually accepting connections.
        import threading
        import time
        import socket as _sock

        def _run_server():
            try:
                # use_reloader=False to avoid double-start in threaded mode
                app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
            except OSError as exc:
                _server_error[0] = exc

        _server_error = [None]  # mutable container for thread → main communication
        thread = threading.Thread(target=_run_server, daemon=True)
        thread.start()

        # Poll the local port until it accepts a TCP connection (or timeout).
        # Using a raw socket connect avoids HTTP overhead and log noise.
        start = time.time()
        timeout = 30.0
        url_ok = False
        while time.time() - start < timeout:
            # If the server thread died (e.g. port already in use), stop waiting.
            if _server_error[0] is not None:
                break
            if not thread.is_alive():
                break
            try:
                s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
                s.close()
                url_ok = True
                break
            except OSError:
                time.sleep(0.3)

        # Restore normal stdout/stderr behavior for the rest of the CLI output
        try:
            sys.stderr = sys.stderr._real
            sys.stdout = sys.stdout._real
        except Exception:
            # If something unexpected happened, ignore and continue
            pass

        if _server_error[0] is not None:
            print()
            print(f"  ❌ Failed to start server on port {port}.")
            print(f"     Error: {_server_error[0]}")
            print(f"     Another neutroNote instance may be using this port.")
            print(f"     Try again (a new port will be auto-selected) or use --port <number>.")
            print()
            import sys as _sys
            _sys.exit(1)
        elif url_ok:
            print()
            print(f"  To open neutroNote, open this link in a browser:")
            print(f"  👉 {url}")
            print()
        else:
            print()
            print("  Server still starting — you may see a brief 'Unable to connect' when opening the URL. Retrying usually helps.")
            print(f"  Try: {url}")
            print()

        # Keep the main thread alive so the daemon server thread keeps running.
        # Ctrl+C will raise KeyboardInterrupt and exit cleanly.
        try:
            thread.join()
        except KeyboardInterrupt:
            print("\n  Server stopped.")
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
