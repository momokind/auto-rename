from pathlib import PurePosixPath


def normalize(path: str) -> str:
    raw = (path or "/").replace("\\", "/").strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    parts = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        if any(ch in seg for ch in ("\x00",)):
            raise ValueError("illegal path")
        parts.append(seg)
    return "/" + "/".join(parts) if parts else "/"


def parent_of(path: str) -> str:
    path = normalize(path)
    if path == "/":
        return "/"
    return str(PurePosixPath(path).parent)


def name_of(path: str) -> str:
    path = normalize(path)
    if path == "/":
        return ""
    return PurePosixPath(path).name


def join_path(base: str, name: str) -> str:
    base = normalize(base)
    name = name.strip("/").replace("\\", "/")
    if not name:
        return base
    return normalize(base.rstrip("/") + "/" + name)


def is_under(path: str, prefix: str) -> bool:
    path = normalize(path)
    prefix = normalize(prefix)
    if prefix == "/":
        return path != "/"
    return path == prefix or path.startswith(prefix + "/")
