# E0 实验结果分析（2026-09-11）

> 实验材料：configs/e0_trio_map.conf、docs/e0-opsheet.md；原始数据：message/host-message.txt（宿主侧）、message/bf2-message_1.txt（设备侧屏幕）、message/e0_1_nhd.csv、e0_2_nad.csv、e0_3_emmc.csv（50/49/35 s 采集）。
> 状态：E0-1 ✅ E0-2 ✅（备选 B 通道，2026-09-11）E0-3 ✅；E0-2b（rshim 对照）可选、低优先级。

## E0-1：NHD（主机直通）——判定 NHD-A（主机直通流量对 Arm 侧计数器不可见）

**宿主侧**（host-message.txt）：两条通路均连接成功且 0 重传——

- 尝试 A（直连主机面）：33.1 Gbps × 8 s = 30.9 GB；
- 尝试 B（/32 路由强制经 eno1，真 NHD 通路）：34.5 Gbps × 8 s = 32.1 GB。
- 结论：BF2 物理口接在实验室 L2 上、eSwitch 有主机直通桥（port↔host PF），**NHD 流量生成通路完全打通**，B 姿势可作为后续 NHD bench 的标准方法。

**设备侧**（e0_1_nhd.csv，50 行）：

| 观察                                                        | 数值                                                              | 含义                                        |
| --------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------- |
| trio 4 事件（TDMA_DATA_BEAT/RT_AF/PBUF_MAC_AF/WRQ_BUF_EMPTY） | **全程 0**                                                        | NHD 流量完全不经过 Arm 侧 TRIO DMA 数据面 ✓（与理论预期一致） |
| pcie0_rx/tx                                               | 基线 220/12438 字节/s，每 5 s 一个 ~780 字节小脉冲                           | 与 net 列的 5 s 心跳**同步出现**（同一行）              |
| pcie1_rx/tx                                               | 基线 25–40K/80–150K 字节/s，每 5 s 一个 ~135K/~350K 脉冲（与 pcie0 错开 ~2 s） | 持续背景 + 周期脉冲                               |
| **pcie0/pcie1 与 iperf3 的相关性**                             | **无**。行 1–20（33 Gbps 流量段）与行 20–50（空载段）读数同量级、同分布                 | **4 GB/s 级流量没有在任何 PCIe 字节列留下痕迹**          |
| smmu TX/RX_DAT_AF                                         | 4–9 / 1–3 次/s 平稳背景                                              | 网格数据通道 FIFO 无流量事件                         |
| tile A72_ACCESS                                           | ~0.6–1.3M/s 背景，行 3 一个 10.75M 尖峰                                 | 与 iperf3 无关的 Arm 侧偶发活动                    |

**判读**：

1. **NHD-A 成立**：主机↔主机面 PF 的流量走内部 PCIe switch 直达 eSwitch/网卡复合体，**不经过任何 TRIO 根复合体链路**——两套 TLR 字节计数器（P/NP/C 全覆盖）对 33 Gbps 毫无反应。Arm 侧 PMC 对 NHD 路径不可见（实测验证）。
2. **pcie0 假说（已由 E0-2 推翻）**：E0-1 时猜测 pcie0 的 5 s 脉冲 = rshim（PCIe 版）管理流量。E0-2 证明 pcie0 = **主机面 PCIe 链路**（承载 56.x 主机↔Arm 管道流量，前向 IN≈流量）；E0-1 的 5 s 小脉冲实为 56.x 管道（他人主机↔Arm 流量）的周期包，经 Arm PF 收发同步计入 net 软件计数。（链路身份 2026-09-14 与师兄讨论后更正，见文末更正节。）
3. **pcie1 假说（已由 E0-2 证实并细化）**：持续背景（TX>RX ≈ 3:1）+ 错位 5 s 脉冲 = 56.x OVS 管道（pf1hpf↔p1↔Arm PF 桥）上他人流量的背景；E0-2 正/反向剖面证实 pcie1 = **Arm 子系统 PCIe 链路**（交付/回注双向，见 E0-2 节与文末更正节）。
4. 尝试 A 的流量同样未留下痕迹 → 主机↔主机直通流量即使跨越主机面链路也不触发 pcie 计数（2026-09-14 更正后 pcie0 = 主机面链路的身份需按此口径理解：计数覆盖主机面↔Arm 方向的路径，纯主机间桥接不触发——口径以师兄确认为准，见文末更正节）。

## E0-2（NAD，备选 B 通道）——主机面 + Arm 子系统双 PCIe 链路实锤（链路身份 2026-09-14 更正）

**执行路径**（2026-09-11）：主尝试（端口 0 wire NAD，172.28.4.250）与备选 A（eno1 wire）均因二层不通放弃（ping 回 Destination Host Unreachable = ARP 无人应答，iperf3 的 EBADF 报错即此之表象）；**备选 B（主机面 56.11→Arm 面 56.103，PCIe→eSwitch→Arm 内部通道）成功**：ping 通、iperf3 两方向连上。该通道实质是实验室已有的 **56.x OVS 主机↔Arm 管道**（pf1hpf 与 Arm PF 同桥、同 /24 网段），并非纯 wire NAD——但对"Arm 面 TRIO 是哪个、trio/TLR 计数器如何响应网卡流量"这一 E0-2 核心问题完全有效：**网口到 Arm 的交付与 DMA 全部跨越 Arm 侧 TRIO，与流量来源（wire 或主机面）无关**。

**宿主侧**（host-message.txt，两方向）：

- 前向（主机→Arm）：**6.62 Gbps**，7.70 GB/10 s，**62,910 重传**，cwnd 全程卡 102–156 KB；
- 反向（Arm→主机）：**11.5 Gbps**，13.4 GB/10 s，7,405 重传。

**设备侧**（e0_2_nad.csv，49 行；行 5–14 = 前向，行 16–30 = 空档，行 31–41 = 反向）：

| 观察              | 前向（数据进 Arm，828 MB/s） | 反向（数据出 Arm，1.44 GB/s） | 含义                         |
| --------------- | -------------------- | --------------------- | -------------------------- |
| pcie0_rx        | 738–899M **≈ 流量**    | 11–13M                | 主机面入站（管道进 DPU）             |
| pcie0_tx        | 7–8M（控制面）            | 1.61–1.91G **≈ 流量**   | 主机面出站（DPU 回主机）             |
| pcie1_rx        | 815–992M **≈ 流量**    | 1.48–1.76G **≈ 流量**   | Arm 子系统入站（交付）              |
| pcie1_tx        | 748–911M **≈ 流量**    | 1.35–1.60G **≈ 流量**   | Arm 子系统出站（回注）              |
| net_rx / net_tx | **逐行相等**，850–950M    | **逐行相等**，1.1–2.0G     | Arm 面收发字节对称                |
| trio 4 事件       | **全程 0**             | **全程 0**              | TDMA 计数器不计数网卡 DMA          |
| smmu / triogen  | 背景不变                 | 背景不变                  | 此速率下网格 FIFO 无拥塞事件          |
| A72_ACCESS      | 34–39M/s             | 45–75M/s              | iperf3 用户态收发处理             |
| IO_ACCESS       | 12.7–15.5M/s         | 22–26M/s              | mlx5 驱动 MMIO（门铃/描述符），发送侧更重 |

**判读**：

1. **NAD 对 Arm 侧 PCIe TLR 完全可见**——E0-2 核心问题答"是"。且是**双链路结构**（链路身份 2026-09-14 经与师兄讨论更正，原"Arm PF DMA/eSwitch 交付"标签作废，见文末更正节）：
   - **pcie0 = 主机面 PCIe 链路**：承载 56.x 主机↔Arm 管道流量，方向严格跟随流量（收时 IN≈流量/OUT≈0，发时 OUT≈流量/IN≈0），两相位互证，教科书级 DMA 剖面；
   - **pcie1 = Arm 子系统 PCIe 链路**：交付进桥（IN）与桥回注（OUT）双向对称，IN≈OUT≈流量。
2. **每帧跨主机面链路一次 + Arm 子系统链路两次的自洽模型**（前向为例）：帧自主机经 56.x 管道进 DPU（pcie0 IN）→ eSwitch → 交付 Arm 桥（pcie1 IN）→ 桥回注到 Arm 面接口（pcie1 OUT，即桥内 SF0 en3f1pf1sf0）→ eSwitch 环回至 Arm 面 SF 收包路径（CX6 复合体内部，不跨计数链路）→ 协议栈。反向对称：Arm 发包（CX6 内部）→ 环回交付桥（pcie1 IN）→ 桥出口 pf1hpf 回注（pcie1 OUT）→ 主机面链路出站（pcie0 OUT）→ 主机。该模型逐列吻合四个剖面（pcie0 前/后向、pcie1 前/后向）。**net rx≈tx 的解释 2026-09-14 已更正**（E1-0 设备核查发现 en3f1pf1sf0↔enp3s0f1s0 为 rx/tx 互换镜像对，net 聚合含两口 → rx=进+出、tx=出+进 恒等，属镜像结构必然，非"环回签名"）；前向 62,910 重传仍由回注路径高压丢包解释（cwnd 卡死；反向不经过 Arm PF 回注、仅 7,405 重传）。
3. **[trio] 块 TDMA_DATA_BEAT 全程为 0** → 方案中"NAD 用 trio TDMA 计数器观测"的假设被实测证伪。**NAD 的正确观测点是 pcie TLR 字节寄存器**（pcie0 方向剖面 + pcie1 对称剖面），方案文档 §4 路径表 NAD 行须据此修订。
4. E0-1 遗留假说裁定：pcie0=rshim 假说**推翻**（其 5 s 脉冲实为 56.x 管道周期包）；pcie1=Arm↔NIC 假说**证实**并细化为"Arm 子系统 PCIe 链路"。E0-1 里 pcie1 的 25–40K/80–150K 背景 = 56.x 管道上他人流量的持续背景。
5. 性能数据点（论文可引用）：同样跨 Arm 面，**收包方向（6.62 Gbps、62K 重传）显著弱于发包方向（11.5 Gbps）**——A72 弱核 + OVS 软件回注在收路径上的代价，正是"NAD 路径开销"论点的实测佐证。

**OVS 实况确认（2026-09-11 用户执行）**：`ovs-vsctl show` 坐实桥结构——**ovsbr1 = {p1, pf1hpf, en3f1pf1sf0, 内部口}**，Arm 面成员是端口 1 的 **SF0（en3f1pf1sf0）**，与预期结构一致（物理口 + 主机面 + Arm 面接口同桥同段）；ovsbr1 内 en3f1pf1sf2 与 ovsbr2 内 en3f1pf1sf3 报 "No such device"，系先前删除 SF 留下的死端口条目，无害。修正一处接口名：正文两跨模型中的 "Arm PF" 即该 SF 接口——PF（enp3s0f1s0）与其 SF 均跨越同一 Arm 侧 TRIO，计数剖面不受影响。残留两问：① **已解决（2026-09-14，E1-0 核查）**：`ip -br addr` 实测 56.103 在 enp3s0f1s0（Arm PF1）；桥成员 en3f1pf1sf0 为其镜像 representor（rx/tx 互换，34.23↔34.20、22.98↔22.13 GB 实锤）；② `ovs-ofctl dump-flows ovsbr1` 看流表（仍低优先级；E1 用到的两条通路 p1↔pf1hpf、pf1hpf↔Arm 面均已被 E0 实测打通）。

## 对论文的影响（待用户批准后改）

- 评审 CRITICAL #2（"NHD 仅 TLR 可见"无官方依据）现在有实测结论：**NHD 对 Arm 侧计数器不可见**（E0-1）；**NAD 对 Arm 侧 TLR 可见**（E0-2，pcie0 主机面链路 + pcie1 Arm 子系统链路双剖面）。论文 PCIe 域表述方向改为："主机直通流量不经 Arm 侧 PCIe 根复合体，Arm 侧 PMC 不可观测（E0 实测）；交付 Arm 的流量必经 Arm 子系统 PCIe 链路（pcie1，E0 实测 IN≈OUT 剖面），经 56.x 主机面管道的流量另跨主机面链路（pcie0，方向剖面），TLR 字节寄存器可完整观测（E0 实测）；NHD 观测采用主机侧手段"。
- 与 7 通道图的"链路 B 完全旁路 Arm 域"表述一致，可互相印证。
- 方案文档 §4 路径表 NAD 行修订：观测点从 trio TDMA_DATA_BEAT 改为 **pcie TLR 字节寄存器**（[trio] TDMA 计数器实测不计数网卡 DMA）。

## TRIO↔端口映射表（E0 完成后汇总）

| 链路/块                    | 实际承载                             | 裁定证据                                                  |
| ----------------------- | -------------------------------- | ----------------------------------------------------- |
| pcie0（主机面 PCIe）         | 56.x 主机↔Arm 管道流量（主机面进出 DPU）      | E0-2 方向剖面（前向 IN≈流量，后向 OUT≈流量）；E0-1 纯主机间桥接不触发（口径待师兄确认） |
| pcie1（Arm 子系统 PCIe）     | Arm 侧交付/回注链路（eSwitch 桥接流量进出 Arm） | E0-2 双向 IN≈OUT≈流量；E0-1 背景 = 56.x 管道他人流量               |
| trio0/trio1 TDMA 事件     | 不计数网卡 DMA（用途另行，弃用）               | E0-2 全程 0 @ 6.6–11.5 Gbps                             |
| 主机面（NHD 直通桥接）           | pcie0/pcie1 均无响应（纯主机间桥接不在计数路径上）  | E0-1 33 Gbps 零响应                                      |
| eMMC                    | 不经 PCIe（网格 RN-I）                 | E0-3                                                  |
| rshim 管理（192.168.100.x） | 未占用 TRIO 主信号；E0-2b 对照可选（低优先级）    | —                                                     |

## E0-3（eMMC 对照）——第一轮作废 + 第二轮成功

**第一轮作废**（fallocate 教训）：`bw=5674MiB/s`、`mmcblk0: ios=0/148`、sys=93.68%——fallocate 的"未写扩展区"读取由内核填零、不落盘。A72_ACCESS 冲到 50M/s（syscall 洪流）反而留下一个数据点。教训已写入方案文档 §9 与操作单 §3：测试文件必须 fio write pass 真实落盘。

**第二轮成功**（2026-09-11，e0_3_emmc.csv + bf2-message.txt）：

- fio：**BW=43.0 MiB/s、IOPS=344、clat 2.9 ms、`mmcblk0: ios=8571/66、util=99.68%`、25 s 读 1076 MiB** → 真实读盘、设备饱和（43 MB/s 即本板 eMMC 真实能力，比一般 eMMC 慢，但无碍判读）；
- 计数器面：
  - **tile_io_access：基线 5–10K/s → fio 窗口稳定台 ~735K/s（约 100 倍），窗口起止与 fio 对齐（行 9–34）** → **eMMC 控制器 DMA 走网格 RN-I 通路**（IB 式旁路）实锤；
  - **定量吻合**：735K 次/s ≈ 344 IOPS × 2048 行/IO（128KB/64B）——IO_ACCESS 按缓存行粒度计数，语义自洽；
  - **trio DMA 计数全零 + pcie0/pcie1 无任何响应** → eMMC 不在 PCIe Switch 下，板载存储 DMA 不经 TRIO/PCIe——路径框架边界确认；
  - A72_ACCESS 几乎不涨（sys=1.08%，vs 第一轮 93.68%）→ 两轮对比坐实"第一轮是 CPU 负载、本轮是真实 DMA"；
  - pcie 背景 5 s 脉冲第三次复现 → 可重复性 ✓。

**E0-3 结论**：eMMC DMA = 网格 RN-I 通路，不经 PCIe；tile IO_ACCESS 是板载存储流量的主观测计数器（预期基线见本表，B5/M1/M5 可按 735K/s 量级做预期校准）。

## 更正（2026-09-14）：pcie0/pcie1 链路身份

经与师兄讨论确认，**pcie0 = 通往主机的 PCIe 链路、pcie1 = 通往 Arm 子系统的 PCIe 链路**。本文档（及方案文档 §4、tools/path_data.py、内存笔记）此前基于 E0-2 流量剖面反推的标签（"pcie0 = Arm PF DMA 链路、pcie1 = eSwitch/OVS 交付链路"）作废。两种映射都能拟合 E0-2 剖面，但按硬件规范身份重读后模型反而更简：前向 = 主机面链路 IN → eSwitch → Arm 链路 IN/OUT 交付回注 → CX6 内部环回收包，不再需要"收包 DMA 再跨 pcie0"的第三跳。

**补充（2026-09-14，OSDI'23 Wei et al.《Characterizing Off-path SmartNIC for Accelerating Distributed Systems》§2.3–2.4，厂商确认的 BF2 拓扑）**：BF2 只有两条 PCIe 链路——**PCIe1 = NIC cores ↔ PCIe switch**（网卡核心连交换机）、**PCIe0 = switch ↔ 主机 PCIe 控制器**（主机面）；SoC(Arm) 与 switch 之间是**内部链路（非 PCIe，厂商确认）**。论文 Fig 1(c) 路径与本项目口径对应：① Client↔Host = NAH/NHD（网卡→主机：NIC cores → PCIe1 → switch → PCIe0 → 主机，跨两条 PCIe）；② Client↔SoC = NAD（网卡→Arm DRAM：NIC cores → PCIe1 → switch → 内部链路 → SoC，只跨 PCIe1）；③ SoC↔Host（RDMA 模式跨 PCIe1 **两次** + PCIe0 一次——§3.3 "crosses the NIC internal PCIe twice, can only utilize half of the PCIe bandwidth" 正是 E0-2 pcie1 IN≈OUT≈流量 剖面的权威解释；DOCA DMA ③* 只跨 PCIe0）。据此，正文 E0-2 判读中 "pcie1 = Arm 子系统 PCIe 链路" 的物理表述不准确，精确说法为：**pcie1 = NIC↔switch 链路，承载去往 Arm 子系统（②③）及去往主机（①）的流量**；E0-2 的 IN≈OUT = 流量经 eSwitch 处理进出 RNIC 各一次。

**官方文档确认（2026-09-14）**：NVIDIA《Performance Monitoring Counters》（v4.15.0，即论文 [54] 的最新版，本地 D:\bf2-collector\Performance Monitoring Counters.md）明确写道：TRIO 是 PCIe 根复合体（"TRIO (PCIe root complex)"），且 "The PCIe TLR statistics for each TRIO are under the 'pcie' block"；BF2 示例 sysfs 目录恰为 pcie0/pcie1 + trio0/trio1（两个 TRIO）。由此 **pcie0/pcie1 的计数口径 = Arm 根复合体事务层所见流量**，而非任意物理链路的原始字节：纯主机↔主机直通与 NIC→主机直通（不经 Arm 根复合体）必然零响应——E0-1"零响应之谜"就此有了官方依据的解释；E0-2/E0-3 流量均有 Arm 侧端点参与，故有响应。TRIO0/TRIO1 各自接 switch 的哪一侧属 NVIDIA TRM（NDA）细节，可请师兄查内部文档一锤定音。

两处连带观察待与师兄确认后定稿：

1. **E0-1 调和口径**：主机↔主机直通（33 Gbps）对 pcie0/pcie1 均无响应，与"pcie0 = 主机面链路"并存 → pcie0 计数覆盖的是主机面↔Arm 方向的路径，纯主机间桥接不触发；此口径以师兄意见为准；
2. **G3/G7 量级差**：pcie0 ≈ 219/251 MB/s，与 net 的 ~17 MB/s 相差 ~13×——若 pcie0 承载 56.x 管道，量级差疑为共享管道上他人流量（E0-1 背景）或计数口径差异。

## 待办

- ~~E0-2~~ 已完成（备选 B 通道，2026-09-11）；TRIO↔端口映射表已汇总（见上节）。
- 方案文档 §4 路径表修订：NAD 行观测点 trio TDMA_DATA_BEAT → pcie TLR 字节寄存器（含 E0-2 的双链路剖面说明）。
- ~~可选确认~~ 桥成员已实锤（ovsbr1 = {p1, pf1hpf, en3f1pf1sf0}，2026-09-11）；残余低优先级：`ip -br addr` 定 56.103 归属 + `ovs-ofctl dump-flows ovsbr1` 看流表。
- 可选对照：E0-2b（rshim 口），低优先级——映射表已闭合，仅为完备性。
- 论文 PCIe 域表述按"对论文的影响"节方向定稿（待用户批准）。
