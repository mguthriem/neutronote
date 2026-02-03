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
from ..services.data import discover_state_ids, discover_reduced_runs, get_run_metadata_lazy, get_run_metadata_quick

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


# =============================================================================
# Development / Debug endpoints
# =============================================================================


@bp.route("/api/dev/reset-timeline", methods=["POST"])
def api_reset_timeline():
    """
    DEV ONLY: Delete all entries from the timeline.
    
    This is a destructive operation for development/testing purposes.
    """
    try:
        # Delete all entries
        num_deleted = Entry.query.delete()
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Deleted {num_deleted} entries from timeline",
            "deleted_count": num_deleted,
        })
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
    API: Execute Python code on the server.
    
    Expects JSON body with:
        - code: Python code string to execute
    
    Returns:
        - success: True/False
        - output: stdout from execution
        - error: error message if failed
        - execution_time: seconds taken
    """
    import subprocess
    import sys
    import time
    
    data = request.get_json()
    if not data or "code" not in data:
        return jsonify({"error": "code required"}), 400
    
    code = data["code"]
    
    # Timeout in seconds
    timeout = 60
    
    # Build the execution wrapper
    # We'll run the code in a subprocess with the same Python environment
    wrapper_code = f'''
import sys
import io
import traceback

# Redirect stdout
_stdout = io.StringIO()
sys.stdout = _stdout

try:
    exec(compile({repr(code)}, "<neutronote>", "exec"), {{"__name__": "__main__"}})
except Exception as e:
    traceback.print_exc(file=sys.stdout)

# Get output
sys.stdout = sys.__stdout__
print(_stdout.getvalue(), end="")
'''
    
    start_time = time.time()
    
    try:
        # Run in subprocess with timeout
        result = subprocess.run(
            [sys.executable, "-c", wrapper_code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=current_app.config.get("UPLOAD_FOLDER", "/tmp"),  # Working directory
        )
        
        execution_time = time.time() - start_time
        
        # Combine stdout and stderr
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr if output else result.stderr
        
        # Check return code
        if result.returncode != 0:
            return jsonify({
                "success": False,
                "error": output or f"Process exited with code {result.returncode}",
                "execution_time": execution_time
            })
        
        return jsonify({
            "success": True,
            "output": output,
            "execution_time": execution_time
        })
        
    except subprocess.TimeoutExpired:
        execution_time = time.time() - start_time
        return jsonify({
            "success": False,
            "error": f"Execution timed out after {timeout} seconds",
            "execution_time": execution_time
        })
    except Exception as e:
        execution_time = time.time() - start_time
        return jsonify({
            "success": False,
            "error": str(e),
            "execution_time": execution_time
        })


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
    body = json.dumps({
        "code": code,
        "output": output,
        "error": is_error
    })
    
    entry = Entry(
        type=Entry.TYPE_CODE,
        title=None,
        body=body
    )
    db.session.add(entry)
    db.session.commit()
    
    return jsonify({
        "success": True,
        "entry_id": entry.id
    })


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
    
    if not run_numbers or not state_id:
        return jsonify({"error": "run_number(s) and state_id required"}), 400
    
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
    return jsonify({
        "success": True,
        "entry_id": entry.id,
        "message": f"Data entry created for {run_desc}",
    })


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
    
    return jsonify({
        "run_number": run_number,
        "title": metadata.get("title", ""),
        "duration": metadata.get("duration", 0.0),
        "start_time": metadata.get("start_time", ""),
    })


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
        workspace_name = f"run_{run_number}"
        plot_data = load_reduced_data_for_plot(
            reduced_file, workspace_name, workspace_index=workspace_index
        )

        # Add run info to the response
        plot_data["run_number"] = run_number
        plot_data["state_id"] = state_id
        plot_data["reduced_file"] = str(reduced_file)

        return jsonify(plot_data)

    except Exception as e:
        return jsonify({
            "error": f"Failed to load plot data: {str(e)}",
            "run_number": run_number,
            "state_id": state_id,
        }), 500


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
            multi_data["runs"].append({
                "run_number": run_number,
                "error": f"Run {run_number} not found in state {state_id}",
            })
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
            multi_data["runs"].append({
                "run_number": run_number,
                "error": str(e),
            })

    return jsonify(multi_data)

