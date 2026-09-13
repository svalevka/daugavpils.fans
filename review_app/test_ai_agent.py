"""
Tests for the AI autonomous approval agent (GitHub issue #43).
Covers:
- JSON parsing and guardrails (confidence clamping, malformed input fallback).
- Text proposals: active auto-approval vs. escalation vs. shadow mode.
- Media proposals: active image auto-approval & upload dispatch vs. escalation.
- Multimodal payload structure (base64 image data).
- Failure/rejection modes: API timeout, spam flag, shadow mode suppression.
- Dashboard display of AI reasoning and confidence.
"""
from __future__ import annotations

import json
from unittest import mock

import ai_agent
from config import AiConfig
from test_support import JPEG_BYTES, ReviewAppTestCase


class AiAgentParsingTest(ReviewAppTestCase):
    def test_parse_valid_approval(self):
        payload = json.dumps(
            {
                "decision": "approve",
                "confidence": 0.95,
                "reasoning": "Legitimate typo correction in band description.",
                "spam_or_vandalism": False,
            }
        )
        res = ai_agent.parse_ai_response(payload)
        self.assertEqual(res.decision, "approve")
        self.assertAlmostEqual(res.confidence, 0.95)
        self.assertEqual(res.reasoning, "Legitimate typo correction in band description.")
        self.assertFalse(res.spam_or_vandalism)

    def test_parse_valid_escalation(self):
        payload = json.dumps(
            {
                "decision": "escalate",
                "confidence": 0.50,
                "reasoning": "Unverifiable biographical claim.",
                "spam_or_vandalism": False,
            }
        )
        res = ai_agent.parse_ai_response(payload)
        self.assertEqual(res.decision, "escalate")
        self.assertAlmostEqual(res.confidence, 0.50)
        self.assertEqual(res.reasoning, "Unverifiable biographical claim.")

    def test_parse_markdown_wrapped_json(self):
        payload = (
            "Here is the evaluation:\n```json\n"
            "{\n"
            '  "decision": "approve",\n'
            '  "confidence": 0.88,\n'
            '  "reasoning": "Valid Discogs link added.",\n'
            '  "spam_or_vandalism": false\n'
            "}\n```"
        )
        res = ai_agent.parse_ai_response(payload)
        self.assertEqual(res.decision, "approve")
        self.assertAlmostEqual(res.confidence, 0.88)
        self.assertEqual(res.reasoning, "Valid Discogs link added.")

    def test_parse_malformed_json_falls_back_to_escalation(self):
        res = ai_agent.parse_ai_response("I cannot decide, this seems confusing.")
        self.assertEqual(res.decision, "escalate")
        self.assertEqual(res.confidence, 0.0)

    def test_parse_confidence_clamped_to_zero_one(self):
        payload1 = json.dumps({"decision": "approve", "confidence": 1.75, "reasoning": "test"})
        res1 = ai_agent.parse_ai_response(payload1)
        self.assertEqual(res1.confidence, 1.0)

        payload2 = json.dumps({"decision": "approve", "confidence": -0.5, "reasoning": "test"})
        res2 = ai_agent.parse_ai_response(payload2)
        self.assertEqual(res2.confidence, 0.0)


class AiAgentTextProposalTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.mock_ai_escalation = self._patch("ai_agent.mail.send_ai_escalation_notification")
        self.mock_ai_trigger_apply = self._patch("ai_agent.github_dispatch.trigger_apply")
        self.mock_ai_api = self._patch("ai_agent.call_ai_api")
        # Enable sync evaluation for deterministic test execution
        self.app.config["AI_SYNC_EVALUATION"] = True

    def test_active_mode_auto_approve_high_confidence(self):
        self.app.config["AI_CONFIG"] = AiConfig(mode="active", api_key="test-key", confidence_threshold=0.80)
        self.mock_ai_api.return_value = json.dumps(
            {
                "decision": "approve",
                "confidence": 0.95,
                "reasoning": "Fixed spelling error in band biography.",
                "spam_or_vandalism": False,
            }
        )

        resp = self.submit(proposed_value="Fixed typo in description.")
        self.assertEqual(resp.status_code, 201)

        proposals = self.fetch_proposals()
        self.assertEqual(len(proposals), 1)
        p = proposals[0]

        # Status flipped to approved automatically
        self.assertEqual(p["status"], "approved")
        self.assertIsNotNone(p["decided_by"])
        self.assertIsNotNone(p["decided_at"])
        self.assertEqual(p["ai_decision"], "approved")
        self.assertAlmostEqual(p["ai_confidence"], 0.95)
        self.assertIn("Fixed spelling error", p["ai_reasoning"])
        self.assertIsNotNone(p["ai_evaluated_at"])

        # Dispatch triggered
        self.mock_ai_trigger_apply.assert_called_once_with(self.app.config["GITHUB_CONFIG"], p["id"])

        # Silent: no escalation email sent
        self.mock_ai_escalation.assert_not_called()
        # Submission notification was also suppressed
        self.mock_send_notification.assert_not_called()

    def test_active_mode_escalates_on_low_confidence(self):
        self.app.config["AI_CONFIG"] = AiConfig(mode="active", api_key="test-key", confidence_threshold=0.80)
        self.mock_ai_api.return_value = json.dumps(
            {
                "decision": "approve",
                "confidence": 0.65,  # below 0.80 threshold
                "reasoning": "Possible biography expansion, but historical authenticity uncertain.",
                "spam_or_vandalism": False,
            }
        )

        resp = self.submit(proposed_value="Added controversial new claim.")
        self.assertEqual(resp.status_code, 201)

        proposals = self.fetch_proposals()
        self.assertEqual(len(proposals), 1)
        p = proposals[0]

        # Remains pending
        self.assertEqual(p["status"], "pending")
        self.assertIsNone(p["decided_by"])
        self.assertEqual(p["ai_decision"], "approve")
        self.assertAlmostEqual(p["ai_confidence"], 0.65)
        self.assertIn("authenticity uncertain", p["ai_reasoning"])

        # No workflow dispatch
        self.mock_ai_trigger_apply.assert_not_called()

        # Escalation email sent to maintainer
        self.mock_ai_escalation.assert_called_once()
        call_kwargs = self.mock_ai_escalation.call_args[1]
        self.assertEqual(call_kwargs["proposal_id"], p["id"])
        self.assertIn("authenticity uncertain", call_kwargs["ai_reasoning"])
        self.assertFalse(call_kwargs["is_shadow"])

    def test_active_mode_escalates_on_spam_flag(self):
        self.app.config["AI_CONFIG"] = AiConfig(mode="active", api_key="test-key", confidence_threshold=0.80)
        self.mock_ai_api.return_value = json.dumps(
            {
                "decision": "approve",
                "confidence": 0.99,
                "reasoning": "Suspicious commercial link injection.",
                "spam_or_vandalism": True,
            }
        )

        resp = self.submit(proposed_value="Buy cheap sunglasses at http://spam.example.com")
        self.assertEqual(resp.status_code, 201)

        proposals = self.fetch_proposals()
        p = proposals[0]
        self.assertEqual(p["status"], "pending")
        self.mock_ai_trigger_apply.assert_not_called()
        self.mock_ai_escalation.assert_called_once()

    def test_shadow_mode_records_evaluation_without_approving(self):
        self.app.config["AI_CONFIG"] = AiConfig(mode="shadow", api_key="test-key", confidence_threshold=0.80)
        self.mock_ai_api.return_value = json.dumps(
            {
                "decision": "approve",
                "confidence": 0.95,
                "reasoning": "Clear grammatical fix.",
                "spam_or_vandalism": False,
            }
        )

        resp = self.submit(proposed_value="Grammar fix.")
        self.assertEqual(resp.status_code, 201)

        proposals = self.fetch_proposals()
        p = proposals[0]

        # In shadow mode, proposal stays pending
        self.assertEqual(p["status"], "pending")
        self.assertIsNone(p["decided_by"])
        self.assertEqual(p["ai_decision"], "approve")
        self.assertAlmostEqual(p["ai_confidence"], 0.95)

        # No git workflow dispatch
        self.mock_ai_trigger_apply.assert_not_called()

        # Shadow notification email sent
        self.mock_ai_escalation.assert_called_once()
        call_kwargs = self.mock_ai_escalation.call_args[1]
        self.assertTrue(call_kwargs["is_shadow"])
        self.assertEqual(call_kwargs["proposal_id"], p["id"])

    def test_api_failure_fallback_escalates(self):
        self.app.config["AI_CONFIG"] = AiConfig(mode="active", api_key="test-key")
        self.mock_ai_api.side_effect = RuntimeError("Z-AI connection timeout")

        resp = self.submit(proposed_value="Some text edit")
        self.assertEqual(resp.status_code, 201)

        proposals = self.fetch_proposals()
        p = proposals[0]
        self.assertEqual(p["status"], "pending")
        self.assertEqual(p["ai_decision"], "escalate")
        self.assertEqual(p["ai_confidence"], 0.0)
        self.assertIn("connection timeout", p["ai_reasoning"])
        self.mock_ai_trigger_apply.assert_not_called()
        self.mock_ai_escalation.assert_called_once()

    def test_evaluation_outside_request_context_builds_dashboard_url(self):
        """Verifies process_proposal_with_ai runs cleanly outside an HTTP request context."""
        import ai_agent

        self.app.config["AI_CONFIG"] = AiConfig(mode="active", api_key="test-key", confidence_threshold=0.80)
        self.mock_ai_api.return_value = json.dumps(
            {
                "decision": "escalate",
                "confidence": 0.50,
                "reasoning": "Ambiguous content requiring human judgment.",
                "spam_or_vandalism": False,
            }
        )

        resp = self.submit(proposed_value="Needs escalation")
        self.assertEqual(resp.status_code, 201)
        proposals = self.fetch_proposals()
        pid = proposals[0]["id"]

        # Run process_proposal_with_ai directly outside any request context
        ai_agent.process_proposal_with_ai(self.app, pid)

        self.mock_ai_escalation.assert_called()
        call_kwargs = self.mock_ai_escalation.call_args[1]
        self.assertTrue(call_kwargs["dashboard_url"].endswith("/dashboard"))


class AiAgentMediaProposalTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.mock_ai_escalation = self._patch("ai_agent.mail.send_ai_escalation_notification")
        self.mock_ai_trigger_media = self._patch("ai_agent.github_dispatch.trigger_media_apply")
        self.mock_ai_api = self._patch("ai_agent.call_ai_api")
        self.mock_submitter_approved = self._patch("ai_agent.mail.send_media_approved_notification")
        self.app.config["AI_SYNC_EVALUATION"] = True

    def test_active_mode_auto_approves_and_dispatches_media_upload(self):
        self.app.config["AI_CONFIG"] = AiConfig(mode="active", api_key="test-key", confidence_threshold=0.80)
        self.mock_ai_api.return_value = json.dumps(
            {
                "decision": "approve",
                "confidence": 0.92,
                "reasoning": "Authentic archival concert photo matching the band.",
                "spam_or_vandalism": False,
            }
        )

        resp = self.submit_media(
            caption="Live at Grifs Club, 1999",
            submitter_name="Janis",
            submitter_contact="janis@example.com",
        )
        self.assertEqual(resp.status_code, 201)

        media = self.fetch_media_proposals()
        self.assertEqual(len(media), 1)
        m = media[0]

        # Status moved directly to publishing
        self.assertEqual(m["status"], "publishing")
        self.assertIsNotNone(m["decided_by"])
        self.assertEqual(m["ai_decision"], "approved")
        self.assertAlmostEqual(m["ai_confidence"], 0.92)
        self.assertIn("Authentic archival concert photo", m["ai_reasoning"])

        # Dispatched apply-media-proposal.yml
        self.mock_ai_trigger_media.assert_called_once_with(self.app.config["GITHUB_CONFIG"], m["id"])

        # Notified submitter
        self.mock_submitter_approved.assert_called_once()

        # Silent: maintainer escalation not sent
        self.mock_ai_escalation.assert_not_called()

    def test_media_escalates_on_uncertain_image(self):
        self.app.config["AI_CONFIG"] = AiConfig(mode="active", api_key="test-key", confidence_threshold=0.80)
        self.mock_ai_api.return_value = json.dumps(
            {
                "decision": "escalate",
                "confidence": 0.40,
                "reasoning": "Image is low resolution and band relevance is ambiguous.",
                "spam_or_vandalism": False,
            }
        )

        resp = self.submit_media(caption="Maybe this is them?")
        self.assertEqual(resp.status_code, 201)

        media = self.fetch_media_proposals()
        m = media[0]
        self.assertEqual(m["status"], "pending")
        self.assertEqual(m["ai_decision"], "escalate")
        self.mock_ai_trigger_media.assert_not_called()
        self.mock_ai_escalation.assert_called_once()
        call_kwargs = self.mock_ai_escalation.call_args[1]
        self.assertTrue(call_kwargs["is_media"])
        self.assertEqual(call_kwargs["proposal_id"], m["id"])

    def test_image_bytes_passed_to_ai_api(self):
        self.app.config["AI_CONFIG"] = AiConfig(mode="active", api_key="test-key")
        self.mock_ai_api.return_value = json.dumps(
            {"decision": "approve", "confidence": 0.85, "reasoning": "Valid photo."}
        )

        self.submit_media()
        self.mock_ai_api.assert_called_once()
        _, kwargs = self.mock_ai_api.call_args
        self.assertIsNotNone(kwargs.get("image_bytes"))
        self.assertEqual(kwargs.get("image_mime"), "image/jpeg")


class AiAgentDashboardViewTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.approver_id = self.seed_approver("approver@example.com")
        self.login_as(self.approver_id)

    def test_dashboard_displays_ai_evaluation_box(self):
        # Seed a proposal with AI evaluation recorded
        self.submit(proposed_value="New biography text.")
        proposals = self.fetch_proposals()
        pid = proposals[0]["id"]

        import sqlite3
        conn = sqlite3.connect(self.database_path)
        conn.execute(
            """
            UPDATE proposals
            SET ai_decision = 'escalated', ai_confidence = 0.65,
                ai_reasoning = 'Requires historical citation for festival dates.'
            WHERE id = ?
            """,
            (pid,),
        )
        conn.commit()
        conn.close()

        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("ai-eval-box", html)
        self.assertIn("AI Agent:", html)
        self.assertIn("ESCALATED", html)
        self.assertIn("65% confidence", html)
        self.assertIn("Requires historical citation for festival dates.", html)
