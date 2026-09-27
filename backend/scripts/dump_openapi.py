"""Write ``backend/openapi.json``, or check that it is current.

uv run python scripts/dump_openapi.py          # rewrite after any request/response model change
uv run python scripts/dump_openapi.py --check  # exit 1 if stale (the test suite checks this too)
"""

import argparse
import sys
from pathlib import Path

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.openapi import OPENAPI_PATH, render  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args()

    current = render()
    if args.check:
        on_disk = OPENAPI_PATH.read_text() if OPENAPI_PATH.exists() else ""
        if on_disk != current:
            print(f"{OPENAPI_PATH} is stale. Run: uv run python scripts/dump_openapi.py")
            return 1
        return 0

    OPENAPI_PATH.write_text(current)
    print(f"wrote {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
