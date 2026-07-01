"""读 reports/*.md (列表 + 单篇内容) + 写(供 /api/ingest/reports 落盘)。

- list_reports(reports_dir="reports") -> list[{name, title}]
    name: 不含 .md 后缀的文件名 (URL 安全标识);
    title: 文件里第一个 "# " 一级标题, 缺则回退到 name。
- read_report(name, reports_dir="reports") -> str
    返回该 .md 文本; 防目录穿越 (name 不得含路径分隔/.. , 解析后必须仍落在 reports_dir 内)。
- write_report(name, content, reports_dir="reports") -> str
    落盘 reports/<name>.md(name 可含子目录如 "agents/xxx", 自动建目录); 防目录穿越
    (仅挡 ".."/绝对路径越界, 与 read_report 不同, 这里**允许**斜杠指定子目录, 否则
    没法给新报告选落哪个子目录); 返回写入文件的 stem, 供调用方联动刷新 report_times。
- bump_time(name_stem, reports_dir="reports", ts=None) -> None
    把 report_times.json 里该报告的时间戳刷成 ts(默认当前时刻)。
"""
import json
import pathlib
import subprocess
import time


def _load_times(reports_dir):
    """读构建期清单 reports/report_times.json -> {name: unix秒}; 缺/坏 -> {}。"""
    p = pathlib.Path(reports_dir) / "report_times.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_times(reports_dir, times):
    p = pathlib.Path(reports_dir) / "report_times.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(times, ensure_ascii=False, indent=2), encoding="utf-8")


def bump_time(name_stem, reports_dir="reports", ts=None):
    """把 report_times.json 里 name_stem 的时间戳刷成 ts(默认当前时刻)。

    ingest 落盘走 HTTP、不经 git commit, list_reports 排序原本靠"构建期清单优先、
    其次 git 提交时间"两级兜底——ingest 场景两者都没有(没打 tag/没 git commit),
    所以必须显式刷这份清单, 否则新报告排序会退到文件 mtime(能用但不如清单精确)。
    """
    times = _load_times(reports_dir)
    times[name_stem] = ts if ts is not None else time.time()
    _save_times(reports_dir, times)


def _safe_write_target(name, reports_dir):
    """校验+解析 write_report 的落盘路径。

    与 read_report 的校验不同: **允许**斜杠(指定子目录, 如 "agents/xxx"), 只挡
    ".."/绝对路径/空字符等真正的越界写法; 解析后仍须落在 reports_dir 内(双保险)。
    """
    if name is None:
        raise ValueError("report name required")
    name = str(name)
    if name.endswith(".md"):
        name = name[:-3]
    if not name or "\x00" in name or "*" in name or "?" in name or "[" in name:
        raise ValueError(f"invalid report name: {name!r}")
    parts = name.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid report name: {name!r}")
    base = pathlib.Path(reports_dir).resolve()
    target = (base / (name + ".md")).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"invalid report name: {name!r}")
    return target


def write_report(name, content, reports_dir="reports"):
    """落盘 reports/<name>.md(自动建子目录), 返回写入文件的 stem。"""
    target = _safe_write_target(name, reports_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target.stem


def _git_ts(path):
    """该文件最后一次 git 提交的 unix 秒; 无 git / 非仓内 / 无提交 -> None。"""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", path.name],
            cwd=str(path.parent), capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    s = out.stdout.strip()
    return int(s) if out.returncode == 0 and s.isdigit() else None


def _resolve_ts(name, path, times):
    """报告排序/显示时间戳(unix 秒)。优先构建期清单(免疫部署 mtime 抹平、
    免容器内无 git), 其次 git 提交时间(本地/有 .git 处), 最后文件 mtime。
    """
    v = times.get(name)
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    ts = _git_ts(path)
    if ts:
        return float(ts)
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _first_h1(text):
    """取文本里第一个 '# 标题' 的标题文字, 缺则 None。"""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


def list_reports(reports_dir="reports"):
    """reports/*.md → [{"name","title","mtime"}], 按时间倒序 (最新在前)。

    mtime: 报告时间戳 (unix 秒), 由 _resolve_ts 解析: 优先构建期清单 report_times.json
    (免疫部署 mtime 抹平), 其次 git 提交时间, 最后文件 mtime。前端据此分组(今天/更早)
    + 显示时间 + 排序, 解决"报告列表看不出新旧 / 部署后全同一时刻"的问题。
    """
    base = pathlib.Path(reports_dir)
    if not base.is_dir():
        return []
    times = _load_times(reports_dir)
    out = []
    # 递归扫 reports/**/*.md (含 agents/scoring/intel 子目录), 但跳过下划线目录
    # (_archive/_state 等非 live 留痕) —— §3 命名迁移: 子目录组织 + 子目录仍上看板。
    for p in sorted(base.rglob("*.md")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        # 跳过任何下划线前缀目录下的文件 (_archive/_state)
        if any(part.startswith("_") for part in rel.parts[:-1]):
            continue
        # 跳过点文件 (macOS AppleDouble "._*" 等隐藏文件, 非真报告且常非 UTF-8)
        if p.name.startswith("."):
            continue
        # name = 文件名 stem (迁移后全局唯一), URL 安全且无斜杠 → 前端/路由不变
        name = p.stem
        title = name
        try:
            t = _first_h1(p.read_text(encoding="utf-8"))
            if t:
                title = t
        except (OSError, UnicodeDecodeError):
            # 坏编码不应拖垮整个列表接口, 回退到文件名当标题
            pass
        mtime = _resolve_ts(name, p, times)
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
    # 拒绝任何含路径分隔/穿越/glob 元字符的标识 (name 是 stem, 不含子路径)
    if (name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name
            or "*" in name or "?" in name or "[" in name):
        raise ValueError(f"invalid report name: {name!r}")

    base = pathlib.Path(reports_dir).resolve()
    # 迁移后报告分布在 reports/{agents,scoring,intel}/ 子目录; name 仍是唯一 stem。
    # 递归找 <name>.md, 跳过下划线目录 (_archive/_state), 比较 stem 精确匹配 (防 glob 注入)。
    target = None
    for p in base.rglob("*.md"):
        rel = p.relative_to(base)
        if any(part.startswith("_") for part in rel.parts[:-1]):
            continue
        if p.stem == name and p.is_file():
            target = p.resolve()
            break
    if target is None:
        raise FileNotFoundError(f"report not found: {name}")
    # 解析后仍须落在 base 内 (双保险防穿越)
    if base != target and base not in target.parents:
        raise ValueError(f"invalid report name: {name!r}")
    return target.read_text(encoding="utf-8")
