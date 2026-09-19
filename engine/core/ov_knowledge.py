"""OpenViking 知识检索 HTTP 客户端（live SDS 溯源用）。

对接 OpenViking server 0.4.x：

    POST /api/v1/search/find      语义召回（等价 ov find）
    GET  /api/v1/content/read     读取精确 Level-2 内容（等价 ov read）

鉴权：``X-API-Key``（或 ``Authorization: Bearer``），可选
``X-OpenViking-Account`` / ``X-OpenViking-User``。密钥只从环境变量或本地密钥
文件读取，不进入 config.json / 任何产物。

纯标准库实现，无第三方依赖；失败一律抛 ``OpenVikingError``，由上层转成
``ClientError``，绝不把空结果包装成“检索成功”。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_KB_PREFIX = "viking://resources/"


class OpenVikingError(RuntimeError):
    def __init__(self, code: str, message: str, uri: str = "", details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.uri = uri
        self.details = details


def kb_prefix(config: Dict[str, Any]) -> str:
    knowledge = config.get("knowledge") or {}
    prefix = str(knowledge.get("uri_prefix") or "").strip() or DEFAULT_KB_PREFIX
    return prefix if prefix.endswith("/") else prefix + "/"


def resolve_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """从 ``knowledge.ov`` 解析 url / 密钥 / 账户 / 超时。"""
    knowledge = config.get("knowledge") or {}
    ov = knowledge.get("ov") or {}
    url = str(ov.get("url") or "").strip().rstrip("/")
    if not url:
        raise OpenVikingError("ov_config", "缺少 knowledge.ov.url：请配置 OpenViking 服务地址")
    if not url.startswith(("http://", "https://")):
        raise OpenVikingError("ov_config", f"knowledge.ov.url 必须是 http(s):// 地址: {url!r}")

    env_name = str(ov.get("api_key_env") or "OV_API_KEY").strip()
    key = os.environ.get(env_name, "").strip()
    if not key:
        key_file = str(ov.get("api_key_file") or "").strip()
        if key_file:
            try:
                with open(key_file, "r", encoding="utf-8") as handle:
                    key = handle.read().strip().splitlines()[0].strip()
            except (OSError, IndexError) as exc:
                raise OpenVikingError("ov_config", f"读取 knowledge.ov.api_key_file 失败: {key_file}: {exc}") from exc
    if not key:
        raise OpenVikingError(
            "ov_config",
            f"缺少 API Key：请设置环境变量 {env_name}（或配置 knowledge.ov.api_key_file 指向本地密钥文件）",
        )
    if len(key) < 8:
        raise OpenVikingError("ov_config", "OpenViking API Key 长度异常（<8 字符），请检查密钥来源")

    try:
        timeout = float(ov.get("timeout") or 60)
    except (TypeError, ValueError):
        timeout = 60.0
    return {
        "url": url,
        "api_key": key,
        "account": str(ov.get("account") or "").strip(),
        "user": str(ov.get("user") or "").strip(),
        "timeout": timeout,
    }


def _request_json(
    settings: Dict[str, Any],
    method: str,
    path: str,
    *,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = settings["url"] + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {
        "X-API-Key": settings["api_key"],
        "Content-Type": "application/json",
    }
    if settings.get("account"):
        headers["X-OpenViking-Account"] = settings["account"]
    if settings.get("user"):
        headers["X-OpenViking-User"] = settings["user"]
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=float(settings["timeout"])) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        code = f"HTTP_{exc.code}"
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            err = (payload or {}).get("error") or {}
            code = str(err.get("code") or code)
            message = str(err.get("message") or raw.decode("utf-8", errors="replace")[:300])
        except Exception:
            message = raw.decode("utf-8", errors="replace")[:300]
        raise OpenVikingError(code, message, uri=url) from exc
    except urllib.error.URLError as exc:
        raise OpenVikingError("connection", f"无法连接 OpenViking ({settings['url']}): {exc.reason}") from exc
    except OSError as exc:
        raise OpenVikingError("connection", f"OpenViking 传输错误: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        raise OpenVikingError("bad_response", "OpenViking 返回非 JSON", uri=url) from None
    if isinstance(payload, dict) and payload.get("status") not in (None, "ok"):
        err = payload.get("error") or {}
        raise OpenVikingError(
            str(err.get("code") or "ERROR"),
            str(err.get("message") or payload.get("message") or "unknown error"),
            uri=url,
            details=err.get("details"),
        )
    if not isinstance(payload, dict):
        raise OpenVikingError("bad_response", "OpenViking 返回结构异常（非 JSON 对象）", uri=url)
    return payload


def _unwrap_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """兼容 REST ``{status, result}`` 与 CLI ``{ok, result}`` 两种信封。"""
    if isinstance(payload.get("result"), dict):
        return payload["result"]
    return payload


def _domain_layer_from_uri(uri: str, prefix: str) -> tuple:
    if prefix and uri.startswith(prefix):
        rel = uri[len(prefix):]
    elif "://" in uri:
        after_authority = uri.split("://", 1)[1]
        parts = after_authority.split("/", 1)
        rel = parts[1] if len(parts) > 1 else parts[0]
    else:
        rel = uri
    segments = [s for s in rel.split("/") if s]
    domain = ""
    layer = ""
    known_prefixes = ("ods", "dwd", "dws", "ads", "dim", "mid", "cdm", "app")
    for seg in segments:
        lowered = seg.lower()
        if seg.endswith((".md", ".json", ".yaml", ".yml")):
            continue
        if lowered.startswith(known_prefixes):
            layer = lowered.split("_")[0].split("-")[0]
        if "-" in lowered and not lowered.endswith((".md", ".json")):
            domain = seg
            break
    if not domain and segments:
        domain = segments[0]
    return domain, layer


def normalize_find_result(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 /api/v1/search/find 或 ``ov find -o json`` 的返回转成统一文档列表。

    每条保留：uri / score / level / context_type / abstract / overview /
    domain / layer，并按 score 降序；无召回返回空列表（不是“成功但有 0 结果”
    之外的任何假象）。
    """
    inner = _unwrap_payload(payload)
    items = []
    for bucket in ("memories", "resources", "skills"):
        items.extend(inner.get(bucket) or [])
    hits: List[Dict[str, Any]] = []
    prefix = DEFAULT_KB_PREFIX
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or item.get("target_uri") or item.get("name") or "")
        if not uri:
            continue
        abstract = str(item.get("abstract") or item.get("snippet") or item.get("content") or item.get("overview") or "")
        domain, layer = _domain_layer_from_uri(uri, prefix)
        hits.append(
            {
                "uri": uri,
                "score": item.get("score"),
                "level": item.get("level"),
                "context_type": item.get("context_type"),
                "domain": domain or item.get("domain") or "",
                "layer": layer or item.get("layer") or "",
                "abstract": abstract,
                "overview": item.get("overview") or "",
            }
        )
    hits.sort(key=lambda d: float(d.get("score") or 0.0), reverse=True)
    return hits


def normalize_read_result(payload: Dict[str, Any]) -> str:
    """提取 read 内容：REST/CLI 都是 ``{result: "<content>"}``。"""
    if isinstance(payload.get("result"), str):
        return payload["result"]
    inner = _unwrap_payload(payload)
    content = inner.get("content") if isinstance(inner, dict) else None
    if content is not None:
        return str(content)
    raise OpenVikingError("read_empty", f"OpenViking read 返回缺少内容: {json.dumps(payload, ensure_ascii=False)[:300]}")


def find(config: Dict[str, Any], query: str) -> List[Dict[str, Any]]:
    settings = resolve_settings(config)
    knowledge = config.get("knowledge") or {}
    try:
        limit = int(knowledge.get("limit") or 10)
        level = int(knowledge.get("level") or 2)
    except (TypeError, ValueError):
        limit, level = 10, 2
    body = {
        "query": str(query or ""),
        "target_uri": kb_prefix(config).rstrip("/"),
        "limit": limit,
        "level": level,
    }
    payload = _request_json(settings, "POST", "/api/v1/search/find", body=body)
    return normalize_find_result(payload)


def read(config: Dict[str, Any], uri: str) -> str:
    settings = resolve_settings(config)
    payload = _request_json(settings, "GET", "/api/v1/content/read", query={"uri": uri})
    return normalize_read_result(payload)
