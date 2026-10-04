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

SUPPORTED_LANGS = ("ru", "lv", "en")
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
        "challenge_question": "Проверочный вопрос: какому городу посвящён этот архив?",
        "challenge_placeholder": "Даугавпилс / Daugavpils",
        "challenge_hint": "Защита от спама: укажите название города (на русском или латышском).",
        "submit_button": "Отправить на проверку",
        "add_member_title": "Добавить участника группы — daugavpils.fans",
        "add_member_heading": "Добавить участника группы",
        "add_member_hint": "Это для тех, кто был участником группы на протяжении времени. "
        "Если человек участвовал только в одном релизе (например, спел на одной записи) — "
        "укажите это на странице этого релиза, в поле «Участники записи».",
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
        "media_or_youtube": "Или добавьте видео по ссылке с YouTube — вместо загрузки файла:",
        "media_youtube_url_label": "Ссылка на видео YouTube",
        "media_rights_attested_label": "Я имею право поделиться этим видео и понимаю, что оно будет добавлено в архив на постоянной основе.",
        "media_youtube_fetching_msg": "Видео загружается по ссылке. Как только загрузка завершится, оно будет отправлено на проверку кураторам — ничего не публикуется автоматически.",
        "thank_you_title": "Спасибо — daugavpils.fans",
        "thank_you_heading": "Спасибо",
        "proposal_submitted_msg": "Ваше предложение отправлено на проверку кураторам. Никакие изменения не публикуются автоматически — они появятся на сайте только после одобрения.",
        "media_submitted_msg": "Файлы ({count}) отправлены на проверку кураторам. Никакие изменения не публикуются автоматически — они появятся на сайте только после одобрения.",
        "media_empty_msg": "Ничего не было отправлено.",
        "media_skipped_heading": "Следующие файлы не удалось принять:",
        "photo_prefix": "Фото",
        "video_prefix": "Видео",
        "add_new_release": "Добавить новый релиз (альбом)",
        "add_release_title": "Добавить новый альбом — daugavpils.fans",
        "add_release_heading": "Добавить новый альбом",
        "add_release_desc": "Загрузите аудиозаписи и укажите информацию об альбоме. После проверки кураторами альбом будет сохранён в архиве и опубликован на сайте.",
        "album_name": "Название альбома",
        "album_year": "Год выпуска (например, 1996)",
        "album_genre": "Жанр (необязательно, через запятую)",
        "album_license": "Лицензия",
        "album_description": "Описание / история записи / liner notes (необязательно)",
        "album_description_en": "Описание на английском (необязательно)",
        "album_cover": "Обложка альбома (JPEG, PNG или WEBP, до 15МБ, необязательно)",
        "album_tracks": "Аудиозаписи альбома (MP3, FLAC, WAV, OGG)",
        "album_submitted_msg": "Альбом «{title}» ({count} треков) успешно отправлен на проверку кураторам. После одобрения он появится в архиве.",
        "album_error_no_tracks": "Пожалуйста, выберите хотя бы один аудиофайл.",
        "add_new_band": "Предложить новую группу",
        "add_band_title": "Предложить новую группу — daugavpils.fans",
        "add_band_heading": "Предложить новую группу",
        "add_band_desc": "Предложите даугавпилсскую группу для архива. Вы можете указать информацию о группе и при желании сразу загрузить первый релиз с аудиозаписями.",
        "band_name": "Название группы",
        "band_founding_date": "Год основания (например, 1993)",
        "band_dissolution_date": "Год распада (если распалась, например, 2001)",
        "band_location": "Город / локация",
        "band_genre": "Жанр (необязательно, через запятую)",
        "band_description": "Биография / история группы (воспоминания, свидетельства)",
        "band_description_en": "История группы на английском (необязательно)",
        "band_photo": "Фотография группы (JPEG, PNG или WEBP, до 25МБ, необязательно)",
        "band_has_release_toggle": "Прикрепить первый релиз / альбом",
        "band_release_section_heading": "Первый релиз / альбом",
        "band_submitted_msg": "Группа «{name}» успешно отправлена на проверку кураторам. После одобрения она появится в архиве.",
        "band_submitted_with_release_msg": "Группа «{name}» и первый релиз «{release}» ({count} треков) успешно отправлены на проверку кураторам. После одобрения они появятся в архиве.",
        "drag_drop_hint": "или перетащите аудиофайлы сюда",
        "preview_audio": "Прослушать трек",
        "pause_audio": "Пауза",
        "uploading_status": "Загрузка... {percent}% ({loaded} из {total})",
        "uploading_processing": "Обработка и проверка файлов на сервере...",
        "upload_network_error": "Ошибка сети при загрузке. Проверьте подключение к интернету и попробуйте снова.",
        "upload_rate_limit_error": "Слишком много запросов. Пожалуйста, подождите час перед повторной отправкой.",
        "upload_server_error": "Ошибка сервера ({status}). Пожалуйста, проверьте файлы и попробуйте снова.",
        "draft_restored": "Восстановлен несохранённый черновик от {time}.",
        "draft_clear": "Очистить черновик",
        "draft_cleared": "Черновик очищен.",
    },
    "lv": {
        "site_title": "daugavpils.fans",
        "support_link": "Atbalsts",
        "propose_edit": "Ieteikt labojumu",
        "pick_band_title": "Ieteikt labojumu — daugavpils.fans",
        "pick_band_heading": "Ieteikt labojumu",
        "pick_band_meta": "Izvēlieties grupu, lai ieteiktu labojumu vai papildinājumu.",
        "pick_target_title": "Ko vēlaties labot vai papildināt? — daugavpils.fans",
        "pick_target_heading": "Ko vēlaties labot vai papildināt?",
        "section_releases": "Ieraksti",
        "section_members": "Dalībnieki",
        "add_new_member": "Pievienot jaunu dalībnieku",
        "section_media": "Mediji",
        "add_photos_or_videos": "Pievienot foto vai video",
        "section_fields": "Lauki",
        "current_text": "Pašreizējais teksts",
        "replacement_text": "Jūsu teksta variants",
        "submitter_name": "Jūsu vārds (neobligāti)",
        "submitter_contact_edit": "Jūsu e-pasts, ja vēlaties saņemt atbildi (neobligāti)",
        "submitter_contact_media": "Jūsu e-pasts, ja vēlaties uzzināt par apstiprināšanu (neobligāti)",
        "honeypot_label": "Atstājiet šo lauku tukšu",
        "challenge_question": "Pārbaudes jautājums: kurai pilsētai ir veltīts šis arhīvs?",
        "challenge_placeholder": "Daugavpils / Даугавпилс",
        "challenge_hint": "Aizsardzība pret mēstulēm: norādiet pilsētas nosaukumu (latviski vai krieviski).",
        "submit_button": "Nosūtīt pārbaudei",
        "add_member_title": "Pievienot grupas dalībnieku — daugavpils.fans",
        "add_member_heading": "Pievienot grupas dalībnieku",
        "add_member_hint": "Šis ir paredzēts tiem, kas bija grupas dalībnieki noteiktā laika posmā. "
        "Ja cilvēks piedalījās tikai vienā ierakstā (piemēram, iedziedāja vienā dziesmā) — "
        "norādiet to attiecīgā ieraksta lapā sadaļā «Ieraksta dalībnieki».",
        "member_name": "Vārds (oriģinālvalodā / kā rakstīts)",
        "member_name_en": "Vārds (latīņu transliterācija, neobligāti)",
        "member_role": "Loma / instruments (neobligāti)",
        "member_role_en": "Loma (angļu valodā, neobligāti)",
        "member_period": "Periods, piemēram, «1994–1996» (neobligāti)",
        "media_title": "Pievienot foto vai video — daugavpils.fans",
        "media_heading": "Pievienot foto vai video",
        "media_desc": "Varat izvēlēties vairākus failus vienlaicīgi. Katrs fails tiek pārbaudīts atsevišķi — nekas netiek publicēts automātiski.",
        "media_field_label": "Fotogrāfijas vai video",
        "media_add_button": "+ Pievienot foto vai video",
        "media_js_video": "Video",
        "media_js_caption": "Paraksts vai piezīme (neobligāti)",
        "media_js_remove": "Dzēst",
        "media_or_youtube": "Vai pievienojiet video ar YouTube saiti — faila augšupielādes vietā:",
        "media_youtube_url_label": "YouTube video saite",
        "media_rights_attested_label": "Man ir tiesības dalīties ar šo video, un es saprotu, ka tas tiks pievienots arhīvam pastāvīgai glabāšanai.",
        "media_youtube_fetching_msg": "Video tiek ielādēts no saites. Tiklīdz ielāde būs pabeigta, tas tiks nosūtīts kuratoru pārbaudei — nekas netiek publicēts automātiski.",
        "thank_you_title": "Paldies — daugavpils.fans",
        "thank_you_heading": "Paldies",
        "proposal_submitted_msg": "Jūsu priekšlikums ir nosūtīts kuratoru pārbaudei. Izmaiņas netiek publicētas automātiski — tās parādīsies vietnē tikai pēc apstiprināšanas.",
        "media_submitted_msg": "Faili ({count}) ir nosūtīti kuratoru pārbaudei. Izmaiņas netiek publicētas automātiski — tie parādīsies vietnē tikai pēc apstiprināšanas.",
        "media_empty_msg": "Nekas netika nosūtīts.",
        "media_skipped_heading": "Šos failus neizdevās pieņemt:",
        "photo_prefix": "Foto",
        "video_prefix": "Video",
        "add_new_release": "Pievienot jaunu albumu",
        "add_release_title": "Pievienot jaunu albumu — daugavpils.fans",
        "add_release_heading": "Pievienot jaunu albumu",
        "add_release_desc": "Augšupielādējiet audioierakstus un norādiet informāciju par albumu. Pēc kuratoru pārbaudes albums tiks saglabāts arhīvā un publicēts vietnē.",
        "album_name": "Albuma nosaukums",
        "album_year": "Izdošanas gads (piemēram, 1996)",
        "album_genre": "Žanrs (neobligāti, atdalot ar komatiem)",
        "album_license": "Licence",
        "album_description": "Apraksts / ieraksta vēsture / liner notes (neobligāti)",
        "album_description_en": "Apraksts angļu valodā (neobligāti)",
        "album_cover": "Albuma vāks (JPEG, PNG vai WEBP, līdz 15MB, neobligāti)",
        "album_tracks": "Albuma audioieraksti (MP3, FLAC, WAV, OGG)",
        "album_submitted_msg": "Albums «{title}» ({count} skaņdarbi) veiksmīgi nosūtīts kuratoru pārbaudei. Pēc apstiprināšanas tas parādīsies arhīvā.",
        "album_error_no_tracks": "Lūdzu, izvēlieties vismaz vienu audiofailu.",
        "add_new_band": "Piedāvāt jaunu grupu",
        "add_band_title": "Piedāvāt jaunu grupu — daugavpils.fans",
        "add_band_heading": "Piedāvāt jaunu grupu",
        "add_band_desc": "Piedāvājiet Daugavpils grupu arhīvam. Varat norādīt informāciju par grupu un pēc vēlēšanās uzreiz pievienot pirmo albumu ar audioierakstiem.",
        "band_name": "Grupas nosaukums",
        "band_founding_date": "Dibināšanas gads (piemēram, 1993)",
        "band_dissolution_date": "Izjukšanas gads (ja izjukusi, piemēram, 2001)",
        "band_location": "Pilsēta / vieta",
        "band_genre": "Žanrs (neobligāti, atdalot ar komatiem)",
        "band_description": "Grupas biogrāfija / vēsture (atmiņas, liecības)",
        "band_description_en": "Grupas vēsture angļu valodā (neobligāti)",
        "band_photo": "Grupas fotogrāfija (JPEG, PNG vai WEBP, līdz 25MB, neobligāti)",
        "band_has_release_toggle": "Pievienot pirmo albumu",
        "band_release_section_heading": "Pirmais albums",
        "band_submitted_msg": "Grupa «{name}» veiksmīgi nosūtīta kuratoru pārbaudei. Pēc apstiprināšanas tā parādīsies arhīvā.",
        "band_submitted_with_release_msg": "Grupa «{name}» un pirmais albums «{release}» ({count} skaņdarbi) veiksmīgi nosūtīti kuratoru pārbaudei. Pēc apstiprināšanas tie parādīsies arhīvā.",
        "drag_drop_hint": "vai ievelciet audiofailus šeit",
        "preview_audio": "Noklausīties skaņdarbu",
        "pause_audio": "Pauze",
        "uploading_status": "Augšupielāde... {percent}% ({loaded} no {total})",
        "uploading_processing": "Failu apstrāde un pārbaude uz servera...",
        "upload_network_error": "Tīkla kļūda augšupielādes laikā. Pārbaudiet interneta pieslēgumu un mēģiniet vēlreiz.",
        "upload_rate_limit_error": "Pārāk daudz pieprasījumu. Lūdzu, uzgaidiet stundu pirms atkārtotas nosūtīšanas.",
        "upload_server_error": "Servera kļūda ({status}). Lūdzu, pārbaudiet failus un mēģiniet vēlreiz.",
        "draft_restored": "Atjaunots nesaglabāts melnraksts no {time}.",
        "draft_clear": "Notīrīt melnrakstu",
        "draft_cleared": "Melnraksts notīrīts.",
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
        "challenge_question": "Verification question: which city is this archive dedicated to?",
        "challenge_placeholder": "Daugavpils / Даугавпилс",
        "challenge_hint": "Anti-spam verification: enter the city name (in English or Latvian).",
        "submit_button": "Submit for review",
        "add_member_title": "Add a band member — daugavpils.fans",
        "add_member_heading": "Add a band member",
        "add_member_hint": "This is for someone who was part of the band over a span of time. "
        "If they only contributed to one release (e.g. sang on a single recording), use that "
        "release's own page instead, under \"Recording credits\".",
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
        "media_or_youtube": "Or add a video from a YouTube link instead of uploading a file:",
        "media_youtube_url_label": "YouTube video link",
        "media_rights_attested_label": "I have the right to share this video and understand it will be permanently archived.",
        "media_youtube_fetching_msg": "The video is being fetched from the link. Once that finishes, it will be sent for review by a curated approver — nothing goes live automatically.",
        "thank_you_title": "Thank you — daugavpils.fans",
        "thank_you_heading": "Thank you",
        "proposal_submitted_msg": "Your proposal has been submitted for review by a curated approver. Nothing goes live automatically - you'll only see the change if it's approved.",
        "media_submitted_msg": "{count} file(s) submitted for review by a curated approver. Nothing goes live automatically - you'll only see the change if it's approved.",
        "media_empty_msg": "Nothing was submitted.",
        "media_skipped_heading": "The following couldn't be accepted:",
        "photo_prefix": "Photo",
        "video_prefix": "Video",
        "add_new_release": "Add a new release (album)",
        "add_release_title": "Add a new album — daugavpils.fans",
        "add_release_heading": "Add a new album",
        "add_release_desc": "Upload audio tracks and provide album details. After curator review, the album will be preserved and published on the archive.",
        "album_name": "Album title",
        "album_year": "Release year (e.g. 1996)",
        "album_genre": "Genre (optional, comma-separated)",
        "album_license": "License",
        "album_description": "Description / liner notes (optional)",
        "album_description_en": "Description in English (optional)",
        "album_cover": "Album cover art (JPEG, PNG or WEBP, up to 15MB, optional)",
        "album_tracks": "Album audio files (MP3, FLAC, WAV, OGG)",
        "album_submitted_msg": "The album \"{title}\" ({count} tracks) has been submitted for curator review. Once approved, it will be added to the archive.",
        "album_error_no_tracks": "Please select at least one audio file.",
        "add_new_band": "Propose a new band",
        "add_band_title": "Propose a new band — daugavpils.fans",
        "add_band_heading": "Propose a new band",
        "add_band_desc": "Propose a Daugavpils band for the archive. You can provide band history and optionally attach their first release with audio recordings.",
        "band_name": "Band name",
        "band_founding_date": "Founding year (e.g. 1993)",
        "band_dissolution_date": "Dissolution year (if defunct, e.g. 2001)",
        "band_location": "Location / city",
        "band_genre": "Genre (optional, comma-separated)",
        "band_description": "Band history / biography (testimony)",
        "band_description_en": "Band history in English (optional)",
        "band_photo": "Band photo (JPEG, PNG or WEBP up to 25MB, optional)",
        "band_has_release_toggle": "Include first release / album",
        "band_release_section_heading": "First release / album",
        "band_submitted_msg": "Band “{name}” was submitted for curator review. Once approved, it will appear in the archive.",
        "band_submitted_with_release_msg": "Band “{name}” and first release “{release}” ({count} tracks) were submitted for curator review. Once approved, they will appear in the archive.",
        "drag_drop_hint": "or drag and drop audio files here",
        "preview_audio": "Preview track",
        "pause_audio": "Pause",
        "uploading_status": "Uploading... {percent}% ({loaded} of {total})",
        "uploading_processing": "Processing and validating files on server...",
        "upload_network_error": "Network error during upload. Please check your internet connection and try again.",
        "upload_rate_limit_error": "Too many submissions. Please wait an hour before submitting again.",
        "upload_server_error": "Server error ({status}). Please check your files and try again.",
        "draft_restored": "Restored unsaved draft from {time}.",
        "draft_clear": "Clear draft",
        "draft_cleared": "Draft cleared.",
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
