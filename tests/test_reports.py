"""report 列表排序: 部署 mtime 被抹平时, 用构建期清单 report_times.json 恢复新旧序。"""
import json
import os

from backend.reports import list_reports


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


def test_subdir_md_excluded_from_list(tmp_path):
    """_archive/ _state/ 子目录里的 .md 不进看板列表(glob('*.md') 非递归)。

    锚住 §3.1 报告迁移的物理前提:把死报告 git mv 进 reports/_archive/ 即从 /reports 列表消失。
    """
    (tmp_path / "live.md").write_text("# LIVE\n", encoding="utf-8")
    (tmp_path / "_archive").mkdir()
    (tmp_path / "_archive" / "dead.md").write_text("# DEAD\n", encoding="utf-8")
    (tmp_path / "_state").mkdir()
    (tmp_path / "_state" / "note.md").write_text("# NOTE\n", encoding="utf-8")
    names = [r["name"] for r in list_reports(str(tmp_path))]
    assert names == ["live"]  # 仅根 .md;子目录内的 dead/note 被排除
