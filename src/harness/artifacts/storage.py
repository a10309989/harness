"""Content-addressed storage backends for immutable artifact blobs."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    @abstractmethod
    async def put(self, content: bytes) -> tuple[str, str]:
        """Store bytes and return (digest, storage_key)."""

    @abstractmethod
    async def get(self, storage_key: str, expected_digest: str) -> bytes:
        """Read bytes and verify their digest."""


class LocalCAS(StorageBackend):
    """SHA-256 content-addressed local storage with atomic writes."""

    def __init__(self, root: str | Path = "data/artifacts") -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for_digest(self, digest: str) -> Path:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Invalid SHA-256 digest")
        return self.root / "sha256" / digest[:2] / digest[2:4] / digest

    async def put(self, content: bytes) -> tuple[str, str]:
        return await asyncio.to_thread(self._put_sync, content)

    def _put_sync(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        target = self.path_for_digest(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fd, temp_name = tempfile.mkstemp(prefix=f".{digest}.", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temp_name, target)
                except FileExistsError:
                    pass
                except OSError:
                    if not target.exists():
                        os.replace(temp_name, target)
            finally:
                Path(temp_name).unlink(missing_ok=True)
        stored = target.read_bytes()
        if hashlib.sha256(stored).hexdigest() != digest:
            raise IOError(f"CAS integrity check failed for {digest}")
        return digest, target.relative_to(self.root).as_posix()

    async def get(self, storage_key: str, expected_digest: str) -> bytes:
        return await asyncio.to_thread(self._get_sync, storage_key, expected_digest)

    def _get_sync(self, storage_key: str, expected_digest: str) -> bytes:
        path = (self.root / storage_key).resolve()
        if self.root not in path.parents:
            raise ValueError("Storage key escapes CAS root")
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_digest:
            raise IOError(
                f"Artifact integrity check failed: expected {expected_digest}, got {actual}"
            )
        return content


class MinIOStorage(StorageBackend):
    """SHA-256 content-addressed storage backed by a private MinIO bucket."""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        *,
        secure: bool = False,
    ) -> None:
        try:
            from minio import Minio
        except ImportError as exc:
            raise RuntimeError("Install the 'minio' dependency to use MinIOStorage") from exc
        self.bucket = bucket
        self._client = Minio(
            endpoint.removeprefix("http://").removeprefix("https://"),
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket_ready = False
        self._bucket_lock = asyncio.Lock()

    @staticmethod
    def key_for_digest(digest: str) -> str:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid SHA-256 digest")
        return f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"

    async def put(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        key = self.key_for_digest(digest)
        await self._ensure_bucket()
        await asyncio.to_thread(self._put_if_absent, key, content)
        return digest, key

    async def get(self, storage_key: str, expected_digest: str) -> bytes:
        self.key_for_digest(expected_digest)
        if storage_key != self.key_for_digest(expected_digest):
            raise ValueError("Storage key does not match expected digest")
        await self._ensure_bucket()
        content = await asyncio.to_thread(self._get_sync, storage_key)
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_digest:
            raise IOError(f"Artifact integrity check failed: expected {expected_digest}, got {actual}")
        return content

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        async with self._bucket_lock:
            if not self._bucket_ready:
                await asyncio.to_thread(self._ensure_bucket_sync)
                self._bucket_ready = True

    def _ensure_bucket_sync(self) -> None:
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)

    def _put_if_absent(self, key: str, content: bytes) -> None:
        from io import BytesIO

        try:
            self._client.stat_object(self.bucket, key)
            return
        except Exception:
            self._client.put_object(self.bucket, key, BytesIO(content), len(content))

    def _get_sync(self, key: str) -> bytes:
        response = self._client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()


class DualReadStorage(StorageBackend):
    """Read from primary storage and fall back to legacy storage during migration."""

    def __init__(self, primary: StorageBackend, fallback: StorageBackend) -> None:
        self.primary = primary
        self.fallback = fallback

    async def put(self, content: bytes) -> tuple[str, str]:
        return await self.primary.put(content)

    async def get(self, storage_key: str, expected_digest: str) -> bytes:
        try:
            return await self.primary.get(storage_key, expected_digest)
        except Exception:
            return await self.fallback.get(storage_key, expected_digest)
