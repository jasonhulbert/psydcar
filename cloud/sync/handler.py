"""HTTP API for device pairing, manifests, and scoped S3 transfers."""

from __future__ import annotations

import json
import logging
import os
import secrets
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from psydcar.sync_protocol import MAX_MANIFEST, checksum, encode

from .service import RequestError, SyncService


class S3:
    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket = bucket

    def get(self, key: str) -> tuple[dict, str]:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            # The execution role intentionally has no ListBucket permission.
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] in (403, 404):
                raise RequestError(404, "Sync record unavailable.") from exc
            raise
        with response["Body"] as stream:
            data = stream.read(MAX_MANIFEST + 4096)
        return json.loads(data), response["ETag"]

    def put(self, key: str, value: dict, etag: str | None = None) -> str:
        precondition = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        try:
            return self.client.put_object(Bucket=self.bucket, Key=key, Body=encode({**value, "revision": secrets.token_hex(16)}),
                                          ContentType="application/json", **precondition)["ETag"]
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] in (409, 412):
                raise RequestError(409, "The cloud version changed. Sync again.") from exc
            raise

    def url(self, method: str, key: str, **params) -> str:
        return self.client.generate_presigned_url(
            method, Params={"Bucket": self.bucket, "Key": key, **params}, ExpiresIn=120,
        )

    def verify(self, key: str, size: int) -> None:
        try:
            info = self.client.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] in (403, 404):
                raise RequestError(409, "A file upload is incomplete. Sync again.") from exc
            raise
        if info["ContentLength"] != size or info.get("ChecksumSHA256") != checksum(key.rsplit("/", 1)[-1]):
            raise RequestError(409, "The uploaded file could not be verified.")


@lru_cache(maxsize=1)
def service() -> SyncService:
    client = boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual", "us_east_1_regional_endpoint": "regional"},
                                              connect_timeout=3, read_timeout=5,
                                              retries={"mode": "standard", "total_max_attempts": 2}))
    return SyncService(S3(client, os.environ["PSYDCAR_SYNC_BUCKET"]), os.getenv("PSYDCAR_SYNC_ENROLLMENT_DIGEST", ""))


def handler(event: dict, _context) -> dict:
    status = 200
    try:
        body = event.get("body")
        if not isinstance(body, str) or len(body.encode()) > MAX_MANIFEST + 4096 or event.get("isBase64Encoded"):
            raise RequestError(413, "Sync request is too large or unreadable.")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Send a sync request object.")
        result = service().dispatch((event.get("headers") or {}).get("authorization", ""), data)
    except RequestError as exc:
        status, result = exc.status, {"detail": str(exc)}
    except (ValueError, UnicodeError) as exc:
        status, result = 400, {"detail": str(exc)}
    except (ClientError, BotoCoreError):
        logging.exception("Sync storage request failed")
        status, result = 503, {"detail": "Cloud storage is temporarily unavailable."}
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": encode(result).decode()}
