# sporttery 开奖(FT 终比分)端点 — spike 结论

> Task 1 产出。供 `backend/results.py` 的 `fetch_results` / `parse_results` 照此实现。
> key 天然 = `matchNumStr`(组彩编号，形如 `周四055`)，与预测库同键。

## 命中端点

```
https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry
```

**注意**：不是简报里猜的 `getMatchPrizeV1.qry`，也不是 `getMatchResultV1.qry`。
- `getMatchResultV1.qry` —— 被 TencentEdgeOne **WAF 按路径硬拦**（HTTP 403 纯文本"禁止访问"，带 `x-waf-uuid` 头），换 IP / 加 cookie / 改 UA 都拦，**不可用**。
- `getMatchPrizeV1.qry` —— 过 WAF 但所有参数组合都回 `{"errorCode":"E0001"}`（不是结果端点）。
- 真正命中的是 **`/gateway/uniform/football/getUniformMatchResultV1.qry`**（来自官方"足球赛果开奖"页 `https://www.sporttery.cn/jc/zqsgkj/` 的 `jc_sgkj_gz.js` 里 `var apiUrl = '/gateway/uniform/' + channelType + '/getUniformMatchResultV1.qry?' + parStr`，`channelType='football'`）。篮球用 `getUniformMatchResultV2.qry`。

## 完整 URL + 全部参数

```
https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry?matchBeginDate=2026-06-25&matchEndDate=2026-06-27&leagueId=&pageSize=30&pageNo=1&isFix=0&matchPage=1&pcOrWap=1
```

| 参数 | 示例值 | 含义 |
|---|---|---|
| `matchBeginDate` | `2026-06-25` | 查询开赛日期下界（`YYYY-MM-DD`，比赛日非售卖日） |
| `matchEndDate` | `2026-06-27` | 查询开赛日期上界（含当天） |
| `leagueId` | （空） | 联赛过滤，空=全部联赛；世界杯在本响应里 `leagueId=72`（注意此 id 与 league 列表里的 id 体系一致但按联赛给值） |
| `pageSize` | `30` | 每页条数 |
| `pageNo` | `1` | 页码（配合 `value.pages` 翻页） |
| `isFix` | `0` | 0=竞猜型(浮动奖)；固定奖金玩法另算，回填用 0 |
| `matchPage` | `1` | football 专属，固定 1（JS 原样） |
| `pcOrWap` | `1` | football 专属，1=PC。原样照搬即可 |

**请求头**（与 `backend/sporttery.py` 同套即可，关键是带 Referer 过 WAF）：
- `User-Agent`: 桌面或 iPhone UA 均可（实测桌面 Chrome UA 通）
- `Referer: https://www.sporttery.cn/`（或 `https://m.sporttery.cn/`）
- `Accept: application/json, text/plain, */*`
- 方法：**GET**，无需 cookie。本机直连(中国住宅 IP)即可；该路径**不被 WAF 拦**。

## 响应结构 + 字段路径(原文)

顶层：`{"success": true, "errorCode": "0", "errorMessage": "处理成功", "value": {...}}`
判成功：`success == true` **且** `errorCode == "0"`（注意 errorCode 是**字符串** `"0"`，JS 里 `d.errorCode == 0` 是弱等于）。

赛果列表在 `value.matchResult`(数组)。每个元素字段：

| 语义 | 字段路径(原文) | 示例 | 备注 |
|---|---|---|---|
| **组彩编号** | `value.matchResult[].matchNumStr` | `"周四055"` | 与预测同 key，直接用 |
| 组彩编号(数字) | `value.matchResult[].matchNum` | `"4055"` | 备用 |
| **主队** | `value.matchResult[].homeTeam` | `"厄瓜多尔"` | `allHomeTeam` 为全称(本响应里两者相同) |
| **客队** | `value.matchResult[].awayTeam` | `"德国"` | `allAwayTeam` 为全称 |
| **终比分(全场)** | `value.matchResult[].sectionsNo999` | `"2:1"` | **全场最终比分**，格式 `主:客`，从此处拆 home/away goals |
| 半场比分 | `value.matchResult[].sectionsNo1` | `"1:1"` | 上半场，回填用不到 |
| **是否完赛/已开奖** | `value.matchResult[].matchResultStatus` | `"2"` | `"2"` = 已完赛已开奖；见下"完赛判定" |
| 胜平负(spf)结果 | `value.matchResult[].winFlag` | `"H"`/`"D"`/`"A"` | **不可靠**：单关/让球池未结算时为空串 `""`，但比分仍在；**别拿它判完赛或推主客胜负**，主客胜负请从 `sectionsNo999` 拆分推 |
| 派奖池状态 | `value.matchResult[].poolStatus` | `"Payout"` / `""` | `Payout`=已派奖；`""`=该池未结算(不影响比分有效性) |
| 比赛日期 | `value.matchResult[].matchDate` | `"2026-06-26"` | |
| 联赛名 | `value.matchResult[].leagueNameAbbr` | `"世界杯"` | `leagueName` 同 |
| 让球盘口 | `value.matchResult[].goalLine` | `"+1"` | 让球数(字符串) |
| 主客胜赔 | `value.matchResult[].h/.d/.a` | `"3.75"` | 历史赔率快照 |

分页字段：`value.total`(总场次)、`value.pages`(总页数)、`value.pageNo`、`value.pageSize`、`value.resultCount`、`value.lastUpdateTime`、`value.leagueList`(联赛字典)。

## 完赛 / 未完赛判定 ⚠ 重要

实测扫了 2026-06-10 ~ 06-27 共 82 场：**该端点只返回"已开奖/已完赛"的场次**——
- 返回的每一场都满足 `matchResultStatus == "2"` 且 `sectionsNo999` 非空。82 场无一例外。
- **未开赛 / 未完赛 / 未开奖的比赛根本不出现在响应里**（查未来日期区间 `matchBeginDate=2026-06-27&matchEndDate=2026-06-30` → `value.total = 0`，空列表）。所以"未完赛"**不是**"字段为空串/null"，而是**整条记录缺席**。

**给 `parse_results` 的契约建议**：
- 完赛判定 = 该场出现在 `value.matchResult` 且 `sectionsNo999` 为非空 `"主:客"` 串（可加 `matchResultStatus == "2"` 双保险）。满足即 `finished=True`，从 `sectionsNo999` split `:` 得 `home_goals` / `away_goals`。
- `winFlag` / `poolStatus` 为空串**不代表未完赛**（让球/单关池未结算而已，比分照样有效）——**不要**用它们当完赛门控，也别用 `winFlag` 反推胜负。
- 防御性兜底:若未来真碰到 `sectionsNo999` 为空/null 的行(本次 spike 区间内未出现),按 `finished=False` 处理或直接过滤。

## fixture

`tests/fixtures/sporttery_results.json` = 上面"完整 URL"(2026-06-25~27)的**真实响应**，12 场全是世界杯，含简报点名的 `周四055`(厄瓜多尔 2:1 德国)。其中 `周四057/056/052` 三场是 `winFlag=""`、`poolStatus=""` 但 `sectionsNo999` 有分的真实样本，正好供解析器测"别信 winFlag、要拆 sectionsNo999"。

## 复现命令

```bash
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
curl -s -A "$UA" -H "Referer: https://www.sporttery.cn/" \
  "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry?matchBeginDate=2026-06-25&matchEndDate=2026-06-27&leagueId=&pageSize=30&pageNo=1&isFix=0&matchPage=1&pcOrWap=1"
```
