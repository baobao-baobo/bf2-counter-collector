# E1 实验设计：eSwitch 流量去向分解（NAD vs NHD 可同时可见）

日期：2026-09-14
状态：设计稿，待用户/师兄确认后实施
前提文档：docs/e0-analysis.md（更正节）、docs/apps-path-experiment-plan.md、memory/bf2-pcie-collector.md

## 1. 目标与理想效果

**问题**：PMC 计数器框架内，pcie0/pcie1 是 Arm 根复合体（TRIO）TLR 统计，纯 NIC↔主机直通（NHD）流量不经过 Arm 根复合体，因此 E0-1 对 33 Gbps 零响应；但 eSwitch 把 NHD 流量交换给主机 PF，该端口的统计（Arm 侧可读）能看到它。

**理想效果**：在 NAD 与 NHD 两类数据路径下，绘制出 eSwitch 流量的**去向分解**——每类负载的流量中，走 Arm 端的字节数与走 host 端的字节数。

**理想主图**（图 2）：横轴为各应用负载，每个负载两根柱：

- eSwitch→Host 分量（NHD，深红 #B2172B）
- eSwitch→Arm 分量（NAD，浅橙 #F5A682）
- NAD 型负载（Redis-Arm、Mix）Arm 柱占主导；NHD 型负载（Redis-Host、iperf3 直通）host 柱占主导，去向翻转一目了然。

## 2. eSwitch 端口语义与方向定义

设备拓扑（2026-09-14 数据流复勘后修正；依据：E0 操作单实况 `ovs-vsctl show`）：

```
wire -- p1(物理口,接实验室L2) -- ovsbr1 --+-- pf1hpf -- PCIe0 -- 主机 PF1（56.11）
                                         +-- Arm SF0 en3f1pf1sf0（56.103，NAD 端点）
pf0hpf -- PCIe0 -- 主机 PF0（不在桥里；仅主机 PF0 面流量/尝试 A hairpin 经过）
```

| 端口/统计                | 身份                             | tx 方向含义                        | rx 方向含义               |
| -------------------- | ------------------------------ | ------------------------------ | --------------------- |
| p1（物理口）              | wire 入口，ovsbr1 成员              | eSwitch→wire                   | **wire→eSwitch（入口）**  |
| pf1hpf（representor）  | 主机 PF1 在 eSwitch 的端口，ovsbr1 成员 | eSwitch→主机（NHD 交付 + Arm→56.11） | 主机→eSwitch（56.11→Arm） |
| Arm SF0（en3f1pf1sf0） | NAD 端点，ovsbr1 成员               | Arm→桥                          | 桥→Arm（NAD 交付字节）       |
| pf0hpf（representor）  | 主机 PF0 在 eSwitch 的端口，**不在桥里**  | eSwitch→PF0                    | PF0→eSwitch           |

**去向判定必须用组合判据**（pf1hpf 同时承载 NHD 与 56.x 两类流量，单口无法区分）：

- **NHD（wire→主机直通）**：p1_rx ≈ pf1hpf_tx ≈ 流量，且 pf1hpf_rx ≈ 0、Arm 网口 ≈ 0
- **NAD（host→Arm，56.x 前向）**：pf1hpf_rx ≈ Arm 网口 rx ≈ 流量，且 p1 ≈ 0
- **NAD（Arm→host，56.x 反向）**：Arm 网口 tx ≈ pf1hpf_tx ≈ 流量，且 p1_tx ≈ 0

交叉印证关系：56.x 管道上 pf1hpf 与 Arm 网口字节应近似相等（同桥同方向）；NHD 直通上 p1_rx ≈ pf1hpf_tx ≈ iperf3 速率。

**数据流复勘（2026-09-14，用户确认前的底稿）**：

- E0-1 尝试 A（33.1 Gbps）：client/server 同在 fujian，数据 fujian→PF0→PCIe0→eSwitch 反射→PCIe0→fujian（hairpin，无 wire 无 Arm）；ACK 走 lo（目标 172.28.4.77 为本机地址）。
- E0-1 尝试 B（34.5 Gbps）：fujian→eno1→L2→p1→ovsbr1→pf1hpf→PCIe0→主机 PF1→fujian（真 NHD，交付口 = pf1hpf）；**ACK 同样走 lo** → 当天实测为单向 wire→host 数据流。
- E0-2 备选 B（6.62/11.5 Gbps）：fujian(PF1 56.11) ↔ eSwitch/ovsbr1 ↔ Arm SF0(56.103)，两端不同协议栈，双向真实跨链路，无 wire。
- **对 E1 的硬约束**：iperf3 client 与 server 不得同机（否则反向走 lo）；NHD 观测口 = p1+pf1hpf 组合判据（pf0hpf 仅在打 PF0 面流量时用）。

## 3. 采集方案

### 3.1 采集器改动（code/collect_net.c 扩展，ASCII 注释）

现状：collect_net.c 遍历所有 /sys/class/net/<iface>/statistics/，只输出聚合 net_rx/net_tx。改动：为配置列表中的每个接口追加两列 `<iface>_rx_bytes, <iface>_tx_bytes`（每接口 delta 方法，字节/秒），配置从 conf 文件读取：

```
[esw] ifaces = pf0hpf, pf1hpf, p1, en3f1pf1sf0
```

- 新增列（以设备实际接口名为准）：`pf0hpf_rx_bytes, pf0hpf_tx_bytes, pf1hpf_rx_bytes, pf1hpf_tx_bytes, p1_rx_bytes, p1_tx_bytes, en3f1pf1sf0_rx_bytes, en3f1pf1sf0_tx_bytes`（接口名以 E1-0 核查结果为准）
- 其余不变：pcie0/pcie1（PMC，继续采集作对照）、tile/trio/smmu/l3 等照旧
- 采样间隔 1 s，窗口协议沿用 G 系列（pre-idle 10 s / 负载 / post-idle 10 s，.phase.log 记录相位）

### 3.2 数据处理

- 新工具或扩展 tools/path_data.py：按相位切窗，各端口求窗口均值（B/s），减去各自 idle 基线得净流量
- 校验不等式：pf1hpf 与 Arm 网口净流量偏差应 <5%（管道自洽）；pf0hpf tx ≈ p0 rx（NHD 自洽）

## 4. 应用清单（现有 + 补充）

| 编号         | 负载                                                                            | 部署         | 路径类型                                   | 状态                                                          |
| ---------- | ----------------------------------------------------------------------------- | ---------- | -------------------------------------- | ----------------------------------------------------------- |
| G3         | Redis+YCSB（server 在 Arm 56.103，client 在 fujian 56.11）                         | 现有         | NAD                                    | ✅ 已有                                                        |
| G7         | Redis+SQLite 混合                                                               | 现有         | NAD（含 CR）                              | ✅ 已有                                                        |
| N0         | iperf3 56.x 管道（host↔Arm）                                                      | 复用 E0-2 通路 | NAD 定标                                 | ✅ 已有（E0-2 数据）                                               |
| **N1**     | iperf3 wire→主机直通（复用 E0-1 尝试 B 通路），多速率 1/5/10/20 Gbps × 8 s                    | **补充**     | NHD 定标                                 | 需跑；⚠️ client 必须在**另一台机器**（E0-1 同机时 ACK 走 lo，反向失真）           |
| **N2**     | Redis-host 版：redis-server 跑在 fujian 主机，客户端从 wire 侧另一台机器进入（经 p1→ovsbr1→pf1hpf） | **补充**     | NHD 应用级                                | 需部署（fujian 上 apt 装 redis-server；客户端放实验室网另一台机器，与 G3 客户端负载对称） |
| **N3（可选）** | RDMA 读/写（host 端点）或 OVS 硬件 offload 转发（对应对接 offload 通道清单）                       | 视实验室环境     | NHD 应用级（RDMA 穿越 PCIe0 一次，OSDI'23 §3.3） | 待师兄确认环境                                                     |

设计要点：**N2 与 G3 是同一应用、同一负载（YCSB）、仅 server 部署位置不同**——eSwitch 去向翻转的对照最干净，论文叙述力最强。N1 提供可控速率的定标点。

## 5. 实验步骤

### E1-0 设备侧端口核查（第一步，必须）

在 BF2 Arm 上执行并记录输出：

```bash
ip link                        # 列出所有网口，确认 pf0hpf/pf1hpf/p0/Arm 网口的确切名字
for i in /sys/class/net/*/; do n=$(basename $i); [ -f $i/statistics/rx_bytes ] && echo $n $(cat $i/statistics/rx_bytes) $(cat $i/statistics/tx_bytes); done
ethtool -S pf0hpf | grep -i bytes   # 确认 vport 字节计数器存在（fallback 备用）
```

输出：端口清单表（名字、统计文件是否更新、vport 计数器名）。若 representor 的 statistics 不随流量更新 → 改用 `ethtool -S` 的 vport 字节计数器（见第 7 节回退）。

### E1-1 方向定标（两通路 iperf3，各方向各 1 次 × 8 s）

| 场景     | 通路                                                   | 预期      | 判据                                                   |
| ------ | ---------------------------------------------------- | ------- | ---------------------------------------------------- |
| 1a     | wire→主机直通（E0-1 尝试 B 通路，速率 10 Gbps，client 在**另一台机器**） | NHD     | p1_rx ≈ pf1hpf_tx ≈ 10 Gbps；pf1hpf_rx ≈ 0；Arm 网口 ≈ 0 |
| 1b     | 反向（主机→wire 10 Gbps，server 在另一台机器）                    | NHD 反向  | pf1hpf_rx ≈ p1_tx ≈ 10 Gbps；Arm 网口 ≈ 0               |
| 1c（可选） | 尝试 A hairpin（fujian 同机打 192.168.101.1，PF0 面）         | PF0 面定标 | pf0hpf 双向 ≈ 速率；p1、pf1hpf ≈ 0                         |
| 2a     | 56.x 管道 host→Arm（E0-2 通路）                            | NAD     | Arm 网口 rx ≈ pf1hpf_rx ≈ 速率；p1 ≈ 0                    |
| 2b     | 反向 Arm→host                                          | NAD 反向  | Arm 网口 tx ≈ pf1hpf_tx ≈ 速率；p1_tx ≈ 0                 |

判据通过 → 方向语义表（第 2 节）成立，进入 E1-2；不通过 → 回到 E1-0 重审端口身份（可能与师兄确认拓扑细节）。

### E1-2 应用矩阵

| 负载                            | 次数    | 时长       | 说明            |
| ----------------------------- | ----- | -------- | ------------- |
| N1 多速率 iperf3（1/5/10/20 Gbps） | 各 1 次 | 8 s      | NHD 定标线（图 1）  |
| N0 56.x iperf3（10 Gbps 级）     | 1 次   | 8 s      | NAD 定标线（图 1）  |
| G3 Redis-Arm                  | 3 次   | 按 G 系列窗口 | 主图 NAD 柱      |
| G7 Mix                        | 3 次   | 按 G 系列窗口 | 主图 NAD 柱（含背景） |
| N2 Redis-Host                 | 3 次   | 同 G3 窗口  | 主图 NHD 柱      |
| N3 RDMA/offload（若环境具备）        | 3 次   | 待定       | 主图 NHD 柱（可选）  |

每轮均保留 pre/post-idle 基线；每轮同时记录 pf0hpf/pf1hpf/p0/Arm 网口 + pcie0/pcie1 PMC 对照列。

## 6. 产出图稿（风格沿用既定规范）

1. **图 1 定标验证图**（证据图）：4 场景（1a/1b/2a/2b）× 各端口净速率柱——证明方向语义与自洽（谁动谁不动）。
2. **图 2 主图：eSwitch 去向分解**：每负载两根柱（eSwitch→Host 深红 #B2172B / eSwitch→Arm 浅橙 #F5A682），线性 y/1e6 粗整数刻度，图例右上外侧，Arial 16/22/20，四边框全留。数据：表 4 各负载窗口均值（净流量，含 56.x 背景标注）。
3. **图 3 论文证据图**：NHD 负载（N1/N2）下 PMC pcie0/pcie1（≈0）vs eSwitch 主机口统计（p1+pf1hpf，≈流量）对照——直接支撑 CRITICAL #2"Arm 根复合体 TLR 计数器对 NHD 不可见，eSwitch 端口统计可见"。

## 7. 风险与回退

| 风险                                          | 回退                                                                           |
| ------------------------------------------- | ---------------------------------------------------------------------------- |
| representor sysfs statistics 不随流量更新（驱动差异）   | 用 `ethtool -S pf1hpf` 的 vport 字节计数器（E1-0 已确认计数器名），采集器改调 ethtool 或 debugfs 路径 |
| pf1hpf 存在 56.x 他人业务背景流量                     | 每轮 idle 基线扣除；组合判据（联立 p1）区分去向；主图标注净流量口径                                       |
| 56.x 管道共享背景（G3/G7 pcie0 残差 219-251 MB/s 同源） | 主图附注"Arm 分量含共享管道背景"；另出一版净流量（减 idle 基线）                                       |
| 主机面应用打不通（ARP/路由问题，E0-1 曾遇）                  | 复用 E0-1 已验证的 /32 路由强制方案；N2 的 wire 侧客户端与 N1 共用已调通的二层通路                        |
| ethtool 每秒 spawn 开销过大                       | 仅在 sysfs 不更新时启用，且放宽间隔至 5 s                                                   |

## 8. 与论文对接

- CRITICAL #2 措辞升级：从"BF2 计数器测不到 NHD"→"Arm 根复合体 TLR 计数器对 NHD 不可见；NHD 可通过 eSwitch 主机口统计（pf0hpf）采集"——图 3 为直接证据。
- 图 2 可作为论文"eSwitch 流量去向分解"主图，覆盖 NAD/NHD 两条路径的实证对比。
- N2（Redis 双部署）为论文提供"同一应用、路径翻转"的对照叙述。

## 待确认清单（提交前）

1. E1-0 端口核查由用户按第 5 节命令在设备执行，结果回传后敲定采集器接口名列表；
2. N2 是否可接受（fujian 上装 redis-server）；N3 是否具备环境（问师兄）；
3. 师兄是否指过更具体的采集点（若与 pf0hpf 不同，按师兄口径调整）。
