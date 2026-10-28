from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .deps import store

router = APIRouter()

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"


def _name(name: str) -> str:
    return name.strip("/")


@router.get("/v2/")
async def ping():
    return {}


@router.head("/v2/")
async def ping_head():
    return Response(status_code=200)


@router.get("/v2/_catalog")
async def catalog():
    repos = []
    if store.repos_dir.is_dir():
        for p in store.repos_dir.rglob("tags"):
            if p.is_dir():
                rel = p.parent.relative_to(store.repos_dir).as_posix()
                repos.append(rel)
    return {"repositories": sorted(set(repos))}


@router.get("/v2/{name:path}/tags/list")
async def tags_list(name: str):
    name = _name(name)
    return {"name": name, "tags": store.list_tags(name)}


@router.post("/v2/{name:path}/blobs/uploads/")
async def start_upload(name: str, request: Request):
    name = _name(name)
    uid = store.start_upload(name)
    loc = f"/v2/{name}/blobs/uploads/{uid}"
    return Response(status_code=202, headers={"Location": loc, "Docker-Upload-UUID": uid, "Range": "0-0"})


@router.patch("/v2/{name:path}/blobs/uploads/{uid}")
async def patch_upload(name: str, uid: str, request: Request):
    data = await request.body()
    try:
        size = store.append_upload(uid, data)
    except FileNotFoundError:
        raise HTTPException(404, "upload unknown")
    loc = f"/v2/{_name(name)}/blobs/uploads/{uid}"
    end = max(size - 1, 0)
    return Response(
        status_code=202,
        headers={"Location": loc, "Docker-Upload-UUID": uid, "Range": f"0-{end}"},
    )


@router.put("/v2/{name:path}/blobs/uploads/{uid}")
async def put_upload(name: str, uid: str, request: Request, digest: str = ""):
    extra = await request.body()
    if extra:
        try:
            store.append_upload(uid, extra)
        except FileNotFoundError:
            raise HTTPException(404, "upload unknown")
    if not digest:
        raise HTTPException(400, "digest required")
    try:
        store.finish_upload(uid, digest)
    except FileNotFoundError:
        raise HTTPException(404, "upload unknown")
    except ValueError as e:
        raise HTTPException(400, str(e))
    loc = f"/v2/{_name(name)}/blobs/{digest}"
    return Response(status_code=201, headers={"Location": loc, "Docker-Content-Digest": digest})


@router.head("/v2/{name:path}/blobs/{digest}")
async def head_blob(name: str, digest: str):
    if not store.blob_exists(digest):
        raise HTTPException(404, "blob unknown")
    size = store.blob_size(digest)
    return Response(
        status_code=200,
        headers={
            "Docker-Content-Digest": digest,
            "Content-Length": str(size),
            "Content-Type": "application/octet-stream",
        },
    )


@router.get("/v2/{name:path}/blobs/{digest}")
async def get_blob(name: str, digest: str):
    if not store.blob_exists(digest):
        raise HTTPException(404, "blob unknown")
    size = store.blob_size(digest)

    async def gen():
        async for chunk in store.iter_blob(digest):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers={"Docker-Content-Digest": digest, "Content-Length": str(size)},
    )


@router.put("/v2/{name:path}/manifests/{reference}")
async def put_manifest(name: str, reference: str, request: Request, content_type: str | None = Header(default=None)):
    body = await request.body()
    mt = content_type or DOCKER_MANIFEST
    digest = store.put_manifest(_name(name), reference, body, mt)
    return Response(
        status_code=201,
        headers={
            "Location": f"/v2/{_name(name)}/manifests/{reference}",
            "Docker-Content-Digest": digest,
        },
    )


@router.get("/v2/{name:path}/manifests/{reference}")
async def get_manifest(name: str, reference: str):
    try:
        body, digest, mt = store.get_manifest(_name(name), reference)
    except FileNotFoundError:
        raise HTTPException(404, "manifest unknown")
    return Response(
        content=body,
        media_type=mt,
        headers={"Docker-Content-Digest": digest, "Content-Length": str(len(body))},
    )


@router.head("/v2/{name:path}/manifests/{reference}")
async def head_manifest(name: str, reference: str):
    try:
        body, digest, mt = store.get_manifest(_name(name), reference)
    except FileNotFoundError:
        raise HTTPException(404, "manifest unknown")
    return Response(
        status_code=200,
        headers={
            "Docker-Content-Digest": digest,
            "Content-Length": str(len(body)),
            "Content-Type": mt,
        },
    )
