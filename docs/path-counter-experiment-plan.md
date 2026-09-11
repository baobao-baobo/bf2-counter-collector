# BF2 六数据路径计数器采集实验方案（草案，供审核）

> 版本：v0.1（2026-09-11）
> 目标：在运行 bench/application 的条件下，对六条数据路径（CR / IH / IB / NAD / NHD / WB，其中 WB 为写回子路径，与论文口径一致）做计数器采集与**按路径拆分对比**，产出论文"数据路径流量分析"所需的数据。
> 依据：官方 PMC 文档 v4.15.0（本地 Performance Monitoring Counters.md）、实机采集器代码与 catalog.c、bench 套件与 run_bench.sh、paper52.conf 轮换机制、路径硬件核实记录（switching-offload-path-study）。

---

## 1. 总体思路评审

### 1.1 采纳的部分

1. **"同一/同类计数器 + 多路径对比"是正确且几乎唯一可行的框架**。BF2 的计数器都是**聚合点观测**——硬件不按路径给事件打标记，同一计数器（如 HNF_REQUESTS）天然混合多条路径的流量。既然硬件不区分，就只能靠**对照与差分**把共享计数器的读数拆分到路径上。这个思路与硬件现实完全匹配，予以采纳。
2. **HNF_REQUESTS 拆分 CR/IH 的示例选得好**。HNF 正是 CR（RN-F）与 IH（RN-I）在网格内的第一个汇聚点，且入口侧有 A72_* 与 IO_* 两组专用计数器可作锚定，是最适合验证归因方法的入门实验。
3. **按 bench 设计 config、最大化同类计数器共采**：方向正确，将落地为第 6 节的"槽位预算 + 分组轮换"机制。

### 1.2 需修正的问题

| # | 原想法中的问题 | 修正 |
|---|---|---|
| 1 | "采集完成后进行流量拆分计算"未给出方法——单计数器读数本身**不含路径信息**，无法直接拆分 | 拆分必须依赖归因三法之一：**相位差分**（首选）、**入口比例模型 + 守恒校验**、**专用计数器直接读数**（详见 §3）。HNF_REQUESTS 单独一个计数器什么都拆不出来 |
| 2 | "采集 A72/DMA→HNF→RN-F/RN-I→MSS→DRAM 链路中**所有**计数器"不可行——Tile HNF 每 tile 只有 **4 个槽位**，22 个事件不可能同采 | 用 paper52.conf 已有的**分组轮换**（6 组 × 4，周期 6 s）覆盖全链；同一时刻 4 个事件，一个轮换周期内全部事件各采一次。另注意 RN-F/RN-I/HNF 不是链上先后节点，而是同一 tile 内不同接口的计数设施 |
| 3 | "NHD 和 NAD 上的 PCIe 计数器作为同一类计数器对比"——**前提未验证**：官方文档只说 TLR 是 per-TRIO 局部统计单元，未定义其是否覆盖不经 Arm 侧 TRIO 的 NHD 流量（即上轮论文评审 CRITICAL #2） | 增加**前置实验 E0**：先实测 TRIO/端口映射与 NHD 可观测性（§4），再决定 NHD 对比用什么计数器。若 TLR 确实不可见 NHD，NHD 实验只能依赖主机侧观测（host NIC 统计 + iperf 吞吐），方案已按两种结局分别给出执行路径 |
| 4 | 链末端写"DRAM"——DRAM 本身无计数器 | 链末端的观测点是 **MSS 边缘**（MEMORY_READS/WRITES，per-tile 聚合）与 **L3 EMEM 接口**（TOTAL_EMEM_RD/WR_REQ，出片流量），两者可对账 |
| 5 | 只选"同时触发多条路径"的 bench——但归因需要**单路径基线**作对照 | bench 矩阵分两组：**基线组**（每路径一条纯路径 bench，控制组）+ **实验组**（多路径并发 bench）。无基线的差分法无法落地 |
| 6 | "一个 application 运行过程中同时触发多条路径"——并发混合流量只能做比例归因，精度受限 | 把"同时"结构化：优先用**相位模板**（单次运行内分 A/B/C 相位）或**双 run 对照**（同负载加/减一条路径），使差分法可用（§3.1、§5.2） |
| 7 | 未提 IB 写方向的观测边界 | IB 读有专用计数器（MEMORY_READS_BYPASS），**IB 写无独立计数**（官方文档未定义）。IB 写只能经守恒式间接推断，方案中显式标注此局限 |
| 8 | 未提 per-tile 计数器的空间归属问题：每 tile 的计数器只统计**本 tile 的核**与**归属本 tile HNF 的地址片** | 多核 bench 需 taskset 对称绑核（8 核均分 4 tile），归因在 **Σ across tiles** 的聚合面上做；入口比例模型天然用聚合值，规避单 tile 地址片偏差 |

---

## 2. 硬件观测模型：路径 × 计数器覆盖矩阵

六条路径上可用的观测点分三类：**专用**（只反映该路径）、**共享**（多路径混合，需归因）、**不可见**。

| 路径 | 专用计数器 | 共享计数器（需归因） | 不可见/无直接观测 |
|---|---|---|---|
| CR | A72_ACCESS / A72_READ / A72_WRITE、RNF_REQUESTS（入口） | HNF_REQUESTS、DIR_HIT、ALLOCATE、VICTIM、POC_*、MEMORY_READS/WRITES、L3 全套、VICTIM_WRITE、MSS_NO_CREDIT、REQ_BUF_EMPTY、tilenet CDN/NDN/DDN | — |
| IH | IO_ACCESS / IO_READS / IO_WRITE / TSO_WRITE（入口） | 同 CR 的 HNF/MSS/L3 全部共享项 | — |
| IB | MEMORY_READS_BYPASS（**仅读方向**） | MEMORY_READS/WRITES、tilenet DDN | **IB 写**（无独立计数，守恒推断） |
| NAD | **pcie TLR 字节寄存器**（pcie0 DMA 剖面 + pcie1 交付剖面，E0-2 实测；[trio] TDMA 计数器实测不计数网卡 DMA，弃用）、triogen/SMMU TX_DAT_AF/RX_DAT_AF（网格数据通道 FIFO） | 进网格后与 IH/IB 混合：HNF/MSS/L3 | — |
| NHD | 无（**E0-1 实测 TLR 不可见**） | 无（Arm 侧全盲） | Arm 侧全部网格/缓存计数器；主机侧观测（host NIC stats、iperf3 吞吐、PCIe link 速率）可作替代 |
| WB | VICTIM_WRITE（tile 侧受害行写回）、L3 EVICTIONS_BANK0/1（逐出侧） | MEMORY_WRITES（写回汇入普通写流量） | WB 的触发路径归属（CR 写 vs IH 写），仅相位差分可辨 |

> 说明：IH 与 IB 的 bench 归属是**预期而非预设**——NVMe fio 直读预期为 IB 式旁路、virtio 网络接收预期为 IH 式相干，但最终以计数器读数裁定（哪个入口计数涨、BYPASS 是否涨、LLC 是否分配）。实验的职责是分类验证，不是按剧本套用。

---

## 3. 归因方法论（"流量拆分计算"的具体化）

### 3.1 相位差分法（首选）

**原理**：控制变量。两个窗口内负载相同、路径集合相差一条，共享计数器读数之差即该路径贡献。

- 相位 A（基线）：只跑 CR 负载（如 stress-ng --cpu 固定线程数）；
- 相位 B（实验）：CR 负载不变 + 叠加 IH 负载（如 fio NVMe 读）；
- IH 在共享计数器 C 上的贡献 = ΔC(B) − ΔC(A)。

**适用**：CR/IH/WB 归因；要求 A/B 相位 CR 负载严格一致（固定循环参数、绑核、预热后计窗）。
**注意**：轮换窗口对齐——差分双方取**同一轮换组**的样本（见 §6.4）；相位切换留 5 s 瞬态丢弃。

### 3.2 入口比例模型 + 守恒校验（并发混合流量）

**原理**：入口专用计数器给出各路径的注入比例，共享计数器按比例归属。

```
HNF_CR = HNF_REQUESTS × ΣA72_ACCESS / (ΣA72_ACCESS + ΣIO_ACCESS)
HNF_IH = HNF_REQUESTS × ΣIO_ACCESS / (ΣA72_ACCESS + ΣIO_ACCESS)
```

**成立条件与校验**：每请求 HNF 处理代价在路径间一致（α≈β）。用多相位回归验证线性性：

```
C = α·CR_in + β·IO_in,  拟合优度 R²；|α−β|/α < 10% 判定模型可用
```

若 α 与 β 显著不同，则"代价非均匀"本身就是发现，改用 3.1 相位差分。

### 3.3 专用计数器直接读数

- IB 读 = MEMORY_READS_BYPASS（专用，无需归因）
- 各路径入口 = A72_* / IO_* / TLR（NAD，E0-2 实测；TDMA_* 不计数网卡 DMA，弃用）
- WB 直接证据 = VICTIM_WRITE + EVICTIONS（配合相位差分定归属）

### 3.4 守恒校验方程（每次实验必须对账）

```
(1) HNF_REQUESTS  ≈ RNF_REQUESTS + 相干 IO 请求量（ΣIO_ACCESS，量级核对）
(2) MEMORY_READS  ≈ Σ(CR+IH 读未命中出片) + MEMORY_READS_BYPASS + Σtile 视窗内差异
(3) L3 出片对账：TOTAL_EMEM_RD_REQ_B0+B1 与 ΣMEMORY_READS 量级一致（读方向）
(4) 命中守恒：TOTAL_CACHE_RD_RES_IN_B0+B1 ≈ HITS_B0+B1（同 half 内核对）
(5) WB 佐证：VICTIM_WRITE 与 EVICTIONS 同升同降（写密集相位）
```

任一条失衡 >20% 都要查因（轮换窗口错位、相位负载漂移、背景流量），失衡本身不得被静默忽略。

---

## 4. 前置实验 E0：TRIO/端口映射与 NHD 可观测性

**目的**：① 决定论文 NHD 表述的最终措辞（上轮评审 CRITICAL #2）；② 决定 NHD bench 用哪组计数器。

**实验 E0-1（NHD 流）**：主机 ↔ BF2 网口跑 iperf3 直通（主机侧 p7 配置见 bench/README），采集器只开 `[pcie]` + `[trio]`：
- 观察 pcie0/pcie1 的 IN/OUT 字节计数是否增长；
- 若**都不涨** → 结论 NHD-A：Arm 侧 TLR 对 NHD 不可见，论文按"待验证/不可见"口径定稿，NHD 实验改用主机侧观测；
- 若**某个涨** → 结论 NHD-B：记录涨的 pcieN 与方向，NHD 获得 Arm 侧观测点，且该 TRIO 即主机向 TRIO。

**实验 E0-2（NAD 流，对照）**：BF2 Arm 侧本机 iperf3 收发，同样只开 `[pcie]` + `[trio]`：
- 预期 trio0 或 trio1 的 TDMA_DATA_BEAT 增长 + 对应 pcieN 字节增长 → 得到 NAD 的 TRIO/端口归属。

**实验 E0-3（eMMC 对照，已按实机无 NVMe 调整）**：fio `--direct=1` 文件直读（/root 下测试文件），观察 trio/pcie 计数器是否响应。预期不响应（eMMC 控制器不在 PCIe Switch 下），从而确认板载存储 DMA 仅走网格、无 PCIe 侧观测——这也是对路径框架边界的一个事实性确认。

**产出**：TRIO↔（主机接口、网口 Arm 面、NVMe）映射表 → 回填论文 PCIe 域段落与 §2 覆盖矩阵 → 解锁 NHD bench 设计。

> **✅ E0 全部完成（2026-09-11，详见 docs/e0-analysis.md）**：
> - **E0-1 → NHD-A**：33 Gbps 主机直通流量对 pcie0/pcie1 零响应——NHD 不经任何 Arm 侧 TRIO，Arm 侧 PMC 不可观测，NHD 观测改为主机侧手段。
> - **E0-2 → NAD 双链路剖面**（备选 B 通道：主机面 56.11→Arm 面 56.103 的 OVS 管道）：pcie0 = 网卡 Arm PF 收发队列 DMA 链路（方向跟随流量）；pcie1 = eSwitch/OVS 交付链路（双向 IN≈OUT≈流量）；**[trio] TDMA_DATA_BEAT 全程 0——TDMA 计数器不计数网卡 DMA，NAD 观测点改用 pcie TLR**。
> - **E0-3 → eMMC 不经 PCIe**：DMA 走网格 RN-I（tile IO_ACCESS 基线 5–10K→735K/s），与预期一致。
> - **映射表**：主机面 = 无 TRIO；网口 Arm 面 = TRIO0（DMA）+ TRIO1（交付）；eMMC = 无 PCIe；rshim 未占用 TRIO 主信号（E0-2b 可选）。

---

## 5. Bench 矩阵

### 5.1 基线组（单路径控制组，每项 3 run 取中位）

| ID | bench | 触发路径 | 关键读数 | 配置 |
|---|---|---|---|---|
| B1 | stress-ng --cpu 8 | CR | 入口 A72_* 全链参考 | path_cr.conf（tile 全 6 组 + L3 全 8 组） |
| B2 | STREAM（读/写 kernel 分开跑） | CR（+写 kernel 时 WB） | MEMORY_READS/WRITES、POC_*、EMEM | 同 B1 |
| B3 | memrand 1GB | CR（miss 型） | DIR_HIT、ALLOCATE、L3 HITS/MISSES | 同 B1 |
| B4 | stress-ng --cache 8 | CR + **WB** | VICTIM_WRITE、EVICTIONS、ALLOCATIONS | 同 B1 |
| B5 | fio eMMC 文件直读（`--direct=1`，实机无 NVMe，文件落 /root） | **IB**（预期，实测裁定） | MEMORY_READS_BYPASS、IO_*、tilenet DDN | path_ib.conf（+tilenet、trio、pcie） |
| B6 | iperf3 Arm 侧（本机收发） | **NAD** | pcie TLR（pcie0 DMA + pcie1 交付剖面）、TX/RX_DAT_AF、net 软件计数 | path_nad.conf（+triogen、smmu、pcie） |
| B7 | iperf3 主机直通 | **NHD** | 纯主机侧（host NIC stats、iperf3 吞吐、PCIe link 速率）——E0-1 定 NHD-A，Arm 侧全盲 | path_nhd.conf |

### 5.2 实验组（多路径，相位模板 A/B/C）

**相位模板与相位时长设计（2026-09-11 定稿）**：单 run 内多相位；**相位时长 = 24 s**——tile 轮换周期 6 s（6 组 × 1 s）与 L3 轮换周期 8 s（8 组 × 1 s）均整除 24，相位边界与组边界天然对齐，每组每相位得到 4（tile）/3（L3）个 1 s 样本，3 run 中位后每组每相位 ≥9 样本；计数速率 ≥1e6/s 量级下 1 s 样本噪声可忽略。单次运行模板：预热 5 s + A 24 s + B 24 s（+ C 24 s，三相位时）+ 收尾 5 s = 58 s（两相位）/ 82 s（三相位）。

| ID | 相位 A（基线） | 相位 B（叠加） | 目标归因 |
|---|---|---|---|
| M1（用户示例落地） | stress-ng --cpu 8 | + fio eMMC 文件直读（--direct=1） | HNF_REQUESTS / MEMORY_READS 拆 CR vs IH |
| M2 | stress-ng --cpu 8 | + memrand -w（写模式） | WB 拆分：VICTIM_WRITE/EVICTIONS 归 CR 写 vs 追加写 |
| M3 | iperf3 Arm 接收（NAD） | + stress-ng --cpu 4（CR 处理） | NAD 网格后段拆分：HNF/MSS 中 NAD vs CR |
| M4 | iperf3 主机直通（NHD） | + iperf3 Arm 侧同网口（NAD） | NAD vs NHD 同源流量对比：Arm 侧 TLR（NAD 涨、NHD 平，E0 实测）＋主机侧统计（双方同量） |
| M5 | fio eMMC `--direct=1`（旁路式） | + fio 页缓存缓冲读（Arm 参与，相干式） | IB vs IH 的 MSS 代价对比（BYPASS vs 相干） |

每个 M 实验对应一份**归因表**（§7.3）与守恒校验表。

---

## 6. Config 设计规范

### 6.1 槽位预算（硬件上限，不可突破）

| 块 | 槽位 | 事件池 | 策略 |
|---|---|---|---|
| tile0–3 | 4/tile | 22 | 6 组轮换（paper52 分组复用） |
| l3cachehalf0–1 | 4/half（官方提 5th，未验证前按 4） | 30 | 8 组轮换 + 整体使能（enable 机制） |
| tilenet0–3 | 4/tile | 35 | 1 组（CDN/DDN/NDN_REQ 三事件） |
| trio0–1 | 4/个 | 16 | 1 组（TDMA 4 事件，paper52 外按需） |
| triogen0–1 | 1/个 | 2 | 全采（免费） |
| smmu0 | 4 | 3 | 全采（TBU_MISS/TX/RX_DAT_AF） |
| pcie0–1（TLR） | 无槽位（机制二） | 12 | **全开**（免费，且是 NHD 唯一候选） |

### 6.2 配置命名与文件

- 基线/实验配置放 `configs/path_<id>.conf`（如 path_cr.conf、path_nad.conf、path_m1.conf），格式沿用 paper52.conf（[global]/[tile] groupN/…/[块] enabled）。
- 原则：**一次 bench 打开其路径链上全部块**；锚点计数器（A72_ACCESS、MEMORY_READS、CYCLES）在轮换中双组重复，保证任何窗口都有时间基与流量锚。

### 6.3 关键差异点（相对 paper52.conf）

1. 打开 [tilenet]、[trio]、[triogen]、[smmu]、[pcie]（paper52 全 disabled）；
2. [net] 软件计数（/sys/class/net 统计）打开，作为 NAD/NHD 的外部对照；
3. 相位差分时轮换周期须整除相位时长——已按 24 s 相位与 tile 6 s / L3 8 s 轮换周期对齐（24 % 6 = 24 % 8 = 0），无需改动轮换；
4. run_bench.sh 的 70 s 固定窗口需扩展为相位式（新增 run_phase.sh 或复用现有脚本扩展，见 §8 里程碑）。

### 6.4 相位与轮换对齐规则

差分双方必须取**同一轮换组号**的样本区间：对每个 group g，只在该组激活窗口内计算 delta 并跨相位求差。禁止跨组混算（轮换是时间分片，组间不同时性会污染差分）。

---

## 7. 采集执行与后处理

### 7.1 运行规程（沿用并扩展 bench/README 已有规范）

- 每个配置 3 run，取中位（median-of-3，与 v1 试运行一致）；
- 窗口：预热 5 s → 相位段 → 收尾 5 s，裁首尾后留稳定段；
- 多核 bench 用 taskset 对称绑核（如 stress-ng 8 线程绑 8 核）；
- 记录环境：后台服务关闭、CPU 频率、相位时间戳写入 CSV 注释行或 sidecar。

### 7.2 拆分计算脚本

新增 `tools/split_path.py`：
- 输入：path_<id>.csv（含轮换组标记）+ 相位 sidecar（相位名/起止行号）；
- 输出：**归因表**（每共享计数器一行：相位 A 速率、相位 B 速率、Δ、归因路径、入口比例交叉验证值）+ **守恒校验表**（§3.4 五式逐条对账）；
- 入口比例模型附带回归输出（α/β/R²）。

### 7.3 输出物模板（以 M1 为例）

```
计数器        | A(CR)速率/s | B(CR+IH)速率/s | Δ(IH)   | 比例模型 IH | 偏差%
HNF_REQUESTS  | 1.2e7       | 2.1e7          | 9e6     | 8.6e6       | 4.4%
MEMORY_READS  | 3e5         | 5e5            | 2e5     | ...         | ...
守恒校验: (1) ✓ 4% (2) ✓ 7% ...
结论: IH 占 HNF 处理量 ~43%，比例模型与相位差分一致 → 方法验证通过
```

---

## 8. 执行顺序与里程碑

1. **E0** ✅（2026-09-11 完成，结果见 §4 与 docs/e0-analysis.md）：NHD-A（TLR 不可见）+ NAD 双链路剖面（pcie0 DMA / pcie1 交付）+ eMMC 不经 PCIe → 论文 PCIe 域表述已定稿方向、NHD 观测集定为主机侧；
2. **基线组 B1–B7**：先出单路径速率基线与轮换对齐验证（check_csv.py 复用）；
3. **实验组 M1**（用户示例）：完整跑通差分 + 比例模型 + 守恒校验三件套，**方法定型后**再铺开 M2–M5；
4. **工具**：run_phase.sh（相位式运行）+ tools/split_path.py 在 M1 前完成；
5. **全矩阵 + 出图**：每 M 实验一张归因图（共享计数器堆叠：A 基线 / Δ贡献），与论文"路径流量分析"小节逐条对应；
6. 数据回传后本地 pandas 后处理（复用 v1 画图管线）。

## 9. 风险与应对

| 风险 | 应对 |
|---|---|
| ~~E0 证明 TLR 对 NHD 不可见~~ **已发生（E0-1 实测）** | NHD-A 分支生效：NHD 观测为主机侧（host NIC stats、iperf3 吞吐、PCIe link 速率），论文表述按"Arm 侧不可见"定稿；M4 对比 = Arm 侧 TLR 平（NHD）vs 涨（NAD）+ 主机侧双方统计 |
| 相位负载漂移（stress-ng 恒定性不足） | 用固定迭代次数的自研循环（memrand 风格）替代；sidecar 记录每相位 CPU 利用率作质控 |
| 轮换不整除相位 | §6.3 规则 3：调整轮换周期至整除 |
| tile 空间不对称（地址片偏差） | 全部归因在 Σtile 聚合面做；绑核对称；若聚合校验失衡，检查单 tile 分布 |
| fio 走 tmpfs 不触发 DMA；实机无 NVMe（系统盘 eMMC） | 用 eMMC 测试文件 + `--direct=1`；**严禁 `--filename=/dev/mmcblk0` 裸设备**（eMMC 是系统盘，写裸设备毁系统）；测试文件大小先看 `df -h /` 余量（建议 1–2 GB）；网络流（iperf3 接收）作备选。**⚠️ E0-3 实测教训（2026-09-11）：`fallocate` 创建的文件是"未写扩展区"，读取由内核填零、完全不落盘（mmcblk0 ios=0，速率 5.9 GB/s 远超 eMMC 上限）——测试文件必须先写真实数据**：`fio --rw=write --direct=1 --size=2G --bs=128k --name=warmup` 或 `dd if=/dev/zero of=... oflag=direct`，写完再跑读相位；每次读实验后核对 fio 输出末尾 `mmcblk0: ios=` 确认真实落盘 |
| IB 写无计数 | 守恒推断 + 在论文中显式声明该边界（与 IB 段"写方向未定义独立计数"呼应） |
| 设备 apt 不通、无外网 | 全部二进制静态交叉编译后离线部署（既有流程），新工具同规 |

## 10. 与论文衔接

| 实验 | 支撑论文内容 |
|---|---|
| E0 | ✅ PCIe 域段落定稿（NHD 不可见 + NAD 双 TRIO 可见，E0 实测）；附录 TLR Scope 注释 |
| B1–B4 | CR/WB 基线速率；图 X 各路径流量量级 |
| B5/B6/B7 | IB/NAD/NHD 单路径刻画；IB 读专用计数验证 |
| M1 | HNF 汇聚点 CR/IH 拆分——分类节总结段"逐路径流量分析"能力的直接证据 |
| M2 | WB 子路径实证（VICTIM_WRITE 与 EVICTIONS 相关性） |
| M3/M4/M5 | NAD 网格后段归因、NAD/NHD 对比、IB/IH 代价对比——数据路径节各段"使用场景"句的事实支撑 |

---

## 待用户确认事项

1. ~~六路径命名~~ **已定（2026-09-11 用户确认）：按论文口径 CR/IH/IB/NAD/NHD/WB，WB 为子路径**
2. ~~设备是否有 NVMe~~ **已定（2026-09-11 实机核查）：无 NVMe，系统盘为 eMMC（mmcblk0）；B5/M1/M5 已改 eMMC 文件直读方案**
3. ~~相位模板时长~~ **已定（2026-09-11）：相位 24 s，模板 5+24+24(+24)+5 s；run_bench.sh 扩展方式 = 新增 run_phase.sh（E0 完成后动工）**
4. ~~E0 排期~~ **已定（2026-09-11 用户确认立即执行）：材料见 configs/e0_trio_map.conf + docs/e0-opsheet.md**
