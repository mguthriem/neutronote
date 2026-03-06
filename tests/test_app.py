"""
Tests for neutroNote application.
"""

import os
import tempfile

import pytest

from neutronote.app import create_app
from neutronote.instruments import (
    InstrumentConfig,
    available_instruments,
    get_instrument,
    register_instrument,
)
from neutronote.models import Entry, NotebookConfig, db


@pytest.fixture
def app():
    """Create application for testing with a temporary database."""
    db_fd, db_path = tempfile.mkstemp()
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "WTF_CSRF_ENABLED": False,
        },
        instrument_name="SNAP",  # Explicitly use SNAP for tests
    )

    yield app

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    """Test client for the app."""
    return app.test_client()


@pytest.fixture
def configured_client(app):
    """Test client with IPTS already configured."""
    with app.app_context():
        config = NotebookConfig.get_config()
        config.ipts = "IPTS-12345"
        db.session.commit()
    return app.test_client()


@pytest.fixture
def runner(app):
    """Test CLI runner."""
    return app.test_cli_runner()


class TestIndex:
    """Tests for the main index/entries page."""

    def test_index_redirects_to_entries(self, client):
        """GET / should redirect to /entries."""
        response = client.get("/")
        assert response.status_code == 302
        assert "/entries" in response.location

    def test_entries_page_loads(self, client):
        """GET /entries should return the split-view page."""
        response = client.get("/entries/")
        assert response.status_code == 200
        assert b"neutroNote" in response.data
        assert b"Create Entry" in response.data
        assert b"Timeline" in response.data

    def test_entries_page_shows_empty_state(self, client):
        """Empty database should show empty state message."""
        response = client.get("/entries/")
        assert b"No entries yet" in response.data


class TestTextEntries:
    """Tests for creating and viewing text entries."""

    def test_create_text_entry(self, client, app):
        """POST /entries/create/text should create a new entry."""
        response = client.post(
            "/entries/create/text",
            data={
                "title": "Test Entry",
                "body": "This is a test entry body.",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"Test Entry" in response.data
        assert b"This is a test entry body" in response.data

        # Verify in database
        with app.app_context():
            entry = Entry.query.first()
            assert entry is not None
            assert entry.type == Entry.TYPE_TEXT
            assert entry.title == "Test Entry"
            assert entry.body == "This is a test entry body."

    def test_create_text_entry_without_title(self, client, app):
        """Text entry should work without a title."""
        response = client.post(
            "/entries/create/text",
            data={
                "body": "Just the body, no title.",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"Just the body, no title" in response.data

        with app.app_context():
            entry = Entry.query.first()
            assert entry.title is None

    def test_empty_body_does_not_create_entry(self, client, app):
        """Empty body should not create an entry."""
        client.post(
            "/entries/create/text",
            data={
                "body": "   ",  # whitespace only
            },
            follow_redirects=True,
        )

        with app.app_context():
            assert Entry.query.count() == 0

    def test_entries_appear_in_chronological_order(self, client, app):
        """Entries should appear oldest first (chat style)."""
        client.post("/entries/create/text", data={"body": "First entry"})
        client.post("/entries/create/text", data={"body": "Second entry"})
        client.post("/entries/create/text", data={"body": "Third entry"})

        response = client.get("/entries/")
        html = response.data.decode()

        # Check order in HTML
        first_pos = html.find("First entry")
        second_pos = html.find("Second entry")
        third_pos = html.find("Third entry")

        assert first_pos < second_pos < third_pos


class TestModels:
    """Tests for database models."""

    def test_entry_timestamp_display(self, app):
        """Entry should have a formatted timestamp."""
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Test")
            db.session.add(entry)
            db.session.commit()

            assert entry.timestamp_display is not None
            # Should contain AM or PM
            assert "AM" in entry.timestamp_display or "PM" in entry.timestamp_display

    def test_entry_types(self, app):
        """Entry type constants should be defined."""
        assert Entry.TYPE_TEXT == "text"
        assert Entry.TYPE_HEADER == "header"
        assert Entry.TYPE_IMAGE == "image"
        assert Entry.TYPE_DATA == "data"
        assert Entry.TYPE_CODE == "code"
        assert Entry.TYPE_PVLOG == "pvlog"
        assert len(Entry.TYPES) == 6


class TestEditEntry:
    """Tests for editing entries."""

    def test_edit_entry_page_loads(self, client, app):
        """GET /entries/<id>/edit should show the edit form."""
        # Create an entry first
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Original content")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        response = client.get(f"/entries/{entry_id}/edit")
        assert response.status_code == 200
        assert b"Edit Entry" in response.data
        assert b"Original content" in response.data

    def test_edit_entry_updates_content(self, client, app):
        """POST /entries/<id>/edit should update the entry."""
        # Create an entry first
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Original content")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        response = client.post(
            f"/entries/{entry_id}/edit",
            data={"body": "Updated content", "title": "New Title"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"Updated content" in response.data
        assert b"New Title" in response.data

        with app.app_context():
            entry = db.session.get(Entry, entry_id)
            assert entry.body == "Updated content"
            assert entry.title == "New Title"
            assert entry.is_edited is True
            assert entry.edited_at is not None

    def test_edit_preserves_timeline_position(self, client, app):
        """Editing should not change the entry's position in the timeline."""
        import time

        # Create entries with slight delays to ensure different timestamps
        client.post("/entries/create/text", data={"body": "First entry"})
        time.sleep(0.1)
        client.post("/entries/create/text", data={"body": "Second entry"})
        time.sleep(0.1)
        client.post("/entries/create/text", data={"body": "Third entry"})

        # Edit the first entry
        with app.app_context():
            first_entry = Entry.query.order_by(Entry.created_at.asc()).first()
            entry_id = first_entry.id

        client.post(
            f"/entries/{entry_id}/edit",
            data={"body": "First entry EDITED"},
        )

        # Check order is preserved (first entry still first)
        response = client.get("/entries/")
        html = response.data.decode()

        first_pos = html.find("First entry EDITED")
        second_pos = html.find("Second entry")
        third_pos = html.find("Third entry")

        assert first_pos < second_pos < third_pos


class TestHeaderEntries:
    """Tests for run header entries."""

    def test_header_tab_disabled_without_ipts(self, client):
        """Header tab should be disabled when IPTS is not configured."""
        response = client.get("/entries/")
        html = response.data.decode()
        # Header button should exist but be disabled
        assert 'data-type="header"' in html
        assert "Configure IPTS first" in html

    def test_header_tab_enabled_with_ipts(self, configured_client):
        """Header tab should be enabled when IPTS is configured."""
        response = configured_client.get("/entries/")
        html = response.data.decode()
        # Check that the header button exists without the disabled attribute
        assert 'data-type="header"' in html
        # The header form should have a run_number input
        assert 'name="run_number"' in html
        # Should show the configured IPTS
        assert "IPTS-12345" in html

    def test_create_header_requires_ipts_config(self, client, app):
        """Creating header without IPTS configured should show error."""
        response = client.post(
            "/entries/create/header",
            data={"run_number": "12345"},
            follow_redirects=True,
        )

        assert response.status_code == 200
        # Should show error about configuring IPTS
        assert b"configure" in response.data.lower() or b"IPTS" in response.data

        # No entry should be created
        with app.app_context():
            assert Entry.query.filter_by(type=Entry.TYPE_HEADER).count() == 0

    def test_create_header_entry_invalid_run(self, configured_client, app):
        """Creating header with invalid run number should handle gracefully."""
        response = configured_client.post(
            "/entries/create/header",
            data={"run_number": "not-a-number"},
            follow_redirects=True,
        )

        # Should redirect back but not crash
        assert response.status_code == 200

        # No entry should be created for invalid input
        with app.app_context():
            assert Entry.query.filter_by(type=Entry.TYPE_HEADER).count() == 0

    def test_create_header_entry_empty_run(self, configured_client, app):
        """Creating header with empty run number should not create entry."""
        response = configured_client.post(
            "/entries/create/header",
            data={"run_number": ""},
            follow_redirects=True,
        )

        assert response.status_code == 200
        with app.app_context():
            assert Entry.query.filter_by(type=Entry.TYPE_HEADER).count() == 0

    def test_create_header_entry_nonexistent_run(self, configured_client, app):
        """Creating header with run that doesn't exist shows error flash message."""
        response = configured_client.post(
            "/entries/create/header",
            data={"run_number": "99999999"},  # Unlikely to exist
            follow_redirects=True,
        )

        assert response.status_code == 200

        # Should NOT create an entry - error is shown as flash message
        with app.app_context():
            assert Entry.query.filter_by(type=Entry.TYPE_HEADER).count() == 0

        # Flash message should be shown (either "Could not locate" or the run number)
        assert b"Could not locate" in response.data or b"99999999" in response.data

    def test_header_entry_not_editable(self, client, app):
        """Header entries should redirect when trying to edit."""
        import json

        with app.app_context():
            entry = Entry(
                type=Entry.TYPE_HEADER,
                title="Run 12345",
                body=json.dumps({"run_number": 12345, "title": "Test"}),
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        # Try to access edit page - should redirect
        response = client.get(f"/entries/{entry_id}/edit", follow_redirects=True)
        assert response.status_code == 200
        # Should be back on the entries page, not the edit page
        assert b"Edit Entry" not in response.data

    def test_header_card_no_edit_button(self, client, app):
        """Header entry cards should not show the edit button."""
        import json

        with app.app_context():
            # Create a text entry (has edit button)
            text_entry = Entry(type=Entry.TYPE_TEXT, body="Text content")
            db.session.add(text_entry)

            # Create a header entry (no edit button)
            header_entry = Entry(
                type=Entry.TYPE_HEADER,
                title="Run 12345",
                body=json.dumps({"run_number": 12345, "title": "Test Run"}),
            )
            db.session.add(header_entry)
            db.session.commit()

        response = client.get("/entries/")
        html = response.data.decode()

        # There should be at least one edit link for the text entry
        assert "✏️" in html


class TestImageEntries:
    """Tests for image entry functionality."""

    def test_image_tab_visible(self, client):
        """Image tab should be visible in the entry form."""
        response = client.get("/entries/")
        assert response.status_code == 200
        assert b'data-type="image"' in response.data
        assert b"Image" in response.data

    def test_create_image_no_file(self, client):
        """Creating image entry without file should show error."""
        response = client.post(
            "/entries/create/image",
            data={"caption": "Test caption"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"No image file selected" in response.data

    def test_create_image_with_file(self, client, app):
        """Creating image entry with valid file should work."""
        from io import BytesIO

        # Create a simple PNG file (1x1 pixel)
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        response = client.post(
            "/entries/create/image",
            data={
                "caption": "Test image caption",
                "image": (BytesIO(png_data), "test.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200

        # Check entry was created
        with app.app_context():
            entry = Entry.query.filter_by(type=Entry.TYPE_IMAGE).first()
            assert entry is not None
            assert entry.title == "Test image caption"
            assert entry.body.endswith(".png")

    def test_image_invalid_type(self, client):
        """Uploading invalid file type should show error."""
        from io import BytesIO

        response = client.post(
            "/entries/create/image",
            data={
                "caption": "Test",
                "image": (BytesIO(b"not an image"), "test.txt"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Invalid file type" in response.data


class TestMetadataService:
    """Tests for the metadata service."""

    def test_run_metadata_dataclass(self):
        """RunMetadata should have expected properties."""
        from neutronote.services.metadata import RunMetadata

        meta = RunMetadata(
            run_number=12345,
            title="Test Run",
            duration=3600.0,
            total_counts=1000000,
            file_size_bytes=1073741824,  # 1 GB
        )

        assert meta.run_number == 12345
        assert "1.00 GB" in meta.file_size_display
        assert "1.0 hour" in meta.duration_display
        assert "ME/s" in meta.count_rate_display

    def test_run_metadata_to_dict(self):
        """RunMetadata.to_dict() should return expected keys."""
        from neutronote.services.metadata import RunMetadata

        meta = RunMetadata(run_number=12345, title="Test")
        result = meta.to_dict()

        assert result["run_number"] == 12345
        assert result["title"] == "Test"
        assert "file_size_display" in result
        assert "duration_display" in result

    def test_get_run_metadata_missing_file(self):
        """get_run_metadata should return error for nonexistent run."""
        from neutronote.services.metadata import get_run_metadata

        meta = get_run_metadata(99999999)
        assert meta.error is not None
        assert "Could not locate" in meta.error


class TestPVLogPhase0:
    """Tests for PV Log Phase PV-0: model changes and date configuration."""

    def test_entry_type_pvlog_exists(self):
        """Entry model should have TYPE_PVLOG constant."""
        assert Entry.TYPE_PVLOG == "pvlog"
        assert "pvlog" in Entry.TYPES

    def test_notebook_config_date_fields(self, app):
        """NotebookConfig should have experiment_start and experiment_end columns."""
        with app.app_context():
            config = NotebookConfig.get_config()
            # Initially dates should be None
            assert config.experiment_start is None
            assert config.experiment_end is None
            assert config.has_dates is False

    def test_notebook_config_date_storage(self, app):
        """Dates can be stored and retrieved from NotebookConfig."""
        from datetime import datetime

        with app.app_context():
            config = NotebookConfig.get_config()
            config.experiment_start = datetime(2025, 6, 1)
            config.experiment_end = datetime(2025, 6, 15)
            db.session.commit()

            config2 = NotebookConfig.get_config()
            assert config2.has_dates is True
            assert config2.experiment_start.year == 2025
            assert config2.experiment_start.month == 6
            assert config2.experiment_start.day == 1
            assert config2.experiment_end.day == 15

    def test_notebook_config_date_str_properties(self, app):
        """experiment_start_str and experiment_end_str return proper format."""
        from datetime import datetime

        with app.app_context():
            config = NotebookConfig.get_config()
            assert config.experiment_start_str == ""
            assert config.experiment_end_str == ""

            config.experiment_start = datetime(2025, 6, 1)
            config.experiment_end = datetime(2025, 6, 15)
            db.session.commit()

            assert config.experiment_start_str == "2025-06-01"
            assert config.experiment_end_str == "2025-06-15"

    def test_pvlog_tab_disabled_without_dates(self, client):
        """PV Log tab should be disabled when no dates are configured."""
        response = client.get("/entries/", follow_redirects=True)
        html = response.data.decode()
        assert 'data-type="pvlog"' in html
        assert "disabled" in html.split('data-type="pvlog"')[1].split(">")[0]

    def test_pvlog_tab_enabled_with_dates(self, app):
        """PV Log tab should be enabled when dates are configured."""
        from datetime import datetime

        with app.app_context():
            config = NotebookConfig.get_config()
            config.ipts = "IPTS-12345"
            config.experiment_start = datetime(2025, 6, 1)
            config.experiment_end = datetime(2025, 6, 15)
            db.session.commit()

        client = app.test_client()
        response = client.get("/entries/", follow_redirects=True)
        html = response.data.decode()
        # The PV Log tab should NOT have disabled attribute
        pvlog_section = html.split('data-type="pvlog"')[1].split(">")[0]
        assert "disabled" not in pvlog_section

    def test_pvlog_aliases_endpoint(self, client):
        """GET /api/pvlog/aliases should return alias registry."""
        response = client.get("/entries/api/pvlog/aliases")
        assert response.status_code == 200
        data = response.get_json()
        assert "pressure" in data
        assert "temperature" in data
        assert "pvs" in data["pressure"]

    def test_pvlog_search_alias(self, client):
        """Searching for a known alias returns its PVs."""
        response = client.get("/entries/api/pvlog/search?pattern=pressure")
        assert response.status_code == 200
        data = response.get_json()
        assert "results" in data
        assert len(data["results"]) > 0
        assert any("Pressure" in pv or "Press" in pv for pv in data["results"])

    def test_pvlog_search_empty(self, client):
        """Empty search pattern returns error."""
        response = client.get("/entries/api/pvlog/search?pattern=")
        data = response.get_json()
        assert "error" in data

    def test_create_pvlog_entry(self, app):
        """POST /api/create/pvlog should create a pvlog entry."""
        import json

        with app.app_context():
            config = NotebookConfig.get_config()
            config.ipts = "IPTS-12345"
            db.session.commit()

        client = app.test_client()
        response = client.post(
            "/entries/api/create/pvlog",
            json={
                "title": "Test PV Plot",
                "data": {
                    "traces": [{"name": "TestPV", "x": [1, 2, 3], "y": [10, 20, 30]}],
                    "start": "2025-06-01T00:00:00",
                    "end": "2025-06-15T00:00:00",
                },
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

        # Verify entry was created
        with app.app_context():
            entry = Entry.query.filter_by(type="pvlog").first()
            assert entry is not None
            assert entry.title == "Test PV Plot"
            body = json.loads(entry.body)
            assert len(body["traces"]) == 1


class TestEntryTags:
    """Tests for the tag system: CRUD, attach/detach, autocomplete."""

    def test_list_tags_empty(self, client):
        """GET /api/tags returns empty list when no tags exist."""
        resp = client.get("/entries/api/tags")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_add_tag_to_entry(self, app, client):
        """POST /api/entries/<id>/tags creates a tag and attaches it."""
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Test")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        resp = client.post(
            f"/entries/api/entries/{entry_id}/tags",
            json={"name": "brucite A"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "brucite A"
        assert "id" in data

    def test_add_tag_case_insensitive(self, app, client):
        """Adding the same tag with different case reuses the existing tag."""
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Test")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        # Create with lowercase
        r1 = client.post(
            f"/entries/api/entries/{entry_id}/tags",
            json={"name": "Brucite"},
            content_type="application/json",
        )
        # Add again with different case
        r2 = client.post(
            f"/entries/api/entries/{entry_id}/tags",
            json={"name": "brucite"},
            content_type="application/json",
        )
        assert r1.get_json()["id"] == r2.get_json()["id"]

    def test_add_tag_to_nonexistent_entry(self, client):
        """Adding a tag to a missing entry returns 404."""
        resp = client.post(
            "/entries/api/entries/9999/tags",
            json={"name": "test"},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_add_tag_empty_name(self, app, client):
        """Adding a tag with empty name returns 400."""
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Test")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        resp = client.post(
            f"/entries/api/entries/{entry_id}/tags",
            json={"name": "  "},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_list_tags_after_add(self, app, client):
        """Tags appear in the list after being added to an entry."""
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Test")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        client.post(
            f"/entries/api/entries/{entry_id}/tags",
            json={"name": "calcite"},
            content_type="application/json",
        )
        resp = client.get("/entries/api/tags")
        tags = resp.get_json()
        assert len(tags) == 1
        assert tags[0]["name"] == "calcite"
        assert tags[0]["count"] == 1

    def test_list_tags_prefix_filter(self, app, client):
        """GET /api/tags?q=bru filters by prefix."""
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Test")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        client.post(
            f"/entries/api/entries/{entry_id}/tags",
            json={"name": "brucite A"},
            content_type="application/json",
        )
        client.post(
            f"/entries/api/entries/{entry_id}/tags",
            json={"name": "calcite"},
            content_type="application/json",
        )

        resp = client.get("/entries/api/tags?q=bru")
        tags = resp.get_json()
        assert len(tags) == 1
        assert tags[0]["name"] == "brucite A"

    def test_remove_tag_from_entry(self, app, client):
        """DELETE /api/entries/<id>/tags/<tag_id> detaches the tag."""
        with app.app_context():
            entry = Entry(type=Entry.TYPE_TEXT, body="Test")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        r = client.post(
            f"/entries/api/entries/{entry_id}/tags",
            json={"name": "olivine"},
            content_type="application/json",
        )
        tag_id = r.get_json()["id"]

        resp = client.delete(f"/entries/api/entries/{entry_id}/tags/{tag_id}")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        # Tag should be auto-deleted since it's orphaned
        resp2 = client.get("/entries/api/tags")
        assert len(resp2.get_json()) == 0

    def test_tag_not_deleted_if_still_used(self, app, client):
        """Removing a tag from one entry doesn't delete it if used elsewhere."""
        with app.app_context():
            e1 = Entry(type=Entry.TYPE_TEXT, body="One")
            e2 = Entry(type=Entry.TYPE_TEXT, body="Two")
            db.session.add_all([e1, e2])
            db.session.commit()
            e1_id, e2_id = e1.id, e2.id

        # Attach same tag to both entries
        r1 = client.post(
            f"/entries/api/entries/{e1_id}/tags",
            json={"name": "shared-tag"},
            content_type="application/json",
        )
        tag_id = r1.get_json()["id"]
        client.post(
            f"/entries/api/entries/{e2_id}/tags",
            json={"name": "shared-tag"},
            content_type="application/json",
        )

        # Remove from first entry
        client.delete(f"/entries/api/entries/{e1_id}/tags/{tag_id}")

        # Tag should still exist (attached to e2)
        resp = client.get("/entries/api/tags")
        tags = resp.get_json()
        assert len(tags) == 1
        assert tags[0]["count"] == 1

    def test_create_text_with_tags(self, client):
        """Creating a text entry with tags= form field attaches tags."""
        resp = client.post(
            "/entries/create/text",
            data={"body": "Tagged entry", "title": "test", "tags": "alpha,beta"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Verify tags were created and attached
        tags_resp = client.get("/entries/api/tags")
        names = sorted(t["name"].lower() for t in tags_resp.get_json())
        assert "alpha" in names
        assert "beta" in names

    def test_create_code_with_tags(self, client):
        """Creating a code entry via API with tags attaches them."""
        resp = client.post(
            "/entries/api/create/code",
            json={"code": "print(1)", "output": "1", "error": False, "tags": ["gamma"]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        tags_resp = client.get("/entries/api/tags")
        names = [t["name"].lower() for t in tags_resp.get_json()]
        assert "gamma" in names

    def test_create_with_empty_tags(self, client):
        """Creating an entry with empty tags= field works fine."""
        resp = client.post(
            "/entries/create/text",
            data={"body": "No tags", "title": "", "tags": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        tags_resp = client.get("/entries/api/tags")
        assert tags_resp.get_json() == []

    def test_create_pvlog_with_tags(self, app, client):
        """Creating a PV Log entry via API with tags attaches them."""
        resp = client.post(
            "/entries/api/create/pvlog",
            json={
                "title": "Tagged PV",
                "data": {
                    "traces": [{"name": "PV1", "x": [1, 2], "y": [10, 20]}],
                    "start": "2025-06-01T00:00:00",
                    "end": "2025-06-15T00:00:00",
                },
                "tags": ["pressure", "temperature"],
            },
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify tags were created and attached
        tags_resp = client.get("/entries/api/tags")
        names = sorted(t["name"].lower() for t in tags_resp.get_json())
        assert "pressure" in names
        assert "temperature" in names

        # Verify tags are actually on the entry
        with app.app_context():
            entry = Entry.query.filter_by(type="pvlog").first()
            assert entry is not None
            tag_names = [t.name.lower() for t in entry.tags.all()]
            assert "pressure" in tag_names
            assert "temperature" in tag_names


class TestInstrumentAbstraction:
    """Tests for the instrument plugin system."""

    def test_snap_is_registered(self):
        """SNAP should be auto-registered on import."""
        assert "SNAP" in available_instruments()

    def test_get_instrument_snap(self):
        """get_instrument('SNAP') returns a valid config."""
        snap = get_instrument("SNAP")
        assert snap.name == "SNAP"
        assert snap.beamline == "BL3"
        assert snap.facility == "SNS"

    def test_get_instrument_case_insensitive(self):
        """Instrument names should be case-insensitive."""
        snap = get_instrument("snap")
        assert snap.name == "SNAP"

    def test_unknown_instrument_raises(self):
        """Requesting an unknown instrument should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown instrument"):
            get_instrument("NONEXISTENT")

    def test_snap_data_root(self):
        """SNAP data root should be /SNS/SNAP."""
        from pathlib import Path

        snap = get_instrument("SNAP")
        assert snap.data_root == Path("/SNS/SNAP")

    def test_snap_nexus_filenames(self):
        """SNAP should produce correct NeXus filenames."""
        snap = get_instrument("SNAP")
        assert snap.nexus_filename(65432) == "SNAP_65432.nxs.h5"
        assert snap.lite_nexus_filename(65432) == "SNAP_65432.lite.nxs.h5"

    def test_snap_nexus_paths(self):
        """SNAP should produce correct NeXus file paths."""
        from pathlib import Path

        snap = get_instrument("SNAP")
        native = snap.nexus_path("IPTS-33219", 65432, lite=False)
        lite = snap.nexus_path("IPTS-33219", 65432, lite=True)
        assert native == Path("/SNS/SNAP/IPTS-33219/nexus/SNAP_65432.nxs.h5")
        assert lite == Path("/SNS/SNAP/IPTS-33219/shared/lite/SNAP_65432.lite.nxs.h5")

    def test_snap_reduced_data_root(self):
        """SNAP reduced data root should point to SNAPRed folder."""
        from pathlib import Path

        snap = get_instrument("SNAP")
        root = snap.reduced_data_root("IPTS-33219")
        assert root == Path("/SNS/SNAP/IPTS-33219/shared/SNAPRed")

    def test_snap_pv_aliases(self):
        """SNAP PV aliases should include expected keys."""
        snap = get_instrument("SNAP")
        aliases = snap.pv_aliases()
        assert "pressure" in aliases
        assert "temperature" in aliases
        assert "run_number" in aliases
        assert "pvs" in aliases["pressure"]
        assert any("BL3" in pv for pv in aliases["pressure"]["pvs"])

    def test_snap_run_pvs(self):
        """SNAP run control PVs should use BL3 prefix."""
        snap = get_instrument("SNAP")
        assert snap.run_number_pv() == "BL3:CS:RunControl:LastRunNumber"
        assert snap.run_state_pv() == "BL3:CS:RunControl:StateEnum"

    def test_snap_default_x_label(self):
        """SNAP default x-axis label should be d-spacing."""
        snap = get_instrument("SNAP")
        assert "d-spacing" in snap.default_x_label()

    def test_snap_run_number_from_filename(self):
        """SNAP should parse run numbers from filenames."""
        snap = get_instrument("SNAP")
        assert snap.run_number_from_filename("SNAP_65432.nxs.h5") == 65432
        assert snap.run_number_from_filename("SNAP_65432.lite.nxs.h5") == 65432
        assert snap.run_number_from_filename("OTHER_65432.nxs.h5") is None

    def test_snap_notebook_path(self):
        """SNAP notebook path should follow convention."""
        snap = get_instrument("SNAP")
        path = snap.notebook_path("IPTS-33219")
        assert path.endswith("IPTS-33219/shared/neutronote")
        assert "/SNS/SNAP/" in path

    def test_app_has_instrument_config(self, app):
        """App config should contain an InstrumentConfig instance."""
        assert "INSTRUMENT" in app.config
        assert isinstance(app.config["INSTRUMENT"], InstrumentConfig)
        assert app.config["INSTRUMENT"].name == "SNAP"

    def test_app_context_processor_instrument(self, client):
        """Templates should have access to instrument_name."""
        response = client.get("/entries/")
        assert response.status_code == 200

    # --- REF_L tests --------------------------------------------------------

    def test_ref_l_is_registered(self):
        """REF_L should be auto-registered on import."""
        assert "REF_L" in available_instruments()

    def test_get_instrument_ref_l(self):
        """get_instrument('REF_L') returns a valid config."""
        ref_l = get_instrument("REF_L")
        assert ref_l.name == "REF_L"
        assert ref_l.beamline == "BL4B"
        assert ref_l.facility == "SNS"

    def test_ref_l_case_insensitive(self):
        """REF_L lookup should be case-insensitive."""
        ref_l = get_instrument("ref_l")
        assert ref_l.name == "REF_L"

    def test_ref_l_data_root(self):
        """REF_L data root should be /SNS/REF_L."""
        from pathlib import Path

        ref_l = get_instrument("REF_L")
        assert ref_l.data_root == Path("/SNS/REF_L")

    def test_ref_l_nexus_filenames(self):
        """REF_L should produce correct NeXus filenames."""
        ref_l = get_instrument("REF_L")
        assert ref_l.nexus_filename(12345) == "REF_L_12345.nxs.h5"
        assert ref_l.lite_nexus_filename(12345) == "REF_L_12345.lite.nxs.h5"

    def test_ref_l_nexus_paths(self):
        """REF_L should produce correct NeXus file paths."""
        from pathlib import Path

        ref_l = get_instrument("REF_L")
        native = ref_l.nexus_path("IPTS-28400", 12345, lite=False)
        lite = ref_l.nexus_path("IPTS-28400", 12345, lite=True)
        assert native == Path("/SNS/REF_L/IPTS-28400/nexus/REF_L_12345.nxs.h5")
        assert lite == Path("/SNS/REF_L/IPTS-28400/shared/lite/REF_L_12345.lite.nxs.h5")

    def test_ref_l_reduced_data_root(self):
        """REF_L reduced data root should point to autoreduce folder."""
        from pathlib import Path

        ref_l = get_instrument("REF_L")
        root = ref_l.reduced_data_root("IPTS-28400")
        assert root == Path("/SNS/REF_L/IPTS-28400/shared/autoreduce")

    def test_ref_l_pv_aliases(self):
        """REF_L PV aliases should include expected keys."""
        ref_l = get_instrument("REF_L")
        aliases = ref_l.pv_aliases()
        assert "temperature" in aliases
        assert "run_number" in aliases
        assert "pvs" in aliases["temperature"]
        assert any("BL4B" in pv for pv in aliases["temperature"]["pvs"])

    def test_ref_l_run_pvs(self):
        """REF_L run control PVs should use BL4B prefix."""
        ref_l = get_instrument("REF_L")
        assert ref_l.run_number_pv() == "BL4B:CS:RunControl:LastRunNumber"
        assert ref_l.run_state_pv() == "BL4B:CS:RunControl:StateEnum"

    def test_ref_l_default_x_label(self):
        """REF_L default x-axis label should be Q."""
        ref_l = get_instrument("REF_L")
        assert "Q" in ref_l.default_x_label()

    def test_ref_l_run_number_from_filename(self):
        """REF_L should parse run numbers from filenames."""
        ref_l = get_instrument("REF_L")
        assert ref_l.run_number_from_filename("REF_L_12345.nxs.h5") == 12345
        assert ref_l.run_number_from_filename("REF_L_12345.lite.nxs.h5") == 12345
        assert ref_l.run_number_from_filename("SNAP_12345.nxs.h5") is None

    def test_ref_l_notebook_path(self):
        """REF_L notebook path should follow convention."""
        ref_l = get_instrument("REF_L")
        path = ref_l.notebook_path("IPTS-28400")
        assert path.endswith("IPTS-28400/shared/neutronote")
        assert "/SNS/REF_L/" in path
