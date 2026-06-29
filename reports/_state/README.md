# reports/_state/ — 机器态 JSON(非给人读的报告)

> 见 `docs/design/2026-06-27-7agent编排-三层边界-设计.md` §3.1/§3.3。
> 这里放**冻结的机器态快照**:一次性写入、事后不可改、零代码引用。搬非删——它们是"诚实盲测"的审计留痕。

现有:
- `v2-blind-snapshot-周四-2026-06-26.json` —— v2 当日盲测快照(冻结)。
- `intel-extracted-周四-2026-06-26.json` —— 当日抽取的确证事实快照(冻结)。

注:`reports/report_times.json`(被 `reports.py` 读)与 `reports/scenario_library.json`(被 wc-prob-v2 的 `load_library` 读)
**有 live 读者、仍留在 reports/ 根**——它们硬编码路径,搬动须同步改代码,不在本目录。
