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
    """reports/*.md → [{"name","title"}], 按 name 排序。"""
    base = pathlib.Path(reports_dir)
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.glob("*.md")):
        if not p.is_file():
            continue
        name = p.stem
        title = name
        try:
            t = _first_h1(p.read_text(encoding="utf-8"))
            if t:
                title = t
        except OSError:
            pass
        out.append({"name": name, "title": title})
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
