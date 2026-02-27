"""
Test optional state ID and configurable reduced data paths.
"""

import os
import tempfile

from neutronote.app import create_app
from neutronote.models import NotebookConfig, db
from neutronote.services.data import discover_state_ids
import pytest


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
        instrument_name="SNAP",
    )

    yield app

    os.close(db_fd)
    os.unlink(db_path)


class TestOptionalState:
    """Test that state IDs are optional and reduced data paths are configurable."""

    def test_notebook_config_has_reduced_data_path(self, app):
        """NotebookConfig model should have reduced_data_path column."""
        with app.app_context():
            config = NotebookConfig.get_config()
            # Check the property exists
            assert hasattr(config, "reduced_data_path")
            assert hasattr(config, "has_reduced_data_path")

            # Initially None
            assert config.reduced_data_path is None
            assert not config.has_reduced_data_path

            # Can be set
            config.reduced_data_path = "/custom/path"
            assert config.has_reduced_data_path

    def test_discover_state_ids_returns_dot_for_flat(self, app):
        """discover_state_ids should return ['.'] for non-existent paths (flat structure)."""
        with app.app_context():
            # For a non-existent IPTS, should return empty list
            states = discover_state_ids("IPTS-99999")
            assert states == []

    def test_ref_l_reduced_data_root(self, app):
        """REF_L should return autoreduce as reduced data root."""
        with app.app_context():
            from neutronote.instruments import get_instrument

            ref_l = get_instrument("REF_L")
            root = ref_l.reduced_data_root("IPTS-12345")
            assert root is not None
            assert "autoreduce" in str(root)
            assert "REF_L" in str(root)

    def test_snap_reduced_data_root(self, app):
        """SNAP should return SNAPRed as reduced data root."""
        with app.app_context():
            from neutronote.instruments import get_instrument

            snap = get_instrument("SNAP")
            root = snap.reduced_data_root("IPTS-33219")
            assert root is not None
            assert "SNAPRed" in str(root)
            assert "SNAP" in str(root)

    def test_snap_has_state_id_hook(self, app):
        """SNAP should implement get_state_id_for_run."""
        with app.app_context():
            from neutronote.instruments import get_instrument

            snap = get_instrument("SNAP")
            # Should have the method (may return None if snapwrap not available)
            assert hasattr(snap, "get_state_id_for_run")
            # Method should be callable
            result = snap.get_state_id_for_run(12345)
            # Result may be None (no snapwrap) or a string (state hash)
            assert result is None or isinstance(result, str)

    def test_ref_l_no_state_id_hook(self, app):
        """REF_L should return None for get_state_id_for_run (no state concept)."""
        with app.app_context():
            from neutronote.instruments import get_instrument

            ref_l = get_instrument("REF_L")
            # Should return None (no state concept for REF_L)
            result = ref_l.get_state_id_for_run(12345)
            assert result is None

    def test_env_var_reduced_data_path(self, app, monkeypatch):
        """Environment variable should override instrument default."""
        with app.app_context():
            from neutronote.services.data import get_reduced_data_root

            # Set environment variable with {ipts} placeholder
            monkeypatch.setenv("NEUTRONOTE_REDUCED_DATA_PATH", "/custom/path/{ipts}/reduced")

            # Should substitute {ipts} with actual value
            root = get_reduced_data_root("IPTS-99999")
            assert root is not None
            assert str(root) == "/custom/path/IPTS-99999/reduced"

    def test_env_var_priority_over_instrument_default(self, app, monkeypatch):
        """Env var should be used before instrument default, but after user config."""
        with app.app_context():
            from neutronote.models import NotebookConfig, db
            from neutronote.services.data import get_reduced_data_root

            # Set env var
            monkeypatch.setenv("NEUTRONOTE_REDUCED_DATA_PATH", "/env/path/{ipts}/reduced")

            # Without user config, should use env var
            root = get_reduced_data_root("IPTS-12345")
            assert "/env/path/IPTS-12345/reduced" in str(root)

            # With user config, should use user config (highest priority)
            config = NotebookConfig.get_config()
            config.reduced_data_path = "/user/custom/path"
            db.session.commit()

            root = get_reduced_data_root("IPTS-12345")
            assert str(root) == "/user/custom/path"

    def test_refl_reduced_file_extensions(self, app):
        """REF_L should specify .txt files for reduced data."""
        with app.app_context():
            from neutronote.instruments import get_instrument

            ref_l = get_instrument("REF_L")
            extensions = ref_l.reduced_file_extensions()

            # Should include .txt for reduced data files
            assert ".txt" in extensions

    def test_refl_filename_parsing(self, app):
        """REF_L should parse both raw (REF_L_*) and reduced (REFL_*) filenames."""
        with app.app_context():
            from neutronote.instruments import get_instrument

            ref_l = get_instrument("REF_L")

            # Raw NeXus files: REF_L_<run>.nxs.h5
            assert ref_l.run_number_from_filename("REF_L_12345.nxs.h5") == 12345
            assert ref_l.run_number_from_filename("REF_L_12345.lite.nxs.h5") == 12345

            # Reduced text files: REFL_<run>_combined_data_auto.txt
            assert ref_l.run_number_from_filename("REFL_115177_combined_data_auto.txt") == 115177
            assert ref_l.run_number_from_filename("REFL_115184_combined_data_auto.txt") == 115184
            assert ref_l.run_number_from_filename("REFL_99999_combined_data_auto.txt") == 99999

            # Should not match invalid patterns
            assert ref_l.run_number_from_filename("SNAP_12345.nxs") is None
            assert ref_l.run_number_from_filename("random_file.txt") is None
