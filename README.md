# 仓核 WebFS

基于 Docker Registry（OCI Distribution v2）的 Web 文件系统。文件以 **blob** 内容寻址存储，目录索引写入 OCI manifest。

## 能力

- 上传：文件内容写入 `sha256` blob
- 下载：按路径取出对应 layer blob
- 目录：mkdir / 列表 / 重命名 / 删除
- 兼容 Registry API：`/v2/`、blob upload、manifest、catalog

## 存储约定

- 仓库：`webfs/vault`
- 索引 tag：`index`（layer mediaType `application/vnd.webfs.index.v1+json`）
- 文件 tag：`file-<path-sha256前16位>`，layer 即文件字节
- 本地落盘：`data/registry/blobs/sha256/...`

## 启动

```bash
python3 -m pip install --break-system-packages -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

或：

```bash
bash start.sh
```

打开浏览器访问服务根路径。也可用标准 registry 客户端对 `/v2/` 探活。
