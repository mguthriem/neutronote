"""
neutroNote – Flask application factory.
"""

import os

from flask import Flask

# Allowed image extensions
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

# Base path for IPTS data (can be overridden for testing)
IPTS_BASE_PATH = "/SNS/SNAP"


def allowed_file(filename):
    """Check if the file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_ipts_notebook_path(ipts: str, base_path: str = None) -> str:
    """Get the notebook storage path for an IPTS.
    
    Args:
        ipts: IPTS number (e.g., "33219")
        base_path: Override base path (for testing)
    
    Returns:
        Path to notebook folder: /SNS/SNAP/IPTS-{ipts}/shared/neutronote/
    """
    base = base_path or IPTS_BASE_PATH
    return os.path.join(base, f"IPTS-{ipts}", "shared", "neutronote")


def create_app(test_config=None, ipts=None):
    """Application factory.
    
    Args:
        test_config: Optional test configuration dict
        ipts: IPTS number for notebook storage. If not provided, uses
              NEUTRONOTE_IPTS environment variable. Falls back to local
              instance/ folder for development.
    """
    app = Flask(__name__, instance_relative_config=True)

    # Determine IPTS and storage location
    ipts = ipts or os.environ.get("NEUTRONOTE_IPTS")
    
    if ipts and not test_config:
        # Production: use IPTS shared folder
        notebook_path = get_ipts_notebook_path(ipts)
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
        SECRET_KEY="dev-secret-change-in-production",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=upload_folder,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16 MB max upload
    )

    if test_config:
        app.config.update(test_config)

    # ---- Extensions ----
    from .models import db

    db.init_app(app)
    with app.app_context():
        db.create_all()

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

    # Make IPTS available in templates
    @app.context_processor
    def inject_ipts():
        return {"current_ipts": app.config.get("IPTS")}

    return app


def main():
    """CLI entry-point for `neutronote` command."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run neutroNote server")
    parser.add_argument(
        "--ipts", "-i",
        help="IPTS number for notebook storage (e.g., 33219)"
    )
    parser.add_argument(
        "--port", "-p",
        type=int, default=5000,
        help="Port to run server on (default: 5000)"
    )
    args = parser.parse_args()
    
    ipts = args.ipts or os.environ.get("NEUTRONOTE_IPTS")
    
    if ipts:
        print(f"📓 neutroNote starting for IPTS-{ipts}")
        print(f"   Storage: {get_ipts_notebook_path(ipts)}")
    else:
        print("📓 neutroNote starting in development mode (local storage)")
    
    app = create_app(ipts=ipts)
    app.run(debug=True, port=args.port)


if __name__ == "__main__":
    main()
