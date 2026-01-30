"""
Entries blueprint – handles the main split-view interface and entry CRUD.
"""

import json
import os
import uuid

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

from ..app import allowed_file
from ..models import Entry, db
from ..services.metadata import get_run_metadata

bp = Blueprint("entries", __name__, url_prefix="/entries")


@bp.route("/")
def index():
    """Main split-view: entry creation on left, timeline on right."""
    entries = Entry.query.order_by(Entry.created_at.asc()).all()
    return render_template("entries/index.html", entries=entries)


@bp.route("/create/text", methods=["POST"])
def create_text():
    """Create a new text entry."""
    body = request.form.get("body", "").strip()
    title = request.form.get("title", "").strip() or None

    if body:
        entry = Entry(type=Entry.TYPE_TEXT, title=title, body=body)
        db.session.add(entry)
        db.session.commit()

    return redirect(url_for("entries.index"))


@bp.route("/create/header", methods=["POST"])
def create_header():
    """Create a new run header entry from a run number."""
    run_number_str = request.form.get("run_number", "").strip()

    if not run_number_str:
        flash("Please enter a run number.", "error")
        return redirect(url_for("entries.index", tab="header"))

    try:
        run_number = int(run_number_str)
    except ValueError:
        flash(f"Invalid run number: '{run_number_str}'. Please enter a valid integer.", "error")
        return redirect(url_for("entries.index", tab="header"))

    # Fetch metadata from the NeXus file
    metadata = get_run_metadata(run_number)

    if metadata.error:
        # Show error in the left panel, don't create an entry
        flash(f"Run {run_number}: {metadata.error}", "error")
        return redirect(url_for("entries.index", tab="header"))

    # Store the metadata as JSON in the body
    entry = Entry(
        type=Entry.TYPE_HEADER,
        title=f"Run {run_number}: {metadata.title}",
        body=json.dumps(metadata.to_dict()),
    )

    db.session.add(entry)
    db.session.commit()

    return redirect(url_for("entries.index"))


@bp.route("/create/image", methods=["POST"])
def create_image():
    """Create a new image entry from an uploaded file."""
    caption = request.form.get("caption", "").strip() or None

    # Check if file was uploaded
    if "image" not in request.files:
        flash("No image file selected.", "error")
        return redirect(url_for("entries.index", tab="image"))

    file = request.files["image"]

    if file.filename == "":
        flash("No image file selected.", "error")
        return redirect(url_for("entries.index", tab="image"))

    if not allowed_file(file.filename):
        flash("Invalid file type. Allowed: PNG, JPG, JPEG, GIF, WebP, SVG.", "error")
        return redirect(url_for("entries.index", tab="image"))

    # Generate unique filename to avoid collisions
    original_name = secure_filename(file.filename)
    ext = original_name.rsplit(".", 1)[1].lower() if "." in original_name else "png"
    unique_name = f"{uuid.uuid4().hex}.{ext}"

    # Save the file
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    file_path = os.path.join(upload_folder, unique_name)
    file.save(file_path)

    # Store filename in body, caption as title
    entry = Entry(
        type=Entry.TYPE_IMAGE,
        title=caption,
        body=unique_name,  # Store just the filename
    )

    db.session.add(entry)
    db.session.commit()

    return redirect(url_for("entries.index"))


@bp.route("/uploads/<filename>")
def uploaded_file(filename):
    """Serve uploaded images."""
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@bp.route("/<int:entry_id>")
def detail(entry_id):
    """View a single entry (for future use)."""
    entry = Entry.query.get_or_404(entry_id)
    return render_template("entries/detail.html", entry=entry)


@bp.route("/<int:entry_id>/edit", methods=["GET", "POST"])
def edit(entry_id):
    """Edit an existing entry."""
    entry = Entry.query.get_or_404(entry_id)

    # Don't allow editing of header entries (they're generated from data)
    if entry.type == Entry.TYPE_HEADER:
        return redirect(url_for("entries.index"))

    if request.method == "POST":
        body = request.form.get("body", "").strip()
        title = request.form.get("title", "").strip() or None

        if body:
            entry.title = title
            entry.body = body
            entry.mark_edited()
            db.session.commit()

        return redirect(url_for("entries.index"))

    # GET: show edit form
    return render_template("entries/edit.html", entry=entry)
