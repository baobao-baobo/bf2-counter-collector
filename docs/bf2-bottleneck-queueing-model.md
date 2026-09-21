# BF2 数据路径排队瓶颈模型设计（BF2 版 PathFinder，代号 BF2-PF）

> 本文档是 PathFinder（SIGCOMM 2025）§4 排队论瓶颈定位方法在 BlueField-2 上的**完整移植设计**：系统模型、计数器角色分配、逐顶点排队规格、瓶颈判定、实现形态、W 标定实验、验证矩阵、开发路线。
> 与既有文档的关系：`pathfinder-ch4-multiperspective.md`（方法多视角深析）、`pathfinder-ch4-queueing-reuse.md`（复用要点初稿）、`pathfinder-to-bf2-mapping.md`（映射草案与 P0–P4 规划）——本文档吸收三者并**落到可执行的设计规格**，其中 W 标定实验、瓶颈判定流程、验证矩阵为新增内容。
> 设计原则沿用论文三条：**证据分级**（直接占用 > Little's Law 反推 > 常数模型）、**排他归属**（物理停留处计 Q，不双计）、**饱和点判定**（瓶颈 = max Q，非最慢点）。

---

## 0. 定位与设计目标

**一句话**：把 BF2 建模为一个多出口 Clos 网络（DDR / PCIe 主机 / 网络 三类出口 + eSwitch 域），对每个硬件顶点做排队建模（L=λ·W），用直接占用证据（MSS_NO_CREDIT、*_AF）校准反推值，逐快照输出 max Q 的 (流, 顶点) 对作为瓶颈判定。

**与 PathFinder 的三个本质差异（决定设计走向）**：

| 维度 | PathFinder（Intel SPR/EMR） | BF2 版 |
|---|---|---|
| 出口 | 单一（CXL DIMM） | 三类出口 + eSwitch 域（更复杂，但也更接近 DPU 真实工作形态） |
| W 来源 | 延迟类计数器直接读 + 相邻跳延迟差 | **无延迟类计数器** → W 几乎全靠标定常数 + 实测 RTT（网络出口）；精度靠直接占用证据校准弥补 |
| 直接占用证据 | 少（靠反推 + 设备打包缓冲） | **多**（MSS_NO_CREDIT / TDMA_RT_AF / TDMA_PBUF_MAC_AF / TX_DAT_AF / RX_DAT_AF / WRQ_BUF_EMPTY）——干扰分析比 Intel 场景证据更硬 |

**设计目标（2026-09-17 二轮修订后）**：给定一个负载快照（1s 全计数器行），回答一问——**当前哪条数据路径最繁忙**（路径载荷 L_p = Σ 顶点压力指数：每计数器按"空闲→饱和"跨度或 λ/C 归一化到 [0,1]、顶点取压力融合值、沿路径累加，使用路径上**全部**已验证计数器，见 §4.6）。顶点级瓶颈定位与延迟类方法（探针 Q_wait 等）保留至未来工作。

---

## 1. 系统模型

### 1.1 图 G=(V,E)

V = BF2 硬件模块（附计数器归属）：

```
[核域]   A72×8 → L1D/L1I（ARM PMU，32 fd）→ L2+HNF（tile0..3，4 槽×4）
[互连]   tilenet（mesh 路由器，N/S/E/W/C 五向）→ SkyMesh 三网：CDN(请求)/DDN(数据)/NDN(响应)
[缓存]   L3（l3cachehalf0/1，HITS/MISSES_BANK0/1，enable 门控）
[内存]   MSS → DDR（板载 DRAM）
[IO 域]  TRIO×2 → SMMU → triogen → PCIe TLR（pcie0=主机面，pcie1=Arm 子系统）
[eSwitch] pf0hpf/pf1hpf/p1/Arm representor（en3f1pf1sf0+enp3s0f1s0）+ OVS 桥
[边缘]   网口（ethernet 进出）、加速器（RegEx/压缩/加密，接口待调研，暂建模为黑盒顶点）
```

### 1.2 流（bFlow）与路径集合

**bFlow** = (发起者, 出口, 请求类型) 三元组。发起者 ∈ {A72 核, DMA 引擎(TRIO), 网口 RX, eSwitch 入口}；出口 ∈ {DDR, PCIe 主机面, Arm 软件栈(NAD), 网口 TX}；请求类型 ∈ {读, 写, 预取, 包}。bFlow 生命周期 = 负载生命周期（与 PathFinder 的 mFlow 同构；BF2 无调度时隙语义，绑定由负载隔离实验完成）。

**静态路径表**（P1 交付物，`configs/path_table.conf`；地址映射软件可控 → 比 Intel 更确定）：

```
P1  核→DDR 读      A72→L1→HNF(L2)→tilenet→L3→MSS→DDR     （命中即止：L1/L2/L3 子路径）
P2  核→DDR 写      A72→L1→HNF→tilenet→L3→MSS→DDR          （写分配/写直达按实测定）
P3  DMA→主机读/写  TRIO→SMMU→pcie0→主机内存               （读=主机→DDR 中转或 DMA 直读）
P4  主机→DDR（DMA） pcie0→TRIO→SMMU→tilenet→L3→MSS→DDR
P5  网→主机（NHD）  网口→eSwitch→pf1hpf→主机                （已验证：p1_rx≡pf1hpf_tx 字节一致）
P6  网→Arm（NAD）   网口→eSwitch→en3f1pf1sf0→Arm 软件栈    （已验证：2a=6.05Gbps=Arm 收包瓶颈）
P7  网→DDR 中转     P6 + Arm 软件写 DDR
P8  Arm→网 TX      Arm 软件栈→eSwitch→网口
```

### 1.3 快照

collect_all 单进程 1s 采样天然时间对齐 = 快照机制（对应论文"调度时隙末全体读数"）。快照序列 = CSV 行序列。**绑定规则**：同一快照行内所有计数器必须同刻——4 槽轮换会破坏同刻性，因此排队分析要求 λ/W/Q^dir 计数器**同组采集**（见 §2 事件组设计）。

---

## 2. 计数器角色分配（三类角色）

把全部计数器按排队模型的输入契约分为三类：

- **λ 类（频率）**：delta ÷ 间隔 → 到达率。要求：与 Q^dir 同组、机制 1 delta 语义。
- **W 类（服务时间）**：BF2 无延迟计数器 → W 来自 (a) 标定常数（微基准测得）、(b) 实测 RTT（网络出口，ping/iperf/redis PING p50）。**这是 BF2 版与论文最大的工程差异，必须靠证据分级原则弥补**。
- **Q^dir 类（直接占用）**：AF 型（almost full）与 NO_CREDIT 型——"正在饱和"的直接观测，用于校准反推值、触发压力判定。

### 逐顶点排队规格（核心表）

| 顶点 | 队列模型 | λ 计数器（已有） | W 来源 | Q^dir 直接证据 | 备注 |
|---|---|---|---|---|---|
| A72/L1 | 转发组件（退化定性） | L1D access(0x40)/miss(0x42)、L1I(0x14/0x01) | L1 延迟常数（标定） | 无（核内盲区） | Q 仅作参考，核内走组合信号定性分类（§4） |
| HNF/L2 | 转发组件 | A72_ACCESS(0x5d)、DIR_HIT(0x61)、ALLOCATE(0x6f≈miss) | W_hit、W_tag 常数（标定） | 无 → Little's Law 反推 | 4 槽：λ 与 Q^dir 同组优先 |
| tilenet/SkyMesh | **互联段（不套 FCFS）** | CDN_REQ(0x12)/DDN_REQ(0x13)/NDN_REQ(0x14) | — | RX_DAT_AF(0x10)/TX_DAT_AF(0x0f)（SMMU/triogen 视角） | 论文同款排除：互联段只用直接证据，不算 Q |
| L3 | 转发组件 | HITS_BANK0/1(0x17/0x18)、MISSES_BANK0/1(0x19/0x1a) | W_hit、W_miss=W_tag 常数（标定） | 无 → 反推 | enable 门控采集纪律；miss 请求不滞留 L3 → W_miss 取 tag 常数（论文 L1D/L2 规则） |
| MSS/DDR | **终止组件** | MEMORY_READS(0x4c)/MEMORY_WRITES(0x4d) | W_ddr 常数（标定，读写分开） | **MSS_NO_CREDIT(0x67)** ★ | Q=λ_hit·W_hit（论文 DIMM 规则）；请求物理滞留 MSS 队列 → 占满即回压 |
| TRIO/DMA | 转发组件 | TDMA_DATA_BEAT(0xa1，32B/beat) | W_tdma 常数（标定） | **TDMA_RT_AF(0xa8)/TDMA_PBUF_MAC_AF(0xa9)** ★ | 读队列与写缓冲分别有 AF |
| SMMU | 转发组件 | TBU_MISS(0x0e) | 常数（标定） | TX_DAT_AF(0x0f)/RX_DAT_AF(0x10) ★ | mesh 写/读 FIFO almost full |
| PCIe TLR | 终止组件 | pcie0/1 TLR byte+packet（机制 2） | W_tlr 常数（标定）+ 实测往返 | TLR 队列状态（若可读） | pcie0=主机面 / pcie1=Arm 子系统 |
| eSwitch 各口 | 转发组件 | 每口 rx/tx delta（8ff40c4）+ OVS 规则 n_bytes（P1 复活，9/15 三层一致） | 实测 RTT（ping/redis PING p50） | offload 统计 = 直接证据 | 网口域是 BF2 特有扩展；NHD/NAD 图已验证去向语义 |
| 网口/TX | 终止组件（出口） | collect_net / e1 每口计数 | 实测 RTT | — | 网络出口的 W 是 BF2 唯一有"真延迟"测量的地方 |

### 事件组设计（4 槽约束下的 λ/Q^dir 同组纪律）

- **稳态主干组**（每 tile 4 槽，= 现有 collect_mem 配置，已部署）：A72_ACCESS(λ_core)、MEMORY_READS(λ_mss_r)、MEMORY_WRITES(λ_mss_w)、**MSS_NO_CREDIT(Q^dir)** ——排队模型输入契约已天然满足。
- **诊断组**（轮换，= paper52.conf 已实现 6 组×4 tile 轮换）：L2 组（A72_ACCESS/DIR_HIT/ALLOCATE/VICTIM_WRITE）、L3（8 事件 2 half）等。**纪律**：换组即机制 1 清零 → 只能在工作负载稳态期切换，切换后基线重读；λ 与 Q^dir 必须同组，否则 L=λW 的两个输入来自不同时段、反推失效。
- **I/O 组**（trio/smmu/triogen，独立块无冲突）：TDMA_DATA_BEAT + TDMA_RT_AF + TDMA_PBUF_MAC_AF + WRQ_BUF_EMPTY；TBU_MISS + TX_DAT_AF + RX_DAT_AF。
- **网络组**：e1_esw.conf（per-port 每口列）+ OVS 规则轮询（collect_pipe.sh，M2 部署后）。

---

## 3. 排队模型规格

### 3.1 公式（论文 ALG 1 的 BF2 版）

```
转发组件（HNF/L3/TRIO/SMMU/eSwitch）：
    Q[p][c] = λ_hit·W_hit + λ_miss·W_miss
    λ_hit = hits[p]/interval，λ_miss = misses[p]/interval
    W_hit = 标定常数；W_miss = W_tag（请求不滞留本组件的场合）
终止组件（MSS/DDR、PCIe TLR、网口 TX）：
    Q[p][c] = λ_serve·W_serve          （λ_serve = MEMORY_READS+WRITES / TLR bytes / TX bytes）
互联段（tilenet/SkyMesh）：
    不套 FCFS；只用 *_AF 直接证据（论文同款排除）
```

**归属规则（排他性）**：请求物理停留在哪，占用就记在哪——L3 miss 只计 W_tag（请求即转发去 MSS）；MSS 计完整 DDR 服务时间。ΣQ 与总"在途请求数"一致，不双计。

### 3.2 W 标定来源（BF2 版证据分级）

```
Tier 1  直接占用证据：Q^dir（*_AF 型 / MSS_NO_CREDIT）——"正在饱和"的直接观测
Tier 2  Little's Law 反推：Q = λ·W_cal（W_cal 来自 §6 标定实验；网络出口用实测 RTT）
Tier 3  核内定性组合信号：miss 模式分类（§4）
```

**校准链（论文"证据分级"的 BF2 形式）**：反推 Q 在无负载时≈0、饱和时与 Q^dir 同步上升 → 模型可信；不一致时优先信 Tier 1（直接证据），并把偏差记录为 W_cal 的系统误差（C0 校准实验定量给出）。

### 3.3 Q 的可比性

Q 量纲统一为"平均在途请求数"（无量纲）。跨顶点可比的前提是 W_cal 单位一致（秒）× λ 单位一致（req/s）——标定实验输出的 `calib.json` 统一单位并注明测量方法。Q 的绝对值含义 = 占用度（服务中+等待），非纯排队数（论文同款语义，见多视角深析 §1.2）。

### 3.4 语义修正（2026-09-17）：常数 W 下的 Q 是利用率 ρ，不是排队长度

**用户质疑成立，本节修正 §3.1 的语义**：PathFinder 的 Q=λ·W 能定位堵塞，是因为其 W 是**实测延迟**、含等待项 W_wait——负载升高时 W 增长、Q 随之增长。BF2 无延迟计数器，W_cal 是低负载标定常数（只有服务项）→ **Q=λ·W_cal = ρ（平均在服务请求数 = 利用率）**，等待项整个丢失，"局部最大 Q"判堵塞退化为"局部最大 ρ"启发式（ρ 不含方差/突发性：突发到达的顶点 ρ=0.6 可以排队，平滑顶点 ρ=0.8 可以无队）。因此：**瓶颈定位不再依赖 λW 反推（降为辅助），主判定改用 §4.4 的度量谱系**；Q_wait = λ·(W_load−W_idle) 的探针恢复方案见 §4.4 Tier 3。PathFinder 四组件中 PFEstimator（时间账本）就是靠等待时间，BF2 等价于该组件不可移植，由 §4.4 的 Tier 1/3 联合替代；其余三组件（PFBuilder 路径表 / PFAnalyzer 占用账 / PFMaterializer 跨快照）照常移植。

---

## 4. 瓶颈判定与诊断流程

### 4.1 判定规则（三档，原型版——2026-09-17 已被 §4.4 取代）

```
① 压力优先：窗口内 Q^dir > 阈值的顶点集合 S（*_AF 或 MSS_NO_CREDIT 非零）
② 若 S 非空 → culprit = argmax_{c∈S, p∈paths(c)} Q[p][c]      （直接证据锚定）
③ 若 S 为空 → 无饱和，报告各顶点 Q 排行（低负载噪声大，不强行判 culprit）
```

- **上游/下游规则**（既有结论，与 ① 互证）：上游未命中率升而下游无回压 → 上游瓶颈（容量/带宽问题）；下游回压 → 下游瓶颈。
- **核内定性分类**（替代核内 stall 深度）：L1 miss 且 L2 hit → 核内带宽受限；逐级 miss → 容量问题；L3 miss + MSS_NO_CREDIT>0 → 内存侧饱和。
- **Case 4 同构预警**：核侧 λ（A72_ACCESS 为代理）下降 + 核侧 Q 反降 + 下游 Q^dir 升 → 瓶颈已迁到下游（发射率链条），不要误优化核侧。

### 4.2 输出（每快照）

```json
{
  "ts": 1789466963,
  "culprit": {"flow": "P4-dma", "vertex": "MSS", "Q": 12.3, "evidence": "MSS_NO_CREDIT=0.41"},
  "saturated": ["MSS", "trio0"],
  "Q_matrix": {"P1-read": {"HNF": 0.9, "L3": 1.2, "MSS": 2.1}, ...},
  "core_class": "L1 miss + L2 hit -> core-side bandwidth",
  "lambda": {"P1-read": 412e6, "P4-dma": 88e6}
}
```

### 4.3 瓶颈迁移检测

1s 快照序列 → 分窗聚类（稳定阶段识别）→ culprit 序列的转移事件 = 瓶颈迁移（预期出现论文 Case 3/4 同构：DMA 负载升高 → culprit 从 HNF 迁到 MSS/PCIe 侧）。

### 4.4 判定度量修正（2026-09-17 修订，取代 4.1）

无延迟计数器的平台上"哪里堵"必须换度量。先把"堵"拆成两个问题：**吞吐瓶颈**（谁饱和、限速点）与**延迟瓶颈**（谁贡献最多等待时间）。PathFinder 用 max Q 一把抓（实测 W 同时含服务+等待）；BF2 两者分开、各用其仪表。

**度量谱系（按证据强度）**

- **Tier 0 软件队列真值（BF2 独有）**：完整 Linux → qdisc backlog（tc -s）、socket 缓冲、OVS/DOCA 规则计数、redis 内部队列——**真实排队长度直接可读**，覆盖 NAD 软件段；NHD 硬件转发段无软件队列（offload 内），该层为空属正常。PathFinder 的 Intel 平台没有用这一层。
- **Tier 1 压力/持续度（直接证据）**：*_AF / MSS_NO_CREDIT 计的是"缓冲占用 ≥ 硬件阈值 θ"的周期数 → **AF 占比 = P(Q≥θ)，占用分布的尾部概率**，随负载单调、饱和时快速上升，不依赖任何模型。局限：θ 未知 → 序数性（只能比谁先越线）；计数语义（每周期 vs 边沿）待设备核实。
- **Tier 1.5 守恒堆积估算（流量守恒）**：对可同时观测输入与输出的队列，Σ(入−出) = Δ堆积 + 丢弃。eSwitch 每口列（8ff40c4 已有，镜像对守恒 <5% 已实证）→ p1_rx − pf1hpf_tx 的区间累计 = eSwitch 内持续堆积 + 丢弃（丢弃用 ethtool -S 配合分解）。局限：1s 窗口把瞬时排队平均掉，只见持续堆积；适用面 = 网络域（内存侧无成对出入计数器）。
- **Tier 2 忙闲占比（真利用率，不需要 W 也不需要容量）**：EMPTY 型计数器（REQ_BUF_EMPTY、WRQ_BUF_EMPTY）计"队列空"周期 → **忙占比 = 1 − empty/cycles**，单服务队列即利用率 ρ，是硬件直接给出的利用率而非模型值。已有数据：req_buf_empty 应用期强负 = 忙占比响应（7 应用已出图）。局限：只覆盖有 EMPTY 计数器的队列。
- **Tier 2b 速率/容量（ρ 估算）**：ρ = λ/C，C 来自文档或饱和实验标定（读写比/突发性分档）。需新增容量表标定。
- **Tier 3 主动探针恢复排队长度（对"局部最大排队长度"的直接回答）**：**Q_wait = λ·(W_load − W_idle)**——W_load 由探针在负载下实测、W_idle 为标定基线（低负载即 W_service+传播项，差值恰为排队等待）。探针按段隔离：membench 工作集 ≤L1 / ≤L2 / 介于 L2-L3 / >>L3 相邻档差分 = 各段 W；ping/redis PING = 网络段；fio iodepth=1 = DMA 段。于是每条路径每个顶点的 Q_wait 可算，**局部最大 Q_wait = 延迟瓶颈点，与 PathFinder 的 max Q 语义同构**（W 来源由被动计数器换成主动探针）。代价：探针有扰动 → 低速率/预留核，只用于离线诊断升级，不进 1s 主流程。

**修订后判定流程**

```
每快照（1s 主流程，全部被动证据）：
  1. 三层度量：AF 占比、忙占比（EMPTY 补）、ρ（速率/容量）
  2. S = {v : AF 占比 > τ}                          # 压力集（直接证据）
  3. S≠∅        → culprit = argmax_{v∈S}（忙占比/ρ）   # 直接证据锚定 + 最忙者
  4. S=∅, max ρ>0.9 → "近饱和预警"点名最高 ρ 顶点（不判死）
  5. S=∅, 全部 ρ<0.9  → 无饱和，只报排行
诊断升级（离线，针对步骤 3 的嫌疑顶点或步骤 5 的慢路径）：
  6. 分段探针 → 每顶点 Q_wait = λ·(W_load−W_idle)
  7. 局部最大 Q_wait = 延迟瓶颈；与吞吐瓶颈（步骤 3）分开报告
```

**"局部最大"三种度量下的合理性裁决**：局部最大 Q_wait（探针版）= 与 PathFinder 同构的定理；局部最大 AF 占比 = 排队尾部证据，可信；局部最大 ρ = 启发式（方差盲区），只当"最忙报告"，不能单独判 culprit。核心原则不变：**瓶颈 = 饱和点，饱和有独立的物理表征（缓冲满、背压、忙占比），不需要延迟计数器**；延迟计数器缺的只是延迟归因，由探针补齐。

### 4.5 范围裁定（2026-09-17 用户决策）：覆盖性核查 + ρ_p 初版机制（机制部分已被 §4.6 取代）

**覆盖性核查结论：背压/瓶颈类计数器不能覆盖所有路径的所有转发点**（约半数覆盖）：

| 顶点 | 背压/忙闲计数器 | 状态 |
|---|---|---|
| HNF/L2 | REQ_BUF_EMPTY（忙占比） | ✓ 已实证（7 应用出图） |
| TRIO | TDMA_RT_AF / TDMA_PBUF_MAC_AF / WRQ_BUF_EMPTY | ✓ 已接入 |
| SMMU/triogen | TX_DAT_AF / RX_DAT_AF | ✓ 已接入 |
| eSwitch/网口 | 每口 rx/tx + OVS 规则 + ethtool -S drop | ✓ 已实证（E1/Part B） |
| MSS/DDR | MSS_NO_CREDIT | ⚠️ 7 应用恒 0、未证实（探针实验 E2-L2 待执行） |
| tilenet | catalog 48 个生成式 DIAG 事件（是否含 per-channel 满/空型未验证） | ⚠️ 存量资源待探针 |
| L3 | 无背压型事件（仅频率类） | ✗ 缺口 |
| PCIe TLR | 机制 2 仅 byte/packet 计数 | ✗ 缺口 |
| A72 核 | 无占用计数（ARMv8 STALL_BACKEND 0x23 为候选，未接入） | ✗ 定性 |

**路径级判定不依赖全覆盖**（每条路径都有已验证的入口观测点），但单入口点 ρ_p=λ_p/C_p 只用一个计数器、违反"全量计数器刻画路径负载"的需求 → **机制已被 §4.6 取代**（本节覆盖性表与未来工作裁定仍有效）：

```
每快照、每路径 p：
  λ_p = delta(入口计数器[p]) / dt      # 全部为现有已验证计数器
  ρ_p = λ_p / C_p                      # C_p = 路径容量（与 λ 同单位）
输出：ρ_p 排序 → 最繁忙路径；路径上任一 Tier-1 计数器（AF/drop）触发 → 压力标记（可用时）
```

| 路径 | 入口计数器（已验证） | 容量参考 C |
|---|---|---|
| P1/P2 核→DDR | MEMORY_READS+WRITES（req/s） | DDR 带宽÷64B（membench 饱和，待测） |
| P3/P4 DMA↔主机 | pcie0 TLR bytes | PCIe 实测带宽（fio 饱和，待测） |
| P5 NHD | p1_rx_bytes | 端口速率（ethtool，p1 疑 25G） |
| P6/P7 NAD/中转 | enp3s0f1s0.tx | **6.6 Gbps（Arm 收包瓶颈，实测 6.05）✓ 已有** |
| P8 TX | 端口 tx_bytes | 端口速率（ethtool） |
| IB/IH（论文口径） | IO_ACCESS | **eMMC 43 MiB/s（E0-3 实测）✓ 已有** |

- **一套机制**：公式唯一（λ÷C），快照复用 collect_all，路径表 = configs/path_table.conf（PFBuilder 静态化）。同域内（同为 bytes/s 或同为 req/s）可直接按 λ 排序，容量表只在跨域比较时需要。
- **W 标定从关键路径完全移除**（ρ=λ/C 不含 W）→ §6 移入未来工作。
- **与 PathFinder 对应**：本机制 = PFAnalyzer 的占用账形态（每快照定量账本 → max → 判定），顶点粒度换路径粒度、Q 换 ρ；PFEstimator（时间账本）+ Tier 3 探针 + 顶点级定位 → **未来工作**（用户裁定：延迟计数器缺失下这些方法偏差不可避免）。
- **已知边界**（论文需披露）：①路径级 ρ 不能定位路径内的哪个顶点是瓶颈；②ρ 无方差项（突发性盲区），压力标记部分补偿；③共享资源争用（P1 与 P4 同经 MSS）不分解，留未来。

### 4.6 路径载荷量化机制（2026-09-17 二轮修订，取代 4.5 的 ρ_p 机制）

**需求（用户裁定）**：路径负载必须由该路径**所有转发点的全部计数器**刻画；各转发点的"队列压力"归一化后沿路径累加，累加值跨路径可比，最大值者为最繁忙路径。

**理论借鉴**：

| 借鉴来源 | 借鉴内容 |
|---|---|
| PathFinder（SIGCOMM'25） | ΣQ 沿路径可加且跨组件可比，源于 Little 定律把 Q 统一到"在途请求数"（无量纲）。本机制保留"逐顶点量化 + 沿路径累加 + 可比性"结构，把无量纲化手段换成经验归一化（无延迟计数器） |
| 操作分析（Denning-Buzen 1978 / Lazowska 1984） | 利用率律 ρ=λ·S 与"容量=平均服务时间倒数"使 ρ=λ/C 合法；服务需求律 D=ΣV_i·S_i 使沿路径累加有物理意义（Σρ=λ·D=总资源需求速率） |
| USE 方法（Gregg） | 每资源三元组 利用率/饱和度/错误 —— 顶点指数 = U（忙占比或 λ/C）+ S（AF/NO_CREDIT 占比）+ E（丢包率）按证据分级融合 |
| 拥塞控制（DCTCP/DCQCN，交换机缓冲计数） | AF 计数器 = 硬件版 ECN 标记（缓冲阈值穿越计数），"阈值穿越占比"作拥塞信号有网络界先例；丢包率 = drops/arrivals 同款 |
| MPKI 与 min-max 标度 | 事件数除以公共分母（每千指令 miss）与机器学习 min-max 归一化——"空闲→饱和"跨度归一化到 [0,1] 的惯例来源 |
| NVIDIA DCGM | 逐引擎利用率百分比向量（SM/内存/IO 各自 0-100%）——"逐组件可比利用率"的工业先例 |
| Top-Down（Yasin 2014） | stall 槽位归因——未来工作方向（需 STALL_BACKEND 接入） |

**机制规格（三层索引）**

```
第 1 层  计数器索引 n_c（每计数器 → [0,1]）：
  频率类：n = clamp((λ−λ_idle)/(λ_sat−λ_idle), 0, 1)   # 空闲基线已有；饱和参考一次应力标定
  或     n = λ/C（容量已知者，ρ 语义，操作分析利用率律）
  压力类：n = AF_cycles/cycles（AF/NO_CREDIT 占比）      # 天然 [0,1]
  忙闲类：n = 1 − empty/cycles（EMPTY 补）               # 天然 [0,1]
  错误类：n = drops/arrivals（丢包率，DCTCP 式）          # 天然 [0,1]
  效率类：n = 1 − hit_ratio（miss 率等，无容量/饱和参考时的兜底）

第 2 层  顶点载荷指数 v_j = max(n_c, c ∈ 顶点 j)      # 压力语义（任一面受压即上升）
        伴生视图：mean(n_c) = "繁忙度"
        证据分级融合：压力类 > 忙闲类 > 频率类 > 效率类

第 3 层  路径载荷 L_p = Σ_{j ∈ path(p)} v_j           # 用户裁定：累加
        伴生视图：mean（每跳平均压力）、max（单点瓶颈视图，PathFinder MAX_Q 同构）
        共享顶点（MSS/tilenet/L3 服务多路径）：按入口比例法（M1，tools/path_data.py
        已实现并守恒校验）拆分份额后计入各路径

判定：最繁忙路径 = argmax_p L_p
输出：每快照 路径×顶点 指数矩阵 + 三聚合 + 压力标记（AF/drop 触发处）
```

**可比性论证（"累加可以比较"的成立条件）**：所有 n_c ∈ [0,1] 无量纲 → v_j、L_p 无量纲，跨顶点、跨路径可直接比较与累加；与 PathFinder ΣQ 的排序语义一致（顶点越接近饱和、指数趋近 1，等价于队长趋近缓冲上限），但**绝对量纲是"归一化压力指数"而非"在途请求数"**——论文须如实表述，不得宣称 L_p 是队长。

**标定增量（P1.5 修订）**：饱和参考表（每主计数器一个 λ_sat：membench→内存类、fio→IO 类、iperf3→网络类、stress-ng --cache→L2 类；bench p1–p7 已有 CSV 可回填初值）。容量表并入饱和参考表（C 与 λ_sat 等价互换）。

**已知边界（叠加 §4.5 三条）**：④饱和参考 λ_sat 依赖一次应力标定（初值可用 bench p1–p7 回填）；⑤累加对长路径有天然偏向——长路径请求真实消耗更多资源、物理上成立，需要"每跳"口径时看 mean/max 伴生视图；⑥频率类无容量/饱和参考者退回效率类（L3 miss 率、DIR_HIT 占比）定性参与。

**验证计划**：①空载快照 → 全指数 ≈ 0；②定向应力快照 → 目标路径 L_p 显著最大；③G1–G7 历史回放 → xz/BFS 应判 CR 路径、Redis 判 NAD 路径、NHD 数据判 P5——与既有判读结论对照。

---

## 5. 系统架构与实现形态

### 5.1 分层（与现有代码的衔接）

```
输出层   JSON 快照 / CSV / 热力图（gnuplot 既定风格）/ 预警
分析层   tools/analyze_bottleneck.py：读 CSV + calib.json → 逐顶点 Q → 判定 → 输出
标定层   tools/calibrate_w.py + 微基准（一次性）
配置层   configs/bottleneck.conf（事件组）、configs/path_table.conf（路径表）、calib.json（W 常数）
采集层   collect_all（现有）+ e1_esw.conf + collect_pipe.sh（P1 路线，M2 部署）
```

### 5.2 CLI 与运行模式

```bash
# 稳态监控（主干组，λ/Q^dir 同组，长期可跑）
sudo ./code/collect_all -c configs/bottleneck.conf -d 60 -o bb_steady.csv
python tools/analyze_bottleneck.py bb_steady.csv --calib calib.json --paths configs/path_table.conf

# 诊断模式（paper52 轮换全量 + 网络组；工作负载稳态期切换）
sudo ./run_phase.sh -c configs/paper52.conf -o bb_diag.csv -t 240 -a "<负载命令>"

# 标定（一次性，输出 calib.json）
python tools/calibrate_w.py --output calib.json   # 按 §6 实验单执行微基准
```

### 5.3 分析器伪代码（算法工程师视角的规格）

```
load calib.json, path_table.conf
for each row in CSV:                      # 1s 快照
    lam = delta(row) / dt                 # 机制 1 delta 语义（8ff40c4 每口列同款）
    for (p, c) in paths × vertices:
        if c in INTERCONNECT: continue    # tilenet 不套 FCFS
        Q[p][c] = queue_model(c)(lam, W)  # 转发组件 / 终止组件两公式
    S = {c : Qdir(c, row) > threshold}    # 压力集
    culprit = argmax over (S or all) of Q
    emit JSON snapshot
window-cluster snapshot series → migration events
```

复杂度 O(rows × paths × vertices) 纯算术，与论文 ALG 1 同量级，可流式计算。

---

## 6. W 标定实验设计（2026-09-17 起移入未来工作：仅探针 Q_wait 需要，P2 不再依赖）

**目的**：给每个顶点的 W_hit/W_tag/W_serve 常数赋值，并检验"W 不随 λ 变"的常数假设。输出 `calib.json`（含均值、方法、日期）。全部在负载隔离条件下做（单路径独享系统）。

| # | 目标常数 | 实验 | 测法 |
|---|---|---|---|
| W1 | L1 hit / L2 hit / W_tag | 指针追逐（membench），工作集 ≤ L1 → ≤ L2 容量 | 每跳平均延迟 → 相减得 W_l1_hit、W_hnf_hit；miss 场景定 W_tag 上界 |
| W2 | L3 hit / L3 miss(tag) | 工作集介于 L2 与 L3 容量之间 | 减 W_hnf_hit 得 W_l3_hit；大工作集 miss 场景验 W_l3_tag |
| W3 | DDR 读/写服务时间 | 工作集 >> L3，随机读（依赖链）/顺序写 | 每请求延迟减上游常数 → W_ddr_r / W_ddr_w；扫频检验常数性 |
| W4 | TDMA/TRIO 服务时间 | fio direct=1、iodepth=1、小块 4KB | 单请求往返减 DDR 部分 → W_tdma |
| W5 | PCIe TLR 往返 | fio iodepth=1 读主机内存 | W_tlr（pcie0/pcie1 分开） |
| W6 | 网络出口服务时间 | ping RTT / redis PING p50 / iperf 双向 | **实测延迟**——BF2 唯一"真 W"来源，且随负载可测（W 的负载曲线直接可得） |

**常数性检验（关键一步）**：每个 W 在 10%→100% 负载扫描下测曲线；若 W 随 λ 上升 → 该顶点常数假设失效，改为"W = W_cal + 排队项"，其排队项由 Q^dir 校准（回退到 Tier 1 证据）。

---

## 7. 验证实验矩阵（论文 Case 3/4/5 的 BF2 版 + 校准）

| 实验 | 场景 | 验证点 | 判据 |
|---|---|---|---|
| **C0 校准** | membench 扫频 10%→100% DDR 带宽（单路径） | 证据分级：反推 Q vs Q^dir 一致性 | Q(λW) 与 MSS_NO_CREDIT 曲线同升、无负载≈0 |
| **C3 干扰** | membench（P1 核路径）+ fio（P4 DMA 路径）共置，DMA 20%→100% | 慢流回压污染快流：HNF/L3 的 Q 与未命中率随 DMA 升 | 核侧组合信号 + Q 单调性（论文 Case 3 同构） |
| **C4 争用/迁移** | 多路 fio 不同速率 + iperf3 并发（三出口齐全） | MAX_Q 判定正确性 + 瓶颈迁移 + 发射率链条 | culprit 随负载迁移；核侧 λ 降而 Q 反降（Case 4 同构） |
| **C5 分配** | 多路 fio 饱和 MSS | λ 反推资源分配 | 请求频率 vs 实测带宽 Pearson ≈ 0.998 复现 |
| **C-E1** | 既有 NHD/NAD 数据回放 | eSwitch 顶点建模正确性 | 2a=6.05Gbps（Arm 收包瓶颈）、N1 字节一致、G 系列 0.1Gbps 与历史同量级——已通过 |
| **C6 网络** | iperf3/redis 经 eSwitch 三去向（Host/Arm/中转） | 网络出口 W 实测 + eSwitch Q | P5/P6/P7 路径 λ 与 Q 自洽 |

---

## 8. 局限与风险（诚实披露，论文写作同款）

1. **核内盲区**：无 SB/LFB/stall 深度计数 → 核内 Q 只做定性分类，不参与 MAX_Q（防误判）。
2. **W 全常数（最大差异）**：无延迟计数器 → Q=λW_cal 只有 ρ 语义、无等待项（§3.4 修正）。补偿 = §4.4 度量谱系：AF 压力证据 + EMPTY 忙占比 + 守恒堆积判吞吐瓶颈，探针 Q_wait 判延迟瓶颈；论文明示该退化与补偿机制（"退化—补偿"本身即为可写的贡献）。
3. **4 槽同刻性**：轮换破坏同刻 → λ/Q^dir 同组纪律 + 稳态期切换 + 基线重读。
4. **L3 门控**：enable 采集纪律（先 enable=1 后读，全程 enable=0 会丢计数）。
5. **混流不可分**：发起者维混流（同计数器多路径）→ 负载隔离实验兜底（论文同款限制，我们的边界在核外、比论文宽松）。
6. **互联段排除**：tilenet 不套 FCFS（论文同款）；*_AF 直接证据覆盖。
7. **加速器路径未建模**：RegEx/压缩/加密接口待调研，先留黑盒顶点。

---

## 9. 开发路线（P0–P4 修订版）

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P0 统一采样器** | collect_all 单进程同刻快照（已具备）+ bottleneck.conf 事件组 | 已具备/小改 |
| **P1 路径表 + λ** | configs/path_table.conf 落地；每路径顶点-计数器映射（含共享顶点 M1 入口比例拆分） | 下一步 |
| **P1.5 饱和参考标定** | 每主计数器 λ_sat 一次应力标定（membench/fio/iperf3/stress-ng --cache；bench p1–p7 CSV 回填初值） | **P2 前置（W 标定已移除，§4.6）** |
| **P2 路径繁忙度分析器** | tools/analyze_bottleneck.py：三层索引（计数器→顶点→路径累加，§4.6）+ 判定 + JSON/图输出 | 依赖 P1.5 |
| **P3 跨快照（可选）** | 分窗聚类 + 繁忙路径迁移检测（接 DL 流水线 30→5 时间步结构） | 复用 PFMaterializer 思路 |
| **P4 调度接口（可选）** | 快照 JSON 协议 + 资源余量/预警输出 | 论文论证阶段可选 |
| **未来工作** | 顶点级瓶颈定位：MSS_NO_CREDIT/tilenet DIAG 探针验证、STALL_BACKEND 接入、探针 Q_wait（需 W 标定）、守恒堆积 | §4.5 裁定保留 |

M2（collect_pipe.sh 部署，Task #30）不阻塞 P1–P2，但 P5/P6/P7 网络路径的 λ 需要它（e1_esw 每口列可先行覆盖 eSwitch 去向）。

---

## 10. 与论文写作的衔接

- 模型章节可直接映射 PathFinder §4 结构：系统模型（Clos 图/bFlow/快照）→ 逐顶点排队规格 → 判定规则 → 案例（C0/C3/C4/C5/C-E1）。
- **我们的增量贡献**（可写入论文的差异化论点）：(1) 多出口扩展（DDR/PCIe/网络三出口 + eSwitch 域，PathFinder 只有单一 CXL 出口）；(2) 无延迟计数器平台上的路径级繁忙度判定：全量计数器的三层归一化索引（计数器→顶点→路径累加；空闲-饱和跨度归一化；借鉴 USE/操作分析/DCTCP；顶点级定位与延迟归因列为未来工作）；(3) 软件队列真值层（qdisc/socket/OVS，可用作 NAD 路径证据，完整 Linux 独有）；(4) 诚实披露退化与补偿机制（论文写作要求：主动披露边界）。
- 反例论证结构可复用论文 Case 4 写法：构造一个"核侧 stall 信号升而排队反降"的 BF2 实例证明两本账（压力账 + 占用账）缺一不可。
