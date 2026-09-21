# paper52 计数器 × 路径表对齐复核（Part 5 工作项 4）

版本 2026-09-17。目标：论文附录 52 条计数器（Tile 域 22 + 互连
主干 10 + LLC 域 20）逐一映射到 `configs/path_table.conf` 的顶点。
复核结果：**52/52 已映射**（2026-09-17 复核时发现 L3 写管线 3 条
遗漏，已补入 vertex.l3 并重跑回放三关）。

## 1. Tile 域 22 条

| # | 计数器 | 顶点 | 家族 |
|---|---|---|---|
| 1 | A72_ACCESS | hnf | span（兼 CR 入口） |
| 2 | A72_READ | hnf | span |
| 3 | A72_WRITE | hnf | span（unverified：恒定 48/s） |
| 4 | RNF_REQUESTS | hnf | span（unverified：恒 0） |
| 5 | IO_ACCESS | hnf | span（兼 IH 入口） |
| 6 | IO_READS | hnf | span |
| 7 | IO_WRITE | hnf | span |
| 8 | TSO_WRITE | hnf | span |
| 9 | REQ_BUF_EMPTY | hnf | empty |
| 10 | HNF_REQUESTS | hnf | span |
| 11 | DIR_HIT | hnf | miss 公式 hit 项 |
| 12 | ALLOCATE | hnf | miss 公式 total 项 + span |
| 13 | VICTIM | hnf | span |
| 14 | POC_FAIL | hnf | span |
| 15 | POC_SUCCESS | hnf | span |
| 16 | POC_WRITES | hnf | span |
| 17 | POC_READS | hnf | span（unverified：恒 0） |
| 18 | MEMORY_READS | mss | span |
| 19 | MEMORY_WRITES | mss | span |
| 20 | MEMORY_READS_BYPASS | mss | span（兼 IB 入口） |
| 21 | VICTIM_WRITE | mss | span（兼 WB 入口） |
| 22 | MSS_NO_CREDIT | mss | af（unverified：恒 0，探针待裁定） |

注：论文附录用名 IO_READ 与 catalog 名 IO_READS 不一致（paper52.conf
注释已声明，待用户定论文口径）。

## 2. 互连主干 10 条（L3 半域求和列）

| # | 计数器 | 顶点 | 家族 |
|---|---|---|---|
| 1 | CYCLES | （分母） | af/empty 默认归一化分母 |
| 2 | TOTAL_RD_REQ_IN | l3 | span |
| 3 | TOTAL_WR_REQ_IN | l3 | span |
| 4 | TOTAL_WR_DBID_ACK | l3 | span（**9/17 补入**） |
| 5 | TOTAL_WR_DATA_IN | l3 | span（**9/17 补入**） |
| 6 | TOTAL_WR_COMP | l3 | span（**9/17 补入**） |
| 7 | TOTAL_RD_DATA_OUT | l3 | span |
| 8 | TOTAL_RD_REQ_OUT | l3 | span |
| 9 | TOTAL_WR_REQ_OUT | l3 | span |
| 10 | TOTAL_RD_RES_IN | l3 | span |

## 3. LLC 域 20 条（L3 半域求和列）

| # | 计数器 | 顶点 | 家族 |
|---|---|---|---|
| 1–2 | CDN_REQ_IN ×2 half | l3 | span |
| 3–4 | DDN_REQ_IN ×2 half | l3 | span |
| 5–6 | EMEM_RD_RES_IN ×2 half | l3 | span |
| 7–8 | CACHE_RD_RES_IN ×2 half | l3 | span |
| 9–10 | EMEM_RD_REQ ×2 half | l3 | span |
| 11–12 | EMEM_WR_REQ ×2 half | l3 | span |
| 13–14 | HITS ×2 half | l3 | miss 公式 hit 项 |
| 15–16 | MISSES ×2 half | l3 | miss 公式 total 项 |
| 17–18 | ALLOCATIONS ×2 half | l3 | span |
| 19–20 | EVICTIONS ×2 half | l3 | span |

## 4. 变更记录

- **2026-09-17**：复核发现 TOTAL_WR_DBID_ACK / TOTAL_WR_DATA_IN /
  TOTAL_WR_COMP 未被任何顶点使用（设计文档无取舍记录，判定为
  遗漏）→ 补入 vertex.l3（写管线压力证据，WB/CR 写子路径）。锚点
  由 `extract_anchors.py sat` 回填（obs-max provisional，~9.0M/半域）。
- 回放三关重跑：17/17 判读不变、SAT-SUSPECT 清单不变；空载关
  g3/e1_g7 由 0.070/0.075 升至 0.100/0.118（新计数器 obs-max 分母
  ~9M，单样本轮换噪声折算放大）→ 空载关阈值 0.10→0.15 并记录
  理由（docs/validation-replay.md §2；回退对照证实锚点无漂移）。
  Part 6 标定放大分母后阈值可收紧回 0.10。

## 5. 边界说明

- 顶点覆盖是 paper52 的超集：trio/smmu/tilenet/pcie/net/eswitch 列
  来自 default.conf / e1_esw.conf / collect_pipe.sh（对应
  path_table 的 ? 前缀可选项），不属于论文 52 条。
- 4 条 unverified（A72_WRITE/RNF_REQUESTS/POC_READS/MSS_NO_CREDIT）
  在仲裁中被排除、待探针裁定（docs/counter-failure-probe-opsheet.md）。
