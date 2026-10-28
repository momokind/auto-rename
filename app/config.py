from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
REGISTRY_DIR = DATA_DIR / "registry"
INDEX_FILE = DATA_DIR / "index.json"
HOST = "0.0.0.0"
PORT = 8000
CHUNK_SIZE = 1024 * 1024
REPO_NAME = "webfs/vault"
INDEX_TAG = "index"
