"""
Sanitizes image metadata to protect community contributors' privacy (GitHub issue #53).
Strips GPS coordinates (GPS IFD 0x8825) and device identifiers (Make, Model, Serial numbers,
Owner names, MakerNote) while preserving display orientation (Orientation 0x0112) and
visual quality.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

SENSITIVE_TAG_IDS = {
    0x010F,  # Make
    0x0110,  # Model
    0x0131,  # Software
    0x013B,  # Artist
    0x014C,  # HostComputer
    0x8298,  # Copyright
    0x8825,  # GPSInfo IFD
    0x927C,  # MakerNote
    0x9286,  # UserComment
    0xA420,  # ImageUniqueID
    0xA430,  # CameraOwnerName
    0xA431,  # BodySerialNumber
    0xA432,  # LensSpecification
    0xA433,  # LensMake
    0xA434,  # LensModel
    0xA435,  # LensSerialNumber
}


def has_sensitive_exif(img: Image.Image) -> bool:
    """Checks whether the image contains any GPS or sensitive device EXIF tags."""
    try:
        exif = img.getexif()
        if not exif:
            return False
        for tag in exif:
            if tag in SENSITIVE_TAG_IDS:
                return True
        if 0x8825 in exif or (hasattr(exif, "_ifds") and 0x8825 in exif._ifds):
            if bool(exif.get_ifd(0x8825)):
                return True
        if 0x8769 in exif or (hasattr(exif, "_ifds") and 0x8769 in exif._ifds):
            sub_ifd = exif.get_ifd(0x8769)
            for tag in sub_ifd:
                if tag in SENSITIVE_TAG_IDS:
                    return True
    except Exception:
        pass
    return False


def strip_sensitive_exif(image_path: Path) -> bool:
    """Strips GPS and personal device metadata from image_path in-place,
    while preserving image display orientation and visual quality.
    Idempotent: if no sensitive metadata is present, the file is untouched.
    Returns True if sensitive metadata was stripped, False if untouched or unreadable.
    """
    image_path = Path(image_path)
    if not image_path.exists() or not image_path.is_file():
        return False

    try:
        with Image.open(image_path) as img:
            format_name = img.format or "JPEG"
            if not has_sensitive_exif(img):
                return False

            exif = img.getexif()
            clean_exif = Image.Exif()

            # Pointer tags to sub-IFDs must not be copied as integer offsets into clean_exif
            IFD_POINTER_TAGS = {0x8769, 0x8825}

            # Preserve orientation and safe root tags
            for tag, val in exif.items():
                if tag not in SENSITIVE_TAG_IDS and tag not in IFD_POINTER_TAGS:
                    clean_exif[tag] = val

            # Preserve safe tags in Exif sub-IFD (0x8769)
            if 0x8769 in exif or (hasattr(exif, "_ifds") and 0x8769 in exif._ifds):
                sub_ifd = exif.get_ifd(0x8769)
                clean_sub = clean_exif.get_ifd(0x8769)
                for tag, val in sub_ifd.items():
                    if tag not in SENSITIVE_TAG_IDS:
                        clean_sub[tag] = val

            tmp_dest = image_path.with_suffix(image_path.suffix + ".tmp_clean")
            save_kwargs: dict[str, Any] = {"format": format_name, "exif": clean_exif}
            if format_name.upper() in ("JPEG", "JPG"):
                save_kwargs["quality"] = "keep"

            try:
                img.save(tmp_dest, **save_kwargs)
            except Exception:
                if "quality" in save_kwargs:
                    save_kwargs.pop("quality")
                img.save(tmp_dest, **save_kwargs)

            tmp_dest.replace(image_path)
            return True
    except Exception:
        return False
