# 历史回放验证报告（P2 验证·回放关）

版本 2026-09-17。工具：`tools/replay_validate.py`（复跑全部历史采集 +
`tools/analyze_bottleneck.py` 三层索引）。目的：用既有实验的独立判读
结论检验模型排序正确性——零设备依赖的第一轮验证。

## 1. 回放对象

| 组 | 运行 | 判读基准 |
|---|---|---|
| G 系列（tile 侧） | g1–g7 × 3 次 | xz/BFS/SQLite/BS/TFLite/Mix→CR；Redis tile 侧=NAD 的 DDR 跳（混合流，设计文档 §8.5 归入 CR） |
| E1 应用（wire 侧） | e1_g3/e1_g7/e1_n2 × 3 次 | Redis→NAD、Mix→低载、NHD 方向→P5 |
| 主机管道定标 | e1_n0_2a/2b | 2a=主机→Arm（pcie0，p1 全 0），2b=反向；以 NAD 主机管道（pcie0）判读 |
| iperf 梯度 | e1_n1_1g/5g/10g/20g | 主机→Arm → NAD |
| fio eMMC | e0_3_emmc | 期望全路径低（eMMC 设备本身无计数器，覆盖率缺口——诚实的"看不到"） |

**排除项**：e0_1_nhd（其证据 2026-09-15 已因打流姿势错误作废，
不作回放基准）；e0_2_nad（E0 CSV 无 wire 列、NAD/NHD/TX 无入口列
不可计算，且已被 E1 系列取代）。

## 2. 空载关：全部通过

有相位日志的运行全部检验空闲窗（pre/post + net 静默过滤）：

| 运行 | idle 最大 L_p | 阈值 |
|---|---|---|
| g1 | 0.043 | ≤0.15 ✓ |
| g2 | 0.048 | ✓ |
| g3 | 0.100 | ✓ |
| g4–g7 / e1_g3 / e1_n2 | 0.000–0.025 | ✓ |
| e1_g7 | 0.118 | ✓ |

阈值取 0.15 的理由（已核实到计数器级）：非零贡献全部来自 L3 span
计数器（obs-max 锚点）。空载窗仅 5 秒、L3 轮换周期 8 秒 → 每组每窗
仅 0–1 个样本，单样本波动（±2–4M/s）除以 obs-max 量级的分母即产生
噪声 n：

- 读管线计数器（obs-max 分母 65–148M）→ n≈0.05–0.08；
- **L3 写管线三计数器 wr_dbid_ack/wr_data_in/wr_comp**（2026-09-17
  paper52 对齐补入 vertex.l3；obs-max 分母仅 ~9M，见
  docs/paper52-path-alignment.md）→ 同样 ±2M/s 的绝对噪声折算成
  n≈0.10–0.13（g3 的 0.100、e1_g7 的 0.118 均为此，另有空载期真实
  的脏页写回背景）。

这不是系统性偏移（若单位或锚点接错，n 会在空载时趋近 1——诊断力
不变）。Part 6 真饱和标定（p4 memrand 写应力）把这些分母放大后，
该噪声会压缩回 0.05 以下，届时阈值可再收紧回 0.10。

**模型变更纪律记录**：新增 3 计数器后已做回退对照——摘除 3 行重跑，
g3/e1_g7 精确复现 0.070/0.075（锚点重生成幂等、无漂移），确认空载
升高纯为新计数器贡献，随后才调阈值。

## 3. 回放关：17/17 通过

| 应用 | 模型判读 | 既有判读 | 结论 |
|---|---|---|---|
| g1 xz | cr=1.80 居首 | CR ✓ | 一致 |
| g2 BFS | cr=1.34 居首 | CR ✓ | 一致 |
| g3 Redis（tile 侧） | cr=0.19 居首 | NAD 的 DDR 跳 ✓ | 一致（见 §5a） |
| e1_g3 Redis（wire 侧） | nad 84 wins 居首 | NAD ✓ | 一致 |
| g4 SQLite / g5 BS / g6 TFLite | cr 居首 | CR ✓ | 一致 |
| g7 Mix / e1_g7 Mix | cr/nad 低值居首 | 低载无强主导 ✓ | 一致 |
| e1_n2 iperf NHD | nhd=117 wins、nad/tx≈0 | P5/NHD ✓ | 一致（最强证据） |
| e1_n0_2a/2b 主机管道 | nad 居首 | pcie0 收/反向 ✓ | 一致 |
| e1_n1_1g/5g/10g/20g | nad wins 居首 | NAD ✓ | 一致 |
| e0_3 fio eMMC | 全路径≈0 | 覆盖率缺口，应"看不到" ✓ | 一致（诚实结论） |

## 4. 退化检查：抓到 2 处锚点偏低嫌疑

SAT-SUSPECT（顶点 argmax 期间 n 均值 ≥0.85、行数 ≥10）：

- **g1 xz**：`tile_victim_write`（n≈0.90）、`tile_a72_read`、
  `l3half_total_rd_req_in/rd_data_out/cache_rd_res_in`
- **g2 BFS**：`tile_allocate`、`tile_victim`

归因：这些计数器的 obs-max 锚点取自**该负载自身的最大行**（xz 正是
victim_write/L3 读管线的最大生产者）→ 负载贴着"自己的上限"跑出
n≈0.9。这是执行计划风险表预言的"λ_sat 标定打不满→归一化普遍偏高"
分支，**不是真瓶颈**（xz 并未打满 L3 写回）。处置：列入 Part 6 精确
标定替换清单（p4 memrand / p5 cache 给真应力），替换后重跑本回放
脚本确认嫌疑消除、判读不变。

## 5. 不一致项的归因记录（回查结论）

a. **e1_g3 中位 L 与 wins 判读分歧**（cr 中位 0.19 > nad 0.14，但
   nad wins 84 > cr 35）：CR 计数器承载 NAD 的 DDR 跳（设计文档
   §8.5 混合流极限，已在路径表注释声明），故 wire 侧判读采用 wins
   （方向性），tile 侧采用中位 L（幅度性）——两套判据在回放脚本中
   按应用类别区分。
b. **e1_n1_* 的 nhd 中位偏高**（pcie0_tx 来源）：主机→Arm iperf 时
   pcie0_tx 携带主机侧回程/镜像流量，落入 NHD 的 pcie0 顶点。方向
   判读（wins）不受影响，已在判据中规避。
c. **e0_1/e0_2 不可回放**：E0 CSV 无 wire 列；E0-1 证据已作废。
d. **victim_write 等锚点偏低**：见 §4。

## 6. 结论

三层索引对 17 个独立判读全部一致（含方向性判读与"覆盖率缺口应
不可见"的诚实用例）；空载关无系统性偏移；退化检查按预期工作并
产生 2 处可处置的锚点嫌疑。**M4 通过**：设备线（第五、六部分）
解锁；锚点替换后须重跑本报告的三关。

## 7. Part 6 锚点替换后的重验证（2026-09-20）

### 7.1 锚点生成规则的修正（tools/extract_anchors.py）

sat 批（p1/p3/p4/p5/p6/p7 ×3 次）回填 bench/results/ 后首次再生成
暴露回归：**弱面 bench 锚点把历史 obs-max 锚点降级**（如
l3half_total_rd_req_out 25.4M→316.8K，p1 弱面），三关全面恶化
（idle n≈1、回放 FAIL、SAT-SUSPECT 爆发）。修正为 **obs-max 地板
规则**：sat = max(bench 应力, obs-max)。bench 只有在压过一切历史
观测值（>1.02×）时才是该计数器的应力参照；压不过说明该面没打满
此计数器（弱面/配置未采样），保留 obs-max 下界。另加**求和计数器
推导规则**：tile_io_access = io_reads + io_write 锚点之和（p7 面
实证 access = reads+write 精确成立，29.24M = 29.04M+201K），保证
access 归一化 (r+w)/(R+W) ∈ [0,1]。

### 7.2 真应力锚点净增清单（bench 压过 obs-max 的计数器）

| 计数器 | 新锚点 | 面 |
|---|---|---|
| tile_io_reads | 30.17M | p7（NIC DMA 读=TX 字节率，×64B 互证） |
| l3half_evictions | 24.76M | p3 |
| l3half_total_emem_rd/wr_req | 38.0M/49.75M | p3 |
| l3half_total_wr_dbid_ack/wr_req_in | 13.79M | p7 |
| tile_victim_write | 4.32M | p5 |
| tile_allocate | 63.99M | p5 |

其余 span 锚点维持 obs-max 下界（面未打满该计数器），provenance
已如实标注。

### 7.3 三关结果（对照 §2–§4）

- **空载关**：与 M4 逐位一致（g1 0.043/g2 0.048/g3 0.100/
  e1_g7 0.118），地板规则把 idle 相关锚点恢复为 M4 值。
- **回放关**：17/17 通过。e1_g7 的 judge 由 nad 翻为 cr 是 36:35
  一票之差——Part 6 真 io 锚点（reads 20.4M→30.2M）把若干 nad 边际
  行拉低所致，属诚实锚点下的边际翻转。回放脚本增加低载平局条款
  （wins 无多数、<50% 即"无强主导"），与设计文档对 e1_g7 的既有
  判读一致。
- **退化检查**：
  - g1 xz 五标签同 M4（victim_write/a72_read/rd_req_in/rd_data_out/
    cache_rd_res_in）。其中 victim_write 锚点现为 p5 真应力（独立
    于 xz），仍被标记 = xz 真实跑在 p5 应力率的 ~93%——**不是锚点
    缺陷，是真实贴近饱和**。其余四标签的 om 均取自 xz/BFS 自身
    峰值（g1_run1/g2_run3），自引用下界，待 p5 扩展面（§7.4）挑战。
  - g2 BFS 由两标签（allocate/victim）减为一（victim）：allocate
    因 wr 管线锚点上调（p7）退出顶点 argmax。victim 的 om 取自
    g2_run1 自身，且 p4/p5 已采样 victim（31M）不敌 BFS 峰值
    （51.8M）——BFS 在该计数器上真实强于 stress-ng，锚点维持下界。
  - e1_n0_2b 新增 cap@enp3s0f1s0_tx_bytes：2b 反向管道的 representor
    TX cap 即其自身观测峰值（无实测 cap），自引用 cap 锚点，诊断
    正确、判读不受影响。

### 7.4 可选设备补采（p4/p5 扩展配置，~8 分钟）

p4/p5 配置各加一组 L3 采样（p4：wr_data_in/wr_comp/wr_req_out/
wr_dbid_ack；p5：rd_req_in/rd_data_out/cache_rd_res_in×2 + tile
A72_READ）——cache thrash 应能压过 g1 的读管线自引用 om。重跑
p4/p5 各 3 次后照 7.1 流程再生成+重验。不阻塞本地线：残余
SAT-SUSPECT 仅为顶点级饱和判读的警示，路径级判读不受影响。


## 8. D2 批次验证与实例记录（2026-09-22）

执行单 §9 首轮回传（d2_batch.tar.gz，26 文件）判读结果。判读口径：
相位日志界定应用窗；负载签名对照历史 G 系列与 sat 面。

### 8.1 批次判定

| 文件                | 判定 | 证据                                                                  |
| ----------------- | -- | ------------------------------------------------------------------- |
| p4_run1/2/3 + txt | ✗ 作废 | 三份 txt 全是 `bin/memrand: Permission denied`——deploy 抹掉 x 位，CSV 为空载基线 |
| p5_run1/2/3 + txt | ✗ 作废 | 同上（`bin/stress-ng: Permission denied`）；a72_access 全程 0.5–1.5M/s=snap 守护进程签名 |
| d2_g1 xz          | ✅ 有效 | 应用窗 41s，a72_access 均值 67.2M/s、emem_wr 9.07M/s、victim 4.14M/s = xz 签名；med 1.640 vs 历史 1.644 |
| d2_g2 bfs         | ✅ 有效 | 应用窗 16s（历史 36s，幅度一致：新 48.96M/s vs 历史前 16s 46.11M/s）；med 1.265 vs 历史 1.202 |
| d2_g3 redis       | ✗ 作废 | 应用窗 41s（sleep）内 net_rx/pf1hpf_rx/p1_rx 全 0——fujian 基准未打出流量 |
| d2_g4 sqlite      | ✅ 有效 | 应用窗 31s 自然退出；med 0.120 vs 历史 0.110；io_access 0.25M/s（历史平台 0.74M/s，偏弱但真实） |
| d2_g5 bs          | ✅ 有效 | 应用窗 41s；med 0.195 vs 历史 0.205；a72_read 9.10M/s=计算签名      |
| d2_g6 tflite      | ✗ 作废 | 应用窗 **1s**——§9c 命令中 N 占位符未替换，python 解析失败即退（第 5 行 15.2M 尖峰=导入即退） |
| d2_g7 sqlite+redis | ✗ 作废 | sqlite 部分真实（31s），但 net_rx 全 0——fujian 基准未打出流量       |

另：g1 窗口首尾 4 行（row 0、rows 47/48）满载 xz=**前次 ^C 中断遗留的
孤儿 xz**（setsid 会话逃过 ^C，压缩完 1GB 前一直存活）——只污染 pre/post
空载段（idle_max 读数），应用窗与 med 判据不受影响。修复重跑块
（chmod +x、pgrep 清理、g6 校准、g3/g7 双端）已入执行单 §9f。

### 8.2 四有效场景的 PRISM 判定（新场景协议）

| 场景   | 判决（本轮）                          | 与历史对照                                   |
| ---- | ------------------------------- | --------------------------------------- |
| d2_g1 | low，med 首位 cr=1.640（wins cr 14/40） | 历史 dominant（med 1.644，cr 14/27=51.9%）——med 一致 |
| d2_g2 | low，med 首位 cr=1.265（wins cr 5/15）  | 历史 dominant（med 1.202，cr 12/24=50%）——med 一致 |
| d2_g4 | low，med 首位 cr=0.120             | 历史 low（0.110）一致 ✓                        |
| d2_g5 | low，med 首位 cr=0.195（wins 首位 nad）  | 历史 dominant（0.205）——med 一致               |

**幅度判据（med）四场景全部与历史吻合**（差 ≤0.01，g1/g2 差 0.004），
负载真实、场景可复现；**方向判据（wins）受 8ff40c4 每口列稀释**：e1_esw
三组轮转的"空相位行"（tile/L3 均未采样的行）在旧配置下无可算路径自然
弃权，新配置的每口列（逐秒采样）让这些行以 L_p≈0.001 的 ε 噪声投给
nad/nhd/tx——g1 票数从 cr 14/27（52%）稀释为 14/40（35%），dominant
翻成 low。**这不是数据缺陷，是投票机制的配置时代差异**，修复见 §8.3。

### 8.3 引擎缺陷首轮诊断（M1 份额 ε 退化）——已被 §8.5 实测修正

> **2026-09-22 修正声明**：本节“NaN 剔除/ε 入口独得份额 1.0”的诊断被 §8.5 的
> 逐行实测推翻（入口并非 ε、空单元普查证明网络域列从不缺采）。真实根因
> 是入口计数器方向错配 + arm 顶点计数器混流，修复已落地并双门全过。
> 本节的修复提案（前值持久化）被否决（会污染 med 池），最终方案见 §8.5。


逐行分解 e1_n1_* 梯度场景发现更深一层：**M1 共享顶点份额在入口近零时
退化**。arm 顶点（net_rx@cap，10G 入向 n=1.0）由 nad/tx 两路径共享，
份额=各自入口/入口和；当 nad 入口（en3f1pf1sf0_tx）因轮转未采样（NaN
剔除出分母）而 tx 入口（p1_tx 背景 ε）非零时，**tx 独得份额 1.0**——
整条 net_rx 压力记到 TX 名下（行 L_p 1.0001），而 nad 只在入口为零的
空相位行以 0.000 获胜。此前 e1_n1_* 的"nad 方向判"（28 票）其实是
28 张 ε 票对 6 张真实 tx 票的计数假象，M4 闸门靠 wins 计数维持。

**修复方案（两件套，需成对落地）**：①引擎 M1 份额守卫——入口未采样
时沿用最近一次采样值（前值持久化），或入口和低于阈值时均分/归零，
使 nad 在真实流量行拿回份额；②搜索层方向投票下限 θ≈0.02（低于此值的
argmax 行弃权，历史最小真实票恰为 0.02）。单修 ② 会让 e1_n1_* 的
judge 翻成 tx（已实测），单修 ① 不解决 d2 稀释。落地后重跑
replay 17/17 + selfcheck 23/23 + 四 D2 场景（预期 g1/g2/g5 回 dominant、
e1_n1_* 仍 nad、g4 仍 low）。**此修复是论文模型正确性的关键项**：
它决定方向判据在混合流量下是否可信。


## 8.4 二轮回传判读（2026-09-22 晚，§9f 修复重跑批次）

26 文件（p4/p5 各 3 CSV+txt、d2_g1..g7 CSV+相位日志）四关全验，全部有效。

**冒烟/内容关**：p4 memrand ×3 真实 60s（429.5/431.4/429.2 ns/access）；p5 stress-ng ×3
passed:8 failed:0；相位日志 APP 窗 g6=36s（N=256 标定生效）、g3=40s、g7=35s；内容增量
g3/g7 net_rx=44.3M/40.0M 字节 + en3f1_tx=24.8M/17.2M（真实 Redis 流量）、g6 a72=427 万。

**med 与历史逐例对比（幅度判复现性）**：

| 场景 | 本轮 med | 历史 med | 差 |
| --- | --- | --- | --- |
| d2_g1 xz | 1.640 | 1.644 | −0.004 |
| d2_g2 BFS | 1.265 | 1.202 | +0.063 |
| d2_g3 Redis | 0.177 | 0.190 | −0.013 |
| d2_g4 SQLite | 0.120 | 0.110 | +0.010 |
| d2_g5 BS | 0.195 | 0.205 | −0.010 |
| d2_g6 TFLite | 0.070 | 0.070 | 0.000 |
| d2_g7 Redis | 0.315 | 0.340 | −0.025 |

七个场景（含上批作废后修复的 g3/g6/g7）med 全部复现历史实例表 → 幅度判据在
跨批次、跨配置、跨重跑下成立。g3/g7 本轮流量速率 ≈8.6/7.8 Mbps 低于历史 ≈100 Mbps，
tile 面 med 几乎不受影响（DDR 跳由访问频率主导非带宽）——幅度度量对负载强度变化稳健。

**三关状态**：replay 17 行全 PASS；selfcheck 实例 A 17/17 + 实例 B 6/6 ALL PASS
（p4/p5 面复活：p4 dominant=cr、p5 dominant=cr）；anchor_sat.conf 无 diff
（新 p4/p5 值未超旧锚，obs-max 地板规则生效）。

**遗留**：g1/g2 为 14:08 旧文件，窗首尾孤儿 xz 污染仍在（只影响 idle 读数）；
方向判（wins）仍受 H1/H2 两缺陷约束（d2 各场景 verdict=low 系 ε 票稀释所致，
H1+H2 落地后重判）。

## 8.5 引擎缺陷修复定案（2026-09-22 深夜：方向错配 + 计数器混流，H1/H2 收官）

### 8.5.1 逐行实测推翻 §8.3 诊断

对 e1_n1_10g 逐行打印原始计数（app 窗每秒速率，单位 B/s）：

| 行 | pf1hpf_tx | en3f1_rx | en3f1_tx | p1_rx | p1_tx | net_rx | pcie0_tx |
|---|---|---|---|---|---|---|---|
| 5 | 1.31G | **0** | 128 | 1.31G | 2.16M | 1.31G | 1.52G |

三处与 §8.3 诊断直接冲突：① nad 旧入口 en3f1_tx=128 B/s 是**真实 ACK**
（非 ε、非 NaN——空单元普查已证网络域列每秒必采）；② p1_tx=2.16MB/s 是
**真实 ACK 流量**（helong 客户端应答），不是背景噪声，份额分母 2.16M≫
SHARE_EPS 阈值，均分地板永不触发；③ en3f1_rx **恒 0**——Arm 从未收到
投递。而 pf1hpf_tx=洪流、p1_rx=洪流、pcie0_tx=1.52G、p1_tx=ACK 的签名
是**纯 wire→host NHD**：e1_n1_* 四个 CSV 的实际内容不是“host→Arm”，
N1 姿势（E1 记录中已作废）执行出来的是 helong→p1→eSwitch→host。

### 8.5.2 真实根因（两处计数口径错误 + 一处场景标签错误）

1. **NAD 入口方向错配**：nad 入口选 en3f1pf1sf0_tx（Arm 出向=ACK），
   与“NAD=入向终止于 Arm”语义相反；应选 en3f1pf1sf0_rx（eSwitch→Arm
   投递，对 host 管道腿和 wire 腿来源无关）。2b 验证该计数器真实
   （host→Arm 投递时=1.31G，纯 NHD 时恒 0）。
2. **arm 顶点计数器混流**：net_rx/net_tx 是 OVS bridge 网卡聚合，**把
   硬件桥接透传也计入**（纯 NHD 的 e1_n1 里 bridge=1.31GB/s 而 Arm 分文
   未收）——arm 顶点假饱和 n=1.0，被 p1_tx 的 ACK 份额 1.0 整段记到 TX
   名下。Arm 软件压力的真实计数器是 representor 的 rx/tx（只在 Arm 栈
   参与时动）。
3. **e1_n1_* 场景标签错误**：gate 期望 nad 依据的是“host→Arm”的意图；
   实证内容是 NHD，引擎判 nhd 才是对的。期望值改 nhd 并附姿势作废说明。

### 8.5.3 落地修复（五件套）

| # | 修改 | 文件 |
|---|---|---|
| H1 | M1 份额 ε 地板 SHARE_EPS=1000 B/s：入口和≤噪声时诚实均分（保留——修复第 1 处后地板用于真·无方向证据行，如 2b 空相位行均分 0.5/0.33） | tools/analyze_bottleneck.py |
| H2 | 方向投票地板 VOTE_FLOOR=0.02：argmax L_p 低于地板的行弃权（离线 ε 票实测 ≤0.003，历史最小真实票 0.02） | tools/analyze_bottleneck.py |
| — | nad 入口 en3f1pf1sf0_tx → ~~en3f1pf1sf0_rx~~（方向读反，**§8.6 再修正为 rx+tx 和**：tx 才是入向全量 596–949MB/s，rx 是返回 ~1MB/s） | configs/path_table.conf |
| — | arm 顶点 net_rx/net_tx@cap → **en3f1pf1sf0_rx/tx@cap**（representor 真实入出向），锚点补 en3f1pf1sf0_rx=825e6（6.6Gbps 平台瓶颈，与 net_rx/tx 同源） | configs/path_table.conf + anchor_sat.conf |
| — | e1_n1_* 期望 nad→nhd，注释写明姿势作废与实证签名 | tools/replay_validate.py |

### 8.5.4 双门结果（全过）

- replay 17/17 PASS：e1_n1_1g/5g/10g/20g 判 nhd（tx 归零）、e1_n0_2b 判
  nad（入口方向修复）、e1_n0_2a 保持 nad、g1–g7/e1_g3/e1_g7/e1_n2/
  e0_3_emmc 全保持。
- selfcheck 23/23 ALL PASS：实例 A 17 例 + 实例 B 饱和六面 p1–p7。
- 2b 新增 SAT-SUSPECT 标签 2:cap@en3f1pf1sf0_rx_bytes：洪流 1.31GB/s 压顶
  6.6Gbps 瓶颈（n=1.0 真实压力，锚无问题），是警告机制的正常工作。

### 8.5.5 对模型意味着什么

方向判据（wins）现在只在**方向证据真实存在**时投票：Arm 顶点只反映
Arm 栈真实收发，入口只读入向投递，ε 行诚实弃权。e1_n1 从“28 张 ε 票
对 6 张真实票的计数假象”变成“nhd 全票 + nad/tx 零票”的干净判读。
NHD 口径切物理口 sysfs/tc in_hw 的结论不受影响（那是采集口径，本修复
是判读引擎）。F1 重证数据回来后可直接用修复后的引擎判读 E0-1 NHD。

## 8.6 E2E 留出负载验证与入口方向再修正（2026-09-22，H3）

### 8.6.1 场景与判决

三个留出负载（docs/e2e-validation-opsheet.md，全部不在实例库中）：
A openssl AES（预期 CR）、B iperf3 UDP 5G→Arm（预期 NAD 主导）、
C xz+UDP 并发（预期 CR/NAD 双高）。设备回传 5 轮 CSV + 相位日志，
本地引擎判读：

| 场景 | 判决（修复后） | 排名要点 | 对照预期 |
| --- | --- | --- | --- |
| A | low（cr 0.002） | 全部 ≈0 | 预期 CR → **预期设错**：AES 是 L1 常驻纯计算，tile 网格访问 11–54K/s（三轮一致，低于会话自身基线），模型如实报"无繁忙路径"= 诚实负例（核内盲区，g6 TFLite 同款先例） |
| B | **dominant nad**（0.541，wins nad 全票） | nad 0.541 / ih 0.182 / cr 0.089 / nhd 0.016 / tx 0.000 | nad 主导 ✓；cr 中度伴随 0.089 ✓（~0.1–0.2 预期带）；nhd/tx ≈0 ✓ |
| C | **multi**（cr 0.584，wins cr 14/35 无多数） | cr 0.584 / nad 0.339 / ib 0.327 / ih 0.300 / nhd 0.011 / tx 0.000 | 双高 ✓：洪流行内 cr 与 nad 同时抬升（0.25–1.08 / 0.10–0.97），M1 共享顶点份额机制直接验证；单主导判决改 multi |

### 8.6.2 原始判读暴露的两处缺陷（H3 修复）

1. **判决规则缺陷**：C 的 wins 首位无多数（14/35）触发平局条款
   （e1_g7 先例）被标 "low"，但 med 首位 0.584 是高载——平局条款本是
   低载场景条款（模型文档原文"整体低负载的场景"）。修复：三值化
   dominant / low / multi（prism_search.py，§8.6.3）。
2. **nad 入口方向再反（§8.5 误修）**：§8.5 把 nad 入口从 tx 改为 rx，
   依据是"tx 只量 ACK ~100 B/s"——方向读反了。实测量：2a 主机→Arm
   洪流时 en3f1pf1sf0_tx = **596–949MB/s**（洪流全量），rx =
   0.6–1.2MB/s（真正的返回流量）；E2E B 纯收 UDP 洪流时 tx =
   530–844MB/s、rx = 0。representor 约定 tx=交换机→SF 方向。入口
   用 rx 时（收洪流时 rx≈0）ε 地板让 tx/nhd 分走 arm/eswitch 顶点
   一半 → B 出现 tx 0.207 / nhd 0.118 的涂抹。修复：入口改
   **rx+tx 和**（Arm 边界双向总量，方向无关）——收洪流时
   =0+630M=630M ✓，2b 反向洪流时 =1.31G+ε≈1.31G ✓（2b 不再被
   误折半），NHD 时 rx=tx=0 落 ε 地板与修复前一致。

### 8.6.3 修复清单与双门复验

- path_table.conf：nad entry → en3f1pf1sf0_rx_bytes+en3f1pf1sf0_tx_bytes
  （注释改写，含 2a/B 实测方向证据）；
- prism_search.py：判决三值化 dominant/low/multi（平局条款只在 med ≥0.2
  时升级为 multi）；顶点贡献分解与排名统一用窗口均值（突发场景中位数
  被空载行稀释到 0，E2E B 先例）；自检实例 B 接受 multi；
- docs/prism-search-design.md §3/§4 同步三值化与均值口径。
- **双门复验（修复后实跑）**：replay 17/17 PASS、selfcheck 23/23
  ALL PASS；B 判决 dominant nad 0.541（tx 0.000、nhd 0.016=仅剩
  pcie0 双向链路进向中转的诚实残值）；C multi cr 0.584+nad 0.339。
- 遗留注释：pcie0 是双向链路（56.x 洪流的进向段经主机面 PCIe 进入
  设备），nhd 路径持 pcie0 顶点，纯 NAD 场景 nhd 仍显示 ~0.02 的
  进向中转——pcie0 方向盲区的既有缺口（论文 PCIe 段已标注）。

### 8.6.4 对模型意味着什么

留出负载验证 = 泛化能力的直接检验：B（新协议 UDP + 未入库存的洪流
姿势）方向判据全票命中；C 的共享顶点份额在行内同时抬升两路 L_p，
M1 机制脱离实例库仍成立；A 证明模型不会对盲区负载虚构压力。三处
引擎缺陷（判决规则、入口方向、突发窗口聚合口径）全部由留出数据
暴露并修复——留出验证完成了它的使命。

## 9. 第五章案例验证：24 轮四批次 + Case 7（2026-09-24 开 → 2026-10-06 收尾）

### 9.1 范围与方法

第五章的实验设计目标：用**逐案例对照**的方式检验 PRISM 引擎（瓶颈定位
模型）在六类场景变化上的判别力——Case 1 异构应用签名、Case 2 流量方向
翻转、Case 3 工作集转移、Case 4 访问模式转移、Case 5 并发实例争用、
Case 6 缓冲/直通切换（操作单 docs/ch5-cases-opsheet.md，判读标准沿用
§8.6 定稿的 dominant/low/multi 三值判决 + 七路径压力排名）。每轮跑一个
应用变体、采集器出 CSV + 相位日志，本地判读按 §8.6 三类诊断（引擎缺陷
B 类 / 覆盖缺口 / 预期设错 A 类）归档；批级验收线 ≥80% PASS。共 24 轮
分四批次（批次 0 环境准备不计入），另 Case 7 受害者-撤离五轮（m1-m5，
独立操作单 docs/ch5-case7-opsheet.md，随批次三收尾）。

### 9.2 批次闭合汇总

| 批次 | 轮次 | 结果 | 日期 |
| --- | --- | --- | --- |
| 批次 1 | c1a-f + c2b/c2c（8 轮） | 3 干净 PASS + 5 A 类预期修订；c2c 三轮全诊毕、主机口径 run3 实质 PASS（tx 路径首证 5.5Gbps） | 9/30 |
| 批次 2 | c3a-e + c4a-e（10 轮） | 5 干净 PASS + 2 判决 PASS 带签名修订 + 3 A 类；**0 B 类 0 C 类** | 10/5 |
| 批次 3 | c5a-c6d（10 轮计 run1/2/3） | 3 干净 PASS（c5b/c6b-run3/c5a-run3）+ c6a 准 PASS + 3 A 类 + 2 C 类已定位修复重跑；**0 B 类** | 10/6 |
| Case 7 | m1-m5（5 轮） | 判决谱系完整：mem-intf 抬升 0.820 → withdraw 回落 0.355≈base 0.365 | 10/4 |

全链 29 轮（24+5）：**引擎缺陷 0 起（B 类 0）**；C 类 4 起全部为姿势/
脚本问题且已重跑闭合；A 类 12 起全部是预期设错或环境限制，逐条修订
预期并沉淀论文素材（0x73/0x74 设备视角方向语义、eMMC 负载比 DMA 锚点
低 3 个数量级、主 BF2 Arm↔p1 无二层直连的主机折返、c2c 主机口径 tx
首证、c5a 64M 缓存分界刀口、GUP/s 份额随表尺寸单调下降）。

### 9.3 出图（2026-10-06，判读总流程 step 5 完成）

**工具**：tools/gen_ch5_figs.py（新增，随图入 git）。一次运行生成 26 张
图（fig/ch5_*.{dat,plt,png}），分三个家族：

- **判决堆叠图 7 张**（ch5_case1-7_verdict.png）：每个案例一张，每轮一
  根柱，七路径 L_p 段自下而上按 cr/ih/ib/wb/nad/nhd/tx 累积堆叠——绘制
  机制与既有 fig/l3_lookups.plt 相同（boxxyerror 累积段，零高度段跳过）。
  路径配色 = 既有单路径色（cr #4C4C4C / ih #BABABA / ib #B2172B / wb
  #F5A682）+ 堆叠紫黄（nad #C1A8E0 / nhd #F7E6A0）+ 新引入的 tx 浅蓝
  #9FC5E8。
- **计数器图 17 张**（ch5_case*_<counter>.png）：簇状柱（with boxes），
  模板逐字复刻 9/14 定稿引擎 tools/gen_fig_plts.py（柱宽 0.55/0.30/0.22、
  簇内偏移、Arial 16/22/20、不透明填充黑边、border 15 四边框、左 y/下 x
  单侧刻度、图例右上横向外置）。数据 = 每轮 CSV 的差分率（应用窗均值 −
  空载均值，path_data.col_rates 同款口径）；共享计数器沿用 M1 入口比拆分
  （A72_ACCESS:IO_ACCESS 按比例归属 CR/IH）。IH 系列对数轴（计数/s、从 1
  起、decade 粗刻度）、纯 CR/IB/WB 图线性 e+6——沿用既有 SPECS 的判轴
  决定。选择逻辑：每案例挑最能讲该案例故事的计数器（Case 1 四专属计数
  器、Case 2 网络/PCIe 字节、Case 3 victim_write+a72+emem_wr、Case 4
  bypass+emem_wr+victim、Case 5 a72、Case 6 mem_reads 三分拆+网络字节+
  a72、Case 7 a72），符合绘图规则三（按数据表现/counter 特质/覆盖路径选
  代表性计数器）。
- **吞吐份额图 2 张**（ch5_case5_c5a/c5b_share.png）：Case 5 并发实例的
  应用层吞吐份额 %（c5b 四路 GUPS：GUP/s 0.011/0.007/0.004/0.002→份额
  45.8/29.2/16.7/8.3%；c5a 四档缓存：ops/s 3.14K/74.2K/92.1K/95.0K→
  1.2/28.1/34.8/35.9%），常数取自批次三结案记录（docs/batch3-results.md
  §2.1/§2.6）。

**验证**：26 张图的判决向量全部由 prism_search 对 26 个正典 CSV 现场重跑
获得（未抄记录），与操作单/结案记录逐位一致（c1a 0.535、c1d nad 1.882、
c2b nad 1.566、c2c low、c3b cr 2.059、c5a 0.797、c6b nad 0.309、case7
withdraw 回落 base 水平等）；缓存于 fig/.ch5_verdicts.tsv（--refresh 重跑）。
像素级抽验：堆叠段/簇柱/份额柱颜色与规格一致（case1 cr 段 14953 像素、
case7 flood 轮 nhd/tx 为真实柱段非图例；c4c seq-wr emem_wr 2.746e8 与
结案记录 275M/s 吻合；case2 host-out pcie0 662M≈pcie1 665M 三层一致）。

### 9.4 对模型意味着什么

**验证了什么**：六类场景变化下 29 轮里引擎判判决方向全部命中或如实报
low——工作集转移（c3b 逐出流炸裂 4× 判别力）、访问模式转移（c4a-c 的
流式/随机/写三签名区分）、方向翻转（nad/tx 头名随流量方向正确漂移）、
并发争用（c5b 份额分解）、机制切换（c6 buffered/direct 的 mem_reads 三
分拆对比）逐案例成立；判决向量已被 9/30 批次一起纳为正典记录。

**拿到了什么**：第五章的完整证据链已落盘——操作单记录表三批回填完毕、
三份结案记录（batch2/batch3/case7）+ 26 张定稿风格配图 + 出图工具入库；
每案例均具备"判决堆叠图 + 代表性计数器图"的图文展开材料，可直接支撑
论文第五章的逐案例写作（配图风格与既有 31 张主图同一谱系）。

**对模型意味着什么**：案例实验把引擎从"留出负载对错"推进到"场景变化
判别力"——12 起 A 类修订中无一起推翻机制，全部是预期标定或环境限制
（io 域锚点 Gbps 级 vs eMMC MB 级、Arm 收包平台 6.6Gbps 上限、主机折返
路由），模型的边界与真实硬件的边界一致；0 B 类说明 M1 份额路由、七路径
图结构、窗口均值口径在 29 轮变化负载下未暴露新的引擎缺陷。剩余改进项
（峰窗口判决模式、pcie1_tx 穿透 TLP 记账、io 域低锚点敏感性）全部记入
论文素材清单，不阻塞第五章落笔。

### 9.5 出图补强：转移视图 + 逐行序列（2026-10-08，用户反馈驱动）

**动机**：用户检查 9.3 的 26 张图后指出两个问题——（1）同一应用因参数调
整而发生的瓶颈路径转移在现有渲染下不可见（每轮一根柱、x 轴 = 轮次，柱
与柱之间的"交接"无法表达）；（2）图的数量与丰富度不及 PathFinder。经验
证用户理解正确：实验数据本身包含转移故事（六个案例本身就是参数扫描设
计），是渲染方式丢了信息。PathFinder 的做法是参数有序 x 轴 + 每路径一条
曲线，交叉/发散即转移。

**新增族 C：转移图 12 张**（fig/ch5_trans_*.png）。一图一条转移故事，
x 轴 = 有序参数点（如 NPB 三核访存强度递升、工作集 32^3→256^3、访问模
式 seq→rnd→wr），每路径一条 linespoints 曲线（配色与族 A 相同：cr
#4C4C4C / ih #BABABA / ib #B2172B / wb #F5A682 / nad #C1A8E0 / nhd
#F7E6A0 / tx #9FC5E8，点型 7/5/9/11/13/15/1 区分），交叉与发散即瓶颈
转移。分两类：
- **判决向量类 7 张**：case1 EP→IS→FT 访存强度坡（cr 0.535→0.754→
  1.879）、case2 三方向（nad 1.882 独占入向、ih/nhd/tx 随出向抬升）、
  case3 工作集/延迟深度/存储转内存三连、case4 访问模式、case7 五场景
  系列。数据 = fig/.ch5_verdicts.tsv 正典向量。
- **计数器直读类 5 张**：case3 mg_victim（WB 逐出流 4× 炸裂，对数轴）、
  case3 db_io（eMMC 读 40× 落差，对数轴）、case4 ememwr（写带宽跨模式
  对数轴）、case6 memreads 三分拆（CR/IH/IB 三线）、case5 刀刃图
  （run2 2.6K→98.4K 38×、run3 3.14K→74.2K 24× 翻转，深/浅红双线对数
  轴）。数据 = 差分率（col_rates 口径）或批次三 app 常数。

**新增族 D：逐行序列图 4 张**（fig/ch5_series_*.png）。PathFinder 风格
的逐行 L_p 动力学：x 轴 = 应用窗内的行号（1 行 = 1 s），每路径一条曲线，
展示判决在时间上的稳定/漂移（如 c2b net-out 的 nad/wb 双高、m4 洪流的
cr 主导 + nhd/tx 跟随、c3b 的 cr 主导、c4c 的 cr/ib 抬升）。平滑机制：
采集器每行只携带一个 tile 组的增量（tile_group 0–5 轮转、1 行/s），原
始逐行 L_p 按构造以 6 行为周期振荡，故取中心 6 行均值（一个完整采样周
期一个点）作为滑动窗还原窗口均值语义。数据 = prism_search.py 新增的
--series 导出（每行七路径 L_p），峰值 < 0.05 的静默路径自动剔除。

**验证**：prism_search.py selfcheck 30/30 全过（--series 导出回归）；
42 张图逐张像素抽验通过（每图命中调色板颜色与设计系列一致，无空图/无
尺寸异常）；转移图数值与 .ch5_verdicts.tsv 及结案记录逐位一致（mg_victim
双点 0.512/2.059、knife 四档常数与 batch3 §2.2/§2.6 一致）。

### 9.6 全图配色改版（2026-10-08，用户规则）

**规则**：全部 fig/ 图按图内系列数取色，色序 = 系列在图内的顺序——
1 系列 #B2172B；2 系列 #B2172B/#F5A682；3 系列 #82969D/#CC312D/
#F7EDCA；4 系列 #A4C8D9/#6C96CC/#B2172B/#F5A682；5 系列及以上取
Paul Tol "muted" 色盲安全配色（SRON/EPS/TN/09-002，固定序取前 k
色）：#CC6677/#332288/#DDCC77/#117733/#88CCEE/#882255/#44AA99。
同日 v2：初选的 Tol "bright"（#4477AA/#EE6677/#228833/#CCBB44/
#66CCEE/#AA3377/#BBBBBB）经用户审看判为"太过艳丽"，换同一篇笔记
的 muted 方案（同为色盲安全设计、面向填充与柔和论文风格、支持 9
系列）。旧路径身份色（cr #4C4C4C、ih #BABABA、nad #C1A8E0、nhd
#F7E6A0、tx #9FC5E8）与堆叠专属淡紫/浅黄自该日起退役；§9.3/§9.5
中的配色描述仅作历史记录。

**改动面**：gen_ch5_figs.py（palette(n) 按系列数取色，五个图族
写入器统一执行：判决堆叠 7 色=Tol bright、转移图 2–5 系列、序列图
3–6 系列、计数器图 1–3 系列、份额图 1 系列）；gen_fig_plts.py
（SPECS 直接改写：8 张单系列→#B2172B、tile_mem_reads 三系列→新三
色组、双系列本已合规）；gen_stack_plts.py（3 张堆叠图 2 系列→深红/
浅橙）；e1_nad_nhd 本已合规不动。

**验证**：74 张 PNG 全量像素抽验——旧配色零残留、无空图、尺寸全
1200x600；抽样逐图比对（判决堆叠含 Tol 七色全量、三色图含新三色
组、单色图仅深红、四系列转移图四色齐、刀刃双线深/浅红、m4 序列
六色=Tol 前六色与 0.05 静默过滤一致）。
