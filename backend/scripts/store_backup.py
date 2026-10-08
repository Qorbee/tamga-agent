"""Copy a database dump off the server, into the file storage's bucket under `backups/`, and remove
the copies there older than the days kept. `deploy/tamga backup --offsite` runs it in the backend's
image, which holds the bucket's keys, and sends the dump on stdin, since the image's user cannot
read the folder the dumps are kept in:

python scripts/store_backup.py --name app-20261008T030000Z.dump --days 14 < <dump>

The dump holds every company's mail, so it goes only to the bucket the files are already in, and is
uploaded in parts: a dump is larger than one request should carry.
"""

import argparse
import sys
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.platform.clock import utcnow  # noqa: E402

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

PREFIX = "backups/"


def client() -> S3Client:
    settings = get_settings()
    if not (
        settings.s3_endpoint_url
        and settings.s3_bucket
        and settings.s3_access_key_id
        and settings.s3_secret_access_key
    ):
        sys.exit("set S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="the dump's file name, kept as its key")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    name = Path(args.name).name
    if not name.startswith("app-") or sys.stdin.isatty():
        sys.exit("send a dump named app-…, on stdin")
    s3 = client()
    bucket = str(get_settings().s3_bucket)
    key = PREFIX + name
    s3.upload_fileobj(sys.stdin.buffer, bucket, key)
    stored = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    if not stored:
        s3.delete_object(Bucket=bucket, Key=key)
        sys.exit("the dump sent was empty: nothing kept")
    print(f"stored: {bucket}/{key}, {stored} bytes")
    cutoff = utcnow() - timedelta(days=args.days)
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for each in page.get("Contents", []):
            if each["LastModified"] < cutoff and each["Key"] != key:
                s3.delete_object(Bucket=bucket, Key=each["Key"])
                print(f"removed: {each['Key']}")


if __name__ == "__main__":
    main()
