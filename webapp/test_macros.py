from __future__ import annotations

import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


class LightboxCaptionTest(unittest.TestCase):
    def setUp(self) -> None:
        templates_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html"]),
        )
        self.env.globals["localize"] = lambda obj, field, lang: (
            obj.get(f"{field}_en") if lang == "en" and obj.get(f"{field}_en") else obj.get(field)
        )

    def test_gallery_lightbox_includes_caption_when_present(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.gallery(images, "band-photo", "fallback", url_fn) }}
""")
        images = [
            {"contentUrl": "media/photo1.webp", "caption": "Описание на русском", "caption_en": "English description"},
        ]
        # Test Russian rendering
        html_ru = tmpl.render(lang="ru", images=images, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<figure class="lightbox-figure">', html_ru)
        self.assertIn('<figcaption class="lightbox-caption">Описание на русском</figcaption>', html_ru)

        # Test English rendering
        html_en = tmpl.render(lang="en", images=images, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<figcaption class="lightbox-caption">English description</figcaption>', html_en)

    def test_gallery_lightbox_omits_caption_when_none(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.gallery(images, "band-photo", "fallback", url_fn) }}
""")
        images = [
            {"contentUrl": "media/photo-no-caption.webp"},
        ]
        rendered = tmpl.render(lang="ru", images=images, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<figure class="lightbox-figure">', rendered)
        self.assertNotIn("lightbox-caption", rendered)

    def test_video_gallery_lightbox_includes_caption_when_present(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.video_gallery(videos, "band-video", "fallback", url_fn) }}
""")
        videos = [
            {"contentUrl": "media/vid1.mp4", "encodingFormat": "video/mp4", "name": "Концерт 1995"},
            {"contentUrl": "media/vid2.mp4", "encodingFormat": "video/mp4", "name": "Репетиция 1996"},
        ]
        rendered = tmpl.render(lang="ru", videos=videos, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<figure class="lightbox-figure">', rendered)
        self.assertIn('<figcaption class="lightbox-caption">Концерт 1995</figcaption>', rendered)
        self.assertIn('<figcaption class="lightbox-caption">Репетиция 1996</figcaption>', rendered)


if __name__ == "__main__":
    unittest.main()
