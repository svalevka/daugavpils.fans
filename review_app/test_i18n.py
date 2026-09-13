#!/usr/bin/env python3
"""
Tests for bilingual proposal flows and language negotiation (GitHub issue #41).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import ReviewAppTestCase  # noqa: E402


class I18nNegotiationTest(ReviewAppTestCase):
    def test_default_language_is_russian(self):
        response = self.client.get("/submit")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'lang="ru"', response.data)
        self.assertIn("Предложить изменение".encode("utf-8"), response.data)
        self.assertIn("Выберите группу".encode("utf-8"), response.data)

    def test_query_param_switches_to_english(self):
        response = self.client.get("/submit?lang=en")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'lang="en"', response.data)
        self.assertIn(b"Propose an edit", response.data)
        self.assertIn(b"Pick a band to correct or extend.", response.data)

    def test_session_persists_chosen_language(self):
        # First request sets language to English
        res1 = self.client.get("/submit?lang=en")
        self.assertEqual(res1.status_code, 200)
        self.assertIn(b'lang="en"', res1.data)

        # Subsequent request without query param maintains English
        res2 = self.client.get("/submit")
        self.assertEqual(res2.status_code, 200)
        self.assertIn(b'lang="en"', res2.data)
        self.assertIn(b"Propose an edit", res2.data)

        # Switching to Russian persists
        res3 = self.client.get("/submit?lang=ru")
        self.assertEqual(res3.status_code, 200)
        self.assertIn(b'lang="ru"', res3.data)

        res4 = self.client.get("/submit")
        self.assertEqual(res4.status_code, 200)
        self.assertIn(b'lang="ru"', res4.data)
        self.assertIn("Предложить изменение".encode("utf-8"), res4.data)

    def test_accept_language_header_negotiation(self):
        # Client preferring English
        res_en = self.client.get("/submit", headers={"Accept-Language": "en-US,en;q=0.9"})
        self.assertEqual(res_en.status_code, 200)
        self.assertIn(b'lang="en"', res_en.data)

        # Client preferring Russian in a new session
        client2 = self.app.test_client()
        res_ru = client2.get("/submit", headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        self.assertEqual(res_ru.status_code, 200)
        self.assertIn(b'lang="ru"', res_ru.data)

    def test_language_switcher_links_preserve_query_params(self):
        url = f"/submit/{self.fx.band_slug}/edit?target=band&field=description&lang=ru"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        # Switcher to English should include target and field query parameters
        self.assertIn('href="/submit/' + self.fx.band_slug + '/edit?target=band&amp;field=description&amp;lang=en"', body)


class BilingualContentTest(ReviewAppTestCase):
    def test_target_picker_in_russian_and_english(self):
        # Russian
        res_ru = self.client.get(f"/submit/{self.fx.band_slug}?lang=ru")
        self.assertEqual(res_ru.status_code, 200)
        self.assertIn("Что вы хотите исправить или дополнить?".encode("utf-8"), res_ru.data)
        self.assertIn("Участники".encode("utf-8"), res_ru.data)
        self.assertIn("Добавить нового участника".encode("utf-8"), res_ru.data)
        self.assertIn("Медиа".encode("utf-8"), res_ru.data)
        self.assertIn("Добавить фото или видео".encode("utf-8"), res_ru.data)
        self.assertIn("Биография / описание".encode("utf-8"), res_ru.data)

        # English
        res_en = self.client.get(f"/submit/{self.fx.band_slug}?lang=en")
        self.assertEqual(res_en.status_code, 200)
        self.assertIn(b"What would you like to correct?", res_en.data)
        self.assertIn(b"Members", res_en.data)
        self.assertIn(b"Add a new member", res_en.data)
        self.assertIn(b"Media", res_en.data)
        self.assertIn(b"Add photos or videos", res_en.data)
        self.assertIn(b"Biography", res_en.data)

    def test_edit_form_labels_in_russian_and_english(self):
        # Russian
        res_ru = self.client.get(
            f"/submit/{self.fx.band_slug}/edit?target=band&field=description&lang=ru"
        )
        self.assertEqual(res_ru.status_code, 200)
        self.assertIn("Биография / описание".encode("utf-8"), res_ru.data)
        self.assertIn("Текущий текст".encode("utf-8"), res_ru.data)
        self.assertIn("Ваш вариант текста".encode("utf-8"), res_ru.data)
        self.assertIn("Ваше имя (необязательно)".encode("utf-8"), res_ru.data)
        self.assertIn("Отправить на проверку".encode("utf-8"), res_ru.data)

        # English
        res_en = self.client.get(
            f"/submit/{self.fx.band_slug}/edit?target=band&field=description&lang=en"
        )
        self.assertEqual(res_en.status_code, 200)
        self.assertIn(b"Biography", res_en.data)
        self.assertIn(b"Current text", res_en.data)
        self.assertIn(b"Your replacement text", res_en.data)
        self.assertIn(b"Your name (optional)", res_en.data)
        self.assertIn(b"Submit for review", res_en.data)

    def test_add_member_form_in_russian_and_english(self):
        # Russian
        res_ru = self.client.get(f"/submit/{self.fx.band_slug}/add-member?lang=ru")
        self.assertEqual(res_ru.status_code, 200)
        self.assertIn("Добавить участника группы".encode("utf-8"), res_ru.data)
        self.assertIn("Имя (на языке оригинала / как пишется)".encode("utf-8"), res_ru.data)
        self.assertIn("Роль / инструмент (необязательно)".encode("utf-8"), res_ru.data)

        # English
        res_en = self.client.get(f"/submit/{self.fx.band_slug}/add-member?lang=en")
        self.assertEqual(res_en.status_code, 200)
        self.assertIn(b"Add a band member", res_en.data)
        self.assertIn(b"Name (as actually written, in its real script)", res_en.data)
        self.assertIn(b"Role (optional)", res_en.data)

    def test_media_form_in_russian_and_english(self):
        # Russian
        res_ru = self.client.get(f"/submit/{self.fx.band_slug}/media?lang=ru")
        self.assertEqual(res_ru.status_code, 200)
        self.assertIn("Добавить фото или видео".encode("utf-8"), res_ru.data)
        self.assertIn("data-i18n-video=\"Видео\"".encode("utf-8"), res_ru.data)
        self.assertIn("data-i18n-remove=\"Удалить\"".encode("utf-8"), res_ru.data)

        # English
        res_en = self.client.get(f"/submit/{self.fx.band_slug}/media?lang=en")
        self.assertEqual(res_en.status_code, 200)
        self.assertIn(b"Add photos or videos", res_en.data)
        self.assertIn(b'data-i18n-video="Video"', res_en.data)
        self.assertIn(b'data-i18n-remove="Remove"', res_en.data)

    def test_done_pages_in_russian_and_english(self):
        # Text proposal submission in Russian
        res_ru = self.submit(proposed_value="Новое описание", lang="ru")
        self.assertEqual(res_ru.status_code, 201)
        self.assertIn("Спасибо".encode("utf-8"), res_ru.data)
        self.assertIn("Ваше предложение отправлено на проверку".encode("utf-8"), res_ru.data)

        # Text proposal submission in English
        res_en = self.submit(proposed_value="New description", lang="en")
        self.assertEqual(res_en.status_code, 201)
        self.assertIn(b"Thank you", res_en.data)
        self.assertIn(b"Your proposal has been submitted for review", res_en.data)


if __name__ == "__main__":
    unittest.main()
