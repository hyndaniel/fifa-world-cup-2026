#!/usr/bin/env python3
"""ingest_client — HK 看板 ingest HTTP 客户端(单一真源)。

refresh_all / push_data / collect_enrich 共用: WC_INGEST_URL/PW/USER 环境变量读取、
Basic Auth 头、JSON POST/GET。此前四个脚本各自拷贝一份, 行为已漂移(有的吞异常
有的裸抛、有的警告缺密码有的不警), 收敛到这里。纯 stdlib。

两种失败语义, 调用方按需选:
  post(path, body)        — 失败抛异常(push_data: 推失败就该让用户看到 traceback)
  post_quiet(path, body)  — 失败打 stderr warn 返回 None(refresh_all: best-effort,
                            缓存已写, 下轮重推)
"""
from __future__ import annotations
import base64
import json
import os
import sys
import urllib.request

INGEST = os.environ.get("WC_INGEST_URL", "http://18.166.71.60:8000").rstrip("/")
PW = os.environ.get("WC_INGEST_PW", "")
USER = os.environ.get("WC_INGEST_USER", "admin")
PW_PLACEHOLDER = "__看板密码__"  # plist 模板占位符; 没换=误配, 鉴权头会带错密码/缺失


def pw_missing():
    """密码未配置(空或还是 plist 占位符)→ HK 若开鉴权必 401。"""
    return PW in ("", PW_PLACEHOLDER)


def auth_header():
    if PW:
        tok = base64.b64encode(f"{USER}:{PW}".encode()).decode()
        return {"Authorization": "Basic " + tok}
    return {}


def post(path, body, *, timeout=30, headers=None):
    """POST JSON 到 INGEST+path, 返回解析后的响应 dict; 失败抛异常。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdr = {"Content-Type": "application/json"}
    if headers:
        hdr.update(headers)
    hdr.update(auth_header())
    req = urllib.request.Request(INGEST + path, data=data, headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def post_quiet(path, body, *, timeout=30, headers=None):
    """post 的 best-effort 版: 失败打 stderr warn, 返回 None。"""
    try:
        return post(path, body, timeout=timeout, headers=headers)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"  [warn] POST {path} -> {e!r}\n")
        return None


def get(path, *, timeout=30, headers=None):
    """GET INGEST+path, 返回解析后的 dict; 失败抛异常。"""
    hdr = {"Accept": "application/json"}
    if headers:
        hdr.update(headers)
    hdr.update(auth_header())
    req = urllib.request.Request(INGEST + path, headers=hdr, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
