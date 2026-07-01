"""push_data.py 的纯函数(不碰网络): _report_name 路径->ingest name 转换,
_collect_all_reports 的下划线目录/隐藏文件过滤。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from push_data import _collect_all_reports, _report_name  # noqa: E402


def test_report_name_strips_root_and_md_suffix(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    p = root / "agents" / "wc-bet__下注复盘.md"
    p.parent.mkdir(parents=True)
    p.write_text("x", encoding="utf-8")
    assert _report_name(p, str(root)) == "agents/wc-bet__下注复盘"


def test_report_name_flat_file_no_subdir(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    p = root / "r.md"
    p.write_text("x", encoding="utf-8")
    assert _report_name(p, str(root)) == "r"


def test_collect_all_reports_skips_underscore_dirs_and_dotfiles(tmp_path):
    root = tmp_path / "reports"
    (root / "agents").mkdir(parents=True)
    (root / "_archive").mkdir()
    (root / "agents" / "live.md").write_text("# LIVE\n", encoding="utf-8")
    (root / "_archive" / "dead.md").write_text("# DEAD\n", encoding="utf-8")
    (root / ".hidden.md").write_text("x", encoding="utf-8")
    (root / "top.md").write_text("# TOP\n", encoding="utf-8")
    got = {p.name for p in _collect_all_reports(str(root))}
    assert got == {"live.md", "top.md"}
