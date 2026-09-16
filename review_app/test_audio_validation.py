#!/usr/bin/env python3
"""
Tests for audio validation, ffprobe inspection, AI watermark detection,
and slug generation in review_app/audio_validation.py (GitHub issues #20, #52).
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audio_validation  # noqa: E402


class CheckFfprobeAvailableTest(unittest.TestCase):
    @patch("shutil.which", return_value="/usr/bin/ffprobe")
    def test_returns_true_when_ffprobe_on_path(self, mock_which):
        self.assertTrue(audio_validation.check_ffprobe_available())
        mock_which.assert_called_once_with("ffprobe")

    @patch("shutil.which", return_value=None)
    def test_returns_false_when_ffprobe_not_on_path(self, mock_which):
        self.assertFalse(audio_validation.check_ffprobe_available())


class ProbeAudioFileTest(unittest.TestCase):
    @patch("audio_validation.check_ffprobe_available", return_value=False)
    def test_raises_error_when_ffprobe_binary_missing(self, mock_check):
        with self.assertRaises(audio_validation.AudioValidationError) as ctx:
            audio_validation.probe_audio_file(Path("/fake/track.mp3"))
        self.assertIn("ffprobe binary is not available", str(ctx.exception))

    @patch("audio_validation.check_ffprobe_available", return_value=True)
    @patch("subprocess.run", side_effect=FileNotFoundError("[Errno 2] No such file or directory: 'ffprobe'"))
    def test_raises_error_on_filenotfounderror(self, mock_run, mock_check):
        with self.assertRaises(audio_validation.AudioValidationError) as ctx:
            audio_validation.probe_audio_file(Path("/fake/track.mp3"))
        self.assertIn("ffprobe failed to inspect", str(ctx.exception))

    @patch("audio_validation.check_ffprobe_available", return_value=True)
    @patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["ffprobe"]))
    def test_raises_error_on_process_failure(self, mock_run, mock_check):
        with self.assertRaises(audio_validation.AudioValidationError) as ctx:
            audio_validation.probe_audio_file(Path("/fake/track.mp3"))
        self.assertIn("ffprobe failed to inspect", str(ctx.exception))

    @patch("audio_validation.check_ffprobe_available", return_value=True)
    @patch("subprocess.run")
    def test_raises_error_on_invalid_json(self, mock_run, mock_check):
        mock_run.return_value = MagicMock(stdout="not json", check=True)
        with self.assertRaises(audio_validation.AudioValidationError) as ctx:
            audio_validation.probe_audio_file(Path("/fake/track.mp3"))
        self.assertIn("invalid JSON output", str(ctx.exception))

    @patch("audio_validation.check_ffprobe_available", return_value=True)
    @patch("subprocess.run")
    def test_raises_error_on_missing_or_zero_duration(self, mock_run, mock_check):
        mock_run.return_value = MagicMock(stdout='{"format": {}}', check=True)
        with self.assertRaises(audio_validation.AudioValidationError) as ctx:
            audio_validation.probe_audio_file(Path("/fake/track.mp3"))
        self.assertIn("no detectable duration", str(ctx.exception))

        mock_run.return_value = MagicMock(stdout='{"format": {"duration": "0"}}', check=True)
        with self.assertRaises(audio_validation.AudioValidationError) as ctx:
            audio_validation.probe_audio_file(Path("/fake/track.mp3"))
        self.assertIn("zero duration", str(ctx.exception))

    @patch("audio_validation.check_ffprobe_available", return_value=True)
    @patch("subprocess.run")
    def test_success_with_valid_audio(self, mock_run, mock_check):
        mock_run.return_value = MagicMock(
            stdout='{"format": {"duration": "145.2", "bit_rate": "320000", "tags": {"title": "Test Song"}}}',
            check=True,
        )
        info = audio_validation.probe_audio_file(Path("/fake/track.mp3"))
        self.assertEqual(info["duration_iso"], "PT2M25S")
        self.assertAlmostEqual(info["duration_seconds"], 145.2)
        self.assertEqual(info["bitrate"], "320 kbps")
        self.assertEqual(info["tags"], {"title": "Test Song"})
        self.assertEqual(info["ai_flags"], [])


class SlugGenerationTest(unittest.TestCase):
    def test_generate_band_slug(self):
        self.assertEqual(audio_validation.generate_band_slug("Крики Мартина"), "kriki-martina")
        self.assertEqual(audio_validation.generate_band_slug("CrossFire"), "crossfire")
        self.assertEqual(audio_validation.generate_band_slug("Baily Button"), "baily-button")

    def test_generate_release_slug(self):
        self.assertEqual(
            audio_validation.generate_release_slug("Карманный Мир", "1993"), "1993-karmannyi-mir"
        )
        self.assertEqual(
            audio_validation.generate_release_slug("1993-already-prefixed", "1993"),
            "1993-already-prefixed",
        )

    def test_generate_release_slug_invalid_year(self):
        with self.assertRaises(audio_validation.AudioValidationError):
            audio_validation.generate_release_slug("Title", "invalid")


class AiDetectionTest(unittest.TestCase):
    def test_detect_ai_audio_signatures(self):
        tags = {"comment": "generated by Suno.ai v3"}
        flags = audio_validation.detect_ai_audio_signatures(tags)
        self.assertEqual(len(flags), 1)
        self.assertIn("AI generator watermark", flags[0])

    def test_detect_ai_text_syntax(self):
        text = "Here are the lyrics:\n[Verse 1]\nSomething here\n[Chorus]\nLalalala"
        flags = audio_validation.detect_ai_text_syntax(text)
        self.assertEqual(len(flags), 1)
        self.assertIn("AI generative music syntax marker", flags[0])


if __name__ == "__main__":
    unittest.main()
