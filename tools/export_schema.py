#!/usr/bin/env python3
"""
Export the archive's data models as standalone JSON Schema files.

This is the step that produces the *real* portable contract: schema/*.schema.json.
Anyone reusing this archive in any language validates against those files,
not against this Python code.
"""
import json
from pathlib import Path

from models import MusicAlbum, MusicGroup

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)

    targets = {
        "band.schema.json": MusicGroup,
        "release.schema.json": MusicAlbum,
    }
    for filename, model in targets.items():
        out_path = SCHEMA_DIR / filename
        schema = model.model_json_schema(by_alias=True)
        out_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
