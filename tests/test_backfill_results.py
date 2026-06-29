# tests/test_backfill_results.py
"""赛果回填闭环编排:只录 finished + 幂等(mock fetch、临时 db、不打真网)。"""
import json
import sqlite3

from backend.results import MatchResult
import tools.backfill_results as B


def test_backfill_records_only_finished(tmp_path):
    db = str(tmp_path / "t.db")
    fake = [MatchResult("周四055", 2, 0, True), MatchResult("周五099", 0, 0, False)]
    keys = B.backfill(db, fake)
    assert keys == ["周四055"]                       # 只录已完赛
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT home_goals,away_goals,outcome FROM match_results WHERE match_key=?",
        ("周四055",)).fetchone()
    # 未完赛那条根本没进表
    n_unfinished = conn.execute(
        "SELECT COUNT(*) FROM match_results WHERE match_key=?", ("周五099",)).fetchone()[0]
    conn.close()
    assert row == (2, 0, "h")
    assert n_unfinished == 0


def test_backfill_idempotent(tmp_path):
    db = str(tmp_path / "t.db")
    fake = [MatchResult("周四055", 2, 0, True)]
    first = B.backfill(db, fake)
    second = B.backfill(db, fake)                     # 跑两遍
    conn = sqlite3.connect(db)
    n = conn.execute(
        "SELECT COUNT(*) FROM match_results WHERE match_key=?", ("周四055",)).fetchone()[0]
    conn.close()
    assert n == 1                                     # upsert,db 不重复
    assert first == ["周四055"]
    assert second == []                               # 已录未变 → 第二遍不返回(台账/跑分卡不重跑)


def test_main_no_results_returns_0(tmp_path, monkeypatch, capsys):
    # mock fetch_results 返回全未完赛 → 无可录 → 打印 + 返回 0
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(B, "fetch_results",
                        lambda *a, **k: [MatchResult("周五099", 0, 0, False)])
    monkeypatch.setattr(B, "_rerun_scorecard", lambda *a, **k: None)  # 不打真 v2
    rc = B.main(["--once", "--cache", db, "--tags-out", str(tmp_path / "ledger.md"),
                 "--scenario-lib", str(tmp_path / "s.json")])
    assert rc == 0
    assert "无新赛果可录" in capsys.readouterr().out


def test_main_reruns_scorecard_even_without_new_results(tmp_path, monkeypatch):
    # 自愈核心:即便本次无新赛果(backfill 返 []),跑分卡重跑仍被无条件调用。
    # 这锚住"上次重跑崩了、本次没新赛果也补跑"的每 5 分钟自愈属性。
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(B, "fetch_results",
                        lambda *a, **k: [MatchResult("周五099", 0, 0, False)])
    calls = []
    monkeypatch.setattr(B, "_rerun_scorecard", lambda cache: calls.append(cache))
    rc = B.main(["--once", "--cache", db, "--tags-out", str(tmp_path / "ledger.md"),
                 "--scenario-lib", str(tmp_path / "s.json")])
    assert rc == 0
    assert calls == [db]                              # 无新赛果仍重跑一次


def test_main_fetch_failure_returns_1(tmp_path, monkeypatch, capsys):
    def _boom(*a, **k):
        raise RuntimeError("网络炸了")
    monkeypatch.setattr(B, "fetch_results", _boom)
    calls = []
    monkeypatch.setattr(B, "_rerun_scorecard", lambda cache: calls.append(cache))
    rc = B.main(["--once", "--cache", str(tmp_path / "t.db"),
                 "--tags-out", str(tmp_path / "ledger.md"),
                 "--scenario-lib", str(tmp_path / "s.json")])
    assert rc == 1
    assert "失败" in capsys.readouterr().err
    assert calls == []                                # fetch 炸了不重跑跑分卡


def test_main_rerun_failure_returns_1_but_keeps_record(tmp_path, monkeypatch, capsys):
    # 重跑跑分卡抛异常 → signal 失败(rc=1)且打 stderr,但前面已成功的
    # record/台账不被吞:db 仍有该场、台账仍有该行。
    db = str(tmp_path / "t.db")
    _empty_odds(db)
    out = tmp_path / "ledger.md"
    monkeypatch.setattr(B, "fetch_results",
                        lambda *a, **k: [MatchResult("周四055", 2, 0, True)])

    def _boom(cache):
        raise RuntimeError("跑分卡炸了")
    monkeypatch.setattr(B, "_rerun_scorecard", _boom)
    rc = B.main(["--once", "--cache", db, "--tags-out", str(out),
                 "--scenario-lib", str(tmp_path / "s.json")])
    assert rc == 1
    assert "失败" in capsys.readouterr().err
    # record 已落库
    conn = sqlite3.connect(db)
    n = conn.execute(
        "SELECT COUNT(*) FROM match_results WHERE match_key=?", ("周四055",)).fetchone()[0]
    conn.close()
    assert n == 1
    # 台账已写该行
    assert "| 周四055 |" in out.read_text(encoding="utf-8")


def _empty_odds(db):
    # mech_tags → baseline_market 会查 odds_cache 表;真链路里它总存在,测试先建空表。
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE IF NOT EXISTS odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.commit()
    conn.close()


def _seed_wc_names(path, rows):
    """临时 wc.db,matches 表给队名(zucai_num, home_cn, away_cn)。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE matches (zucai_num TEXT, home_cn TEXT, away_cn TEXT)")
    conn.executemany("INSERT INTO matches VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()


def test_render_mech_tags_regenerates_with_names_and_legend(tmp_path):
    db = str(tmp_path / "t.db")
    _empty_odds(db)
    B.backfill(db, [MatchResult("周四055", 2, 0, True), MatchResult("周五061", 0, 1, True)])
    wc = str(tmp_path / "wc.db")
    _seed_wc_names(wc, [("周四055", "土耳其", "美国"), ("周五061", "挪威", "法国")])
    out = tmp_path / "ledger.md"

    B.render_mech_tags(db, str(out), wc)
    text = out.read_text(encoding="utf-8")
    # h/d/a 图例
    assert "**h**=主胜" in text and "**d**=平" in text and "**a**=客胜" in text
    # 对阵列 + 队名补全
    assert "| 对阵 |" in text
    assert "土耳其 vs 美国" in text and "挪威 vs 法国" in text
    # 两场都在(actual: 周四055 2:0=h, 周五061 0:1=a)
    assert "| 周四055 |" in text and "| 周五061 |" in text

    # 确定性重生成:再跑内容完全一致、表头只一份
    B.render_mech_tags(db, str(out), wc)
    text2 = out.read_text(encoding="utf-8")
    assert text2 == text
    assert text2.count("| 对阵 |") == 1


def test_render_mech_tags_blank_name_when_wc_db_missing(tmp_path):
    # wc.db 不存在 → 队名留空,不崩
    db = str(tmp_path / "t.db")
    _empty_odds(db)
    B.backfill(db, [MatchResult("周四055", 2, 0, True)])
    out = tmp_path / "ledger.md"
    B.render_mech_tags(db, str(out), str(tmp_path / "nope.db"))
    text = out.read_text(encoding="utf-8")
    assert "| 周四055 |" in text                        # 行在,对阵列空
    assert "| 对阵 |" in text


def test_backfill_rerecords_when_score_corrected(tmp_path):
    # 承重墙自愈契约:临时比分被改判为终比分 → 检测到变化、重录并返回 key(非幂等跳过)。
    # 杀变异体『只要 key 存在就跳过』——那会把更正后的终比分静默丢弃(台账/跑分卡不刷新)。
    from backend.baseline import get_result_goals
    db = str(tmp_path / "t.db")
    assert B.backfill(db, [MatchResult("周四055", 2, 0, True)]) == ["周四055"]
    # 同 key、比分变了 → 重录
    assert B.backfill(db, [MatchResult("周四055", 2, 1, True)]) == ["周四055"]
    assert get_result_goals(db, "周四055") == (2, 1)   # 库里已更新为更正后的比分


def test_main_empty_db_selfheals_rc0(tmp_path, monkeypatch):
    # 新部署/清库后,backfill.main 跑【真】_rerun_scorecard(不 mock)在空库上自愈:
    # rc=0 且产出"配对场数: 0"最小跑分卡。锚住每5min重跑在空库不崩的承重墙防御。
    import tools.v2_report as V
    db = str(tmp_path / "t.db")
    _empty_odds(db)                                    # 真链路里 odds_cache 总在(mech_tags 会查)
    card = tmp_path / "card.md"
    monkeypatch.setattr(V, "DEFAULT_OUT", str(card))   # 防真 v2_report 覆写实机 reports/scoring/三方跑分卡.md
    monkeypatch.setattr(B, "fetch_results", lambda *a, **k: [])   # 无赛果
    rc = B.main(["--once", "--cache", db, "--tags-out", str(tmp_path / "ledger.md"),
                 "--wc-db", str(tmp_path / "nope.db"),
                 "--scenario-lib", str(tmp_path / "s.json")])
    assert rc == 0
    assert "配对场数: 0" in card.read_text(encoding="utf-8")


def test_rebuild_scenario_hits_from_db(tmp_path):
    # 真集成:播种 odds + 赛果(打平)+ v2 标了"默契平" → 命中回填;重跑幂等不翻倍。
    from backend.v2_predict import build_v2_prediction, record_v2_prediction
    from backend.baseline import HAD_CFG, baseline_market
    db = str(tmp_path / "t.db")
    mk = "周三053"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                 "VALUES (?,?,?,?,?,?)",
                 ("2026-06-25T09:00:00+08:00", "zucai", mk, "南非 vs 韩国", "ko",
                  json.dumps({"had": {"h": 6.00, "d": 3.87, "a": 1.44}})))
    conn.commit()
    conn.close()
    B.backfill(db, [MatchResult(mk, 1, 1, True)])            # 实际打平 → "默契平" 命中
    bl = baseline_market(db, mk, HAD_CFG)
    pred = build_v2_prediction(mk, "乱", ["默契平"],
                               {"had": {"baseline": bl["baseline"], "deviations": []}})
    record_v2_prediction(db, mk, pred)

    lib_path = str(tmp_path / "scen.json")
    B.rebuild_scenario_hits(db, lib_path)
    lib = {s["name"]: s for s in json.load(open(lib_path, encoding="utf-8"))}
    assert lib["默契平"]["triggered"] == 1 and lib["默契平"]["hits"] == 1
    assert lib["生死战必有胜负"]["triggered"] == 0          # 没标的剧本不动

    B.rebuild_scenario_hits(db, lib_path)                    # 再跑:幂等不翻倍
    lib2 = {s["name"]: s for s in json.load(open(lib_path, encoding="utf-8"))}
    assert lib2["默契平"]["triggered"] == 1 and lib2["默契平"]["hits"] == 1


def test_rebuild_scenario_hits_no_v2_tags_writes_seed(tmp_path):
    # 有赛果但 v2 没标任何剧本 → 写回种子(全 0),不崩
    db = str(tmp_path / "t.db")
    _empty_odds(db)
    B.backfill(db, [MatchResult("周四055", 2, 0, True)])
    lib_path = str(tmp_path / "scen.json")
    B.rebuild_scenario_hits(db, lib_path)
    lib = json.load(open(lib_path, encoding="utf-8"))
    assert all(s["triggered"] == 0 and s["hits"] == 0 for s in lib)


def test_main_wires_scenario_rebuild(tmp_path, monkeypatch):
    # main 在自愈 try 块里无条件调 rebuild_scenario_hits(同台账/跑分卡)
    db = str(tmp_path / "t.db")
    _empty_odds(db)
    monkeypatch.setattr(B, "fetch_results", lambda *a, **k: [])
    monkeypatch.setattr(B, "_rerun_scorecard", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(B, "rebuild_scenario_hits",
                        lambda cache, lib: calls.append((cache, lib)))
    rc = B.main(["--once", "--cache", db, "--tags-out", str(tmp_path / "l.md"),
                 "--scenario-lib", str(tmp_path / "s.json")])
    assert rc == 0
    assert calls == [(db, str(tmp_path / "s.json"))]


def test_rebuild_scenario_hits_empty_baseline_no_crash(tmp_path):
    # 承重墙:某场 had 赔率全 null(部分抓取)→ baseline_market 返回 truthy 但 baseline={}。
    # rebuild 不能因 max({}) 崩(否则回填每5分钟 rc=1、剧本台账冻住)。fav 退化为 None,
    # 不需热门的剧本(默契平=真打平)仍正常计;需热门的剧本(橡皮擦)非平则跳过。
    from backend.v2_predict import build_v2_prediction, record_v2_prediction
    from backend.baseline import HAD_CFG, baseline_market
    db = str(tmp_path / "t.db")
    mk = "周三053"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                 "VALUES (?,?,?,?,?,?)",
                 ("2026-06-25T09:00:00+08:00", "zucai", mk, "x vs y", "ko",
                  json.dumps({"had": {"h": None, "d": None, "a": None}})))
    conn.commit()
    conn.close()
    B.backfill(db, [MatchResult(mk, 1, 1, True)])            # 打平
    bl = baseline_market(db, mk, HAD_CFG)
    assert bl and bl["baseline"] == {}                       # 复现:truthy 但空 baseline
    # v2 标了两个剧本:默契平(不需热门)+ 死亡橡皮擦轮换(需热门)
    pred = build_v2_prediction(mk, "乱", ["默契平", "死亡橡皮擦轮换"],
                               {"had": {"baseline": {"h": 33, "d": 34, "a": 33},
                                        "deviations": []}})
    record_v2_prediction(db, mk, pred)

    lib_path = str(tmp_path / "scen.json")
    B.rebuild_scenario_hits(db, lib_path)                    # 不得抛 ValueError
    lib = {s["name"]: s for s in json.load(open(lib_path, encoding="utf-8"))}
    assert lib["默契平"]["triggered"] == 1 and lib["默契平"]["hits"] == 1   # 打平→命中
    assert lib["死亡橡皮擦轮换"]["triggered"] == 1                          # 打平→记为命中(平也算)


def test_main_scenario_rebuild_failure_returns_1_but_keeps_record(tmp_path, monkeypatch, capsys):
    # 剧本台账重建抛异常 → rc=1 + stderr,但前面已成功的 record/台账/跑分卡不被吞。
    # 锚住:rebuild_scenario_hits 在同一自愈 try 块、失败也走统一错误契约。
    db = str(tmp_path / "t.db")
    _empty_odds(db)
    monkeypatch.setattr(B, "fetch_results",
                        lambda *a, **k: [MatchResult("周四055", 2, 0, True)])
    monkeypatch.setattr(B, "_rerun_scorecard", lambda *a, **k: None)

    def _boom(*a, **k):
        raise RuntimeError("剧本台账炸了")
    monkeypatch.setattr(B, "rebuild_scenario_hits", _boom)
    rc = B.main(["--once", "--cache", db, "--tags-out", str(tmp_path / "ledger.md"),
                 "--scenario-lib", str(tmp_path / "s.json")])
    assert rc == 1
    assert "失败" in capsys.readouterr().err
    conn = sqlite3.connect(db)                          # record 仍落库,未被吞
    n = conn.execute("SELECT COUNT(*) FROM match_results WHERE match_key=?",
                     ("周四055",)).fetchone()[0]
    conn.close()
    assert n == 1
