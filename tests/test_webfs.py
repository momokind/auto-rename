import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import config

config.DATA_DIR = Path("/tmp/webfs-test-data")
config.REGISTRY_DIR = config.DATA_DIR / "registry"
config.DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("WEBFS_TEST", "1")

from app.main import app, fs, store  # noqa: E402


def setup_module():
    store.root = config.REGISTRY_DIR
    store.blobs_dir = store.root / "blobs" / "sha256"
    store.repos_dir = store.root / "repositories"
    store.uploads_dir = store.root / "uploads"
    store.blobs_dir.mkdir(parents=True, exist_ok=True)
    store.repos_dir.mkdir(parents=True, exist_ok=True)
    store.uploads_dir.mkdir(parents=True, exist_ok=True)


client = TestClient(app)


def test_health_and_v2():
    r = client.get("/api/health")
    assert r.status_code == 200
    r = client.get("/v2/")
    assert r.status_code == 200
    assert r.headers.get("Docker-Distribution-API-Version") == "registry/2.0"


def test_mkdir_visible_in_list():
    r = client.post("/api/mkdir", json={"path": "/", "name": "visible-dir"})
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["type"] == "dir"
    assert item["path"] == "/visible-dir"
    r = client.get("/api/list", params={"path": "/"})
    names = [i["name"] for i in r.json()["items"]]
    assert "visible-dir" in names
    types = {i["name"]: i["type"] for i in r.json()["items"]}
    assert types["visible-dir"] == "dir"


def test_mkdir_upload_download():
    r = client.post("/api/mkdir", json={"path": "/", "name": "docs"})
    assert r.status_code == 200
    content = b"hello registry blob"
    r = client.post(
        "/api/upload",
        data={"path": "/docs"},
        files={"file": ("note.txt", io.BytesIO(content), "text/plain")},
    )
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["digest"].startswith("sha256:")
    assert item["size"] == len(content)
    r = client.get("/api/list", params={"path": "/docs"})
    names = [i["name"] for i in r.json()["items"]]
    assert "note.txt" in names
    r = client.get("/api/download", params={"path": "/docs/note.txt"})
    assert r.status_code == 200
    assert r.content == content
    r = client.get("/v2/_catalog")
    assert "webfs/vault" in r.json()["repositories"]
    r = client.get("/v2/webfs/vault/tags/list")
    assert "index" in r.json()["tags"]
