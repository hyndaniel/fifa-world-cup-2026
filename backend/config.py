"""配置加载: 读 config.toml, 缺文件或缺字段→默认值。"""
import pathlib

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 8000, "password": "change-me"},
    "poll": {"interval_sec": 180, "zucai_pools": "had,hhad,ttg", "mode": "direct"},
    "poly": {"tag_id": "102232", "gamma_base": "https://gamma-api.polymarket.com"},
    "zucai": {
        "api": "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry",
        "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) Mobile/15E148",
    },
    "value": {"devig_yellow_below": 1.03},
    "wallet": {"A_unit_pct": 1.5, "B_weekly_budget": 100},
}


def load_config(path="config.toml"):
    p = pathlib.Path(path)
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if p.exists():
        user = tomllib.loads(p.read_text(encoding="utf-8"))
        for k, v in user.items():
            cfg.setdefault(k, {}).update(v)
    return cfg
