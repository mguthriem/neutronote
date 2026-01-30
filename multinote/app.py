"""
multiNote – Flask application factory.
"""

import os

from flask import Flask

# Allowed image extensions
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}


def allowed_file(filename):
    """Check if the file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def create_app(test_config=None):
    """Application factory."""
    app = Flask(__name__, instance_relative_config=True)

    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)

    # Upload folder for images
    upload_folder = os.path.join(app.instance_path, "uploads")
    os.makedirs(upload_folder, exist_ok=True)

    # Default configuration
    app.config.from_mapping(
        SECRET_KEY="dev-secret-change-in-production",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{os.path.join(app.instance_path, 'multinote.db')}",
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

    return app


def main():
    """CLI entry-point for `multinote` command."""
    app = create_app()
    app.run(debug=True)


if __name__ == "__main__":
    main()
