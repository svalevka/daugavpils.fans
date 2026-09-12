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

    def test_video_gallery_renders_clickable_thumbnails_with_play_badge(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.video_gallery(videos, "band-video", "fallback", url_fn) }}
""")
        videos = [
            {"contentUrl": "media/vid1.mp4", "encodingFormat": "video/mp4", "name": "Концерт 1995"},
        ]
        rendered = tmpl.render(lang="ru", videos=videos, url_fn=lambda u: f"https://archive.org/{u}")
        # Thumbnail is an <a> link to the lightbox target
        self.assertIn('<a href="#band-video-1" class="gallery-item video-item"', rendered)
        # First frame preview via preload="metadata" and #t=0.001
        self.assertIn('<div class="video-thumb-wrap">', rendered)
        self.assertIn('<video preload="metadata" muted playsinline tabindex="-1" aria-hidden="true">', rendered)
        self.assertIn('src="https://archive.org/media/vid1.mp4#t=0.001"', rendered)
        # Play badge overlay is present
        self.assertIn('<span class="video-play-badge" aria-hidden="true"></span>', rendered)
        # Thumbnail caption
        self.assertIn('<figcaption>Концерт 1995</figcaption>', rendered)
        # No yellow box expand-link
        self.assertNotIn("expand-link", rendered)
        # Lightbox is generated even for a single video
        self.assertIn('<div class="lightbox video-lightbox" id="band-video-1">', rendered)
        self.assertIn('data-video="Концерт 1995"', rendered)
        self.assertNotIn("lightbox-prev", rendered)
        self.assertNotIn("lightbox-next", rendered)

    def test_video_gallery_multi_video_includes_navigation(self) -> None:
        tmpl = self.env.from_string("""
{% import "_macros.html" as macros with context %}
{{ macros.video_gallery(videos, "band-video", "fallback", url_fn) }}
""")
        videos = [
            {"contentUrl": "media/vid1.mp4", "encodingFormat": "video/mp4", "name": "Video 1"},
            {"contentUrl": "media/vid2.mp4", "encodingFormat": "video/mp4", "name": "Video 2"},
        ]
        rendered = tmpl.render(lang="ru", videos=videos, url_fn=lambda u: f"https://archive.org/{u}")
        self.assertIn('<a href="#band-video-2" class="lightbox-prev" aria-label="Previous video">&#10094;</a>', rendered)
        self.assertIn('<a href="#band-video-2" class="lightbox-next" aria-label="Next video">&#10095;</a>', rendered)


if __name__ == "__main__":
    unittest.main()
