"""读 reports/*.md (列表 + 单篇内容)。

- list_reports(reports_dir="reports") -> list[{name, title}]
    name: 不含 .md 后缀的文件名 (URL 安全标识);
    title: 文件里第一个 "# " 一级标题, 缺则回退到 name。
- read_report(name, reports_dir="reports") -> str
    返回该 .md 文本; 防目录穿越 (name 不得含路径分隔/.. , 解析后必须仍落在 reports_dir 内)。
"""
import pathlib


def _first_h1(text):
    """取文本里第一个 '# 标题' 的标题文字, 缺则 None。"""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


def list_reports(reports_dir="reports"):
    """reports/*.md → [{"name","title","mtime"}], 按修改时间倒序 (最新在前)。

    mtime: 文件最后修改时间 (unix 秒)。前端据此分组(今天/更早)+ 显示时间,
    并按时间排序, 解决"报告列表按文件名乱序、看不出新旧"的问题。
    """
    base = pathlib.Path(reports_dir)
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.glob("*.md")):
        if not p.is_file():
            continue
        # 跳过点文件 (macOS AppleDouble "._*" 等隐藏文件, 非真报告且常非 UTF-8)
        if p.name.startswith("."):
            continue
        name = p.stem
        title = name
        try:
            t = _first_h1(p.read_text(encoding="utf-8"))
            if t:
                title = t
        except (OSError, UnicodeDecodeError):
            # 坏编码不应拖垮整个列表接口, 回退到文件名当标题
            pass
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append({"name": name, "title": title, "mtime": mtime})
    # 最新生成/更新的报告排最前
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def read_report(name, reports_dir="reports"):
    """返回 reports/<name>.md 文本; 防目录穿越。

    name 不应含后缀 (有 .md 也容忍)。任何含路径分隔或越出 reports_dir 的请求 → ValueError。
    找不到文件 → FileNotFoundError。
    """
    if name is None:
        raise ValueError("report name required")
    name = str(name)
    if name.endswith(".md"):
        name = name[:-3]
    # 拒绝任何含路径分隔/穿越的标识
    if name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"invalid report name: {name!r}")

    base = pathlib.Path(reports_dir).resolve()
    target = (base / f"{name}.md").resolve()
    # 解析后仍须落在 base 内 (双保险防穿越)
    if base != target and base not in target.parents:
        raise ValueError(f"invalid report name: {name!r}")
    if not target.is_file():
        raise FileNotFoundError(f"report not found: {name}")
    return target.read_text(encoding="utf-8")
