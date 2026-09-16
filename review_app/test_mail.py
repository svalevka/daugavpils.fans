"""
Unit tests for review_app.mail module (_send and outbound notification helpers).
"""
import unittest
from unittest.mock import MagicMock, patch

from config import SmtpConfig
import mail


class MailTest(unittest.TestCase):
    def test_send_starttls_port_587(self):
        smtp_cfg = SmtpConfig(
            host="smtp.resend.com",
            port=587,
            from_addr="noreply@daugavpils.fans",
            user="resend",
            password="re_test_key",
        )
        mock_smtp = MagicMock()
        mock_enter = mock_smtp.__enter__.return_value

        with patch("mail.smtplib.SMTP", return_value=mock_smtp) as mock_cls:
            mail._send(smtp_cfg, "test@example.com", "Test Subject", "Test Body")

            mock_cls.assert_called_once_with("smtp.resend.com", 587, timeout=30)
            mock_enter.starttls.assert_called_once()
            mock_enter.login.assert_called_once_with("resend", "re_test_key")
            mock_enter.send_message.assert_called_once()

            msg = mock_enter.send_message.call_args[0][0]
            self.assertEqual(msg["Subject"], "Test Subject")
            self.assertEqual(msg["From"], "noreply@daugavpils.fans")
            self.assertEqual(msg["To"], "test@example.com")
            self.assertEqual(msg.get_content().strip(), "Test Body")

    def test_send_ssl_port_465(self):
        smtp_cfg = SmtpConfig(
            host="smtp.resend.com",
            port=465,
            from_addr="noreply@daugavpils.fans",
            user="resend",
            password="re_test_key",
        )
        mock_smtp = MagicMock()
        mock_enter = mock_smtp.__enter__.return_value

        with patch("mail.smtplib.SMTP_SSL", return_value=mock_smtp) as mock_cls:
            mail._send(smtp_cfg, "test@example.com", "SSL Subject", "SSL Body")

            mock_cls.assert_called_once_with("smtp.resend.com", 465, timeout=30)
            mock_enter.starttls.assert_not_called()
            mock_enter.login.assert_called_once_with("resend", "re_test_key")
            mock_enter.send_message.assert_called_once()

    def test_send_unauthenticated_no_user(self):
        smtp_cfg = SmtpConfig(
            host="localhost",
            port=25,
            from_addr="noreply@example.com",
            user=None,
        )
        mock_smtp = MagicMock()
        mock_enter = mock_smtp.__enter__.return_value

        with patch("mail.smtplib.SMTP", return_value=mock_smtp) as mock_cls:
            mail._send(smtp_cfg, "test@example.com", "No Auth", "Body")

            mock_cls.assert_called_once_with("localhost", 25, timeout=30)
            mock_enter.starttls.assert_not_called()
            mock_enter.login.assert_not_called()
            mock_enter.send_message.assert_called_once()

    def test_helpers_call_send(self):
        smtp_cfg = SmtpConfig(host="localhost", port=25, from_addr="noreply@example.com")
        with patch("mail._send") as mock_send:
            mail.send_magic_link(smtp_cfg, "user@example.com", "https://example.com/verify?token=abc")
            mock_send.assert_called_once()
            self.assertIn("https://example.com/verify?token=abc", mock_send.call_args[0][3])

        with patch("mail._send") as mock_send:
            mail.send_admin_magic_link(smtp_cfg, "admin@example.com", "https://example.com/admin/verify?token=def")
            mock_send.assert_called_once()
            self.assertIn("https://example.com/admin/verify?token=def", mock_send.call_args[0][3])

        with patch("mail._send") as mock_send:
            mail.send_welcome_invitation(smtp_cfg, "new@example.com", "Alice", ["admin"], "https://example.com/login")
            mock_send.assert_called_once()
            self.assertIn("Alice", mock_send.call_args[0][3])
            self.assertIn("admin", mock_send.call_args[0][3])

        with patch("mail._send") as mock_send:
            mail.send_submission_notification(smtp_cfg, ["m1@example.com", "m2@example.com"], "Summary of proposal")
            self.assertEqual(mock_send.call_count, 2)

        with patch("mail._send") as mock_send:
            mail.send_media_approved_notification(smtp_cfg, "submitter@example.com", "photo.jpg")
            mock_send.assert_called_once()
            self.assertIn("photo.jpg", mock_send.call_args[0][3])

        with patch("mail._send") as mock_send:
            mail.send_ai_escalation_notification(
                smtp_cfg,
                ["admin@example.com"],
                proposal_id=42,
                target_summary="Band / Release",
                details="Details here",
                ai_decision="reject",
                ai_confidence=0.55,
                ai_reasoning="Needs manual confirmation",
                dashboard_url="https://example.com/dashboard",
                is_media=False,
                is_shadow=False,
            )
            mock_send.assert_called_once()
            self.assertIn("#42", mock_send.call_args[0][2])
            self.assertIn("Needs manual confirmation", mock_send.call_args[0][3])

    def test_send_proposal_decision_notification_approved_ru(self):
        smtp_cfg = SmtpConfig(host="localhost", port=25, from_addr="noreply@daugavpils.fans")
        with patch("mail._send") as mock_send:
            mail.send_proposal_decision_notification(
                smtp_cfg,
                "fan@example.com",
                decision="approved",
                proposal_type="edit",
                target_summary="Dvinsk (band.genre)",
                review_notes="Отличное уточнение!",
                live_url="https://daugavpils.fans/bands/dvinsk/",
                lang="ru",
            )
            mock_send.assert_called_once()
            _, to_addr, subject, body = mock_send.call_args[0]
            self.assertEqual(to_addr, "fan@example.com")
            self.assertIn("Ваше предложение принято", subject)
            self.assertIn("Dvinsk (band.genre)", body)
            self.assertIn("текстовые правки", body)
            self.assertIn("https://daugavpils.fans/bands/dvinsk/", body)
            self.assertIn("Отличное уточнение!", body)

    def test_send_proposal_decision_notification_approved_en(self):
        smtp_cfg = SmtpConfig(host="localhost", port=25, from_addr="noreply@daugavpils.fans")
        with patch("mail._send") as mock_send:
            mail.send_proposal_decision_notification(
                smtp_cfg,
                "fan@example.com",
                decision="approved",
                proposal_type="album",
                target_summary="Dvinsk — Demo",
                live_url="https://daugavpils.fans/en/bands/dvinsk/demo/",
                lang="en",
            )
            mock_send.assert_called_once()
            _, to_addr, subject, body = mock_send.call_args[0]
            self.assertEqual(to_addr, "fan@example.com")
            self.assertIn("Your contribution was approved", subject)
            self.assertIn("Dvinsk — Demo", body)
            self.assertIn("new album", body)
            self.assertIn("https://daugavpils.fans/en/bands/dvinsk/demo/", body)

    def test_send_proposal_decision_notification_rejected_ru_with_notes(self):
        smtp_cfg = SmtpConfig(host="localhost", port=25, from_addr="noreply@daugavpils.fans")
        with patch("mail._send") as mock_send:
            mail.send_proposal_decision_notification(
                smtp_cfg,
                "fan@example.com",
                decision="rejected",
                proposal_type="media",
                target_summary="Dvinsk (video)",
                review_notes="дубликат видео 2004 года",
                lang="ru",
            )
            mock_send.assert_called_once()
            _, to_addr, subject, body = mock_send.call_args[0]
            self.assertEqual(to_addr, "fan@example.com")
            self.assertIn("Ваше предложение на daugavpils.fans", subject)
            self.assertIn("Dvinsk (video)", body)
            self.assertIn("фото / видео", body)
            self.assertIn("дубликат видео 2004 года", body)

    def test_send_proposal_decision_notification_rejected_en_without_notes(self):
        smtp_cfg = SmtpConfig(host="localhost", port=25, from_addr="noreply@daugavpils.fans")
        with patch("mail._send") as mock_send:
            mail.send_proposal_decision_notification(
                smtp_cfg,
                "fan@example.com",
                decision="rejected",
                proposal_type="band",
                target_summary="Unknown Band",
                review_notes=None,
                lang="en",
            )
            mock_send.assert_called_once()
            _, to_addr, subject, body = mock_send.call_args[0]
            self.assertEqual(to_addr, "fan@example.com")
            self.assertIn("Your submission status", subject)
            self.assertIn("Unknown Band", body)
            self.assertIn("new band", body)
            self.assertNotIn("Curator notes", body)

    def test_send_proposal_decision_notification_invalid_or_empty_contact(self):
        smtp_cfg = SmtpConfig(host="localhost", port=25, from_addr="noreply@daugavpils.fans")
        with patch("mail._send") as mock_send:
            mail.send_proposal_decision_notification(
                smtp_cfg,
                "",
                decision="approved",
                proposal_type="edit",
                target_summary="Dvinsk",
            )
            mock_send.assert_not_called()

        with patch("mail._send") as mock_send:
            mail.send_proposal_decision_notification(
                smtp_cfg,
                "invalidaddress",
                decision="rejected",
                proposal_type="edit",
                target_summary="Dvinsk",
            )
            mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
