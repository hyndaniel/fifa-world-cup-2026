"""report 列表排序: 部署 mtime 被抹平时, 用构建期清单 report_times.json 恢复新旧序。"""
import json
import os

import pytest

from backend.reports import bump_time, list_reports, read_report, write_report


def test_orders_by_manifest_when_mtimes_equal(tmp_path):
    """两文件 mtime 相同(模拟部署 COPY 抹平), 清单决定新旧序。"""
    (tmp_path / "aaa.md").write_text("# AAA\n", encoding="utf-8")
    (tmp_path / "zzz.md").write_text("# ZZZ\n", encoding="utf-8")
    os.utime(tmp_path / "aaa.md", (1000, 1000))
    os.utime(tmp_path / "zzz.md", (1000, 1000))
    (tmp_path / "report_times.json").write_text(
        json.dumps({"aaa": 1700000000, "zzz": 1700009999}), encoding="utf-8"
    )
    out = list_reports(str(tmp_path))
    names = [r["name"] for r in out]
    assert names.index("zzz") < names.index("aaa")  # 清单里更新的排前
    assert out[0]["mtime"] == 1700009999


def test_falls_back_to_mtime_without_manifest(tmp_path):
    """无清单 + 非 git 仓 → 退回文件 mtime 排序。"""
    (tmp_path / "old.md").write_text("# OLD\n", encoding="utf-8")
    (tmp_path / "new.md").write_text("# NEW\n", encoding="utf-8")
    os.utime(tmp_path / "old.md", (1000, 1000))
    os.utime(tmp_path / "new.md", (2000, 2000))
    out = list_reports(str(tmp_path))
    assert out[0]["name"] == "new"


def test_manifest_file_not_listed_as_report(tmp_path):
    """report_times.json 本身不算报告(非 .md)。"""
    (tmp_path / "r.md").write_text("# R\n", encoding="utf-8")
    (tmp_path / "report_times.json").write_text("{}", encoding="utf-8")
    names = [r["name"] for r in list_reports(str(tmp_path))]
    assert names == ["r"]


def test_underscore_subdir_md_excluded_from_list(tmp_path):
    """_archive/ _state/ (下划线前缀目录) 里的 .md 不进看板列表。

    §3 命名迁移: glob 改递归后, 仍须把死报告/机器态 (_archive/_state) 挡在看板外。
    """
    (tmp_path / "live.md").write_text("# LIVE\n", encoding="utf-8")
    (tmp_path / "_archive").mkdir()
    (tmp_path / "_archive" / "dead.md").write_text("# DEAD\n", encoding="utf-8")
    (tmp_path / "_state").mkdir()
    (tmp_path / "_state" / "note.md").write_text("# NOTE\n", encoding="utf-8")
    names = [r["name"] for r in list_reports(str(tmp_path))]
    assert names == ["live"]  # 根 .md 收;下划线目录内的 dead/note 排除


def test_live_subdir_md_included_in_list(tmp_path):
    """§3 命名迁移: agents/scoring/intel 等非下划线子目录的报告**要**上看板, name=stem。"""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "wc-score-v1__比分预测.md").write_text("# 比分预测\n", encoding="utf-8")
    (tmp_path / "scoring").mkdir()
    (tmp_path / "scoring" / "三方跑分卡.md").write_text("# 三方跑分卡\n", encoding="utf-8")
    (tmp_path / "_archive").mkdir()
    (tmp_path / "_archive" / "dead.md").write_text("# DEAD\n", encoding="utf-8")
    names = {r["name"] for r in list_reports(str(tmp_path))}
    assert names == {"wc-score-v1__比分预测", "三方跑分卡"}  # 子目录 live 进、_archive 不进
    titles = {r["title"] for r in list_reports(str(tmp_path))}
    assert "三方跑分卡" in titles  # H1 标题正常解析(跨子目录)


def test_matchsim_zucai_and_intel_stage_matches(tmp_path):
    """比赛模拟带 zucai(标题结尾竞猜号); 赛前情报带 stage + 从正文 H2 解析的完整对阵名。"""
    (tmp_path / "match-sims").mkdir()
    (tmp_path / "match-sims" / "比赛模拟-加拿大vs摩洛哥-2026-07-04.md").write_text(
        "# 比赛模拟 · 加拿大 vs 摩洛哥（R16 · 089）\n", encoding="utf-8")
    (tmp_path / "match-sims" / "比赛模拟-巴西vs日本-2026-06-29.md").write_text(
        "# 比赛模拟 · 巴西 vs 日本（R32 · 074）\n", encoding="utf-8")
    (tmp_path / "intel").mkdir()
    (tmp_path / "intel" / "2026-07-02__赛前情报-R32.md").write_text(
        "# Deep-search 淘汰赛 R32 赛前情报\n"
        "## 西班牙 vs 奥地利（ET 7/2 15:00 · SoFi）\n正文\n"
        "## 080 · 🇨🇩 England vs 🇨🇩 DR Congo（ET 7/1）\n"
        "## ① 组E|Curaçao 库拉索 vs Ivory Coast 科特迪瓦 · 16:00 ET\n"
        "## 缺口清单\n", encoding="utf-8")
    (tmp_path / "intel" / "2026-06-25__赛前情报-6场.md").write_text("# 六场\n", encoding="utf-8")
    by = {r["name"]: r for r in list_reports(str(tmp_path))}
    assert by["比赛模拟-加拿大vs摩洛哥-2026-07-04"]["zucai"] == 89
    assert by["比赛模拟-巴西vs日本-2026-06-29"]["zucai"] == 74
    r32 = by["2026-07-02__赛前情报-R32"]
    assert r32["stage"] == "淘汰赛R32"
    # 中文原样; 纯英文译中文(England→英格兰, DR Congo→刚果金); 中英混排取中文
    assert r32["matches"] == ["西班牙 vs 奥地利", "英格兰 vs 刚果金", "库拉索 vs 科特迪瓦"]
    assert by["2026-06-25__赛前情报-6场"]["stage"] == "小组赛"  # 无 H2 → matches 为 []
    assert by["2026-06-25__赛前情报-6场"]["matches"] == []


def test_dir_field_is_top_level_subdir(tmp_path):
    """每条报告带 dir = reports/ 下顶层子目录 (供前端分类导航); 根目录报告 dir=''。"""
    (tmp_path / "match-sims").mkdir()
    (tmp_path / "match-sims" / "比赛模拟-加拿大vs摩洛哥-2026-07-04.md").write_text(
        "# 比赛模拟\n", encoding="utf-8")
    (tmp_path / "intel").mkdir()
    (tmp_path / "intel" / "2026-07-02__赛前情报-R32.md").write_text("# 情报\n", encoding="utf-8")
    (tmp_path / "根级报告.md").write_text("# 根\n", encoding="utf-8")
    dirs = {r["name"]: r["dir"] for r in list_reports(str(tmp_path))}
    assert dirs["比赛模拟-加拿大vs摩洛哥-2026-07-04"] == "match-sims"
    assert dirs["2026-07-02__赛前情报-R32"] == "intel"
    assert dirs["根级报告"] == ""  # 根目录报告顶层子目录为空


def test_read_report_resolves_subdir_by_stem(tmp_path):
    """read_report 用 stem (无斜杠) 取到子目录里的报告 → 前端 URL/路由不变。"""
    (tmp_path / "intel").mkdir()
    (tmp_path / "intel" / "2026-06-29__赛前情报-R32.md").write_text("# 情报\n正文", encoding="utf-8")
    assert "正文" in read_report("2026-06-29__赛前情报-R32", str(tmp_path))


def test_read_report_rejects_traversal_and_glob(tmp_path):
    """name 含斜杠/穿越/glob 元字符一律拒绝 (子目录改造后仍守边界)。"""
    (tmp_path / "_archive").mkdir()
    (tmp_path / "_archive" / "dead.md").write_text("# DEAD\n", encoding="utf-8")
    for bad in ["../secret", "agents/x", "..", "*", "a?b", ""]:
        with pytest.raises((ValueError, FileNotFoundError)):
            read_report(bad, str(tmp_path))
    # 下划线目录里的报告不可经 read_report 取出 (与列表口径一致)
    with pytest.raises(FileNotFoundError):
        read_report("dead", str(tmp_path))


def test_write_report_creates_subdir_and_readable_back(tmp_path):
    """write_report 允许斜杠指定子目录(与 read_report 不同, 供 ingest 新报告落对目录);
    落盘后能被 read_report(用 stem)读回。"""
    stem = write_report("agents/wc-bet__下注复盘", "# 复盘\n正文", str(tmp_path))
    assert stem == "wc-bet__下注复盘"
    assert (tmp_path / "agents" / "wc-bet__下注复盘.md").exists()
    assert "正文" in read_report("wc-bet__下注复盘", str(tmp_path))


def test_write_report_overwrites_existing(tmp_path):
    write_report("r", "# R\n旧内容", str(tmp_path))
    write_report("r", "# R\n新内容", str(tmp_path))
    assert read_report("r", str(tmp_path)) == "# R\n新内容"


def test_write_report_rejects_traversal(tmp_path):
    for bad in ["../secret", "a/../../b", "..", "*", "a?b", ""]:
        with pytest.raises(ValueError):
            write_report(bad, "x", str(tmp_path))
    # 越界写入没有留下任何文件
    assert not (tmp_path.parent / "secret.md").exists()


def test_write_report_rejects_non_string_content(tmp_path):
    """content 误传成 list/数字/布尔 → ValueError(不是 Path.write_text 的 TypeError),
    这样 /api/ingest/reports 现有的 except ValueError 才能兜住, 不至于整批 500。"""
    for bad_content in [["not", "a", "string"], 123, True, {"k": "v"}]:
        with pytest.raises(ValueError):
            write_report("r", bad_content, str(tmp_path))
    assert not (tmp_path / "r.md").exists()


def test_bump_time_sets_and_overrides_manifest_entry(tmp_path):
    (tmp_path / "report_times.json").write_text(
        json.dumps({"old": 1000}), encoding="utf-8"
    )
    bump_time("new", str(tmp_path), ts=1700000000)
    times = json.loads((tmp_path / "report_times.json").read_text(encoding="utf-8"))
    assert times == {"old": 1000, "new": 1700000000}
