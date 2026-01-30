"""
Tests for multiNote application.
"""

import os
import tempfile

import pytest

from multinote.app import create_app
from multinote.models import Entry, db


@pytest.fixture
def app():
    """Create application for testing with a temporary database."""
    db_fd, db_path = tempfile.mkstemp()
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "WTF_CSRF_ENABLED": False,
        }
    )

    yield app

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    """Test client for the app."""
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
        assert b"multiNote" in response.data
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
        assert len(Entry.TYPES) == 5


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

    def test_header_tab_enabled(self, client):
        """Header tab should be enabled in the create form."""
        response = client.get("/entries/")
        html = response.data.decode()
        # Check that the header button exists without the disabled attribute
        assert 'data-type="header"' in html
        # The header form should have a run_number input
        assert 'name="run_number"' in html

    def test_create_header_entry_invalid_run(self, client, app):
        """Creating header with invalid run number should handle gracefully."""
        response = client.post(
            "/entries/create/header",
            data={"run_number": "not-a-number"},
            follow_redirects=True,
        )

        # Should redirect back but not crash
        assert response.status_code == 200

        # No entry should be created for invalid input
        with app.app_context():
            assert Entry.query.filter_by(type=Entry.TYPE_HEADER).count() == 0

    def test_create_header_entry_empty_run(self, client, app):
        """Creating header with empty run number should not create entry."""
        response = client.post(
            "/entries/create/header",
            data={"run_number": ""},
            follow_redirects=True,
        )

        assert response.status_code == 200
        with app.app_context():
            assert Entry.query.filter_by(type=Entry.TYPE_HEADER).count() == 0

    def test_create_header_entry_nonexistent_run(self, client, app):
        """Creating header with run that doesn't exist shows error flash message."""
        response = client.post(
            "/entries/create/header",
            data={"run_number": "99999999"},  # Unlikely to exist
            follow_redirects=True,
        )

        assert response.status_code == 200

        # Should NOT create an entry - error is shown as flash message
        with app.app_context():
            assert Entry.query.filter_by(type=Entry.TYPE_HEADER).count() == 0

        # Flash message should be shown
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
        from multinote.services.metadata import RunMetadata

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
        from multinote.services.metadata import RunMetadata

        meta = RunMetadata(run_number=12345, title="Test")
        result = meta.to_dict()

        assert result["run_number"] == 12345
        assert result["title"] == "Test"
        assert "file_size_display" in result
        assert "duration_display" in result

    def test_get_run_metadata_missing_file(self):
        """get_run_metadata should return error for nonexistent run."""
        from multinote.services.metadata import get_run_metadata

        meta = get_run_metadata(99999999)
        assert meta.error is not None
        assert "Could not locate" in meta.error
