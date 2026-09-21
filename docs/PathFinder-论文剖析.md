# PathFinder 论文深度剖析

> 论文：*Understanding and Profiling CXL.mem Using PathFinder*（SIGCOMM 2025，UW-Madison / Intel / Beihang）
> 本文档按四项要求组织：**背景知识 → 逐章分析 → 核心方法剖析 → SmartNIC（BF-2）落地设计**。
> 与 `pathfinder-to-bf2-mapping.md`（8/10 设计草案）配合阅读：后者是映射结论，本文档是完整的论文剖析与方法学提炼。
> 论文文本在 `papers_txt/pathfinder.txt`，原文 PDF 在 `pathfinder.pdf`。

---

## 1. 背景知识（读懂论文所需）

### 1.1 CXL 与内存池化

**CXL（Compute Express Link）** 是基于 PCIe 物理层的高速互联协议，暴露三类协议：

| 协议 | 功能 |
|---|---|
| CXL.io | 类 PCIe（增强版：最多 32 lane，非一致读写） |
| CXL.cache | 设备可以缓存宿主内存（设备作 master 的一致性） |
| **CXL.mem** | **宿主 CPU 用 load/store 指令直接访问设备内存**——本文的研究对象 |

内存池化（memory pooling/disaggregation）：把内存从服务器里拆出来做成独立池子，多机共享、按需分配，实现独立扩展、高利用率和低成本。CXL.mem 是池化的技术基础：**Type-3 设备**（无 CPU 的 CXL DIMM/内存卡）对宿主表现为一个 NUMA 节点，应用零修改即可使用远程内存。一致性引擎在宿主处理器（master），设备是被动 subordinate。

**性能量级（论文 §2.3 实测，SPR 平台）**——理解全文动机的关键数字：

| 内存类型 | 随机访问延迟 | 带宽 |
|---|---|---|
| 本地 DIMM | 103.2 ns | 131.1 GB/s |
| 同机 NUMA 节点 | 163.6 ns | 94.4 GB/s |
| **CXL DIMM** | **355.3 ns** | **17.6 GB/s** |

CXL 内存比本地慢 ~3.4 倍、带宽只有 ~1/7。关键不是"慢"本身，而是**慢请求会污染整条处理器流水线**（详见 §2.2 分析）。

### 1.2 CXL.mem 事务（协议层）

宿主（master）→ 设备（subordinate）为 M2S 方向，反向为 S2M：

- M2S：**Req**（读请求，不带数据）、**RwD**（写请求，带数据）
- S2M：**DRS**（Data Response，读返回数据）、**NDR**（No Data Response，写完成确认）
- flit 模式：68B / 268B / PBR（Port-Based Routing）
- 设备侧收到 M2S 后解析命令、读写存储介质、返回 S2M 响应

### 1.3 Intel SPR/EMR 数据路径上的每一跳（§2.2 + §3 反复出现）

| 组件 | 定位 | 作用 |
|---|---|---|
| **SB**（Store Buffer） | 每核 FIFO | 解耦 store 指令退休与完成；写路径第一站 |
| **L1D** | 每核 48KB | 数据一级缓存 |
| **LFB**（Line Fill Buffer） | 每核 FIFO | 缓存本核 miss 后的读响应；数十条 cacheline 深 |
| **L2** | 每核 2MB | 二级缓存（SPR 起非包含） |
| **LLC**（L3） | 共享、分片 | SPR 60MB / EMR 160MB；每片与 CHA 同址 |
| **CHA**（Caching and Home Agent） | 每 LLC 片一个 | 一致性引擎；内含 **SF**（Snoop Filter，一致性目录）和 **TOR**（Table of Requests，请求表——后文核心角色） |
| **Mesh 互联** | 片上 NoC | 连接核、CHA、IMC、M2PCIe |
| **IMC**（Integrated Memory Controller） | 每通道 | 本地 DDR 控制器；内含 **RPQ**（读挂起队列）、**WPQ**（写挂起队列） |
| **M2PCIe** | 每端点 | FlexBus I/O 宿主控制器；mesh ↔ CXL 链路的桥 |
| **FlexBus** | Intel I/O 架构 | CXL 根访问点 |
| **CXL DIMM** | 设备侧 | 自带内存控制器和命令队列（设备侧排队，IMC 不参与——§3.4 关键发现） |

一致性协议：MESIF。地址映射：mesh 路由由地址哈希决定（不公开），LLC 片选由地址决定，跨片/跨 socket 请求走 snoop。

**SNC**（Sub-NUMA Clustering）：把 socket 分成多个 NUMA 簇，LLC miss 的目标分布因此细分（local / SNC / remote / CXL）。

### 1.4 四种内存请求类型（产生 CXL.mem 事务的架构请求）

| 类型 | 含义 | 走哪条路 |
|---|---|---|
| **DRd** | 需求读（demand load） | L1D → LFB → L2 → LLC → mesh → MC/M2PCIe → DIMM |
| **DWr** | 需求写 | SB 先行（退休解耦）→ L1D（需独占态）→ **writeback 时才真正落内存** |
| **RFO**（Read For Ownership） | 写前取独占权 | 数据行处于 S/I/F 态时触发；路径与 DRd 相同，可发自 L1D/L2/LLC |
| **HW/SW PF** | 硬/软预取 | 异步发生；L1/L2/LLC 都有 HW 预取器（SPR 起）；触发 DRd/RFO |

### 1.5 PMU 三类计数器（§3.1，方法论基石）

1. **事件计数器**：某事件发生次数（hit/miss/命令数）——给出**频率 λ**
2. **条件周期计数器**：某条件持续多少 cycle（缓冲满、信用饥饿、请求 outstanding）——给出**排队/停顿证据**
3. **数据喂养延迟计数器**：数据响应从发起到返回的等待 cycle——给出**延迟 W**

两种工作模式：连续计数（读到 stop/reset 为止）、采样（溢出中断）。

**论文用到的 PMU 全集（"232 个计数器"的构成，附录表 1–4）**：

| 组件 PMU | 可用数 | 选用数 | 关键内容 |
|---|---|---|---|
| SB | 2 | 2 | SB 满停顿（读写混合 / 纯写两种场景） |
| L1D | 12 | 5 | hit/miss/淘汰 + L1D miss 停顿 + 响应等待 |
| LFB | 2 | 2 | LFB hit、LFB 满停顿 |
| L2 | 44 | 25 | 4 类请求 hit/miss（退休+投机）+ 7 个停顿/响应周期计数 + ORO（outstanding 请求深度） |
| Core LLC（CHA 核视角） | 81 | 60 | LLC 停顿/响应 + 一致性 + **HitM bitmap（命中数据来源）** + miss 目标分布 |
| Socket/CCD LLC（CHA 片视角） | 686 | 108 | 一致性状态机 + snoop filter + **TOR inserts/occupancy/threshold × 请求类型 × 目标** |
| IMC | 54 | 18 | RPQ/WPQ 非空周期、插入、占用、CAS 命令 |
| M2PCIe | 34 | 4 | 入口队列插入/非空、出口 ack/数据块 |
| CXL 设备 | 56 | 8 | M2S/S2M 打包缓冲（插入/非空/满） |

> 232 = 2+5+2+25+60+108+18+4+8，恰好全部用上。

### 1.6 论文借用的网络域方法（§4.1 明示的五件工具）

| 网络方法 | 原文用途 |
|---|---|
| **traceroute** | 逐跳发现路径（TTL 探测）→ PFBuilder 的思路来源 |
| **reverse traceroute** | 从目的端反向探测 → PFEstimator 的反向传播思路来源 |
| **delay-based queueing analysis**（含 Little's Law） | 由延迟变化归因队列占用 → PFAnalyzer 的数学基础 |
| **network snapshot** | 同步快照 → 快照驱动的整体框架 |

**Little's Law**：L = λ·W。平均队列长度 = 到达率 × 平均等待时间。PFAnalyzer 的核心公式：
- 不转发 miss 的组件（LFB、DIMM）：L = λ_hit · W_hit
- 转发 miss 的组件（L1D、L2、LLC）：L = λ_hit·W_hit + λ_miss·W_miss，其中 L1D/L2 的 W_miss = W_tag（tag 查找常数，由容量/相联度决定），LLC 的 W_miss = 请求 miss 延迟（请求滞留 TOR 队列直到完成）

### 1.7 相关系统概念（论文对比/合作的对手方）

- **TMA（Top-Down Analysis）**：Intel VTune 的方法论——把流水线分层（前端/后端），用计数器层级定位瓶颈。PathFinder 的 stall 分解是 **空间（路径）维度上的 TMA**；TMA 只覆盖核内，无法关联片外 CXL 访问。
- **TPP**（Transparent Page Placement）：Linux 透明页面放置，按访问热度把热页迁到本地、冷页放 CXL。
- **Colloid**：运行时内存分层管理，用**每层内存延迟**（CHA 的 DRd miss 延迟）指导 TPP 迁移——Case 7 中 PathFinder 与它协作优化。

---

## 2. 逐章分析

### 2.1 Abstract（论文自述的一句话版）

用现有 PMU 能力、以"多级 Clos 网络"视角端到端剖析 CXL.mem 协议执行：快照式、路径驱动的 profiling；四项技术（路径构建 / stall 分解 / 干扰分析 / 跨快照分析）；基于 Linux perf 实现，七个案例验证。

### 2.2 §1 Introduction——问题定义与三大挑战

**动机链**：CXL 内存池化兴起 → CXL.mem 让 load/store 直达远端 DIMM → 但 CXL 访问慢，且**不只是拖慢应用，而是停住流水线、改变内存子系统行为**。三种具体病态现象：

1. **队列组件拥塞 + 回压传导**：SB/LFB/CHA 这类队列型组件被 CXL 请求填满，回压沿数据路径反向传播，**连本地内存应用一起受害**；
2. **局部性/工作集不可预测**：CXL 取数慢 → 预取引发的取数更慢 → 缓存层中本地请求与 CXL 请求互抢槽位 → 局部性行为改变；
3. **资源欠利用**：CPU 回压 + 内存级并行度受限 → 即使互联和系统总线带宽充足，也没有更多数据移动被调度出来（"有带宽却发不出请求"）。

**三大挑战**（论文一切设计都围绕解决它们）：

| # | 挑战 | 实质 |
|---|---|---|
| 1 | 多条非透明数据路径 | 路径取决于数据局部性（可能命中任意一级缓存），执行特征由计算/内存/I/O 三层共同决定 |
| 2 | 与纳秒级乱序流水线紧耦合 | 查询接口极少；**主动插桩（tracing/instrumentation）不可行、不可接受** |
| 3 | 本地流与 CXL 流交织 | 共享同一套硬件，计数混在一起，无法区分"谁是元凶" |

**既有工具为何不够**：VTune/uProf（TMA）只看片内、无法关联片外；multichase/STREAM 类只能报告使用情况、不能诊断病态场景；NoC/PCIe 基准框架不连接源（核）与目的（内存）。**缺一个端到端工具**。

**写作手法注记**：三大挑战其实是对应后文四大技术的——挑战 1→PFBuilder，挑战 2→（PMU 免插桩 + 快照机制），挑战 3→PFEstimator/PFAnalyzer。§2.3 又把挑战复述一遍并附 prior work 缺陷，这是 SIGCOMM 的论证结构：**问题 → 挑战 → 为何现有手段不行 → 本文思路**。

### 2.3 §2 Background and Motivation——协议、路径、量化差距

- **2.1 协议**：三协议 + M2S/S2M 事务 + flit 模式 + Type-3 设备形态（无 CPU NUMA 节点）。
- **2.2 数据路径**：四种架构请求（DRd/DWr/RFO/PF）各自如何走完核→缓存→互联→FlexBus→DIMM 全程。图 1 是全文章最重要的图：**四种请求 = 四种路径**，后续所有分析都按这四条路径分类。
- **2.3 问题量化 + 挑战 + prior work**：给出 1.1 节的延迟/带宽表；三类病态现象；三挑战；三类工具不足。

**要传达的信息**：CXL.mem 的性能问题是一个**系统级**问题，不是单点问题——因此需要端到端、多组件联合的诊断工具。

### 2.4 §3 Dissecting CXL.mem Execution——PMU 实证（全文的实验地基）

> 这是最容易被跳读、但**最重要**的一章。它做了三件事：(a) 考察慢 CXL 访问对流水线行为的影响；(b) 把计数器映射到不同数据路径；(c) 摸清计数器的能力与局限。§4 的整个系统建立在这些实证结论上。

**3.2 核 PMU**（SB / L1D / LFB / L2）：

| 组件 | 关键发现 |
|---|---|
| SB | CXL 下 SB 满停顿平均 **1.9×（读写混合）/ 2.0×（纯写）**——写回提交变慢 |
| L1D | 流水线停顿 **2.1×**、响应等待 **1.4×**；DRd+RFO 命中 **−22.8%**——局部性变差 |
| LFB | **反直觉发现**：lbm/leela 的 LFB 命中率反而 +88.5%/+12.0%（停顿 −15.4%/−54.6%）——CXL 拉长响应反而帮了"重用距离稍远"的应用；其余应用命中 −14.2%、停顿 +59.2% |
| L2 | 停顿 **2.7×**；投机型 load/store 指令 +46.3%（执行变长导致）；命中率普遍下降（DRd hit −33.3% 等），但个别应用反向变化 |

**3.3 CHA PMU**：核心能力是**目标分布枚举**——LLC hit 时 HitM bitmap 告知数据来自本地片/跨片/跨 socket snoop；LLC miss 时计数器告知数据由哪里服务（本地 DDR / SNC DDR / 远端 socket 缓存 / CXL DIMM）。发现：CXL 下 LLC 命中大幅下降（DRd −46.5%）、miss 大幅上升（4.2×）；miss 中 **38.4%/4.1%/49.2% 被跨片/跨 socket snoop 直接服务**——慢内存反而提升了 LLC 局部性（请求滞留更久，snoop 命中机会变大）。

**3.4 Uncore PMU**：**关键发现——CXL 访问几乎不在 IMC 排队**（设备自带内存控制器，设备侧排队），因此分析纯 CXL 流时可忽略 IMC；但这意味着**混合流场景下本地 DIMM 造成的 IMC 排队可能反过来阻塞 CXL 访问**。M2PCIe 计数器是"真实 CXL 流量"的地面真值（每端点）。

**3.5 CXL 设备 PMU**：CXL 3.0/3.1 规范定义了 QoS 遥测（light/optical/moderate/severe overload 四档），但现有 DIMM 不支持——作者留作 future work。

**3.6 泛化性**：同批实验在 EMR 上重复，趋势一致（幅度因 LLC 更大而更小）——证明**方法论不依赖单一平台**。

**3.7 小结**：CHA / M2PCIe / CXL PMU 提供 CXL 流量的地面真值；核 PMU 提供分路径的行为分析。

**写作手法注记**：§3 展示了一个可复用的研究范式——**先逐组件做"本地 vs 远端"对照实验建立计数器语义，再在其上搭系统**。这正好对应我们 BF-2 工作的既有做法（8/13 实机验证 42 列计数、与历史采集交叉验证），后续新增分析层也应延续"先实证、后建模"的顺序。

### 2.5 §4 PathFinder Design——系统设计（核心章节）

**三个设计目标**：端到端（覆盖全路径）、信息丰富（stall/排队/局部性多维度）、轻量（纳秒级事务下开销必须极小——实测 1.3% CPU、38MB 内存）。

**4.1 核心思想**：把处理器+芯片组看作**多级 Clos 网络**——请求方向入口级是核，出口级是 CXL DIMM，中间级是各片上模块（按地址转发，像交换机）；**缓存命中或预取发生时，中间级也能变成入口/出口，形成多个嵌套子 Clos**（路径 stage 不一致）。每个硬件模块装一个 PMU 遥测引擎（PTE），然后套用网络遥测技术。

**4.2 系统模型**（形式化定义，务必吃透）：

- **图** G=(V,E)：顶点 = 架构模块（源/目的/中间交换级，中间级有 M 入 × N 出端口）；边 = 互联链路
- **mFlow**：Core_i ↔ DIMM_j 之间的双向流，含全部 load/store/prefetch 命令与响应（按提交顺序）。三个性质：(1) 应用相关（生命周期随负载）；(2) 位置敏感（线程迁移到新核或触碰新 DIMM 就新建）；(3) 双向。数量上界 = 核数 × DIMM 数
- **Path**：每次 load/store 发出时实例化；由地址映射**确定性**决定；前向/反向子路径对称
- **快照**：**每次 OS 调度时隙结束（或抢占发生）时采集全部 PMU**，绑定当前运行的 mFlow；mFlow 生命周期 = 快照时间序列

**4.3 PFBuilder——路径构建**：traceroute 不可行（片上硬件不可编程、无 TTL 机制），替代方案：**用各层 hit/miss 计数反推路径图**。流程：(1) 从核 PMU 出发，按 SB/L1D/LFB/L2 的各类 hit 数计算每路径流量；(2) L2 之后去向不明（路由/片选/目的地址决定）——**关键突破口：TOR（Table of Requests）**，硬件记录"核→CHA"映射和 miss 目标（本地/SNC/远端/CXL），逐请求类型可枚举；(3) LLC miss 服务顺序：本地片 → SNC 片 → 远端 socket 片（snoop）→ MC → DIMM；(4) 本地用 IMC 计数、CXL 用 M2PCIe 计数收尾。**输出：带定量流量（穿越请求数）的路径图**。局限：多流并发时，片外路径可按目标区分（TOR 目标相关计数），核内路径的 hit 全部混在同一计数器里无法分。

**4.4 PFEstimator——stall 分解**：reverse traceroute 思路。**自底向上反向传播**：从 CXL DIMM 的打包缓冲占用出发，按各 FlexBus 根端口流量**比例分摊**队列占用 → 加上本段自身的等待/信用饥饿 → Host Uncore 的 RPQ/WPQ 延迟按 DIMM 归属再按 CHA 分摊 → CHA 内按 TOR 分到 LLC 片 → 核内逐级（LLC→L2→LFB→L1D→SB）**背负（piggyback）下游传来的 stall 份额 + 本段 stall**。读写使用不同计数器。**这是解决挑战 3（流交织无法分离）的核心手段：总 stall 是混合的，按流量比例把 CXL 引起的部分剥离出来。**

**4.5 PFAnalyzer——干扰分析（culprit 定位）**：每个硬件顶点建模为 **FCFS 队列**（变体），用 Little's Law 由（hit 频率, miss 频率, 延迟）估计每组件平均排队长度；**相邻跳延迟差**作为本跳的 W；L1D/L2 的 W_miss 用常数 W_tag；LLC 的 W_miss 用 miss 延迟；LFB/DIMM 只算 hit 项。**输出：排队度最高的 (组件, 路径) = 当前快照的元凶**。能捕捉瓶颈转移（bottleneck migration）。

**4.6 PFMaterializer——跨快照分析**：快照压缩为**分层树记录**（edge/vertex/mFlow/path 四张表）存时序库（InfluxDB）；CLI 把用户场景翻译成查询链：范围限定 → 聚合统计（min/max/avg/movingAverage）→ **时序聚类分窗**（识别稳定阶段）→ **Holt-Winters 趋势/季节/残差分解**（识别可预测访问模式）→ **Pearson 跨应用相关**（定位局部性影响因子）。

### 2.6 §5 Evaluation——七个案例（每案例证明一种能力）

**5.1 平台**：SPR（双 Xeon Gold 6438Y+，32 核，60MB LLC，Agilex FPGA CXL 卡 16GB，Linux 6.5，开 SNC、关超线程/Turbo）+ EMR（双 Xeon Gold 6530，160MB LLC，Micron CZ120，Linux 6.15）；77 个应用（SPEC CPU2017 / PARSEC / SPLASH-2x / GAP / Redis+YCSB）。

| Case | 能力 | 关键结果 |
|---|---|---|
| 1 **路径分类** | PFBuilder | fotonik3d：核内热点路径 DRd，uncore 热点 HWPF（59.3%）；CXL 命中是本地 LLC 命中的 8.1×；gcc 两快照对比：RFO 占比 1.1%→69.0%，精确定位流量构成变化 |
| 2 **stall 分解** | PFEstimator | fft 的 DRd stall 分布：FlexBus+MC 42.7% + CXL DIMM 40.3%，核内各层合计 <20%；raytrace 的 FlexBus+MC 占 67.1%；**stall 自 uncore 向核递减**（DRd −74.5%、RFO −67.8%）；HWPF 在 FlexBus 的 stall 与 DRd 在 L1D/L2 的 stall 相关（BFS 353.5↔209.8/179.5ns，FREQ 92.2↔13.4/0.7ns）——**间接量化了 L1/L2 预取器的遮蔽效果** |
| 3 **本地 vs CXL 干扰** | PFBuilder+PFAnalyzer+PFEstimator | 同核放本地流+CXL 流、CXL 负载 20%→100%：FlexBus/CHA 不堵，但核内 SB/L1D/LFB/L2/LLC stall 升 1.7–2.4×；瓶颈从"L1D 上的 DRd"**转移**到"L2 上的 DRd" |
| 4 **并发 CXL 流争用** | 同上 | YCSB 吞吐 −77.4%；FlexBus+MC 延迟 4.3×、DRd 排队 4.6×；向上传导：LLC stall 1.8×、L2 1.8×、LFB 2.9×、SB 2.1×；**L1D 排队反降 41%**（瓶颈已转移到 FlexBus 的 HWPF）——回压传导链完整呈现 |
| 5 **带宽分配** | PFAnalyzer+PFBuilder | 4 条 CXL 流饱和 FlexBus：带宽降幅不均（37.7% vs 74.7%，取决于访问模式）；**请求频率与获得带宽 Pearson 相关系数 0.998**——用请求频率即可推断运行时带宽分配 |
| 6 **数据局部性** | PFMaterializer | 聚类分窗识别稳定阶段；bwaves 与 lbm 共置时 LLC miss 降 20.6%，与 roms 共置无此效果——**跨应用局部性影响因子** |
| 7 **性能优化验证** | 全套 | TPP 效果量化（GUPS +3.0× 等）；与 Colloid 协作：用 PFBuilder 选主导请求类型 + PFEstimator 的按类型延迟替换 Colloid 的固定 DRd 延迟 → GUPS 再 +1.1× |

**5.9 讨论**：(1) 局限——核内缺 RFO/HWPF/DWr 的细分计数器（L1D/LFB 只能监测 DRd；L2 的 RFO 计数混合了需求与预取；stall 计数器只覆盖需求 load）；(2) 开销——1.3% CPU + 38MB。

### 2.7 §6 Related Work / §7 Conclusion

相关工作三块：profiling 系统（perf/VTune/gprof 等）、内存/存储分离（Pond/Caption/Melody 等）、宿主网络诊断（Hostping、host congestion control）。结论重申核心思想。

---

## 3. 核心方法剖析

### 3.1 思想脉络：三个洞察

论文真正的贡献不是四个技术本身（都是网络域成熟技术），而是**三个洞察构成的视角迁移**：

| # | 洞察 | 内容 | 破解的挑战 |
|---|---|---|---|
| 1 | **硬件即网络** | 处理器+芯片组 = 多级 Clos 网络；核=入口，DIMM=出口，片上模块=中间交换机；缓存命中使中间级可变入口/出口 → 嵌套子 Clos | 挑战 1（非透明路径）：一旦是网络，路径就是可枚举、可建模的对象 |
| 2 | **PMU = 现成 in-band 遥测** | 每个模块自带计数器，等于每个交换机自带遥测引擎；零插桩 | 挑战 2（不可插桩）：连纳秒级流水线都能观测 |
| 3 | **聚合计数 → 三种恢复手段** | PMU 计数是聚合的（不区分流），用"构建、分摊、反推"三招恢复 per-path 信息 | 挑战 3（流交织）：比例分摊（stall）、Little's Law 反推（排队）、hit/miss 差分（路径） |

**四个技术 = 洞察 3 的三招 + 时间维度**：

```
PFBuilder      构建  —— 空间恢复：路径是什么、各有多少流量
PFEstimator    分摊  —— 垂直归属：混合 stall 沿路径自底向上按比例分给各流
PFAnalyzer     反推  —— 水平归属：共享顶点上各流的排队度（Little's Law）
PFMaterializer 时间  —— 跨快照的模式识别（阶段、趋势、相关）
```

### 3.2 架构组织（分层）

```
┌────────────────────────────────────────────────────────────┐
│ 输入层   任务规格：被剖析应用(PID/共置应用) + 运行环境(绑核/mnode) │
│          剖析配置(模式/粒度/资源上限) + 报告规格(要什么统计量)     │
├────────────────────────────────────────────────────────────┤
│ 采集层   每个硬件模块一个 PTE（PMU-based Telemetry Engine）      │
│          核 PTE(SB/L1D/LFB/L2) / CHA PTE / Uncore PTE /        │
│          FlexBus PTE / CXL 设备 PTE                            │
├────────────────────────────────────────────────────────────┤
│ 快照层   每个 OS 调度时隙末（或抢占时）抓全部 PMU，绑定当前 mFlow  │
│          生成压缩 digest（分层树：edge/vertex/mFlow/path 表）    │
├────────────────────────────────────────────────────────────┤
│ 分析层   快照内：PFBuilder(路径图) → PFEstimator(stall分解)      │
│                  → PFAnalyzer(排队度/culprit)                  │
│          快照间：PFMaterializer（时序库查询链）                  │
├────────────────────────────────────────────────────────────┤
│ 输出层   周期摘要 + 显著信息（访问强度/局部性变化/延迟分解/         │
│          硬件瓶颈/流间干扰）                                    │
└────────────────────────────────────────────────────────────┘
```

分层要点：**采集/快照/分析/输出解耦**；分析层又分"快照内（空间分析）"与"快照间（时间分析）"两个子层。

### 3.3 工作流（快照驱动的 pipeline）

```
任务规格 → [每个调度时隙循环] →
  ① 抓快照（全部 PMU 读数）
  ② PFBuilder：由 hit/miss + TOR 构建本快照的路径图（路径 × 定量流量）
  ③ PFEstimator：自底向上把 CXL 引起的 stall 按流量比例分摊到每路径每组件
  ④ PFAnalyzer：每组件 Little's Law 排队度 → 定位 (组件,路径) culprit
  ⑤ digest 压缩 → 写入时序库
→ [查询] CLI 场景 → Flux 查询链（限定→聚合→聚类分窗→趋势分解→相关分析）
```

### 3.4 数据流（每快照内的计算链）

```
原始 PMU 读数（232 计数器）
  │
  ├─ hit/miss 频率 → λ（每路径每组件到达率）
  ├─ 延迟/响应计数 → W（每组件服务时间：常数 W_tag / 实测 miss 延迟 / 相邻跳差）
  ├─ 队列占用/满计数 → 直接排队证据（设备打包缓冲、M2PCIe 入口、RPQ/WPQ）
  │
  ▼ PFBuilder：hit/miss 逐层差分 + TOR 目标分布 → 路径图（哪条路径有多少流量）
  ▼ PFEstimator：占用证据 × 路径流量权重 → 每路径每组件的 stall 分解
  ▼ PFAnalyzer：L = λ·W（含 miss 修正项）→ 每 (组件,路径) 排队度 → culprit
  ▼ digest 表结构：
     edge   表：穿越路径列表 / 流量负载 / 可用带宽 / 排队度
     vertex 表：PMU 原始计数 / 竞争路径间的资源分配
     mFlow/path 表：路由信息 + 运行元数据
```

**数据结构设计的要点**：digest 是"图快照"而非"表快照"——按系统模型（顶点/边/流/路径）组织，查询才能在图上做（例：`path.dst=LLC AND path.mflow.pid=X`）。

### 3.5 四技术的分工与依赖

```
PFBuilder ──(路径图+流量)──┬─→ PFEstimator（垂直：沿路径逐级分摊 stall）
                          └─→ PFAnalyzer（水平：共享顶点上跨路径排队）
                          └─→ PFMaterializer（时间：跨快照演化）
PFAnalyzer 与 PFEstimator 互证：stall 分解给出"哪里慢"，排队度给出"哪里堵"，
二者共同支撑 culprit 判定与瓶颈转移检测。
```

### 3.6 可迁移的方法学原则（六条）

1. **视角迁移**：把研究对象建模成网络/图，即可借用该域全套成熟工具（本文一次借了 5 件）。
2. **空间枚举优先**：硬件不可探测时，用"确定性路径 + 逐跳差分计数"恢复路径图；关键的加速器是**目标分布计数器**（Intel TOR / AMD CCD 等价物）——找到它，路径构建就通了。
3. **守恒与差分**：每跳流入 = 流出 + 本地吸收（hit）；各级 hit/miss 计数天然满足这一守恒律，用于校验与反推。
4. **比例分摊**：聚合计数混流不可分时，按各流流量比例归属；配合**逐段 piggyback**（下游份额 + 本段自身）实现端到端归属链。
5. **证据分级**：直接占用证据 > Little's Law 反推 > 常数模型；有直接计数器时用其校准反推值。
6. **时间追踪**：瓶颈是动态的——culprit 随负载迁移（Case 3/4 都展示了转移）；快照序列 + 聚类分窗是捕捉迁移的机制。

---

## 4. SmartNIC（BF-2）环境下的落地设计

> 目标：把 PathFinder 的方法迁移到 BF-2，回答"**SmartNIC 上如何找性能瓶颈路径**"，并给出基于既有计数器工作（collect_all 已完成 8/13 实机验证）的建模与实施方案。

### 4.1 环境差异分析（Intel+CXL vs BF-2）

| 维度 | Intel SPR/EMR + CXL | BlueField-2 | 对迁移的影响 |
|---|---|---|---|
| 请求类型 | DRd/RFO/DWr/HWPF 四类细分 | 只有 读/写/预取 粗粒度 | 路径语义退化，按"读/写/预取 × 发起者"建模 |
| 目标分布计数 | TOR 按请求×目标枚举（9 场景） | **tilenet 三通道 NDN/CDN/DDN 天然按目标区分**；地址映射软件可控 | 有等价物，且路由确定性更强 |
| 出口类型 | 单一（CXL DIMM） | **三类出口**：DDR / PCIe 主机 / 网络口 | PFEstimator 的"起点"有三个，反向传播需三分支 |
| 停顿计数 | 每组件 stall cycle 计数器丰富 | 无核内 stall 深度计数；但有**回压/队列满计数**（MSS_NO_CREDIT、*_AF） | 反向传播的"货币"从 stall cycle 换成回压证据 |
| 硬件约束 | 计数器多、无槽位限制 | **Tile HNF 22 事件 4 槽**；机制 1 重编程清零历史 | 需要事件组轮换与诊断/稳态双模式 |
| 流定义 | 核↔DIMM（应用进程的 mFlow） | **端点对 × 执行者**：网络流、DMA 流、核任务流 | mFlow 概念重定义（见 4.2） |
| 优势 | — | **直接占用/回压计数器**（MSS_NO_CREDIT、TDMA_RT_AF、TX/RX_DAT_AF 就是"正在饱和"的直接证据） | 干扰分析不需要 Little's Law 反推，可直接观察 |

**BF-2 的相对优势（关键结论）**：PathFinder 最难的"路径构建"（要靠 TOR 补全）在 BF-2 上**更简单**——路径空间有限且确定（8 核 × 3 类出口 × 少量执行者），地址映射软件可控；最耗估算的"干扰分析"在 BF-2 上**有直接证据**。代价是核内粒度粗（无 stall 深度）、4 槽约束。

### 4.2 BF-2 版系统模型重定义

**图 G=(V,E)**：V = {A72 核/L1、Tile HNF(L2)、l3cachehalf、MSS(DDR)、tilenet、trio、SMMU、PCIe TLR、加速器}；E = SkyMesh 通道（NDN/CDN/DDN）、TDMA 总线、TBU 通路。

**mFlow（重定义）** = **（端点对 × 执行者）**的持续数据流，三类典型：

| mFlow | 路径骨架 | 出口 |
|---|---|---|
| 核任务流（软件路径） | A72 → L1 → HNF(L2) → [L3] → MSS/DDR | DDR |
| 网络数据面流（硬件直通） | 网口 → tilenet(RX) → NDN/CDN → DDR / 核 / TRIO | DDR 或核或主机 |
| 主机 DMA 流 | PCIe 主机 → trio → TDMA → SMMU(TBU) → DDR | DDR |
| （未来）加速器流 | RegEx/压缩/TLS 引擎 → … → DDR | DDR |

**Path**：由（端点对 × 执行者 × 目标）确定；静态路径表即可枚举（§4.3）。**快照**：1s tick 全体计数器读数（collect_all 已实现同刻对齐——单进程采样，天然满足 PathFinder"快照绑定流"的时间一致性前提）。

### 4.3 四技术的 BF-2 落地

**① PFBuilder_BF2——路径构建（静态路径表 + 逐跳差分 + 三通道目标分布）**

```
[ARM核] ─A72_ACCESS→ [Tile HNF/L2] ─hit→ 止
                          │miss
                          ▼
                    [L3 l3cachehalf] ─hit→ 止（需 enable 门控，否则退化为差分）
                          │miss
                          ▼
                    [MSS/DDR]（MSS_NO_CREDIT = 回压证据）
[网口] ─RX→ [tilenet] ─NDN→ DDR / ─CDN→ 核 / ─DDN→ TRIO(主机)
[主机] ─PCIe→ [trio] ─TDMA_DATA_BEAT→ [SMMU/TBU] → DDR 或 核
```

- **TOR 等价物**：tilenet 的 NDN/CDN/DDN 三通道计数**本身就是"请求 × 目标"计数器**（与 Intel TOR 的 miss-target 枚举同构）；核侧目标（本地/远端）由**软件地址区间解析**确定（BF-2 地址映射软件可控，比 Intel 更确定）。
- 每路径流量 = 各跳计数差分的**守恒校验**：流入 = 命中 + 流出。
- 输出：带定量流量的路径图（静态表 × 实时计数）。

**② PFEstimator_BF2——回压分解（反向传播的"货币"换成回压证据）**

出口三分支，自底向上反传：

```
第1层(出口)  MSS_NO_CREDIT(DDR) / PCIe TLR 队列 + TDMA_RT_AF(主机) / tilenet RX_DAT_AF(网络)
第2层(互联)  triogen TDMA_RT_AF、TDMA_PBUF_MAC_AF / SMMU TX_DAT_AF
第3层(缓存)  L3 MISSES(按半区) / HNF miss / DIR_MISS
第4层(核)    L1D/L1I miss（精度上限——无 stall 深度计数）
```

- 分摊算法**直接沿用**：每段 = 下游继承份额 × 本路径流量占比 + 本段自身回压证据。
- 例：MSS_NO_CREDIT 持续时间按（A72 读 miss 流 / tilenet→DDR 流 / DMA 写流）各自流入 MSS 的流量比例分摊，得到"内存侧饱和对各发起者造成的等待"。
- **精度限制与缓解**（沿用 8/10 文档结论）：核内只能到"L1 miss"粒度；用**逐级 miss 组合信号做定性 stall 分类**：
  - L1 miss 且 L2 hit → 核内带宽问题
  - 逐级 miss（L1→L2→L3 全 miss）→ 容量问题
  - L3 miss + MSS_NO_CREDIT>0 → 内存侧饱和
  - TDMA_RT_AF>0 → DMA 队列满；RX_DAT_AF>0 → 网络侧回压

**③ PFAnalyzer_BF2——干扰分析（Little's Law + 直接证据校准）**

- λ：各跳计数 delta / 时间窗口（MEMORY_READS、TDMA_DATA_BEAT、A72_ACCESS 等）。
- W：**微基准标定**（L2 tag 延迟、L3 延迟、DDR 延迟为常数），与 PathFinder 的 W_tag 常数策略一致。
- **BF-2 优势**：MSS_NO_CREDIT / *_AF 等直接占用计数作为 Little's Law 反推值的**校准基准**——反推值在无负载时≈0、饱和时与直接证据同升，即模型可信。
- 输出：每（组件,路径）排队度 + culprit + **瓶颈转移序列**（同 Case 3/4：瓶颈在 L2→L3→DDR→网格之间移动）。

**④ PFMaterializer_BF2——跨快照分析（整体复用，接入 DL 管线）**

纯软件层，与 PathFinder 完全同构：1s CSV（已实现）→ 1min 聚合 → 分窗聚类（识别稳定阶段）→ 趋势/季节/残差分解 → 跨任务相关分析。**与 DL 管线的关系**：30 步→5 步（1min 粒度）模型的输入正是"移动平均 + 聚类分窗"的产物；Holt-Winters 类分解作为特征工程参考。

### 4.4 数据流与架构（与既有代码的衔接）

```
采集层(已有)   collect_all 配置驱动 12 块采样 → 1s CSV（8/13 实机验证 42 列）
                ├─ 同刻快照（单进程，满足时间一致性）
                ├─ 诊断模式事件组轮换（4 槽限制下的路径覆盖，60s/组）
                ▼
建模层(P1)    静态路径表(配置化) × 实时计数 → 每路径四维向量
                ├─ 利用率：使用量/能力（CPU%、DDR/PCIe 带宽利用率）
                ├─ 效率：命中率、放大因子（MEMORY_READS/A72_ACCESS 等）
                ├─ 压力：回压证据（MSS_NO_CREDIT、*_AF，二值/程度）
                └─ 形态：流量来源构成（A72 vs DMA vs 网络占比）
                ▼
分析层(P2)    stall 定性分类器（组合信号）+ 排队/干扰分析器（Little's Law）
               + culprit 判定：压力信号触发 + 逐级利用率/效率下行方向
                ▼
输出层(P3/P4) 瓶颈报告 + 每路径资源余量（能力−用量，带预测）→ DL 模型/调度器
```

**路径级瓶颈判定规则**（4.4 节核心输出）：该路径压力信号触发 + 利用率/效率逐级下行——上游未命中率升而下游无回压 → 上游瓶颈；下游回压 → 下游瓶颈。

### 4.5 实施路线（P0–P4 细化）与验证实验设计

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P0 统一采样器** | 单进程多路采样 + 同刻快照 | **已完成**（collect_all，8/13 实机验证） |
| **P1 路径表 + 资源画像层** | 静态路径表配置化；输出每路径四维向量 CSV | 下一步 |
| **P2 瓶颈分析层** | stall 定性分类 + Little's Law 排队度 + culprit 检测 + 诊断轮换模式 | |
| **P3 跨快照分析** | 分窗聚类/趋势分解；接入 30→5 DL 管线 | |
| **P4 调度接口** | 快照 JSON 协议 + 资源余量/瓶颈预警输出 | |

**验证实验矩阵（论文七案例的 BF-2 对照版）**——这是"方法对不对"的判据：

| 论文案例 | BF-2 对照实验 | 验证目标 |
|---|---|---|
| 1 路径分类 | 单独跑 fio（DMA 路径）、iperf3（网络路径）、membench（核路径），采集后核对路径表流量占比与负载类型一致 | 路径构建正确性 |
| 2 stall 分解 | 饱和负载下 MSS_NO_CREDIT 按来源（核/DMA/网络）分摊，与各源单独满载时的回压对比 | 分摊算法准确性 |
| 3 本地 vs 远端干扰 | **membench + fio 共置**：观测核侧 L1/L2 miss 与 L3 命中率随 DMA 强度变化（论文 Case 3 的同构：慢流回压污染快流） | 干扰归因 |
| 4 并发流争用 | 多路 fio 不同速率 + iperf3 并发：PCIe TLR/MSS 排队度逐流分解 | 争用定位 |
| 5 带宽分配 | 多流请求频率 vs 实测带宽的 Pearson 相关（论文 0.998 的验证） | 带宽推断 |
| 6 局部性 | 不同共置负载组合下 L2/L3 hitrate 变化 | 跨任务影响 |
| 7 优化验证 | 未来卸载/放置策略（如 page 迁移、任务绑核）的前后对比量化 | 工具闭环价值 |

**W 标定实验**：单路径微基准（核读 DDR、DMA 读、网络转存）测各跳延迟常数——P2 的前提。

### 4.6 风险与待定

- 核内粒度粗（无 SB/LFB/ORO 等价物）：瓶颈在核内时只能定性分类，**方法的价值区间在互联/内存/IO 层**——这与 SmartNIC 的瓶颈分布（板载内存带宽、PCIe、ARM 算力，见 Smart/ 三论文调研）恰好吻合。
- 4 槽约束：诊断模式轮换的窗口拼接会损失同刻性；**稳态主干 + 诊断轮换**双模式是既定方案（8/10 文档 §4.3）。
- 板载加速器（RegEx/压缩/TLS）路径待师兄调研确认后补入路径表。
- 事件组重编程清零历史：诊断模式换组需基线重读（collect_all 已有机制）。

---

## 附录：关键数字速查

- 延迟/带宽：本地 103.2ns/131.1GB/s；NUMA 163.6ns/94.4GB/s；CXL 355.3ns/17.6GB/s（SPR）
- CXL 下核内变化：SB 停顿 1.9–2.0×；L1D 停顿 2.1×、命中 −22.8%；L2 停顿 2.7×；LLC 停顿 2.1×、命中 −41~62%、miss 4–5×
- Case 2 典型分布：fft DRd stall = FlexBus+MC 42.7% + CXL DIMM 40.3% + 核内合计 <20%
- Case 5：请求频率 vs 带宽 Pearson = 0.998
- 开销：1.3% CPU、38MB 内存（每调度时隙一快照）
- 计数器：232 个 = SB2+L1D5+LFB2+L2 25+核LLC 60+片LLC 108+IMC 18+M2PCIe 4+CXL 8
- 平台：SPR 6438Y+ 60MB LLC / Linux 6.5；EMR 6530 160MB LLC / Linux 6.15；77 应用验证
