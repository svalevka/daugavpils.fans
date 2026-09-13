"""
Localization strings and language negotiation for review_app (see GitHub issue #41).

Provides:
- Bilingual UI strings (Russian default, English) for the public proposal flows.
- Locale detection: query parameter (?lang=) -> form field (lang) -> session -> cookie -> Accept-Language -> default 'ru'.
- Session persistence so navigating or submitting preserves the chosen language.
- Helper for generating language-switcher URLs preserving current path and query params.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode
from flask import request, session

SUPPORTED_LANGS = ("ru", "en")
DEFAULT_LANG = "ru"

STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "site_title": "daugavpils.fans",
        "support_link": "Поддержка",
        "propose_edit": "Предложить изменение",
        "pick_band_title": "Предложить изменение — daugavpils.fans",
        "pick_band_heading": "Предложить изменение",
        "pick_band_meta": "Выберите группу, чтобы предложить исправление или дополнение.",
        "pick_target_title": "Что вы хотите исправить или дополнить? — daugavpils.fans",
        "pick_target_heading": "Что вы хотите исправить или дополнить?",
        "section_releases": "Релизы",
        "section_members": "Участники",
        "add_new_member": "Добавить нового участника",
        "section_media": "Медиа",
        "add_photos_or_videos": "Добавить фото или видео",
        "section_fields": "Поля",
        "current_text": "Текущий текст",
        "replacement_text": "Ваш вариант текста",
        "submitter_name": "Ваше имя (необязательно)",
        "submitter_contact_edit": "Ваш email, если хотите получить ответ (необязательно)",
        "submitter_contact_media": "Ваш email, если хотите узнать об одобрении (необязательно)",
        "honeypot_label": "Оставьте это поле пустым",
        "submit_button": "Отправить на проверку",
        "add_member_title": "Добавить участника группы — daugavpils.fans",
        "add_member_heading": "Добавить участника группы",
        "member_name": "Имя (на языке оригинала / как пишется)",
        "member_name_en": "Имя (латинская транслитерация, необязательно)",
        "member_role": "Роль / инструмент (необязательно)",
        "member_role_en": "Роль (на английском языке, необязательно)",
        "member_period": "Период, например «1994–1996» (необязательно)",
        "media_title": "Добавить фото или видео — daugavpils.fans",
        "media_heading": "Добавить фото или видео",
        "media_desc": "Можно выбрать несколько файлов сразу. Каждый файл проверяется отдельно — ничего не публикуется автоматически.",
        "media_field_label": "Фотографии или видео",
        "media_add_button": "+ Добавить фото или видео",
        "media_js_video": "Видео",
        "media_js_caption": "Подпись или примечание (необязательно)",
        "media_js_remove": "Удалить",
        "thank_you_title": "Спасибо — daugavpils.fans",
        "thank_you_heading": "Спасибо",
        "proposal_submitted_msg": "Ваше предложение отправлено на проверку кураторам. Никакие изменения не публикуются автоматически — они появятся на сайте только после одобрения.",
        "media_submitted_msg": "Файлы ({count}) отправлены на проверку кураторам. Никакие изменения не публикуются автоматически — они появятся на сайте только после одобрения.",
        "media_empty_msg": "Ничего не было отправлено.",
        "media_skipped_heading": "Следующие файлы не удалось принять:",
        "photo_prefix": "Фото",
        "video_prefix": "Видео",
    },
    "en": {
        "site_title": "daugavpils.fans",
        "support_link": "Support",
        "propose_edit": "Suggest a change",
        "pick_band_title": "Propose an edit — daugavpils.fans",
        "pick_band_heading": "Propose an edit",
        "pick_band_meta": "Pick a band to correct or extend.",
        "pick_target_title": "What would you like to correct? — daugavpils.fans",
        "pick_target_heading": "What would you like to correct?",
        "section_releases": "Releases",
        "section_members": "Members",
        "add_new_member": "Add a new member",
        "section_media": "Media",
        "add_photos_or_videos": "Add photos or videos",
        "section_fields": "Fields",
        "current_text": "Current text",
        "replacement_text": "Your replacement text",
        "submitter_name": "Your name (optional)",
        "submitter_contact_edit": "Your email, if you'd like to be reachable (optional)",
        "submitter_contact_media": "Your email, if you'd like us to let you know it was approved (optional)",
        "honeypot_label": "Leave this field blank",
        "submit_button": "Submit for review",
        "add_member_title": "Add a band member — daugavpils.fans",
        "add_member_heading": "Add a band member",
        "member_name": "Name (as actually written, in its real script)",
        "member_name_en": "Name (Latin transliteration, optional)",
        "member_role": "Role (optional)",
        "member_role_en": "Role (English translation, optional)",
        "member_period": "Period, e.g. \"1994-1996\" (optional)",
        "media_title": "Add photos or videos — daugavpils.fans",
        "media_heading": "Add photos or videos",
        "media_desc": "You can select several files at once. Each one is reviewed on its own — nothing goes live automatically.",
        "media_field_label": "Photos or videos",
        "media_add_button": "+ Add photos or videos",
        "media_js_video": "Video",
        "media_js_caption": "Caption or note (optional)",
        "media_js_remove": "Remove",
        "thank_you_title": "Thank you — daugavpils.fans",
        "thank_you_heading": "Thank you",
        "proposal_submitted_msg": "Your proposal has been submitted for review by a curated approver. Nothing goes live automatically - you'll only see the change if it's approved.",
        "media_submitted_msg": "{count} file(s) submitted for review by a curated approver. Nothing goes live automatically - you'll only see the change if it's approved.",
        "media_empty_msg": "Nothing was submitted.",
        "media_skipped_heading": "The following couldn't be accepted:",
        "photo_prefix": "Photo",
        "video_prefix": "Video",
    },
}


def get_locale() -> str:
    """Determine the active language for the current request.

    Priority order:
    1. Query param: ?lang=ru or ?lang=en (explicit user choice, saved to session)
    2. Form data: lang=ru or lang=en (on POST submissions)
    3. Session: session['lang'] (persisted from previous request)
    4. Cookie: cookie 'lang'
    5. Accept-Language header (browser preference negotiation)
    6. Default: 'ru' (matching the rest of the archive)
    """
    # 1. Query parameter
    lang = request.args.get("lang")
    if lang in SUPPORTED_LANGS:
        session["lang"] = lang
        return lang

    # 2. Form field
    form_lang = request.form.get("lang")
    if form_lang in SUPPORTED_LANGS:
        session["lang"] = form_lang
        return form_lang

    # 3. Session
    sess_lang = session.get("lang")
    if sess_lang in SUPPORTED_LANGS:
        return sess_lang

    # 4. Cookie
    cookie_lang = request.cookies.get("lang")
    if cookie_lang in SUPPORTED_LANGS:
        return cookie_lang

    # 5. Accept-Language header
    if request.accept_languages:
        best = request.accept_languages.best_match(SUPPORTED_LANGS)
        if best:
            return best

    # 6. Default
    return DEFAULT_LANG


def get_text(key: str, lang: str | None = None) -> str:
    """Look up a localized UI string."""
    if lang is None:
        lang = get_locale()
    return STRINGS.get(lang, STRINGS[DEFAULT_LANG]).get(key, key)


def lang_switch_url(target_lang: str) -> str:
    """Generate a URL for the current request with the lang parameter updated."""
    raw_query = request.query_string.decode("utf-8") if request.query_string else ""
    pairs = [(k, v) for k, v in parse_qsl(raw_query, keep_blank_values=True) if k != "lang"]
    pairs.append(("lang", target_lang))
    return f"{request.path}?{urlencode(pairs)}"
