"""UI-chrome strings for the site (labels, nav, headings) - not archive content.

Band/release names, bios, and track titles stay in whatever language they
were actually written in (see CONTEXT.md / README) - only the site's own
wrapper text gets translated here.
"""

STRINGS = {
    "ru": {
        "archive_title": "Архив музыки Даугавпилса",
        "site_tagline": "Записи, фотографии и истории музыкальной сцены Даугавпилса.",
        "all_bands": "Все группы",
        "also_known_as": "Также известна как",
        "members": "Участники",
        "photos": "Фотографии",
        "video": "Видео",
        "releases": "Релизы",
        "elsewhere": "Ещё",
        "tracklist": "Треклист",
        "licensed_under": "Лицензия",
        "support_link": "Поддержка",
    },
    "en": {
        "archive_title": "Daugavpils music archive",
        "site_tagline": "Recordings, photos, and stories from the Daugavpils (Latvia) music scene.",
        "all_bands": "All bands",
        "also_known_as": "Also known as",
        "members": "Members",
        "photos": "Photos",
        "video": "Video",
        "releases": "Releases",
        "elsewhere": "Elsewhere",
        "tracklist": "Tracklist",
        "licensed_under": "Licensed under",
        "support_link": "Support",
    },
}

LANGS = list(STRINGS.keys())
DEFAULT_LANG = "ru"


def lang_prefix(lang: str) -> str:
    return "" if lang == DEFAULT_LANG else f"{lang}/"


def home_url(lang: str, base_path: str = "") -> str:
    return f"{base_path}/{lang_prefix(lang)}"


def band_url(lang: str, band_slug: str, base_path: str = "") -> str:
    return f"{base_path}/{lang_prefix(lang)}bands/{band_slug}/"


def release_url(lang: str, band_slug: str, release_slug: str, base_path: str = "") -> str:
    return f"{base_path}/{lang_prefix(lang)}bands/{band_slug}/{release_slug}/"
