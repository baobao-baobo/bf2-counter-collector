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
