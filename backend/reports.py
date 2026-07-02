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
import re
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
    """落盘 reports/<name>.md(自动建子目录), 返回写入文件的 stem。

    content 非字符串(如客户端误传成 list/数字)统一转成 ValueError, 而不是让
    Path.write_text 抛 TypeError——调用方(/api/ingest/reports)按条 catch ValueError
    隔离单条失败, 不该因为类型不对就让整批 500。
    """
    if not isinstance(content, str):
        raise ValueError(f"content must be str, got {type(content).__name__}")
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


def _zucai_from_title(title):
    """比赛模拟标题结尾的竞猜序号, 例 '…（R32 · 087）'/'…（R16 · 089）' → 87/89。
    取括号内结尾那串数字; 解析不出返回 None(前端排序时落到末尾)。"""
    m = re.search(r"(\d+)\s*[）)]\s*$", title or "")
    return int(m.group(1)) if m else None


# 文件名轮次 → 展示用轮次名; 顺序即匹配优先级(R16 先于 R32 免子串误配, 虽当前无歧义)。
_INTEL_ROUNDS = (("R16", "淘汰赛R16"), ("R32", "淘汰赛R32"), ("6场", "小组赛"))


def _intel_stage(name):
    """从文件名推轮次显示名: '…赛前情报-R32'→'淘汰赛R32'; '…-6场'→'小组赛'; 缺则 ''。"""
    for key, label in _INTEL_ROUNDS:
        if key in name:
            return label
    return ""


# 国旗/emoji/变体选择符/ZWJ/tag 字符(England 旗是 base+tag 序列)一并剥掉。
_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    r"︀-️‍\U000E0000-\U000E007F]")


try:
    from .teammap import CN2EN
except ImportError:  # 容器/脚本以顶层模块跑时的回退
    from teammap import CN2EN  # type: ignore

# 英文队名 → 中文(CN2EN 反转 + 报告正文里出现过、写法与 CN2EN 取值不同的别名),
# 让纯英文 H2 的老情报报告也显示中文全名, 与中文报告统一。
_EN2CN = {en: cn for cn, en in CN2EN.items()}
_EN2CN.update({
    "USA": "美国", "Côte d'Ivoire": "科特迪瓦", "Ivory Coast": "科特迪瓦",
    "Bosnia & Herzegovina": "波黑", "Türkiye": "土耳其", "Turkey": "土耳其",
})


def _prefer_cn(side):
    """一侧队名: 中英混排(如 'Curaçao 库拉索')只留中文; 纯英文尽量译中文, 无对照留英文。"""
    side = side.strip()
    if re.search(r"[一-鿿]", side):
        cn = " ".join(re.findall(r"[一-鿿]+", side))
        return cn or side
    return _EN2CN.get(side, side)


def _intel_matches(text):
    """从赛前情报正文的 '## X vs Y（…）' H2 抽完整对阵名(去序号/国旗/场地时间)。

    跨报告格式不一(有的带 '周六067'/'080 ·'/'① 组E|'/国旗/'揭幕战 ·', 场地用括号或
    ' · ' 接), 尽力清洗; 清洗后仍含 ' vs ' 才收, 否则跳过。中英混排优先取中文。
    返回 ['西班牙 vs 奥地利', …](纯英文源报告则保留英文全名, 无中文可取)。
    """
    out = []
    for line in text.splitlines():
        if not line.startswith("## ") or " vs " not in line:
            continue
        core = re.split(r"[（(]", line[3:])[0]          # 砍掉场地/时间括号
        core = _EMOJI_RE.sub("", core)                   # 去国旗 emoji
        core = re.sub(r"^\s*周[一二三四五六日]?\d+\s*", "", core)   # '周六067 '
        core = re.sub(r"^\s*\d+\s*·\s*", "", core)         # '080 · '
        core = re.sub(r"^\s*[①②③④⑤⑥⑦⑧⑨]\s*", "", core)    # '① '
        core = re.sub(r"^\s*组[A-Z]\s*\|\s*", "", core)     # '组E|'
        core = re.sub(r"揭幕战\s*·\s*", "", core)           # 'R32 揭幕战 · '
        core = re.sub(r"^\s*R32\s+", "", core)              # 残留 'R32 '
        # 场地/时间可能用 ' · ' 接在对阵后(无括号) → 取含 vs 的那段
        seg = next((s for s in core.split(" · ") if " vs " in s), core)
        seg = re.sub(r"\s+", " ", seg).strip(" ·|-")
        parts = seg.split(" vs ")
        if len(parts) == 2:
            out.append(f"{_prefer_cn(parts[0])} vs {_prefer_cn(parts[1])}")
    return out


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
        text = ""
        try:
            text = p.read_text(encoding="utf-8")
            t = _first_h1(text)
            if t:
                title = t
        except (OSError, UnicodeDecodeError):
            # 坏编码不应拖垮整个列表接口, 回退到文件名当标题
            pass
        mtime = _resolve_ts(name, p, times)
        # dir = reports/ 下的顶层子目录 (match-sims/intel/agents/scoring); 根目录报告为 ""。
        # 前端据此把报告分类导航 (比赛模拟/赛前情报/预测台账/跑分卡)。
        top = rel.parts[0] if len(rel.parts) > 1 else ""
        item = {"name": name, "title": title, "mtime": mtime, "dir": top}
        # 分类导航附加字段: 比赛模拟按竞猜序号排序; 赛前情报显示"轮次 · 完整对阵名"。
        if top == "match-sims":
            item["zucai"] = _zucai_from_title(title)
        elif top == "intel":
            item["stage"] = _intel_stage(name)
            item["matches"] = _intel_matches(text)
        out.append(item)
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
