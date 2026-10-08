"""Copy every stored file to another bucket under the same key, such as from this machine's storage
to the deployed R2 bucket, so a database moved with them finds each file where it was.

uv run python scripts/copy_files.py --endpoint https://<account id>.r2.cloudflarestorage.com \
    --bucket <bucket> --access-key-id <key id>

The source is this machine's storage, from backend/.env. The target's secret key is asked for, so it
never sits in the shell's history. A key the target already holds is skipped: a key names its
file's content, so a stopped run is simply run again.
"""

import argparse
import getpass
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def client(endpoint: str, access_key_id: str, secret: str, region: str) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret,
        region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def held(target: S3Client, bucket: str, key: str) -> bool:
    try:
        target.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--access-key-id", required=True)
    parser.add_argument("--region", default="auto", help="R2's is auto")
    parser.add_argument("--prefix", default="", help="only the keys under it, such as a company id")
    args = parser.parse_args()

    settings = get_settings()
    if not (settings.s3_endpoint_url and settings.s3_access_key_id and settings.s3_bucket):
        print("this machine's storage is not configured (S3_* in backend/.env)", file=sys.stderr)
        return 1
    source = client(
        settings.s3_endpoint_url,
        settings.s3_access_key_id,
        settings.s3_secret_access_key.get_secret_value() if settings.s3_secret_access_key else "",
        settings.s3_region,
    )
    target = client(args.endpoint, args.access_key_id, getpass.getpass("secret key: "), args.region)

    copied = skipped = size = 0
    pages = source.get_paginator("list_objects_v2").paginate(
        Bucket=settings.s3_bucket, Prefix=args.prefix
    )
    for page in pages:
        for item in page.get("Contents", []):
            key = item["Key"]
            if held(target, args.bucket, key):
                skipped += 1
                continue
            stored = source.get_object(Bucket=settings.s3_bucket, Key=key)
            target.put_object(
                Bucket=args.bucket,
                Key=key,
                Body=stored["Body"].read(),
                ContentType=stored.get("ContentType", "application/octet-stream"),
            )
            copied += 1
            size += item["Size"]
            if copied % 100 == 0:
                print(f"{copied} copied, {size / 1e6:.0f} MB")
    print(f"{copied} copied ({size / 1e6:.0f} MB), {skipped} already there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
