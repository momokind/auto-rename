from __future__ import annotations

import mimetypes
import os
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import DATA_DIR, PORT
from .deps import store
from .fsindex import WebFS
from .paths import join_path, normalize
from .registry_api import router as registry_router

DATA_DIR.mkdir(parents=True, exist_ok=True)
fs = WebFS(store)

app = FastAPI(title="Registry WebFS", version="1.0.0")
app.include_router(registry_router)


@app.middleware("http")
async def registry_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/v2"):
        response.headers["Docker-Distribution-API-Version"] = "registry/2.0"
    return response

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def ensure_loaded(request: Request, call_next):
    if not fs._loaded:
        await fs.load()
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_exc(_request: Request, exc: HTTPException):
    detail = exc.detail
    if not isinstance(detail, str):
        detail = str(detail)
    return JSONResponse({"ok": False, "error": detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def valid_exc(_request: Request, exc: RequestValidationError):
    return JSONResponse({"ok": False, "error": "参数无效"}, status_code=422)


@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = os.path.join(static_dir, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "webfs"}


@app.get("/api/stats")
async def stats():
    await fs.load()
    return {"ok": True, **fs.stats()}


@app.get("/api/list")
async def list_dir(path: str = "/"):
    await fs.load()
    path = normalize(path)
    node = fs.get(path)
    if not node:
        raise HTTPException(404, "路径不存在")
    if node["type"] != "dir":
        raise HTTPException(400, "不是目录")
    crumbs = []
    acc = ""
    crumbs.append({"name": "根目录", "path": "/"})
    for seg in [s for s in path.split("/") if s]:
        acc += "/" + seg
        crumbs.append({"name": seg, "path": acc})
    return {"ok": True, "path": path, "crumbs": crumbs, "items": fs.children(path)}


@app.get("/api/tree")
async def tree(path: str = "/"):
    await fs.load()
    try:
        return {"ok": True, "tree": fs.tree(path)}
    except FileNotFoundError:
        raise HTTPException(404, "路径不存在")


@app.get("/api/search")
async def search(q: str = ""):
    await fs.load()
    return {"ok": True, "items": fs.search(q)}


@app.post("/api/mkdir")
async def mkdir(payload: dict):
    await fs.load()
    path = payload.get("path") or ""
    name = (payload.get("name") or "").strip()
    if name:
        path = join_path(path or "/", name)
    try:
        node = await fs.mkdir(path)
    except FileExistsError:
        raise HTTPException(409, "路径已存在且不是目录")
    except NotADirectoryError:
        raise HTTPException(400, "父路径不是目录")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "item": node}


@app.post("/api/upload")
async def upload(
    path: str = Form("/"),
    file: UploadFile = File(...),
):
    await fs.load()
    filename = file.filename or "unnamed"
    dest = join_path(path or "/", filename)
    digest, size = await store.put_blob_stream(file)
    ctype = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        node = await fs.put_file(dest, digest, size, ctype)
    except IsADirectoryError:
        raise HTTPException(400, "目标是目录")
    except NotADirectoryError:
        raise HTTPException(400, "父路径不是目录")
    return {"ok": True, "item": node}


@app.post("/api/rename")
async def rename(payload: dict):
    await fs.load()
    src = payload.get("src") or ""
    dst = payload.get("dst") or ""
    try:
        node = await fs.rename(src, dst)
    except FileNotFoundError:
        raise HTTPException(404, "源路径不存在")
    except FileExistsError:
        raise HTTPException(409, "目标已存在")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except NotADirectoryError:
        raise HTTPException(400, "父路径不是目录")
    return {"ok": True, "item": node}


@app.post("/api/delete")
async def delete(payload: dict):
    await fs.load()
    path = payload.get("path") or ""
    try:
        await fs.delete(path)
    except FileNotFoundError:
        raise HTTPException(404, "路径不存在")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


@app.get("/api/meta")
async def meta(path: str):
    await fs.load()
    node = fs.get(path)
    if not node:
        raise HTTPException(404, "路径不存在")
    return {"ok": True, "item": node}


@app.get("/api/download")
async def download(path: str, request: Request):
    await fs.load()
    node = fs.get(path)
    if not node:
        raise HTTPException(404, "文件不存在")
    if node["type"] != "file":
        raise HTTPException(400, "不是文件")
    digest = node["digest"]
    size = int(node.get("size") or store.blob_size(digest))
    filename = node["name"] or "download"
    ctype = node.get("content_type") or "application/octet-stream"
    encoded = quote(filename)
    headers = {
        "Content-Length": str(size),
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        "Docker-Content-Digest": digest,
        "X-WebFS-Digest": digest,
        "X-WebFS-Path": node["path"],
    }

    async def gen():
        async for chunk in store.iter_blob(digest):
            yield chunk

    return StreamingResponse(gen(), media_type=ctype, headers=headers)


@app.get("/api/preview")
async def preview(path: str):
    await fs.load()
    node = fs.get(path)
    if not node or node["type"] != "file":
        raise HTTPException(404, "文件不存在")
    if int(node.get("size") or 0) > 2 * 1024 * 1024:
        raise HTTPException(413, "文件过大，无法预览")
    data = await store.read_blob(node["digest"])
    ctype = node.get("content_type") or "application/octet-stream"
    if ctype.startswith("text/") or ctype in (
        "application/json",
        "application/javascript",
        "application/xml",
    ):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        return {"ok": True, "kind": "text", "content": text, "item": node}
    if ctype.startswith("image/"):
        import base64

        b64 = base64.b64encode(data).decode("ascii")
        return {"ok": True, "kind": "image", "content": f"data:{ctype};base64,{b64}", "item": node}
    return {"ok": True, "kind": "binary", "item": node}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=False)
