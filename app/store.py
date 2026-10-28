from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import AsyncIterator

import aiofiles
import aiofiles.os

from .config import CHUNK_SIZE, REGISTRY_DIR


class RegistryStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or REGISTRY_DIR)
        self.blobs_dir = self.root / "blobs" / "sha256"
        self.repos_dir = self.root / "repositories"
        self.uploads_dir = self.root / "uploads"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def _blob_file(self, digest: str) -> Path:
        hexdigest = digest.split(":")[-1].lower()
        return self.blobs_dir / hexdigest[:2] / hexdigest

    def blob_exists(self, digest: str) -> bool:
        return self._blob_file(digest).is_file()

    def blob_size(self, digest: str) -> int:
        path = self._blob_file(digest)
        if not path.is_file():
            raise FileNotFoundError(digest)
        return path.stat().st_size

    def blob_path(self, digest: str) -> Path:
        path = self._blob_file(digest)
        if not path.is_file():
            raise FileNotFoundError(digest)
        return path

    async def put_blob_bytes(self, data: bytes, digest: str | None = None) -> tuple[str, int]:
        if digest is None:
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
        dest = self._blob_file(digest)
        if not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp-" + uuid.uuid4().hex[:8])
            async with aiofiles.open(tmp, "wb") as f:
                await f.write(data)
            os.replace(tmp, dest)
        return digest, dest.stat().st_size

    async def put_blob_stream(self, stream) -> tuple[str, int]:
        hasher = hashlib.sha256()
        size = 0
        tmp = self.uploads_dir / ("tmp-" + uuid.uuid4().hex)
        async with aiofiles.open(tmp, "wb") as f:
            while True:
                chunk = await stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
                await f.write(chunk)
        digest = "sha256:" + hasher.hexdigest()
        dest = self._blob_file(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            await aiofiles.os.remove(tmp)
        else:
            os.replace(tmp, dest)
        return digest, size

    async def put_blob_fileobj(self, fileobj) -> tuple[str, int]:
        hasher = hashlib.sha256()
        size = 0
        tmp = self.uploads_dir / ("tmp-" + uuid.uuid4().hex)
        async with aiofiles.open(tmp, "wb") as f:
            while True:
                chunk = fileobj.read(CHUNK_SIZE)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                hasher.update(chunk)
                size += len(chunk)
                await f.write(chunk)
        digest = "sha256:" + hasher.hexdigest()
        dest = self._blob_file(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            await aiofiles.os.remove(tmp)
        else:
            os.replace(tmp, dest)
        return digest, size

    async def iter_blob(self, digest: str) -> AsyncIterator[bytes]:
        path = self.blob_path(digest)
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    async def read_blob(self, digest: str) -> bytes:
        path = self.blob_path(digest)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    def start_upload(self, name: str) -> str:
        uid = uuid.uuid4().hex
        path = self.uploads_dir / uid
        path.write_bytes(b"")
        session = {"uuid": uid, "name": name, "size": 0}
        (self.uploads_dir / f"{uid}.json").write_text(json.dumps(session), encoding="utf-8")
        return uid

    def upload_file(self, uid: str) -> Path:
        path = self.uploads_dir / uid
        if not path.exists():
            raise FileNotFoundError(uid)
        return path

    def append_upload(self, uid: str, data: bytes) -> int:
        path = self.upload_file(uid)
        with path.open("ab") as f:
            f.write(data)
        size = path.stat().st_size
        meta_path = self.uploads_dir / f"{uid}.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["size"] = size
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return size

    def finish_upload(self, uid: str, digest: str) -> int:
        src = self.upload_file(uid)
        hasher = hashlib.sha256()
        with src.open("rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
        actual = "sha256:" + hasher.hexdigest()
        if actual != digest.lower() and actual != digest:
            expected = digest.split(":")[-1].lower()
            if hasher.hexdigest() != expected:
                raise ValueError(f"digest mismatch: expected {digest}, got {actual}")
        dest = self._blob_file(actual)
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = src.stat().st_size
        if dest.is_file():
            src.unlink(missing_ok=True)
        else:
            os.replace(src, dest)
        (self.uploads_dir / f"{uid}.json").unlink(missing_ok=True)
        return size

    def _repo_dir(self, name: str) -> Path:
        safe = name.strip("/")
        d = self.repos_dir / safe
        d.mkdir(parents=True, exist_ok=True)
        (d / "tags").mkdir(exist_ok=True)
        (d / "revisions").mkdir(exist_ok=True)
        return d

    def put_manifest(self, name: str, reference: str, body: bytes, media_type: str) -> str:
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        repo = self._repo_dir(name)
        rev = repo / "revisions" / digest.replace(":", "_")
        rev.write_bytes(body)
        (repo / "revisions" / f"{digest.replace(':', '_')}.mt").write_text(media_type, encoding="utf-8")
        if not reference.startswith("sha256:"):
            tag_file = repo / "tags" / reference.replace("/", "_")
            tag_file.write_text(digest, encoding="utf-8")
        return digest

    def get_manifest(self, name: str, reference: str) -> tuple[bytes, str, str]:
        repo = self._repo_dir(name)
        if reference.startswith("sha256:"):
            digest = reference
        else:
            tag_file = repo / "tags" / reference.replace("/", "_")
            if not tag_file.is_file():
                raise FileNotFoundError(reference)
            digest = tag_file.read_text(encoding="utf-8").strip()
        rev = repo / "revisions" / digest.replace(":", "_")
        if not rev.is_file():
            raise FileNotFoundError(digest)
        media_path = repo / "revisions" / f"{digest.replace(':', '_')}.mt"
        media_type = "application/vnd.docker.distribution.manifest.v2+json"
        if media_path.is_file():
            media_type = media_path.read_text(encoding="utf-8").strip()
        return rev.read_bytes(), digest, media_type

    def list_tags(self, name: str) -> list[str]:
        tags_dir = self._repo_dir(name) / "tags"
        if not tags_dir.is_dir():
            return []
        return sorted(p.name for p in tags_dir.iterdir() if p.is_file())

    def delete_blob(self, digest: str) -> None:
        path = self._blob_file(digest)
        path.unlink(missing_ok=True)

    def list_blobs(self) -> list[tuple[str, int]]:
        result = []
        if not self.blobs_dir.is_dir():
            return result
        for sub in self.blobs_dir.iterdir():
            if not sub.is_dir():
                continue
            for f in sub.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    result.append(("sha256:" + f.name, f.stat().st_size))
        return result
