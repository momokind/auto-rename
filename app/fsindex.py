from __future__ import annotations

import json
import time
from typing import Any

from .config import INDEX_TAG, REPO_NAME
from .paths import is_under, join_path, name_of, normalize, parent_of
from .store import RegistryStore

OCI_CONFIG_MEDIA = "application/vnd.oci.image.config.v1+json"
OCI_LAYER_MEDIA = "application/vnd.oci.image.layer.v1.tar"
OCI_MANIFEST_MEDIA = "application/vnd.oci.image.manifest.v1+json"
WEBFS_INDEX_MEDIA = "application/vnd.webfs.index.v1+json"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WebFS:
    def __init__(self, store: RegistryStore):
        self.store = store
        self._index: dict[str, Any] = {"version": 1, "nodes": {}}
        self._loaded = False

    def _empty_index(self) -> dict[str, Any]:
        ts = now_iso()
        return {
            "version": 1,
            "nodes": {
                "/": {
                    "type": "dir",
                    "name": "",
                    "path": "/",
                    "created_at": ts,
                    "updated_at": ts,
                }
            },
        }

    async def load(self) -> None:
        if self._loaded:
            return
        try:
            body, _, _ = self.store.get_manifest(REPO_NAME, INDEX_TAG)
            manifest = json.loads(body.decode("utf-8"))
            layers = manifest.get("layers") or []
            if not layers:
                self._index = self._empty_index()
            else:
                digest = layers[0]["digest"]
                raw = await self.store.read_blob(digest)
                self._index = json.loads(raw.decode("utf-8"))
                if "nodes" not in self._index:
                    self._index = self._empty_index()
        except FileNotFoundError:
            self._index = self._empty_index()
            await self.save()
        if "/" not in self._index["nodes"]:
            self._index["nodes"]["/"] = {
                "type": "dir",
                "name": "",
                "path": "/",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        self._loaded = True

    async def save(self) -> None:
        payload = json.dumps(self._index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest, size = await self.store.put_blob_bytes(payload)
        config = json.dumps(
            {
                "architecture": "amd64",
                "os": "linux",
                "config": {"Labels": {"webfs": "index", "webfs.version": "1"}},
                "rootfs": {"type": "layers", "diff_ids": [digest]},
                "history": [{"created": now_iso(), "created_by": "webfs index"}],
            }
        ).encode("utf-8")
        cfg_digest, cfg_size = await self.store.put_blob_bytes(config)
        manifest = {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST_MEDIA,
            "config": {
                "mediaType": OCI_CONFIG_MEDIA,
                "digest": cfg_digest,
                "size": cfg_size,
            },
            "layers": [
                {
                    "mediaType": WEBFS_INDEX_MEDIA,
                    "digest": digest,
                    "size": size,
                }
            ],
        }
        self.store.put_manifest(
            REPO_NAME,
            INDEX_TAG,
            json.dumps(manifest, separators=(",", ":")).encode("utf-8"),
            OCI_MANIFEST_MEDIA,
        )

    def get(self, path: str) -> dict[str, Any] | None:
        path = normalize(path)
        node = self._index["nodes"].get(path)
        return dict(node) if node else None

    def exists(self, path: str) -> bool:
        return normalize(path) in self._index["nodes"]

    def children(self, path: str) -> list[dict[str, Any]]:
        path = normalize(path)
        node = self._index["nodes"].get(path)
        if not node or node["type"] != "dir":
            return []
        items = []
        for p, n in self._index["nodes"].items():
            if p == path:
                continue
            if parent_of(p) == path:
                items.append(dict(n))
        items.sort(key=lambda x: (0 if x["type"] == "dir" else 1, x["name"].lower()))
        return items

    def tree(self, path: str = "/") -> dict[str, Any]:
        path = normalize(path)
        node = self._index["nodes"].get(path)
        if not node:
            raise FileNotFoundError(path)
        item = dict(node)
        if node["type"] == "dir":
            item["children"] = [self.tree(c["path"]) for c in self.children(path)]
        return item

    async def mkdir(self, path: str) -> dict[str, Any]:
        path = normalize(path)
        if path == "/":
            return self.get("/")
        if self.exists(path):
            node = self.get(path)
            if node["type"] != "dir":
                raise FileExistsError(path)
            return node
        parent = parent_of(path)
        if not self.exists(parent):
            await self.mkdir(parent)
        parent_node = self.get(parent)
        if parent_node["type"] != "dir":
            raise NotADirectoryError(parent)
        ts = now_iso()
        node = {
            "type": "dir",
            "name": name_of(path),
            "path": path,
            "created_at": ts,
            "updated_at": ts,
        }
        self._index["nodes"][path] = node
        self._index["nodes"][parent]["updated_at"] = ts
        await self.save()
        return dict(node)

    async def put_file(
        self,
        path: str,
        digest: str,
        size: int,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        path = normalize(path)
        if path == "/":
            raise IsADirectoryError("/")
        parent = parent_of(path)
        if not self.exists(parent):
            await self.mkdir(parent)
        parent_node = self.get(parent)
        if parent_node["type"] != "dir":
            raise NotADirectoryError(parent)
        existing = self._index["nodes"].get(path)
        if existing and existing["type"] == "dir":
            raise IsADirectoryError(path)
        ts = now_iso()
        node = {
            "type": "file",
            "name": name_of(path),
            "path": path,
            "digest": digest,
            "size": size,
            "content_type": content_type or "application/octet-stream",
            "created_at": existing["created_at"] if existing else ts,
            "updated_at": ts,
        }
        self._index["nodes"][path] = node
        self._index["nodes"][parent]["updated_at"] = ts
        await self._put_file_manifest(path, digest, size, node["content_type"])
        await self.save()
        return dict(node)

    async def _put_file_manifest(self, path: str, digest: str, size: int, content_type: str) -> None:
        config = json.dumps(
            {
                "architecture": "amd64",
                "os": "linux",
                "config": {
                    "Labels": {
                        "webfs": "file",
                        "webfs.path": path,
                        "webfs.content_type": content_type,
                    }
                },
                "rootfs": {"type": "layers", "diff_ids": [digest]},
            }
        ).encode("utf-8")
        cfg_digest, cfg_size = await self.store.put_blob_bytes(config)
        manifest = {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST_MEDIA,
            "config": {
                "mediaType": OCI_CONFIG_MEDIA,
                "digest": cfg_digest,
                "size": cfg_size,
            },
            "layers": [
                {
                    "mediaType": content_type or OCI_LAYER_MEDIA,
                    "digest": digest,
                    "size": size,
                    "annotations": {"webfs.path": path},
                }
            ],
        }
        tag = self._path_tag(path)
        self.store.put_manifest(
            REPO_NAME,
            tag,
            json.dumps(manifest, separators=(",", ":")).encode("utf-8"),
            OCI_MANIFEST_MEDIA,
        )

    def _path_tag(self, path: str) -> str:
        import hashlib

        path = normalize(path)
        h = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
        return "file-" + h

    async def delete(self, path: str) -> None:
        path = normalize(path)
        if path == "/":
            raise PermissionError("cannot delete root")
        node = self._index["nodes"].get(path)
        if not node:
            raise FileNotFoundError(path)
        to_remove = []
        if node["type"] == "dir":
            for p in self._index["nodes"]:
                if p == path or is_under(p, path):
                    to_remove.append(p)
        else:
            to_remove.append(path)
        for p in to_remove:
            self._index["nodes"].pop(p, None)
        parent = parent_of(path)
        if parent in self._index["nodes"]:
            self._index["nodes"][parent]["updated_at"] = now_iso()
        await self.save()

    async def rename(self, src: str, dst: str) -> dict[str, Any]:
        src = normalize(src)
        dst = normalize(dst)
        if src == "/":
            raise PermissionError("cannot rename root")
        node = self._index["nodes"].get(src)
        if not node:
            raise FileNotFoundError(src)
        if self.exists(dst):
            raise FileExistsError(dst)
        parent = parent_of(dst)
        if not self.exists(parent):
            await self.mkdir(parent)
        if self.get(parent)["type"] != "dir":
            raise NotADirectoryError(parent)
        mapping = []
        for p, n in list(self._index["nodes"].items()):
            if p == src or is_under(p, src):
                suffix = p[len(src) :] if p != src else ""
                new_path = dst + suffix if suffix else dst
                mapping.append((p, new_path, n))
        for old, new, n in mapping:
            cloned = dict(n)
            cloned["path"] = new
            cloned["name"] = name_of(new)
            cloned["updated_at"] = now_iso()
            self._index["nodes"][new] = cloned
            self._index["nodes"].pop(old, None)
        self._index["nodes"][parent]["updated_at"] = now_iso()
        await self.save()
        return self.get(dst)

    def stats(self) -> dict[str, Any]:
        files = 0
        dirs = 0
        bytes_total = 0
        for n in self._index["nodes"].values():
            if n["type"] == "dir":
                dirs += 1
            else:
                files += 1
                bytes_total += int(n.get("size") or 0)
        blobs = self.store.list_blobs()
        blob_bytes = sum(s for _, s in blobs)
        return {
            "files": files,
            "dirs": dirs,
            "logical_bytes": bytes_total,
            "blob_count": len(blobs),
            "blob_bytes": blob_bytes,
            "repo": REPO_NAME,
        }

    def search(self, query: str, limit: int = 80) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        hits = []
        for n in self._index["nodes"].values():
            if n["path"] == "/":
                continue
            hay = (n.get("name", "") + " " + n.get("path", "")).lower()
            if q in hay:
                hits.append(dict(n))
            if len(hits) >= limit:
                break
        return hits
