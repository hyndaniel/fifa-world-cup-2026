# tests/test_backfill_results.py
"""赛果回填闭环编排:只录 finished + 幂等(mock fetch、临时 db、不打真网)。"""
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
    rc = B.main(["--once", "--cache", db, "--tags-out", str(tmp_path / "ledger.md")])
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
    rc = B.main(["--once", "--cache", db, "--tags-out", str(tmp_path / "ledger.md")])
    assert rc == 0
    assert calls == [db]                              # 无新赛果仍重跑一次


def test_main_fetch_failure_returns_1(tmp_path, monkeypatch, capsys):
    def _boom(*a, **k):
        raise RuntimeError("网络炸了")
    monkeypatch.setattr(B, "fetch_results", _boom)
    calls = []
    monkeypatch.setattr(B, "_rerun_scorecard", lambda cache: calls.append(cache))
    rc = B.main(["--once", "--cache", str(tmp_path / "t.db"),
                 "--tags-out", str(tmp_path / "ledger.md")])
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
    rc = B.main(["--once", "--cache", db, "--tags-out", str(out)])
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
    monkeypatch.setattr(V, "DEFAULT_OUT", str(card))   # 防真 v2_report 覆写实机 reports/预测v2.md
    monkeypatch.setattr(B, "fetch_results", lambda *a, **k: [])   # 无赛果
    rc = B.main(["--once", "--cache", db, "--tags-out", str(tmp_path / "ledger.md"),
                 "--wc-db", str(tmp_path / "nope.db")])
    assert rc == 0
    assert "配对场数: 0" in card.read_text(encoding="utf-8")
