"""S3BlobStore: any S3-compatible object store (AWS S3, R2, B2, Garage, SeaweedFS…), ADR-0009.

boto3 is synchronous; calls run in a worker thread. ``upload_fileobj`` switches to multipart
uploads automatically for large files.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from typing import Any

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from codura_docs.ports.blob import BlobData, check_blob_key
from codura_docs.ports.errors import ProviderUnavailable
from codura_docs.tenancy import TenantContext

_CHUNK = 1024 * 1024
_TRANSFER = TransferConfig(multipart_threshold=8 * 1024 * 1024, multipart_chunksize=8 * 1024 * 1024)


class S3BlobStore:
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        client: Any = None,
    ) -> None:
        self._bucket = bucket
        self._s3 = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"}, s3={"addressing_style": "path"}
            ),
        )

    async def put(self, ctx: TenantContext, key: str, data: BlobData, *, content_type: str) -> int:
        check_blob_key(ctx, key)
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as spool:
            written = 0
            if hasattr(data, "read"):
                while chunk := await asyncio.to_thread(data.read, _CHUNK):
                    spool.write(chunk)
                    written += len(chunk)
            else:
                async for chunk in data:
                    spool.write(chunk)
                    written += len(chunk)
            spool.seek(0)
            try:
                await asyncio.to_thread(
                    self._s3.upload_fileobj,
                    spool,
                    self._bucket,
                    key,
                    ExtraArgs={"ContentType": content_type},
                    Config=_TRANSFER,
                )
            except (BotoCoreError, ClientError) as exc:
                raise ProviderUnavailable("s3", str(exc)) from exc
        return written

    async def open(self, ctx: TenantContext, key: str) -> AsyncIterator[bytes]:
        check_blob_key(ctx, key)
        try:
            obj = await asyncio.to_thread(self._s3.get_object, Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise FileNotFoundError(key) from exc
            raise ProviderUnavailable("s3", str(exc)) from exc
        body = obj["Body"]
        try:
            while chunk := await asyncio.to_thread(body.read, _CHUNK):
                yield chunk
        finally:
            body.close()

    async def exists(self, ctx: TenantContext, key: str) -> bool:
        check_blob_key(ctx, key)
        try:
            await asyncio.to_thread(self._s3.head_object, Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise ProviderUnavailable("s3", str(exc)) from exc
        return True

    async def delete(self, ctx: TenantContext, key: str) -> None:
        check_blob_key(ctx, key)
        try:
            await asyncio.to_thread(self._s3.delete_object, Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise ProviderUnavailable("s3", str(exc)) from exc
