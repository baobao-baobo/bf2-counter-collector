# PathFinder §4 数据路径瓶颈分析方法研究及 BF2 场景复用分析

> 论文：*Understanding and Profiling CXL.mem Using PathFinder*（SIGCOMM 2025）
> 本文档范围：论文第 4 章（PathFinder Design）中**数据路径瓶颈分析**部分——即 §4.4（PFEstimator，stall 分解）与 §4.5（PFAnalyzer，排队论干扰分析）——的方法拆解、思想梳理，以及在我们 BF2 场景上的复用设计。
> 与已有文档的关系：`PathFinder-论文剖析.md` 是全篇剖析（含 BF2 落地设计），本文档是 §4 瓶颈分析的**专项深挖**；`pathfinder-to-bf2-mapping.md` 是 8/10 映射草案。
> 引用均出自论文原文（`papers_txt/pathfinder.txt`）与 9/08 深度问答结论。

---

## 0. 结论速览（TL;DR）

1. **排队论在 PathFinder 中的角色**：不是装饰性理论，而是整套瓶颈定位方法的**判定核心**——Little's Law（L = λ·W）把"哪里慢"（W）和"哪里堵"（L）正交化，瓶颈的操作性定义由此产生：**排队度最大的 (路径, 组件) 对 = 当前快照的 culprit**。
2. **方法一句话**：**两本账 + 一个判定**。PFEstimator 记"时间账"（自底向上比例分摊 stall，回答"谁在等"）；PFAnalyzer 记"占用账"（逐顶点 Little's Law 反推排队度，回答"堆在哪"）；判定 = MAX_Q。两本账缺一不可——Case 4 专门构造了一个 stall 上升但排队下降的反直觉案例（L1D stall +1.7× 而 Q −41%）证明"stall 最大 ≠ 瓶颈"。
3. **思想三句话**：① 视角迁移——"硬件即网络"，把黑盒硬件翻译成多级 Clos 网络，借用网络域成熟工具箱；② 证据分级——直接占用证据 > Little's Law 反推 > 常数模型；③ 瓶颈是动态的——饱和点随负载迁移，快照序列 + 分窗是捕捉机制。
4. **BF2 复用结论**：排队模型**直接可套**；且 BF2 在"干扰分析"这一环**比 Intel 场景更有利**——PathFinder 最耗力气反推的东西（排队占用）我们有直接证据（MSS_NO_CREDIT、*_AF 就是"正在饱和"的直接观测）。代价在核内：无 stall 深度计数，PFEstimator 的核内部分退化为定性分类。方法的价值区间（互联/内存/IO 层）恰好覆盖 BF2 的实际瓶颈分布。

---

## 1. §4 的定位与瓶颈分析在其中的位置

§4 共六节，回答四个递进问题，瓶颈分析是第三问的完整答案：

| 节                  | 回答的问题                  | 借用的网络方法                                      | 与瓶颈分析的关系           |
| ------------------ | ---------------------- | -------------------------------------------- | ------------------ |
| 4.1 Key Idea       | 为什么能把硬件当网络分析           | Clos 网络视角                                    | 建模前提               |
| 4.2 System Model   | 怎么形式化（图/流/路径/快照）       | —                                            | 数据模型               |
| 4.3 PFBuilder      | **空间**：路径是什么、各有多少流量    | traceroute（思路）                               | 瓶颈分析的输入（每路径 λ）     |
| 4.4 PFEstimator    | **垂直**：混合 stall 中"谁在等" | reverse traceroute                           | 瓶颈分析第一半（时间账本）      |
| 4.5 PFAnalyzer     | **水平**：共享顶点上"堆在哪"      | delay-based queueing analysis + Little's Law | 瓶颈分析第二半（占用账本，判定核心） |
| 4.6 PFMaterializer | **时间**：瓶颈如何演化          | network snapshot / TSA                       | 瓶颈迁移的捕捉            |

关键理解：**§4.4 和 §4.5 是同一问题的两个正交分解**。stall 分解沿"路径的垂直方向"归属（哪段路径造成了等待），排队分析沿"共享顶点的水平方向"归属（同一组件上哪条流堆得最多）。瓶颈判定需要两者互证：stall 分解给出"哪里慢"，排队度给出"哪里堵"，而**只有堵点才是可行动的优化目标**（慢点可能只是堵点的受害者）。

---

## 2. 排队论方法拆解（PFAnalyzer 为核心的完整分析）

### 2.1 建模前提：硬件顶点 = FCFS 队列

论文原文（§4.5）：

> "PathFinder views each hardware (vertex) in the Clos network graph as a software switch and models it as a queueing model. … Except for interconnect routing, components along the data path can be modeled as a variant of the FCFS queue (S3-FIFO)."

- 除了互联路由，数据路径上的每个组件（L1D/L2/LLC/LFB/IMC/DIMM）都建模为 **FCFS 队列的变体**。
- 观察量的来源只有一个通道——PMU，且每个模块的 PMU 天然提供两类计数器（§3 的实证结论）：
  - **频率类**（HitCnt/MissCnt）→ 到达率 λ；
  - **延迟类**（Delay）→ 服务时间 W。
- 于是 Little's Law 的两个输入都有了，排队长度 L 变成"可反推量"：**L = λ·W**。
- 论文对方法来源的明示（§4.5）：*"Delay-based queueing analysis has been demonstrated in computer networks. The idea is to attribute the queueing occupancy of a flow to the individual flow based on its delay variation."* ——这是网络域 delay-based 方法的直接移植。

### 2.2 三个变量的来源（工程的精细之处）

| 变量      | 来源                 | 说明                        |
| ------- | ------------------ | ------------------------- |
| λ（到达率）  | hit/miss 计数 ÷ 时钟周期 | 由 PFBuilder 的路径图按路径、按组件切分 |
| W（服务时间） | **三种来源，按证据强弱分级**   | 见下                        |
| L（排队度）  | 反推 L = λ·W         | 无量纲的"平均排队请求数"             |

**W 的三种来源（证据分级思想的第一次出现）**：

1. **相邻跳延迟差**（默认归属方式）：*"We attribute the core-observed request latency to each on-path component by computing the latency difference between the current hop and the previous hop, used as the delay W."* —— 核观测到的请求延迟，沿路径逐跳做差，差就是本跳的服务时间。这是**排他性归属**的保证：ΣW = 总延迟，不双计。
2. **延迟计数器直接读**：数据喂养延迟类计数器直接给 W。
3. **常数模型**：`W_tag`——由缓存容量和相联度决定的 tag 查找周期常数。

### 2.3 两种队列模型的选择规则（方法精髓所在）

PFAnalyzer 对组件分两类，套不同公式（论文 Algorithm 1）：

```
转发 miss 的组件（L1D / L2 / LLC）：  L = λ_hit·W_hit + λ_miss·W_miss
终止组件（LFB / DIMM）：              L = λ_hit·W_hit
```

关键在 **W_miss 的位置相关性**——论文原话：

> "For L1D and L2, we use W_tag as W_miss … For LLC, we use the request miss delay as W_miss, since missing requests remain in the CHA TOR queue until they are completed."

这条规则的深层逻辑是**排队归属规则：请求物理停留在哪里，排队度就记在哪个组件头上**：

| 组件         | miss 之后请求在哪             | W_miss 取什么     | 理由                                |
| ---------- | ----------------------- | -------------- | --------------------------------- |
| L1D / L2   | 立即转发给下级，本组件只做 tag 查找    | **W_tag（常数）**  | miss 请求不在这里排队等待数据                 |
| LLC        | 滞留 CHA TOR 队列直到 miss 完成 | **完整 miss 延迟** | 请求真的在 LLC 侧排队                     |
| LFB / DIMM | 请求终止于此                  | 只有 hit 项       | LFB 负载属 uncore 路径；DIMM 持有完整数据不再转发 |

这条"物理停留处才计 Q"的规则 + 相邻跳延迟差的 W 来源，共同保证了**排他性**：所有组件的排队度之和 = 总延迟 × 总流量，任何一微秒的等待都恰好被记一次。9/08 问答里记录的"MSHR 归入 LFB/uncore 是建模取舍"就是这个规则的边界案例。

### 2.4 判定：MAX_OCC 与"瓶颈 = 饱和点"

Algorithm 1 的最后一行：

```
culprit_path = MAX_OCC(Q)     # Q[p][c]：路径 p 在组件 c 上的排队度
```

- 每个快照的 **culprit = 排队度最大的 (路径, 组件) 对**。
- **这不是"最慢点"判定，而是"饱和点"判定**。Little's Law 的威力正在于此：W 大但 λ 小（偶尔慢一次），排队度并不高；真正成为瓶颈的是"慢 × 频繁"的组件。
- **为什么 stall 最大 ≠ 瓶颈**（Case 4 的反直觉证据）：YCSB 的 L1D stall 上升 1.7×，但 L1D 排队度**下降 41%**。原因是发射率链条：

```
下游争用（FlexBus 饱和）→ 延迟上升 → LFB/ROB 被占满 → 核发射停摆
→ 上游 λ 下降 → 上游排队度下降（虽然每条请求更慢了）
```

真正的瓶颈已转移到 FlexBus+MC 的 HWPF 路径。**慢点是受害者，堵点才是元凶**——如果只看 stall 分解，会在 L1D 上做无用优化。9/08 问答补充的定量锚点：核自身也服从 Little's Law，N_LFB = λ_miss × W_miss。

### 2.5 两本账的互补（为什么 §4.4 和 §4.5 缺一不可）

| 维度      | PFEstimator（时间账本）                | PFAnalyzer（占用账本）                |
| ------- | -------------------------------- | ------------------------------- |
| 回答      | "谁在等"——CXL 引起的 stall 沿路径怎么分布     | "堆在哪"——共享顶点上哪条流堆得多              |
| 归属方式    | 继承归因：自底向上，下游份额按流量比例 piggyback 上游 | 排他归因：物理停留处计 Q                   |
| 数学工具    | 比例分摊 + 逐段累加                      | Little's Law                    |
| 输出      | 每路径每组件的 stall 分解                 | 每路径每组件的排队度 + culprit            |
| 独有能力    | 给出**因果链**（回压如何逐级传导）              | 给出**可行动的堵点**（MAX_Q）             |
| 单独使用的盲区 | Case 4 会误判 L1D                   | 固有延迟不可见、核内 CXL/本地不可分、给不出优化所需延迟值 |

反向传播的分摊细节（§4.4，ALG 2）：从 CXL DIMM 的打包缓冲占用（`unc_cxlcm_rxc_pack_buf`）出发 → 按各 FlexBus 根端口（RC）的流量比例分摊 → 加上本段信用饥饿（`unc_m2p_rxc_cycles_ne`）→ Host Uncore 的 RPQ/WRQ 延迟按 DIMM 归属再按 CHA 分摊 → CHA 内按 TOR 分到 LLC 片 → 核内逐级（LLC→L2→LFB→L1D→SB）piggyback 下游份额 + 本段 stall。每一步的形态都是同一个模板：**下游继承份额 × 本路径流量占比 + 本段自身证据**。

论文对排队积压成因的一句话（同样适用于任何排队组件）：*"The queue buildup at a CXL DIMM MC happens because its memory command handling rate (egress) cannot catch up with the request arrival rate (ingress)."* ——ingress/egress 不对称，这正是 Little's Law 里 λ > μ 时 L 发散的网络语言。

### 2.6 排队分析的上游应用（Case 5：从堵点反推资源分配）

瓶颈确定之后的二次利用：FlexBus 饱和时，各 mFlow 的**请求频率**与**实际获得带宽**的 Pearson 相关系数高达 **0.998**。含义：**一旦确认了饱和点，排队模型的 λ 本身就给出了资源分配比例**——不需要逐流测带宽。这是"排队模型作为推理基础设施"而非"一次性诊断工具"的示范。

---

## 3. 方法学提炼（平台无关的一般化步骤）

把 §2 的细节抽象成六步 pipeline——这是可以整个搬到 BF2 上的骨架：

```
① 建模      目标系统 → 图 G=(V,E)；定义流（端点对×执行者）、路径（确定性）、快照（时间对齐的全体读数）
② 采集      三类证据：频率类（→λ）、延迟类（→W）、直接占用类（→Q 的校准基准）
③ 空间恢复   守恒差分（流入 = 命中 + 流出）+ 目标分布计数 → 每路径定量流量
④ 垂直归因   自底向上：下游份额 × 流量占比 + 本段证据（piggyback 模板）
⑤ 水平归因   逐顶点 Little's Law（两种队列模型）→ 排队度 → MAX_Q 判定 culprit
⑥ 时间维度   快照序列 + 分窗聚类 → 瓶颈迁移检测
```

三条设计原则（比六步更本质）：

1. **证据分级**：直接占用证据 > 反推（Little's Law）> 常数模型；有直接计数器时必须用它校准反推值。这保证模型在"无负载时 ≈0、饱和时与直接证据同升"。
2. **排他性归属**：任何等待恰好被记一次（物理停留处计 Q、相邻跳差计 W）——不双计是跨流对比可信的前提。
3. **饱和点判定**：瓶颈 = 排队度最大的 (路径, 组件) 对，而非最慢点。慢是症状，堵是病因。

---

## 4. 解决问题的思想（哲学层梳理）

### 4.1 视角迁移："硬件即网络"（最重要的一条）

PathFinder 最大的贡献不是四个技术（全部来自网络域），而是**把处理器+芯片组看作多级 Clos 网络**：核 = 入口，DIMM = 出口，片上模块 = 中间交换机（按地址转发）；缓存命中让中间级可变入口/出口，形成嵌套子 Clos。视角一旦成立，网络域沉淀了几十年的工具箱（traceroute、reverse traceroute、delay-based queueing analysis、snapshot、TSA）全部可用。

**本质方法论**：面对一个无探测接口的黑盒系统，找一个**同构的、工具丰富的成熟领域**，把问题翻译过去，借用其全部方法论。这条对我们 BF2 工作的适用性极强——**DPU 的 tilenet/TDMA 本来就是 switch 式硬件**，这个视角迁移在 DPU 上比在 CPU 上更自然（我们甚至不用"想象"它是网络，它的一部分就是网络）。

### 4.2 从"测量什么"到"归属给谁"

PMU 计数是聚合的（混流不可分）。PathFinder 的答案是用三种恢复手段从聚合量中恢复 per-path 信息：**构建**（hit/miss 差分）、**分摊**（按流量比例）、**反推**（Little's Law）。三者共同的数学地基是**守恒律**：流入 = 命中 + 流出。先有守恒，才能差分、才能分摊。

### 4.3 瓶颈的操作性定义

- 瓶颈 = **饱和点** = max Q 的 (路径, 组件) 对。
- Little's Law 把"慢"（W）与"堵"（L）**正交化**：慢但流量小 ≠ 瓶颈；慢 × 频繁 = 堵 = 瓶颈。
- 这个定义的工程价值：它给出**可行动的优化目标**——堵点要么降 λ（减少给它送请求），要么提 μ（换更快的实现/分流）。

### 4.4 证据分级与自我校准

模型永远有误差，PathFinder 的策略不是追求模型精确，而是**用更强的证据校准更弱的推理**：直接占用计数器（设备打包缓冲、M2PCIe 入口队列非空周期）作为 Little's Law 反推值的校准基准。反推值在无负载时 ≈0、饱和时与直接证据同步上升，即模型可信。**"可校准"比"精确"更重要**。

### 4.5 动态观：瓶颈是时间的函数

Case 3 展示了瓶颈从 L1D 转移到 L2，Case 4 展示了瓶颈从 L1D 转移到 FlexBus+MC——**同样的应用，瓶颈随负载条件迁移**。因此任何"静态的一次性诊断"都是不足的，快照机制把连续系统离散化成时间序列，PFMaterializer 用分窗聚类识别"稳定阶段"、用 Holt-Winters 分解趋势——瓶颈迁移的捕捉是设计目标而非附加功能。

### 4.6 与 TMA 的定位关系

PathFinder 自述为"空间维度（路径）上的 TMA"：TMA 在核内做层级瓶颈定位，PathFinder 把同样的思想沿**数据路径的空间方向**扩展并打通到片外。我们 BF2 的工作可类比为"DPU 数据路径上的 TMA"。

---

## 5. 在我们的 BF2 场景上的复用

### 5.1 场景对比：为什么可复用

| 维度     | PathFinder（Intel SPR/EMR + CXL）          | 我们的工作（BF2 DPU）                                                            |
| ------ | ---------------------------------------- | ------------------------------------------------------------------------- |
| 工作性质   | 用现成计数器端到端剖析数据路径、定位瓶颈                     | **相同**                                                                    |
| 数据路径   | 核 → L1/L2/LLC → mesh → MC/FlexBus → DIMM | A72/L1 → HNF(L2) → L3 → MSS(DDR)；网口 → tilenet → 三通道；PCIe → trio/TDMA/SMMU |
| 出口     | 单一（CXL DIMM）                             | 三类（DDR / PCIe 主机 / 网络）                                                    |
| 计数器约束  | 232 个、无槽位限制                              | Tile 4 槽、机制 1 重编程清零                                                       |
| 核内粒度   | SB/LFB/ORO/stall 深度齐全                    | 粗（A72_ACCESS 等，无 stall 深度）                                                |
| 直接占用证据 | 少（靠反推 + 设备打包缓冲）                          | **多（MSS_NO_CREDIT、TDMA_RT_AF、RX/TX_DAT_AF 等）**                            |

**核心判断**：PathFinder 三环节中——路径构建（我们更简单：tilenet 三通道 NDN/CDN/DDN 天然按目标区分 + 地址映射软件可控）、**干扰分析（我们有直接占用证据，比他们反推更硬）**、stall 分解（我们核内退化）。两个环节占优、一个环节退化，且退化的环节（核内）恰好不是 BF2 瓶颈的主要分布区（板载内存带宽、PCIe、ARM 算力才是）。

### 5.2 排队模型在 BF2 各顶点的适用性检查（逐顶点）

| BF2 顶点           | FCFS 建模                    | λ 来源（现有计数器）                   | W 来源            | 直接占用证据                             |
| ---------------- | -------------------------- | ----------------------------- | --------------- | ---------------------------------- |
| A72/L1           | 部分可行（粗粒度）                  | A72_ACCESS 等                  | 微基准标定常数         | 无（核内盲区）                            |
| HNF (L2)         | ✅ 可行                       | HNF hit/miss 计数（机制 1/2 delta） | W_tag 常数（微基准标定） | 无直接，反推 + 校准                        |
| L3               | ✅ 可行（需 enable 门控）          | L3 计数（按半区）                    | 标定常数 / 相邻跳差     | 部分（l3cachehalf 相关）                 |
| MSS/DDR          | ✅ **终止组件模型** L=λ_hit·W_hit | MEMORY_READS 等流入量             | DDR 延迟常数        | **MSS_NO_CREDIT** ★                |
| tilenet/TDMA/TBU | ✅ FCFS 队列变体（switch 本色）     | TDMA_DATA_BEAT、tilenet 三通道计数  | 标定常数            | **TDMA_RT_AF、*_AF、RX/TX_DAT_AF** ★ |
| trio/PCIe TLR    | ✅ 同上                       | trio 计数                       | 标定常数            | **TLR 队列/回压证据** ★                  |

**结论**：排队模型在 BF2 片外顶点上**完全成立且证据更硬**——PathFinder 对 DIMM 排队只能反推，我们直接看 MSS_NO_CREDIT；他们对 FlexBus 排队只能反推，我们直接看 *_AF。**校准链**：Little's Law 反推值（无负载时≈0、饱和时与 *_AF 同升）即模型可信度检验，这是把论文的"证据分级"原则落到我们计数器体系上的直接形式。

核内部分按 9/08 问答的既有结论处理：L1 miss 且 L2 hit → 核内带宽问题；逐级 miss → 容量问题；L3 miss + MSS_NO_CREDIT>0 → 内存侧饱和。**核内用组合信号定性分类，片外用排队模型定量归因**——这就是 BF2 版的两本账分工。

### 5.3 落地设计要点（衔接既有 P1–P4 规划）

1. **W 标定实验（P2 前置）**：单路径微基准测 HNF tag 延迟、L3 延迟、DDR 延迟——与论文的 W_tag 常数策略完全同构。这是整个排队模型的地基，优先级应提到 P2 之前。
2. **λ 计算**：计数 delta ÷ 时间窗。collect_all 的 1s 同刻快照已满足（单进程采样天然满足时间一致性，对应论文"快照绑定 mFlow"的前提）。
3. **排队度公式直接套用**：转发组件 L = λ_hit·W_hit + λ_miss·W_miss；终止组件（MSS、网络出口）L = λ_hit·W_hit。HNF/L3 的 W_miss 按论文规则取 W_tag；MSS 排队取完整 miss 延迟（请求物理停留于 MSS 队列）。
4. **culprit 判定**：MAX_Q 排序 + 压力信号触发 + 既有"上游/下游"规则（上游未命中率升而下游无回压 → 上游瓶颈；下游回压 → 下游瓶颈）。两本账互证：*_AF/MSS_NO_CREDIT（时间账）与排队度（占用账）应指向同一组件，不一致时优先信直接证据（论文的证据分级原则）。
5. **瓶颈迁移检测**：1s 快照序列 + 分窗聚类（P3 已规划）。预期 BF2 上会出现论文 Case 3/4 的同构现象：DMA 负载升高 → 瓶颈从核侧迁移到 MSS/PCIe 侧；且可能出现"核侧发射率下降 + 核侧排队度反降"的 Case 4 同构（以 A72_ACCESS 发射率作为 λ 降的代理观测）。
6. **负载发生器已就绪**：验证实验需要的三类负载——membench（核路径）、fio（DMA 路径）、iperf3（网络路径）——工具和用法都已具备（fio-guide.md / iperf3-guide.md），正好构成三出口的对照实验矩阵。

### 5.4 对照验证实验设计（论文 Case 3/4/5 的 BF2 版）

| 论文案例              | BF2 对照实验                                                                 | 验证的排队方法点                              |
| ----------------- | ------------------------------------------------------------------------ | ------------------------------------- |
| Case 3 本地 vs 远端干扰 | membench（核路径）+ fio（DMA 路径）共置，DMA 强度 20%→100%，观测核侧 HNF/L3 排队度与 stall 组合信号 | 慢流回压污染快流：下游证据沿路径向上传导（piggyback 分摊的验证） |
| Case 4 并发流争用      | 多路 fio 不同速率 + iperf3 并发：MSS/TLR 排队度逐流分解                                  | MAX_Q 判定的正确性 + 瓶颈迁移检测 + 发射率下降的代理观测    |
| Case 5 带宽分配       | 多路 fio 并发饱和 MSS：请求频率 vs 实测带宽的 Pearson 相关                                 | λ 反推资源分配的 0.998 结论是否在 DPU 复现          |
| 校准实验              | Little's Law 反推排队度 vs MSS_NO_CREDIT/_AF 直接证据的曲线一致性                       | 证据分级原则：反推值在无负载≈0、饱和时与直接证据同升           |

### 5.5 风险与开放问题

1. **核内盲区**：Case 4 的"L1D 排队反降"类反直觉现象在 BF2 核内可能观测不到——以 A72_ACCESS 发射率作代理，接受核内只做定性判断（与既有结论一致：方法价值区间在互联/内存/IO 层）。
2. **4 槽约束**：诊断模式事件组轮换损失同刻性。对策不变：稳态主干 + 诊断轮换双模式；瓶颈分析所需的"频率类 + 占用类"计数优先保证同组采集（这是排队模型的输入完整性要求，比论文的单一平台无约束场景更需要在事件组设计上花心思）。
3. **L3 门控**：L3 计数需 enable 门控，否则 L3 排队度退化为差分估计。
4. **混流假设**：并发多流时同一计数器混流，tilenet 三通道可解"目标"维，但"发起者"维需靠负载隔离实验（论文同样承认核内不可分——我们的边界在核外，比论文更宽松）。
5. **排队模型的适用边界**：论文明确排除互联路由（"except for interconnect routing"）——BF2 的 TDMA 总线/Mesh 互联段同理，互联段用 *_AF 直接证据而非 FCFS 模型。

---

## 6. 论文写作层面的借鉴（对硕士论文的额外价值）

1. **观察驱动建模的论证结构**：§3 先实证（逐组件对照实验建立计数器语义）→ §4.5 再建模（FCFS + 两种模型选择规则）。对应我们的"先实机验证 42 列、后搭模型"顺序，写作时可明示这一方法论顺序。
2. **反直觉案例的证伪设计**：Case 4 用一个"stall 上升但排队下降"的案例**证明两本账的必要性**——论文里"为什么需要两个机制"的论证不是靠枚举优点，而是靠构造一个单机制会误判的案例。我们论文中论证"占用账本 + 时间账本"时可复用这一写法（预期 BF2 上构造出同构反例）。
3. **局限的主动披露**：论文明确写出建模取舍（MSHR 归 LFB/uncore、核内路径混流不可分），并把它写成"方法的边界"而非"缺陷"。我们的 BF2 版本同样应披露核内粗粒度与 4 槽约束下的精度边界。

---

## 附录：论文原文关键引文（§4.4/§4.5）

- 视角：*"PathFinder views each hardware (vertex) in the Clos network graph as a software switch and models it as a queueing model."*
- FCFS：*"Except for interconnect routing, components along the data path can be modeled as a variant of the FCFS queue (S3-FIFO)."*
- W 的默认归属：*"We attribute the core-observed request latency to each on-path component by computing the latency difference between the current hop and the previous hop, used as the delay W."*
- 两种模型：*"For components that forward miss requests to lower levels, we use the extended formulation, L = λ_hit·W_hit + λ_miss·W_miss."*
- W_miss 的位置相关：*"For L1D and L2, we use W_tag as W_miss … For LLC, we use the request miss delay as W_miss, since missing requests remain in the CHA TOR queue until they are completed."*
- 终止组件：*"For LFB and DIMM … we adopt the L = λ_hit·W_hit model."*
- 判定：*"The component and path with the maximum queue length are identified as the culprit of the current snapshot."*
- 积压成因：*"The queue buildup at a CXL DIMM MC happens because its memory command handling rate (egress) cannot catch up with the request arrival rate (ingress)."*
