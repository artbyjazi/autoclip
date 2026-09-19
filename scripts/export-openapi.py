"""Write the deterministic FastAPI schema used as the frontend contract."""

from __future__ import annotations

import json
from pathlib import Path

from clippy.app import create_app


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "docs" / "openapi-v1.json"
    destination.write_text(
        json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
