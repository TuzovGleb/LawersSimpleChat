"""S3 / Yandex Object Storage access (async).

Reads uploaded document bytes from object storage so the backend can extract
their text. Mirrors the addressing/endpoint settings of lib/s3-client.ts on the
Next.js side: path-style addressing, ru-central1, storage.yandexcloud.net.
"""
import logging

import aioboto3
from botocore.config import Config

logger = logging.getLogger(__name__)


class S3Client:
    """Thin async wrapper around aioboto3 for object downloads.

    A boto session is cheap and reusable; the actual client is opened per call
    via an async context manager (the aioboto3 idiom), so nothing long-lived is
    held open across requests.
    """

    def __init__(
        self,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint_url: str,
        region: str,
    ):
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._session = aioboto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        # Yandex Object Storage needs path-style addressing (forcePathStyle).
        self._config = Config(signature_version="s3v4", s3={"addressing_style": "path"})

    @property
    def bucket(self) -> str:
        return self._bucket

    async def download(self, object_key: str) -> bytes:
        """Fetch the full object body as bytes."""
        async with self._session.client(
            "s3", endpoint_url=self._endpoint_url, config=self._config
        ) as s3:
            resp = await s3.get_object(Bucket=self._bucket, Key=object_key)
            async with resp["Body"] as stream:
                return await stream.read()
