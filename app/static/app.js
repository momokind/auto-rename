const $ = (sel) => document.querySelector(sel);
const listEl = $("#list");
const crumbsEl = $("#crumbs");
const searchEl = $("#search");
const dropzone = $("#dropzone");
const fileInput = $("#file-input");
const dlg = $("#dlg");
const dlgTitle = $("#dlg-title");
const dlgInput = $("#dlg-input");
const preview = $("#preview");
const pvName = $("#pv-name");
const pvBody = $("#pv-body");

let cwd = "/";
let view = "files";
let dlgMode = null;
let dlgTarget = null;

function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 2800);
}

function fmtSize(n) {
  n = Number(n || 0);
  if (n < 1024) return n + " B";
  const u = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do {
    n /= 1024;
    i++;
  } while (n >= 1024 && i < u.length - 1);
  return n.toFixed(n >= 10 ? 1 : 2) + " " + u[i];
}

function shortDigest(d) {
  if (!d) return "-";
  const hex = d.replace(/^sha256:/, "");
  return "sha256:" + hex.slice(0, 8) + "…" + hex.slice(-6);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function openDlg(title, value, mode, target) {
  dlgMode = mode;
  dlgTarget = target || null;
  dlgTitle.textContent = title;
  dlgInput.value = value || "";
  dlgInput.placeholder = mode === "mkdir" ? "目录名，如 docs" : "新名称";
  dlg.showModal();
  setTimeout(() => dlgInput.focus(), 30);
}

async function api(url, opts) {
  const res = await fetch(url, opts);
  let data = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) data = await res.json();
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

async function refreshStats() {
  try {
    const s = await api("/api/stats");
    $("#st-files").textContent = s.files;
    $("#st-dirs").textContent = s.dirs;
    $("#st-logical").textContent = fmtSize(s.logical_bytes);
    $("#st-blobs").textContent = s.blob_count;
    $("#st-phys").textContent = fmtSize(s.blob_bytes);
  } catch (e) {
    toast(e.message, true);
  }
}

function renderCrumbs(crumbs) {
  crumbsEl.innerHTML = "";
  crumbs.forEach((c, i) => {
    if (i) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "/";
      crumbsEl.appendChild(sep);
    }
    const b = document.createElement("button");
    b.className = "crumb" + (i === crumbs.length - 1 ? " now" : "");
    b.textContent = c.name;
    b.onclick = () => openDir(c.path);
    crumbsEl.appendChild(b);
  });
}

function renderList(items) {
  listEl.innerHTML = "";
  if (!items.length) {
    listEl.innerHTML = '<div class="empty">此目录为空。上传文件或以 Blob 写入 Registry。</div>';
    return;
  }
  for (const it of items) {
    const row = document.createElement("div");
    row.className = "row" + (it.type === "dir" ? " dir-row" : "");
    const name = document.createElement("button");
    name.className = "name-btn" + (it.type === "dir" ? " dir" : "");
    name.innerHTML = (it.type === "dir" ? '<span class="folder-mark">[目录]</span>' : "") + escapeHtml(it.name || "/");
    name.onclick = () => {
      if (it.type === "dir") openDir(it.path);
      else openPreview(it.path);
    };
    const type = document.createElement("span");
    type.className = "type";
    type.textContent = it.type === "dir" ? "目录" : "文件";
    const size = document.createElement("span");
    size.textContent = it.type === "file" ? fmtSize(it.size) : "—";
    const digest = document.createElement("span");
    digest.className = "digest";
    digest.title = it.digest || "";
    digest.textContent = it.type === "file" ? shortDigest(it.digest) : "manifest/dir";
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = (it.updated_at || "").replace("T", " ").replace("Z", "");
    const acts = document.createElement("div");
    acts.className = "acts";
    if (it.type === "file") {
      const dl = document.createElement("button");
      dl.textContent = "下载";
      dl.onclick = () => {
        location.href = "/api/download?path=" + encodeURIComponent(it.path);
      };
      acts.appendChild(dl);
    }
    const rn = document.createElement("button");
    rn.textContent = "重命名";
    rn.onclick = () => renameItem(it);
    const del = document.createElement("button");
    del.textContent = "删除";
    del.onclick = () => removeItem(it);
    acts.appendChild(rn);
    acts.appendChild(del);
    row.append(name, type, size, digest, time, acts);
    listEl.appendChild(row);
  }
}

function showFilesView() {
  view = "files";
  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === "files");
  });
  $("#panel-files").classList.remove("hidden");
  $("#panel-blobs").classList.add("hidden");
  $("#panel-registry").classList.add("hidden");
}

async function openDir(path) {
  cwd = path || "/";
  searchEl.value = "";
  showFilesView();
  try {
    const data = await api("/api/list?path=" + encodeURIComponent(cwd));
    renderCrumbs(data.crumbs);
    renderList(data.items);
  } catch (e) {
    toast(e.message, true);
  }
}

async function openPreview(path) {
  try {
    const data = await api("/api/preview?path=" + encodeURIComponent(path));
    pvName.textContent = data.item.path;
    pvBody.innerHTML = "";
    if (data.kind === "text") {
      const pre = document.createElement("pre");
      pre.textContent = data.content;
      pvBody.appendChild(pre);
    } else if (data.kind === "image") {
      const img = document.createElement("img");
      img.src = data.content;
      pvBody.appendChild(img);
    } else {
      pvBody.textContent = "二进制 blob，请下载查看。Digest " + data.item.digest;
    }
    preview.showModal();
  } catch (e) {
    toast(e.message, true);
  }
}

async function uploadFiles(files) {
  for (const file of files) {
    const fd = new FormData();
    fd.append("path", cwd);
    fd.append("file", file);
    try {
      await api("/api/upload", { method: "POST", body: fd });
      toast("已写入 blob：" + file.name);
    } catch (e) {
      toast(file.name + " 失败：" + e.message, true);
    }
  }
  await openDir(cwd);
  await refreshStats();
}

function mkdirPrompt() {
  openDlg("新建目录", "", "mkdir");
}

function renameItem(it) {
  openDlg("重命名", it.name, "rename", it);
}

async function submitDlg(e) {
  e.preventDefault();
  const name = dlgInput.value.trim();
  if (!name) {
    toast("名称不能为空", true);
    return;
  }
  if (/[\\/]/.test(name) || name === "." || name === "..") {
    toast("名称不能包含路径分隔符", true);
    return;
  }
  const mode = dlgMode;
  const target = dlgTarget;
  dlg.close();
  try {
    if (mode === "mkdir") {
      const data = await api("/api/mkdir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: cwd, name }),
      });
      toast("目录已创建：" + data.item.path);
      await openDir(cwd);
      await refreshStats();
      return;
    }
    if (mode === "rename" && target) {
      if (name === target.name) return;
      const parent = target.path === "/" ? "/" : target.path.split("/").slice(0, -1).join("/") || "/";
      const dst = (parent === "/" ? "" : parent) + "/" + name;
      await api("/api/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src: target.path, dst }),
      });
      toast("已重命名");
      await openDir(cwd);
    }
  } catch (err) {
    toast(err.message, true);
  }
}

async function removeItem(it) {
  if (!confirm("删除 " + it.path + " ？目录将递归删除索引项。")) return;
  try {
    await api("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: it.path }),
    });
    toast("已删除");
    await openDir(cwd);
    await refreshStats();
  } catch (e) {
    toast(e.message, true);
  }
}

async function loadBlobs() {
  try {
    const catalog = await api("/v2/_catalog");
    const tags = await api("/v2/webfs/vault/tags/list");
    $("#blob-dump").textContent = JSON.stringify({ catalog, tags, hint: "文件内容按 sha256 blob 存放于 data/registry/blobs" }, null, 2);
  } catch (e) {
    $("#blob-dump").textContent = e.message;
  }
}

async function loadRegistry() {
  try {
    const ping = await fetch("/v2/");
    const catalog = await api("/v2/_catalog");
    $("#catalog-dump").textContent = JSON.stringify(
      { status: ping.status, catalog, example: "curl -I http://host/v2/" },
      null,
      2
    );
  } catch (e) {
    $("#catalog-dump").textContent = e.message;
  }
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.onclick = async () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    view = btn.dataset.view;
    $("#panel-files").classList.toggle("hidden", view !== "files");
    $("#panel-blobs").classList.toggle("hidden", view !== "blobs");
    $("#panel-registry").classList.toggle("hidden", view !== "registry");
    if (view === "blobs") await loadBlobs();
    if (view === "registry") await loadRegistry();
  };
});

$("#btn-mkdir").onclick = mkdirPrompt;
fileInput.onchange = () => {
  if (fileInput.files.length) uploadFiles(fileInput.files);
  fileInput.value = "";
};

["dragenter", "dragover"].forEach((ev) => {
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("active");
  });
});
["dragleave", "drop"].forEach((ev) => {
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("active");
  });
});
dropzone.addEventListener("drop", (e) => {
  const files = e.dataTransfer.files;
  if (files && files.length) uploadFiles(files);
});

let t = null;
searchEl.addEventListener("input", () => {
  clearTimeout(t);
  t = setTimeout(async () => {
    const q = searchEl.value.trim();
    if (!q) return openDir(cwd);
    try {
      const data = await api("/api/search?q=" + encodeURIComponent(q));
      renderCrumbs([{ name: "检索", path: cwd }]);
      renderList(data.items);
    } catch (e) {
      toast(e.message, true);
    }
  }, 220);
});

$("#pv-close").onclick = () => preview.close();
$("#dlg-cancel").onclick = () => dlg.close();
$("#dlg-form").addEventListener("submit", submitDlg);

openDir("/");
refreshStats();
