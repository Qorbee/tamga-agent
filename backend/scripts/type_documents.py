"""Has the typist type the read files of a company that code could not type, newest first: a
measured trial before the worker types the rest under TYPED_FILES_PER_DAY.

uv run python scripts/type_documents.py --limit 20   (the pilot company; asks, writes nothing)
uv run python scripts/type_documents.py --limit 300 --write
uv run python scripts/type_documents.py --company-id <id> --limit 20

Prints each file's answer and the tokens the run used. Each file sends its name, the vision
model's line on it and the first 1,500 characters of its text to the typist's model (AGENT_MODELS,
"typist"); nothing reaches a mailbox or the books. With --write a file is asked about once.
"""

import argparse
import asyncio
import sys
import uuid
from collections import Counter
from pathlib import Path

from pydantic_ai.usage import RunUsage

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import packs  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.modules.documents import service  # noqa: E402
from app.platform import llm  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db  # noqa: E402


async def run(company_id: uuid.UUID, *, limit: int, write: bool) -> None:
    import_all_models()
    packs.register_all()
    init_db(str(get_settings().database_url))
    usage = RunUsage()
    try:
        answered = await service.type_files(company_id, limit=limit, write=write, usage=usage)
    finally:
        await dispose_db()
    for filename, key in answered:
        print(f"{key or '-':>20}  {filename}")
    print(Counter(key or "none" for _, key in answered).most_common())
    print(
        f"{len(answered)} file(s) on {llm.model_name('typist')}: {usage.requests} request(s), "
        f"{usage.input_tokens} input and {usage.output_tokens} output tokens"
    )
    print("written" if write else "a trial, nothing written")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--write", action="store_true", help="write the types; else a trial")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    asyncio.run(run(args.company_id, limit=args.limit, write=args.write))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
