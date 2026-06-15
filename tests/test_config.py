from backend.config import load_config


def test_load_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")  # 缺文件→默认
    assert cfg["poll"]["interval_sec"] == 180
    assert cfg["poly"]["tag_id"] == "102232"
