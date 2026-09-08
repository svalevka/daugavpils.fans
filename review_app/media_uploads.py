"""
Sniffing, size-limiting, and on-disk storage for uploaded photos/videos
(see GitHub issue #21). No image/video library dependency: just the
handful of file-signature checks needed to tell a real image/video apart
from anything else, and which one it is - review_app/requirements.txt
stays free of libmagic/Pillow/etc. for this. The filename and the
browser-supplied Content-Type are never trusted (this is a public upload
endpoint); only the file's own leading bytes decide its type.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024  # 1 MiB


class UploadRejected(Exception):
    """A file failed sniffing or exceeded its size cap - callers catch
    this per-file so one bad file in a batch upload doesn't sink the
    others (see media_submissions.py)."""


def _sniff(head: bytes) -> tuple[str, str, str] | None:
    """Identify a file from its first bytes. Returns
    (media_type, content_type, file_extension), or None if it isn't a
    recognized image/video format."""
    if head[:3] == b"\xff\xd8\xff":
        return "image", "image/jpeg", ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "image/png", ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif", ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp", ".webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevm", b"hevs", b"mif1", b"msf1"):
            return "image", "image/heic", ".heic"
        if brand == b"qt  ":
            return "video", "video/quicktime", ".mov"
        # Every other ISO-base-media-file ftyp brand in practical use
        # (isom/iso2/mp41/mp42 from encoders, avc1/M4V /M4A  from Apple
        # devices, 3gp*/3g2* from older phones) is video/mp4-compatible
        # enough for our purposes - we're sniffing to accept real
        # camera/phone output, not validating strict container profiles.
        return "video", "video/mp4", ".mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video", "video/webm", ".webm"
    return None


def save_upload(
    file_storage, uploads_dir: Path, max_bytes_by_type: dict[str, int]
) -> tuple[str, str, str, int]:
    """Stream `file_storage` to disk under `uploads_dir`. Returns
    (stored_filename, content_type, media_type, size_bytes). Raises
    UploadRejected - and leaves no partial file behind - if the content
    isn't a recognized image/video, or exceeds
    max_bytes_by_type[media_type]."""
    stream: BinaryIO = file_storage.stream
    head = stream.read(64)
    sniffed = _sniff(head)
    if sniffed is None:
        raise UploadRejected("not a recognized image or video file")
    media_type, content_type, extension = sniffed
    max_bytes = max_bytes_by_type[media_type]

    uploads_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{secrets.token_hex(16)}{extension}"
    dest = uploads_dir / stored_filename

    size = len(head)
    if size > max_bytes:
        raise UploadRejected(f"exceeds the {max_bytes // (1024 * 1024)}MB limit for {media_type}s")

    with open(dest, "wb") as out:
        out.write(head)
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise UploadRejected(f"exceeds the {max_bytes // (1024 * 1024)}MB limit for {media_type}s")
            out.write(chunk)

    return stored_filename, content_type, media_type, size
