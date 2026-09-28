"""Private file storage over the S3 API: Cloudflare R2 in production, SeaweedFS locally (ADR-0001).

Files are addressed by content, ``<company id>/<sha256>``: the same file stored twice for one
company is one object, and a key never leaks a file name. Nothing is public; a browser reads a file
through a short-lived presigned URL. boto3 is synchronous, so each network call runs in a worker
thread instead of blocking the event loop.
"""

import asyncio
import hashlib
import uuid
from functools import cache
from typing import TYPE_CHECKING, Final

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings

if TYPE_CHECKING:
    # Types only: the stubs are a dev dependency, absent from the production image.
    from mypy_boto3_s3 import S3Client

PRESIGN_SECONDS: Final = 300


class StorageNotConfigured(RuntimeError):
    pass


def content_key(company_id: uuid.UUID, data: bytes) -> str:
    return f"{company_id}/{hashlib.sha256(data).hexdigest()}"


async def put(key: str, data: bytes, *, content_type: str) -> None:
    client, bucket = _client(), _bucket()
    await asyncio.to_thread(
        client.put_object, Bucket=bucket, Key=key, Body=data, ContentType=content_type
    )


async def get(key: str) -> bytes:
    client, bucket = _client(), _bucket()
    response = await asyncio.to_thread(client.get_object, Bucket=bucket, Key=key)
    return await asyncio.to_thread(response["Body"].read)


async def delete(key: str) -> None:
    client, bucket = _client(), _bucket()
    await asyncio.to_thread(client.delete_object, Bucket=bucket, Key=key)


def presign(key: str, *, filename: str | None = None, seconds: int = PRESIGN_SECONDS) -> str:
    """A URL that reads one object for ``seconds``. Signing is local: no network call."""
    params = {"Bucket": _bucket(), "Key": key}
    if filename is not None:
        # The stored key has no name; the download gets the original one back.
        params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{_quote(filename)}"
    return _signing_client().generate_presigned_url("get_object", Params=params, ExpiresIn=seconds)


async def ready() -> bool:
    """The bucket exists and our keys can reach it."""
    try:
        client, bucket = _client(), _bucket()
        await asyncio.to_thread(client.head_bucket, Bucket=bucket)
    except StorageNotConfigured, BotoCoreError, ClientError:
        return False
    return True


@cache
def _client() -> S3Client:
    return _new_client(get_settings().s3_endpoint_url)


@cache
def _signing_client() -> S3Client:
    """Signs URLs for the host a browser reaches, which a signature covers: under compose's app
    profile the backend reaches storage:8333, the browser localhost:8333."""
    settings = get_settings()
    return _new_client(settings.s3_public_endpoint_url or settings.s3_endpoint_url)


def _new_client(endpoint_url: str | None) -> S3Client:
    settings = get_settings()
    if not (endpoint_url and settings.s3_access_key_id and settings.s3_secret_access_key):
        raise StorageNotConfigured("set S3_ENDPOINT_URL, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        region_name=settings.s3_region,
        # Path-style addressing: SeaweedFS has no per-bucket hostnames, and R2 accepts both.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _bucket() -> str:
    bucket = get_settings().s3_bucket
    if not bucket:
        raise StorageNotConfigured("set S3_BUCKET")
    return bucket


def _quote(filename: str) -> str:
    from urllib.parse import quote

    return quote(filename, safe="")
