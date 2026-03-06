"""
Entries blueprint – handles the main split-view interface and entry CRUD.
"""

import json
import logging
import os
import shutil
import uuid

from flask import (
    Blueprint,
    abort,
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

from ..app import allowed_file, ALLOWED_EXTENSIONS
from ..models import Entry, NotebookConfig, Tag, db
from ..services.metadata import get_run_metadata
from ..services.data import (
    discover_state_ids,
    discover_reduced_runs,
    get_run_metadata_lazy,
    get_run_metadata_quick,
)
from ..services.kernel import get_kernel_manager

logger = logging.getLogger(__name__)

bp = Blueprint("entries", __name__, url_prefix="/entries")


@bp.route("/")
def index():
    """Main split-view: entry creation on left, timeline on right."""
    instrument = current_app.config["INSTRUMENT"]
    pv_aliases = instrument.pv_aliases()

    config = NotebookConfig.get_config()
    entries = Entry.query.order_by(Entry.created_at.asc()).all()

    # Build default reduced data path hint (for UI)
    default_reduced_path = None
    if config.ipts:
        # Check if env var is set
        env_path = os.environ.get("NEUTRONOTE_REDUCED_DATA_PATH")
        if env_path:
            default_reduced_path = env_path.replace("{ipts}", config.ipts)
        else:
            # Use instrument default
            root = instrument.reduced_data_root(config.ipts)
            if root:
                default_reduced_path = str(root)

    return render_template(
        "entries/index.html",
        entries=entries,
        config=config,
        aliases=pv_aliases,
        default_reduced_path=default_reduced_path,
    )


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
    experiment_start_str = request.form.get("experiment_start", "").strip()
    experiment_end_str = request.form.get("experiment_end", "").strip()
    reduced_data_path = request.form.get("reduced_data_path", "").strip() or None

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

    instrument = current_app.config["INSTRUMENT"]
    ipts_path = instrument.ipts_path(ipts)
    if not ipts_path.exists():
        flash(f"IPTS folder not found: {ipts_path}", "error")
        return redirect(url_for("entries.index"))

    # Validate reduced data path if provided
    if reduced_data_path:
        reduced_path = Path(reduced_data_path)
        if not reduced_path.exists():
            flash(f"Reduced data path not found: {reduced_data_path}", "warning")
        elif not reduced_path.is_dir():
            flash(f"Reduced data path is not a directory: {reduced_data_path}", "error")
            return redirect(url_for("entries.index"))

    # Parse optional experiment dates
    from datetime import datetime, timezone

    experiment_start = None
    experiment_end = None
    if experiment_start_str:
        try:
            experiment_start = datetime.strptime(experiment_start_str, "%Y-%m-%d")
        except ValueError:
            flash("Invalid start date format.", "error")
            return redirect(url_for("entries.index"))
    if experiment_end_str:
        try:
            experiment_end = datetime.strptime(experiment_end_str, "%Y-%m-%d")
        except ValueError:
            flash("Invalid end date format.", "error")
            return redirect(url_for("entries.index"))
    if experiment_start and experiment_end and experiment_end < experiment_start:
        flash("End date must be after start date.", "error")
        return redirect(url_for("entries.index"))

    # Update the notebook config
    config = NotebookConfig.get_config()
    config.ipts = ipts
    config.title = notebook_title
    config.experiment_start = experiment_start
    config.experiment_end = experiment_end
    config.reduced_data_path = reduced_data_path
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
    filename = secure_filename(filename)
    if not filename:
        abort(400)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


# =============================================================================
# Server-side file browser for image selection
# =============================================================================


def _get_ipts_shared_root():
    """Return the IPTS shared directory root, or None if not configured."""
    ipts = current_app.config.get("IPTS")
    if not ipts:
        return None
    instrument = current_app.config["INSTRUMENT"]
    return str(instrument.data_root / f"IPTS-{ipts}" / "shared")


IMAGE_EXTENSIONS = {f".{ext}" for ext in ALLOWED_EXTENSIONS}


@bp.route("/api/browse", methods=["GET"])
def api_browse_files():
    """Browse the IPTS shared directory for images.

    Query params:
        path: relative path within the shared directory (default: "")

    Returns JSON: {root, path, parent, dirs: [{name}], files: [{name, size}]}
    """
    shared_root = _get_ipts_shared_root()
    if not shared_root:
        return jsonify(error="IPTS not configured"), 400

    rel_path = request.args.get("path", "").strip("/")
    browse_dir = os.path.normpath(os.path.join(shared_root, rel_path))

    # Security: ensure we stay within the shared root
    if not browse_dir.startswith(shared_root):
        return jsonify(error="Access denied"), 403

    if not os.path.isdir(browse_dir):
        return jsonify(error="Directory not found"), 404

    dirs = []
    files = []
    try:
        for item in sorted(os.listdir(browse_dir)):
            full = os.path.join(browse_dir, item)
            if os.path.isdir(full):
                # Skip hidden directories and the neutronote storage folder
                if item.startswith(".") or item == "neutronote":
                    continue
                dirs.append({"name": item})
            elif os.path.isfile(full):
                ext = os.path.splitext(item)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    size = os.path.getsize(full)
                    files.append({"name": item, "size": size})
    except PermissionError:
        return jsonify(error="Permission denied"), 403

    # Build parent path for "up" navigation
    parent = os.path.dirname(rel_path) if rel_path else None

    return jsonify(
        root=f"IPTS-{current_app.config['IPTS']}/shared",
        path=rel_path,
        parent=parent,
        dirs=dirs,
        files=files,
    )


@bp.route("/api/pick-image", methods=["POST"])
def api_pick_image():
    """Copy a server-side image into the uploads folder and create an entry.

    JSON body: {path: "relative/path/to/image.png", caption: "optional"}
    """
    data = request.get_json(silent=True) or {}
    rel_path = data.get("path", "").strip("/")
    caption = data.get("caption", "").strip() or None

    if not rel_path:
        return jsonify(error="No file path provided"), 400

    shared_root = _get_ipts_shared_root()
    if not shared_root:
        return jsonify(error="IPTS not configured"), 400

    src = os.path.normpath(os.path.join(shared_root, rel_path))

    # Security: stay within shared root
    if not src.startswith(shared_root):
        return jsonify(error="Access denied"), 403

    if not os.path.isfile(src):
        return jsonify(error="File not found"), 404

    filename = os.path.basename(src)
    if not allowed_file(filename):
        return jsonify(error="Invalid file type"), 400

    # Check file size (16 MB limit)
    if os.path.getsize(src) > current_app.config.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024):
        return jsonify(error="File too large (max 16 MB)"), 400

    # Copy to uploads with unique name
    ext = filename.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    dest = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_name)
    shutil.copy2(src, dest)

    # Create the entry
    entry = Entry(
        type=Entry.TYPE_IMAGE,
        title=caption,
        body=unique_name,
    )
    db.session.add(entry)
    db.session.commit()

    return jsonify(ok=True, entry_id=entry.id)


# =============================================================================
# Development / Debug endpoints
# =============================================================================


@bp.route("/api/dev/reset-timeline", methods=["POST"])
def api_reset_timeline():
    """
    DEV ONLY: Delete all entries from the timeline.

    This is a destructive operation for development/testing purposes.
    Only available when the app is running in debug mode.
    """
    if not current_app.debug:
        abort(404)
    try:
        # Delete all entries
        num_deleted = Entry.query.delete()
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Deleted {num_deleted} entries from timeline",
                "deleted_count": num_deleted,
            }
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route("/api/upload-snapshot", methods=["POST"])
def upload_snapshot():
    """
    API: Upload a plot snapshot PNG from Plotly.toImage().

    Expects JSON body with:
        - image_data: base64-encoded PNG data (data:image/png;base64,...)

    Returns:
        - filename: the saved filename to include in data entry
    """
    import base64

    data = request.get_json()
    if not data or "image_data" not in data:
        return jsonify({"error": "image_data required"}), 400

    image_data = data["image_data"]

    # Parse data URL: data:image/png;base64,iVBORw0KGgo...
    if "," in image_data:
        header, encoded = image_data.split(",", 1)
    else:
        encoded = image_data

    try:
        image_bytes = base64.b64decode(encoded)
    except Exception as e:
        return jsonify({"error": f"Invalid base64 data: {e}"}), 400

    # Generate unique filename
    filename = f"snapshot_{uuid.uuid4().hex}.png"
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    file_path = os.path.join(upload_folder, filename)

    with open(file_path, "wb") as f:
        f.write(image_bytes)

    return jsonify({"success": True, "filename": filename})


@bp.route("/api/execute", methods=["POST"])
def execute_code():
    """
    API: Execute Python code in the persistent kernel.

    Expects JSON body with:
        - code: Python code string to execute

    Returns:
        - success: True/False
        - output: stdout from execution
        - error: error message if failed
        - execution_time: seconds taken

    Security: only accepts requests from localhost.
    """
    # Reject requests that don't originate from localhost
    remote = request.remote_addr
    if remote not in ("127.0.0.1", "::1", None):
        abort(403)

    data = request.get_json()
    if not data or "code" not in data:
        return jsonify({"error": "code required"}), 400

    code = data["code"]

    # Execute in persistent kernel
    kernel = get_kernel_manager()
    result = kernel.execute(code)

    if result.success:
        return jsonify(
            {"success": True, "output": result.output, "execution_time": result.execution_time}
        )
    else:
        return jsonify(
            {
                "success": False,
                "error": result.error or result.output,
                "execution_time": result.execution_time,
            }
        )


@bp.route("/api/kernel/status")
def kernel_status():
    """API: Get kernel status and memory info."""
    kernel = get_kernel_manager()
    status = kernel.get_status()
    memory = kernel.get_memory_info()
    variables = kernel.get_variables()

    return jsonify(
        {
            "status": status.to_dict(),
            "memory": memory.to_dict(),
            "variables": variables,
            "variable_count": len(variables),
        }
    )


@bp.route("/api/kernel/workspaces")
def kernel_workspaces():
    """API: Get list of workspaces in the kernel."""
    kernel = get_kernel_manager()
    workspaces = kernel.get_workspaces()

    return jsonify(
        {
            "workspaces": [ws.to_dict() for ws in workspaces],
            "count": len(workspaces),
        }
    )


@bp.route("/api/kernel/restart", methods=["POST"])
def kernel_restart():
    """API: Restart the kernel (clears all workspaces)."""
    kernel = get_kernel_manager()
    success = kernel.restart()

    return jsonify(
        {
            "success": success,
            "message": "Kernel restarted" if success else "Failed to restart kernel",
        }
    )


@bp.route("/api/kernel/workspaces/<name>", methods=["DELETE"])
def kernel_delete_workspace(name):
    """API: Delete a workspace from the kernel."""
    kernel = get_kernel_manager()
    success, message = kernel.delete_workspace(name)

    return jsonify(
        {
            "success": success,
            "message": message,
            "name": name,
        }
    ), (200 if success else 400)


@bp.route("/api/create/code", methods=["POST"])
def api_create_code():
    """
    API: Create a code entry in the timeline.

    Expects JSON body with:
        - code: Python code string
        - output: execution output
        - error: True if the output is an error

    Returns:
        - success: True/False
        - entry_id: ID of created entry
    """
    data = request.get_json()
    if not data or "code" not in data:
        return jsonify({"error": "code required"}), 400

    code = data["code"]
    output = data.get("output", "")
    is_error = data.get("error", False)

    # Store code and output as JSON in body
    body = json.dumps({"code": code, "output": output, "error": is_error})

    entry = Entry(type=Entry.TYPE_CODE, title=None, body=body)
    db.session.add(entry)
    db.session.commit()

    return jsonify({"success": True, "entry_id": entry.id})


@bp.route("/api/create/data", methods=["POST"])
def api_create_data():
    """
    API: Create a new data entry from the plot viewer.

    Expects JSON body with:
        - run_number: int (single run) OR
        - run_numbers: list of int (multi-run)
        - state_id: str
        - workspace: str (dsp_all, dsp_bank, dsp_column)
        - selected_spectra: list of int (for multi-spectrum workspaces)
        - x_range: [min, max] (optional, zoom state)
        - y_range: [min, max] (optional, zoom state)
        - snapshot: str (optional, filename of pre-uploaded PNG snapshot)
        - title: str (optional, defaults to run title)
        - note: str (optional, user's annotation)
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    # Support both single run_number and run_numbers array
    run_number = data.get("run_number")
    run_numbers = data.get("run_numbers", [])

    # Normalize to run_numbers list
    if run_number and not run_numbers:
        run_numbers = [int(run_number)]
    elif run_numbers:
        run_numbers = [int(r) for r in run_numbers]

    state_id = data.get("state_id")
    workspace = data.get("workspace", "dsp_all")
    selected_spectra = data.get("selected_spectra", [])
    x_range = data.get("x_range")  # [min, max] or None
    y_range = data.get("y_range")  # [min, max] or None
    snapshot = data.get("snapshot")  # PNG filename from upload-snapshot
    title = data.get("title", "").strip()
    note = data.get("note", "").strip()

    # state_id is now optional (may be None for instruments without state concept)
    if not run_numbers:
        return jsonify({"error": "run_number(s) required"}), 400

    # Get run metadata for default title
    config = NotebookConfig.get_config()
    if not config.is_configured:
        return jsonify({"error": "Notebook IPTS not configured"}), 400

    # Build the entry body as JSON
    entry_body = {
        "run_numbers": run_numbers,  # Always store as array
        "run_number": run_numbers[0],  # Backward compat: first run
        "state_id": state_id,
        "workspace": workspace,
        "selected_spectra": selected_spectra,
        "ipts": config.ipts,
        "note": note,
    }

    # Include zoom range if provided
    if x_range:
        entry_body["x_range"] = x_range
    if y_range:
        entry_body["y_range"] = y_range

    # Include snapshot filename if provided
    if snapshot:
        entry_body["snapshot"] = snapshot

    # Generate title if not provided
    if not title:
        if len(run_numbers) == 1:
            metadata = get_run_metadata_quick(config.ipts, run_numbers[0])
            title = f"Run {run_numbers[0]}: {metadata.get('title', 'Untitled')}"
        else:
            title = f"Runs {', '.join(map(str, run_numbers[:3]))}"
            if len(run_numbers) > 3:
                title += f"... ({len(run_numbers)} total)"

    # Create the entry
    entry = Entry(
        type=Entry.TYPE_DATA,
        title=title,
        body=json.dumps(entry_body),
    )

    db.session.add(entry)
    db.session.commit()

    run_desc = str(run_numbers[0]) if len(run_numbers) == 1 else f"{len(run_numbers)} runs"
    return jsonify(
        {
            "success": True,
            "entry_id": entry.id,
            "message": f"Data entry created for {run_desc}",
        }
    )


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

    return jsonify(
        {
            "ipts": config.ipts,
            "states": state_ids,
            "count": len(state_ids),
        }
    )


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

    return jsonify(
        {
            "state_id": state_id,
            "ipts": config.ipts,
            "runs": [r.to_dict() for r in runs],
            "count": len(runs),
        }
    )


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


@bp.route("/api/runs/<int:run_number>/metadata")
def api_get_run_metadata(run_number):
    """
    API: Get metadata (title, duration, start_time) for a specific run.

    This endpoint loads metadata lazily - call it after getting the run list
    to populate metadata for display. It reads directly from the native NeXus
    file to get accurate metadata quickly.

    Query params:
        - state_id: (optional) The state ID (not used, metadata from native file)
    """
    config = NotebookConfig.get_config()

    if not config.is_configured:
        return jsonify({"error": "Notebook IPTS not configured"}), 400

    # Read metadata directly from native NeXus file (faster than reduced file)
    metadata = get_run_metadata_quick(config.ipts, run_number)

    return jsonify(
        {
            "run_number": run_number,
            "title": metadata.get("title", ""),
            "duration": metadata.get("duration", 0.0),
            "start_time": metadata.get("start_time", ""),
        }
    )


@bp.route("/api/runs/metadata/batch", methods=["POST"])
def api_get_run_metadata_batch():
    """
    API: Get metadata for multiple runs in a single request.

    Accepts JSON body with 'run_numbers' array.
    Returns metadata for all requested runs.
    """
    config = NotebookConfig.get_config()

    if not config.is_configured:
        return jsonify({"error": "Notebook IPTS not configured"}), 400

    data = request.get_json()
    if not data or "run_numbers" not in data:
        return jsonify({"error": "run_numbers array required in request body"}), 400

    run_numbers = data["run_numbers"]
    if not isinstance(run_numbers, list):
        return jsonify({"error": "run_numbers must be an array"}), 400

    results = {}
    for run_number in run_numbers[:50]:  # Limit to 50 runs per batch
        try:
            run_num = int(run_number)
            metadata = get_run_metadata_quick(config.ipts, run_num)
            results[str(run_num)] = {
                "run_number": run_num,
                "title": metadata.get("title", ""),
                "duration": metadata.get("duration", 0.0),
                "start_time": metadata.get("start_time", ""),
            }
        except (ValueError, TypeError):
            continue

    return jsonify({"metadata": results})


@bp.route("/api/runs/<int:run_number>/plot-data")
def api_get_plot_data(run_number):
    """
    API: Get plot data for a specific reduced run.

    Returns JSON suitable for Plotly.js visualization.

    Query params:
        - state_id: (required) The state ID for the reduction
        - workspace: Which workspace to return. Options:
            - "all" (default): Return all workspaces (for overview)
            - "dsp_all": Combined d-spacing data
            - "dsp_bank": Per-bank data
            - "dsp_column": Per-column data
            - Or a numeric index (0, 1, 2...)
    """
    from ..services.data import load_reduced_data_for_plot

    config = NotebookConfig.get_config()

    if not config.is_configured:
        return jsonify({"error": "Notebook IPTS not configured"}), 400

    state_id = request.args.get("state_id", "").strip()
    if not state_id:
        return jsonify({"error": "state_id parameter required"}), 400

    workspace_param = request.args.get("workspace", "all").strip()

    # Find the reduced file for this run
    runs = discover_reduced_runs(config.ipts, state_id, lite=True, latest_only=True)
    matching = [r for r in runs if r.run_number == run_number]

    if not matching:
        return jsonify({"error": f"Run {run_number} not found in state {state_id}"}), 404

    reduced_file = matching[0].reduced_file

    # Determine workspace selection
    workspace_index = None
    if workspace_param != "all":
        # Try parsing as integer index
        try:
            workspace_index = int(workspace_param)
        except ValueError:
            # Treat as name substring match
            workspace_index = workspace_param

    try:
        # Debug logging
        print(f"DEBUG: Loading plot data for run {run_number}")
        print(f"DEBUG: File path: {reduced_file}")
        print(f"DEBUG: File suffix: {reduced_file.suffix if hasattr(reduced_file, 'suffix') else 'N/A'}")
        print(f"DEBUG: File exists: {reduced_file.exists() if hasattr(reduced_file, 'exists') else 'N/A'}")
        
        workspace_name = f"run_{run_number}"
        plot_data = load_reduced_data_for_plot(
            reduced_file, workspace_name, workspace_index=workspace_index
        )
        
        print(f"DEBUG: Successfully loaded plot data")

        # Add run info to the response
        plot_data["run_number"] = run_number
        plot_data["state_id"] = state_id
        plot_data["reduced_file"] = str(reduced_file)

        return jsonify(plot_data)

    except Exception as e:
        print(f"DEBUG: Error loading plot data: {e}")
        import traceback
        traceback.print_exc()
        return (
            jsonify(
                {
                    "error": f"Failed to load plot data: {str(e)}",
                    "run_number": run_number,
                    "state_id": state_id,
                }
            ),
            500,
        )


@bp.route("/api/runs/multi/plot-data")
def api_get_multi_plot_data():
    """
    API: Get plot data for multiple runs overlaid.

    Returns JSON suitable for Plotly.js visualization with multiple traces.

    Query params:
        - runs: (required, multiple) Run numbers to include
        - state_id: (required) The state ID for the reduction
        - workspace: Which workspace to return (default: dsp_all)
    """
    from ..services.data import load_reduced_data_for_plot

    config = NotebookConfig.get_config()

    if not config.is_configured:
        return jsonify({"error": "Notebook IPTS not configured"}), 400

    run_numbers = request.args.getlist("runs", type=int)
    if not run_numbers:
        return jsonify({"error": "At least one 'runs' parameter required"}), 400

    state_id = request.args.get("state_id", "").strip()
    if not state_id:
        return jsonify({"error": "state_id parameter required"}), 400

    workspace_param = request.args.get("workspace", "dsp_all").strip()

    # Find the reduced files for these runs
    runs = discover_reduced_runs(config.ipts, state_id, lite=True, latest_only=True)
    run_map = {r.run_number: r for r in runs}

    # Collect plot data for each run
    multi_data = {
        "type": "multi",
        "runs": [],
        "state_id": state_id,
        "workspace": workspace_param,
    }

    # Determine workspace selection
    workspace_index = None
    if workspace_param != "all":
        try:
            workspace_index = int(workspace_param)
        except ValueError:
            workspace_index = workspace_param

    for run_number in run_numbers:
        if run_number not in run_map:
            multi_data["runs"].append(
                {
                    "run_number": run_number,
                    "error": f"Run {run_number} not found in state {state_id}",
                }
            )
            continue

        reduced_file = run_map[run_number].reduced_file

        try:
            workspace_name = f"run_{run_number}"
            plot_data = load_reduced_data_for_plot(
                reduced_file, workspace_name, workspace_index=workspace_index
            )
            plot_data["run_number"] = run_number
            plot_data["reduced_file"] = str(reduced_file)
            multi_data["runs"].append(plot_data)

        except Exception as e:
            multi_data["runs"].append(
                {
                    "run_number": run_number,
                    "error": str(e),
                }
            )

    return jsonify(multi_data)


# ---------- PV Log API endpoints ----------


@bp.route("/api/pvlog/search", methods=["GET"])
def pvlog_search():
    """Search for PV channel names.

    Query params:
        pattern: PV name or partial name (will be wrapped in % for LIKE)
    Returns:
        JSON {results: [pv_name, ...]} or {error: ...}
    """
    pattern = request.args.get("pattern", "").strip()
    if not pattern:
        return jsonify({"error": "No search pattern provided", "results": []})

    # Check if it's an alias first
    from neutronote.services.pvlog import PVLogService

    if PVLogService.is_alias(pattern):
        aliases = PVLogService.list_aliases()
        alias_info = aliases[pattern.lower()]
        return jsonify({"results": alias_info["pvs"], "alias": pattern.lower()})

    # Otherwise, search Oracle
    try:
        svc = PVLogService()
        results = svc.search_channels(pattern)
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e), "results": []})


@bp.route("/api/pvlog/query", methods=["GET"])
def pvlog_query():
    """Query PV time-series data.

    Query params:
        pv: PV name(s) – can be repeated
        start: ISO datetime string
        end: ISO datetime string
    Returns:
        JSON {traces: [{name, x, y, units, dtype, count}, ...]}
    """
    pv_names = request.args.getlist("pv")
    start_str = request.args.get("start", "")
    end_str = request.args.get("end", "")

    if not pv_names:
        return jsonify({"error": "No PV names provided"})

    from datetime import datetime

    try:
        start = datetime.fromisoformat(start_str) if start_str else None
        end = datetime.fromisoformat(end_str) if end_str else None
    except ValueError as e:
        return jsonify({"error": f"Invalid date format: {e}"})

    if not start or not end:
        # Fall back to notebook config dates
        config = NotebookConfig.get_config()
        if config.has_dates:
            start = start or config.experiment_start
            end = end or config.experiment_end
        else:
            return jsonify({"error": "No time range specified and no experiment dates configured"})

    max_points = request.args.get("max_points", 5000, type=int)

    from neutronote.services.pvlog import PVLogService

    try:
        svc = PVLogService()
        traces = []
        for pv in pv_names:
            ts = svc.query_pv(pv, start, end, max_points=max_points)
            traces.append(ts.to_plot_json())
        return jsonify(
            {
                "traces": traces,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)})


@bp.route("/api/pvlog/aliases", methods=["GET"])
def pvlog_aliases():
    """Return the PV alias registry."""
    from neutronote.services.pvlog import PVLogService

    return jsonify(PVLogService.list_aliases())


@bp.route("/api/pvlog/resolve", methods=["GET"])
def pvlog_resolve():
    """Resolve an alias: query all candidate PVs, return active traces + runs.

    Query params:
        alias: alias name (e.g. "pressure", "temperature")
        start: ISO datetime (optional – falls back to experiment dates)
        end: ISO datetime (optional)
        max_points: int (default 5000)

    Returns JSON:
        {
            alias: str,
            traces: [{name, pv, x, y, units, dtype, count}, ...],
            runs: [{run_number, start, end}, ...],
            start: ISO str,
            end: ISO str,
            skipped: [pv_names with <2 points],
        }
    """
    alias = request.args.get("alias", "").strip()
    if not alias:
        return jsonify({"error": "No alias specified"})

    start_str = request.args.get("start", "")
    end_str = request.args.get("end", "")
    max_points = request.args.get("max_points", 5000, type=int)

    from datetime import datetime as _dt

    try:
        start = _dt.fromisoformat(start_str) if start_str else None
        end = _dt.fromisoformat(end_str) if end_str else None
    except ValueError as e:
        return jsonify({"error": f"Invalid date format: {e}"})

    if not start or not end:
        config = NotebookConfig.get_config()
        if config.has_dates:
            start = start or config.experiment_start
            end = end or config.experiment_end
        else:
            return jsonify({"error": "No time range and no experiment dates configured"})

    from neutronote.services.pvlog import PVLogService

    try:
        svc = PVLogService()

        # Resolve alias → active PV traces (with validity filtering)
        active, skipped_info = svc.resolve_alias(alias, start, end, max_points=max_points)
        traces = [ts.to_plot_json() for ts in active]

        # Build skipped list: PV names + reasons
        skipped = [s["pv"] for s in skipped_info]
        skipped_details = skipped_info  # [{pv, reason}, ...]

        # Run intervals for the same time range
        runs = svc.query_runs(start, end)

        return jsonify(
            {
                "alias": alias.lower(),
                "traces": traces,
                "runs": runs,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "skipped": skipped,
                "skipped_details": skipped_details,
            }
        )
    except Exception as e:
        import traceback

        logger.error("pvlog_resolve error: %s", traceback.format_exc())
        return jsonify({"error": str(e)})


@bp.route("/api/create/pvlog", methods=["POST"])
def create_pvlog():
    """Save a PV Log entry to the timeline.

    Expects JSON body: {title: str, data: {traces: [...], start, end}}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"})

    title = data.get("title", "").strip() or "PV Log"
    plot_data = data.get("data", {})

    # Store the full plot data as JSON in the entry body
    entry = Entry(
        type=Entry.TYPE_PVLOG,
        title=title,
        body=json.dumps(plot_data),
    )
    db.session.add(entry)
    db.session.commit()

    return jsonify({"success": True, "entry_id": entry.id})


# =========================================================================
# Tag API
# =========================================================================


@bp.route("/api/tags")
def list_tags():
    """Return all tags with their usage count (for autocomplete).

    Optional query param ``q`` filters by prefix (case-insensitive).
    """
    q = request.args.get("q", "").strip().lower()
    query = Tag.query
    if q:
        query = query.filter(Tag.name.ilike(f"{q}%"))
    tags = query.order_by(Tag.name).all()

    result = []
    for tag in tags:
        result.append({"id": tag.id, "name": tag.name, "count": tag.entries.count()})
    return jsonify(result)


@bp.route("/api/entries/<int:entry_id>/tags", methods=["POST"])
def add_tag_to_entry(entry_id):
    """Attach a tag to an entry.  Creates the tag if it doesn't exist.

    Expects JSON body: ``{"name": "brucite A"}``
    """
    entry = db.session.get(Entry, entry_id)
    if entry is None:
        return jsonify({"error": "Entry not found"}), 404

    data = request.get_json()
    if not data or not data.get("name", "").strip():
        return jsonify({"error": "Tag name required"}), 400

    name = data["name"].strip()

    # Find or create the tag (case-insensitive match)
    tag = Tag.query.filter(Tag.name.ilike(name)).first()
    if tag is None:
        tag = Tag(name=name)
        db.session.add(tag)

    # Attach if not already linked
    if tag not in entry.tags.all():
        entry.tags.append(tag)

    db.session.commit()
    return jsonify({"id": tag.id, "name": tag.name})


@bp.route("/api/entries/<int:entry_id>/tags/<int:tag_id>", methods=["DELETE"])
def remove_tag_from_entry(entry_id, tag_id):
    """Detach a tag from an entry."""
    entry = db.session.get(Entry, entry_id)
    if entry is None:
        return jsonify({"error": "Entry not found"}), 404

    tag = db.session.get(Tag, tag_id)
    if tag is None:
        return jsonify({"error": "Tag not found"}), 404

    if tag in entry.tags.all():
        entry.tags.remove(tag)

    # If the tag is now orphaned (no entries), delete it
    db.session.flush()
    if tag.entries.count() == 0:
        db.session.delete(tag)

    db.session.commit()
    return jsonify({"success": True})
