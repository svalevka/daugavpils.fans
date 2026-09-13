import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from format_commit_message import format_commit_message, format_step_summary


class FormatCommitMessageTest(unittest.TestCase):
    def test_text_proposal_with_ai_audit_info(self):
        proposal = {
            "id": 26,
            "band_slug": "m-spirit",
            "release_slug": None,
            "target": "band",
            "field": "description",
            "decided_by_name": "AI Approval Agent",
            "ai_decision": "approved",
            "ai_confidence": 0.95,
            "ai_reasoning": "Minimal punctuation cleanup.",
        }
        msg = format_commit_message(proposal)
        self.assertIn("Apply approved proposal #26", msg)
        self.assertIn("Target: m-spirit (band.description)", msg)
        self.assertIn("Decided by: AI Approval Agent", msg)
        self.assertIn("AI Decision: approved (95% confidence)", msg)
        self.assertIn("AI Reasoning: Minimal punctuation cleanup.", msg)
        self.assertIn("Proposal-ID: 26", msg)

    def test_human_approved_proposal(self):
        proposal = {
            "id": 14,
            "band_slug": "degradanti",
            "release_slug": "demo",
            "target": "release",
            "field": "year",
            "decided_by_name": "Sergei Valevka",
            "ai_decision": None,
            "ai_confidence": None,
            "ai_reasoning": None,
        }
        msg = format_commit_message(proposal)
        self.assertIn("Apply approved proposal #14", msg)
        self.assertIn("Target: degradanti/demo (release.year)", msg)
        self.assertIn("Decided by: Sergei Valevka", msg)
        self.assertNotIn("AI Decision", msg)
        self.assertIn("Proposal-ID: 14", msg)

    def test_media_proposal_formatting(self):
        proposal = {
            "id": 5,
            "band_slug": "m-spirit",
            "release_slug": None,
            "media_type": "image",
            "original_filename": "live.jpg",
            "caption": "Live in Riga 2008",
            "decided_by_name": "AI Approval Agent",
            "ai_decision": "approved",
            "ai_confidence": 0.90,
            "ai_reasoning": "Authentic band photo.",
        }
        msg = format_commit_message(proposal, is_media=True)
        self.assertIn("Apply approved media proposal #5", msg)
        self.assertIn("Target: m-spirit (image: live.jpg)", msg)
        self.assertIn("Caption: Live in Riga 2008", msg)
        self.assertIn("Decided by: AI Approval Agent", msg)
        self.assertIn("AI Decision: approved (90% confidence)", msg)
        self.assertIn("AI Reasoning: Authentic band photo.", msg)
        self.assertIn("Media-Proposal-ID: 5", msg)

    def test_album_proposal_formatting(self):
        proposal = {
            "id": 42,
            "band_slug": "degradanti",
            "release_slug": "1998-live",
            "name": "Live in Riga 1998",
            "tracks": [{"position": 1, "name": "Track 1"}, {"position": 2, "name": "Track 2"}],
            "decided_by_name": "AI Approval Agent",
            "ai_decision": "approved",
            "ai_confidence": 0.95,
            "ai_reasoning": "Authentic live bootleg.",
        }
        msg = format_commit_message(proposal, is_album=True)
        self.assertIn("Apply approved album proposal #42", msg)
        self.assertIn("Target: degradanti/1998-live (Live in Riga 1998 - 2 tracks)", msg)
        self.assertIn("Decided by: AI Approval Agent", msg)
        self.assertIn("AI Decision: approved (95% confidence)", msg)
        self.assertIn("AI Reasoning: Authentic live bootleg.", msg)
        self.assertIn("Album-Proposal-ID: 42", msg)

    def test_step_summary_markdown(self):
        proposal = {
            "id": 26,
            "band_slug": "m-spirit",
            "release_slug": None,
            "target": "band",
            "field": "description",
            "decided_by_name": "AI Approval Agent",
            "ai_decision": "approved",
            "ai_confidence": 0.95,
            "ai_reasoning": "Minimal punctuation cleanup.",
        }
        summary = format_step_summary(proposal)
        self.assertIn("### Applied Proposal #26", summary)
        self.assertIn("`m-spirit (band.description)`", summary)
        self.assertIn("**AI Approval Agent**", summary)
        self.assertIn("`approved` (95% confidence)", summary)
        self.assertIn("> **AI Reasoning**: Minimal punctuation cleanup.", summary)

    def test_band_proposal_formatting(self):
        proposal = {
            "id": 88,
            "name": "Новая Группа",
            "band_slug": "novaya-gruppa",
            "has_release": True,
            "release_name": "Первый Альбом",
            "decided_by_name": "AI Approval Agent",
            "ai_decision": "approved",
            "ai_confidence": 0.98,
            "ai_reasoning": "Authentic Daugavpils band from 1990s.",
        }
        msg = format_commit_message(proposal, is_band=True)
        self.assertIn("Apply approved band proposal #88", msg)
        self.assertIn("Target: bands/novaya-gruppa (Новая Группа + release Первый Альбом)", msg)
        self.assertIn("Decided by: AI Approval Agent", msg)
        self.assertIn("AI Decision: approved (98% confidence)", msg)
        self.assertIn("AI Reasoning: Authentic Daugavpils band from 1990s.", msg)
        self.assertIn("Band-Proposal-ID: 88", msg)

    def test_step_summary_band_proposal(self):
        proposal = {
            "id": 88,
            "name": "Новая Группа",
            "band_slug": "novaya-gruppa",
            "has_release": False,
            "decided_by_name": "Curator",
        }
        summary = format_step_summary(proposal, is_band=True)
        self.assertIn("### Applied Band Proposal #88", summary)
        self.assertIn("`bands/novaya-gruppa (Новая Группа)`", summary)
        self.assertIn("**Curator**", summary)


if __name__ == "__main__":
    unittest.main()

