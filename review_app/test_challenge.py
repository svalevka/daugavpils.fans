#!/usr/bin/env python3
"""
Unit tests for review_app/challenge.py (GitHub issue #71).
"""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import challenge  # noqa: E402


class ChallengeVerificationTest(unittest.TestCase):
    def test_canonical_answers_accepted(self):
        self.assertTrue(challenge.verify_challenge("Daugavpils"))
        self.assertTrue(challenge.verify_challenge("daugavpils"))
        self.assertTrue(challenge.verify_challenge("DAUGAVPILS"))
        self.assertTrue(challenge.verify_challenge("Даугавпилс"))
        self.assertTrue(challenge.verify_challenge("даугавпилс"))
        self.assertTrue(challenge.verify_challenge("ДАУГАВПИЛС"))
        self.assertTrue(challenge.verify_challenge("Dvinsk"))
        self.assertTrue(challenge.verify_challenge("двинск"))
        self.assertTrue(challenge.verify_challenge("Dunaburg"))
        self.assertTrue(challenge.verify_challenge("Dünaburg"))
        self.assertTrue(challenge.verify_challenge("дюнабург"))
        self.assertTrue(challenge.verify_challenge("D-pils"))
        self.assertTrue(challenge.verify_challenge("dpils"))
        self.assertTrue(challenge.verify_challenge("дпилс"))

    def test_prefixed_and_suffixed_answers_accepted(self):
        self.assertTrue(challenge.verify_challenge("город Даугавпилс"))
        self.assertTrue(challenge.verify_challenge("г. Даугавпилс"))
        self.assertTrue(challenge.verify_challenge("г.Даугавпилс"))
        self.assertTrue(challenge.verify_challenge("city of Daugavpils"))
        self.assertTrue(challenge.verify_challenge("Daugavpils, Latvia"))
        self.assertTrue(challenge.verify_challenge("Даугавпилс, Латвия"))
        self.assertTrue(challenge.verify_challenge("в Даугавпилсе"))
        self.assertTrue(challenge.verify_challenge("in Daugavpils"))

    def test_whitespace_and_punctuation_handling(self):
        self.assertTrue(challenge.verify_challenge("   Daugavpils   "))
        self.assertTrue(challenge.verify_challenge("Daugavpils!"))
        self.assertTrue(challenge.verify_challenge("«Даугавпилс»"))
        self.assertTrue(challenge.verify_challenge("\"Daugavpils\""))

    def test_invalid_and_empty_answers_rejected(self):
        self.assertFalse(challenge.verify_challenge(""))
        self.assertFalse(challenge.verify_challenge("   "))
        self.assertFalse(challenge.verify_challenge(None))
        self.assertFalse(challenge.verify_challenge("Riga"))
        self.assertFalse(challenge.verify_challenge("Рига"))
        self.assertFalse(challenge.verify_challenge("London"))
        self.assertFalse(challenge.verify_challenge("spam-bot-12345"))
        self.assertFalse(challenge.verify_challenge("https://example.com/pharmacy"))
        self.assertFalse(challenge.verify_challenge(12345))


if __name__ == "__main__":
    unittest.main()
