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
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

from ..app import allowed_file
from ..models import Entry, NotebookConfig, db
from ..services.metadata import get_run_metadata
from ..services.data import discover_state_ids, discover_reduced_runs

bp = Blueprint("entries", __name__, url_prefix="/entries")


@bp.route("/")
def index():
    """Main split-view: entry creation on left, timeline on right."""
    config = NotebookConfig.get_config()
    entries = Entry.query.order_by(Entry.created_at.asc()).all()
    return render_template("entries/index.html", entries=entries, config=config)


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


@bp.route("/setup", methods=["POST"])
def setup_notebook():
    """Set or update the notebook IPTS configuration."""
    ipts_str = request.form.get("ipts", "").strip()
    notebook_title = request.form.get("notebook_title", "").strip() or None

    if not ipts_str:
        flash("Please enter an IPTS number.", "error")
        return redirect(url_for("entries.index"))

    # Normalize IPTS input (accept "IPTS-12345" or just "12345")
    ipts_str = ipts_str.upper().replace("IPTS-", "").strip()
    if not ipts_str.isdigit():
        flash(f"Invalid IPTS format. Use 'IPTS-12345' or '12345'.", "error")
        return redirect(url_for("entries.index"))

    ipts = f"IPTS-{ipts_str}"

    # Verify the IPTS folder exists
    from pathlib import Path
    ipts_path = Path("/SNS/SNAP") / ipts
    if not ipts_path.exists():
        flash(f"IPTS folder not found: {ipts_path}", "error")
        return redirect(url_for("entries.index"))

    # Update the notebook config
    config = NotebookConfig.get_config()
    config.ipts = ipts
    config.title = notebook_title
    from datetime import datetime, timezone
    config.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    flash(f"Notebook configured for {ipts}", "success")
    return redirect(url_for("entries.index"))


@bp.route("/create/header", methods=["POST"])
def create_header():
    """Create a new run header entry from a run number."""
    config = NotebookConfig.get_config()

    if not config.is_configured:
        flash("Please configure the notebook IPTS first.", "error")
        return redirect(url_for("entries.index", tab="header"))

    run_number_str = request.form.get("run_number", "").strip()

    if not run_number_str:
        flash("Please enter a run number.", "error")
        return redirect(url_for("entries.index", tab="header"))

    try:
        run_number = int(run_number_str)
    except ValueError:
        flash(f"Invalid run number: '{run_number_str}'. Please enter a valid integer.", "error")
        return redirect(url_for("entries.index", tab="header"))

    # Use the notebook's IPTS for file lookup
    metadata = get_run_metadata(run_number, ipts=config.ipts)

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


# =============================================================================
# API endpoints for reduced data discovery
# =============================================================================


@bp.route("/api/states")
def api_get_states():
    """
    API: Get available state IDs for the current notebook's IPTS.

    Returns JSON: {"states": ["abc123...", "def456..."], "ipts": "IPTS-12345"}
    """
    config = NotebookConfig.get_config()

    if not config.is_configured:
        return jsonify({"error": "Notebook IPTS not configured", "states": []}), 400

    state_ids = discover_state_ids(config.ipts)

    return jsonify({
        "ipts": config.ipts,
        "states": state_ids,
        "count": len(state_ids),
    })


@bp.route("/api/states/<state_id>/runs")
def api_get_runs(state_id):
    """
    API: Get reduced runs for a specific state ID.

    Returns JSON with run list, supports optional filtering.
    Query params:
        - search: filter runs containing this substring
        - limit: max number of results (default: all)
    """
    config = NotebookConfig.get_config()

    if not config.is_configured:
        return jsonify({"error": "Notebook IPTS not configured", "runs": []}), 400

    # Get all reduced runs for this state
    runs = discover_reduced_runs(config.ipts, state_id, lite=True, latest_only=True)

    # Optional filtering
    search = request.args.get("search", "").strip()
    if search:
        try:
            search_num = int(search)
            runs = [r for r in runs if search in str(r.run_number)]
        except ValueError:
            pass  # Non-numeric search, ignore for now

    # Optional limit
    limit = request.args.get("limit", type=int)
    if limit and limit > 0:
        runs = runs[:limit]

    return jsonify({
        "state_id": state_id,
        "ipts": config.ipts,
        "runs": [r.to_dict() for r in runs],
        "count": len(runs),
    })


@bp.route("/api/runs/<int:run_number>/info")
def api_get_run_info(run_number):
    """
    API: Get detailed info for a specific run number.

    Looks up the run in all states to find reduction info.
    """
    config = NotebookConfig.get_config()

    if not config.is_configured:
        return jsonify({"error": "Notebook IPTS not configured"}), 400

    state_id = request.args.get("state_id", "").strip()

    if not state_id:
        return jsonify({"error": "state_id parameter required"}), 400

    runs = discover_reduced_runs(config.ipts, state_id, lite=True, latest_only=True)
    matching = [r for r in runs if r.run_number == run_number]

    if not matching:
        return jsonify({"error": f"Run {run_number} not found in state {state_id}"}), 404

    return jsonify(matching[0].to_dict())

