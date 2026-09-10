#!/usr/bin/env python3
"""
Unit tests for publish_to_archive_org.py's --metadata-only behavior and
schema validation.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import build_valid_archive
from publish_to_archive_org import (
    publish_item,
    sync_metadata,
    validate_metadata_schemas,
)


class PublishMetadataOnlyTest(unittest.TestCase):
    def test_validate_metadata_schemas_passes_without_media_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            fx.audio_path.unlink()  # Media file absent

            # Should not raise or exit
            validate_metadata_schemas(bands_dir)

    def test_validate_metadata_schemas_aborts_on_slug_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            fx.band_yaml.write_text(fx.band_yaml.read_text().replace("test-band", "wrong-slug"))

            with self.assertRaises(SystemExit):
                validate_metadata_schemas(bands_dir)

    def test_publish_item_metadata_only_syncs_without_media_upload(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "band.yaml"
            yaml_path.write_text("name: Test\nsameAs: []\n")

            with patch("internetarchive.modify_metadata", return_value=mock_response) as mock_modify:
                with patch("internetarchive.upload") as mock_upload:
                    ok = publish_item(
                        item_id="daugavpils-fans-test",
                        files={},
                        metadata={"title": "Test"},
                        dry_run=False,
                        yaml_path=yaml_path,
                        metadata_only=True,
                    )

            self.assertTrue(ok)
            mock_modify.assert_called_once_with("daugavpils-fans-test", metadata={"title": "Test"})
            mock_upload.assert_not_called()

    def test_sync_metadata_treats_no_changes_error_as_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"success":false,"error":"no changes to _meta.xml"}'

        with patch("internetarchive.modify_metadata", return_value=mock_response):
            ok = sync_metadata("daugavpils-fans-test", {"title": "Test"})

        self.assertTrue(ok)

    def test_sync_metadata_reports_real_errors(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with patch("internetarchive.modify_metadata", return_value=mock_response):
            ok = sync_metadata("daugavpils-fans-test", {"title": "Test"})

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
