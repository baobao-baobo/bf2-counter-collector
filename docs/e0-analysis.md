# E0 实验结果分析（2026-09-11）

> 实验材料：configs/e0_trio_map.conf、docs/e0-opsheet.md；原始数据：message/host-message.txt（宿主侧）、message/bf2-message.txt（设备侧屏幕）、message/e0_1_nhd.csv（50 s 采集）。
> 状态：E0-1 完成 ✅；E0-2/E0-3 待执行。

## E0-1：NHD（主机直通）——判定 NHD-A（主机直通流量对 Arm 侧计数器不可见）

**宿主侧**（host-message.txt）：两条通路均连接成功且 0 重传——
- 尝试 A（直连主机面）：33.1 Gbps × 8 s = 30.9 GB；
- 尝试 B（/32 路由强制经 eno1，真 NHD 通路）：34.5 Gbps × 8 s = 32.1 GB。
- 结论：BF2 物理口接在实验室 L2 上、eSwitch 有主机直通桥（port↔host PF），**NHD 流量生成通路完全打通**，B 姿势可作为后续 NHD bench 的标准方法。

**设备侧**（e0_1_nhd.csv，50 行）：

| 观察 | 数值 | 含义 |
|---|---|---|
| trio 4 事件（TDMA_DATA_BEAT/RT_AF/PBUF_MAC_AF/WRQ_BUF_EMPTY） | **全程 0** | NHD 流量完全不经过 Arm 侧 TRIO DMA 数据面 ✓（与理论预期一致） |
| pcie0_rx/tx | 基线 220/12438 字节/s，每 5 s 一个 ~780 字节小脉冲 | 与 net 列的 5 s 心跳**同步出现**（同一行） |
| pcie1_rx/tx | 基线 25–40K/80–150K 字节/s，每 5 s 一个 ~135K/~350K 脉冲（与 pcie0 错开 ~2 s） | 持续背景 + 周期脉冲 |
| **pcie0/pcie1 与 iperf3 的相关性** | **无**。行 1–20（33 Gbps 流量段）与行 20–50（空载段）读数同量级、同分布 | **4 GB/s 级流量没有在任何 PCIe 字节列留下痕迹** |
| smmu TX/RX_DAT_AF | 4–9 / 1–3 次/s 平稳背景 | 网格数据通道 FIFO 无流量事件 |
| tile A72_ACCESS | ~0.6–1.3M/s 背景，行 3 一个 10.75M 尖峰 | 与 iperf3 无关的 Arm 侧偶发活动 |

**判读**：
1. **NHD-A 成立**：主机↔主机面 PF 的流量走内部 PCIe switch 直达 eSwitch/网卡复合体，**不经过任何 TRIO 根复合体链路**——两套 TLR 字节计数器（P/NP/C 全覆盖）对 33 Gbps 毫无反应。Arm 侧 PMC 对 NHD 路径不可见（实测验证）。
2. **pcie0 假说**：其 5 s 脉冲与 net 心跳逐行同步 → pcie0 的 TRIO 链路携带 rshim（PCIe 版）管理流量；rshim 心跳经 PCIe 送达 Arm 后同时计入 tmfifo 软件计数，故两者同秒出现。
3. **pcie1 假说**：持续背景（TX>RX ≈ 3:1）+ 错位 5 s 脉冲 = Arm↔NIC 链路（mlx5 驱动轮询/固件心跳）。**待 E0-2 验证**：若 iperf3 打 Arm 侧网口时 pcie1 爆量且 trio TDMA_DATA_BEAT 起跳，则 pcie1 = 网口 Arm 面 TRIO 实锤。
4. 尝试 A 的流量同样未留下痕迹 → 连"主机面直连"也绕过 TRIO，排除 TLR 挂接在主机链路上的可能。

## 对论文的影响（待用户批准后改）

- 评审 CRITICAL #2（"NHD 仅 TLR 可见"无官方依据）现在有实测结论：**NHD 对 Arm 侧计数器不可见**。论文 PCIe 域表述方向改为："主机直通流量不经 Arm 侧 PCIe 根复合体，Arm 侧 PMC 不可观测（E0 实测）；NHD 观测采用主机侧手段"。
- 与 7 通道图的"链路 B 完全旁路 Arm 域"表述一致，可互相印证。

## E0-3（eMMC 对照，第一轮 2026-09-11）——作废：fio 没有真正读盘

**fio 自证**（bf2-message_1.txt）：`READ: bw=5674MiB/s (5949MB/s)`，`mmcblk0: ios=0/148`，读延迟 21 µs，sys=93.68%。
- 5.9 GB/s 是 eMMC 物理上限（~300 MB/s）的 20 倍，且 fio 自报磁盘**读 IO 为 0** → 全部读操作由内核填零完成，**没有发生任何 eMMC 访问**。
- 原因：`fallocate -l 2G` 创建的是"未写扩展区"（unwritten extents），读取返回零页，不经设备。
- 顺带结论：E0-3 配置下 A72_ACCESS 在 45K IOPS 的 psync syscall 洪流下冲到 ~50M/s（基线 0.7M/s，70 倍）——A72_ACCESS 对"内核 syscall 密集负载"的响应量级有了一个真实数据点（fio psync 在 BF2 上是 CPU 绑定负载）。

**计数器面**（e0_3_emmc.csv，35 行）：fio 窗口（行 11–35）内 tile_io_access 维持 5–11K/s 基线无尖峰、trio 全零、pcie0/pcie1 维持 E0-1 同样的 5 s 背景脉冲（背景模式完全复现 = 可重复性 ✓）、smmu/triogen 平稳——与"没有真实 IO"完全自洽。**本次不能作为 eMMC DMA 是否走 RN-I 的证据**。

**E0-3 重做要求**（已写入方案文档 §9 风险表）：先写真实数据再读：
```bash
/root/bf2k/bench/bin/fio --filename=/root/fio_testfile --rw=write --direct=1 --size=2G --bs=128k --name=warmup   # 真实落盘
# 然后照旧：采集 35 s + --rw=read --direct=1 --runtime=25
```
判读时核对 fio 末尾 `mmcblk0: ios=` 数千以上、BW 在 100–400 MB/s 量级，才确认真的读盘。

## 待办

- **E0-2**（NAD）：需先在 BF2 `ip a` 侦察 ConnectX 网口（Arm 侧）IP，由 Claude 指认目标后再打 iperf3；rshim 口测试作对照组 E0-2b。
- **E0-3 重做**（真读盘版本）：预期 tile IO_ACCESS 涨、trio/pcie 不动；若 pcie0/pcie1 出现响应则重新评估 eMMC 挂接位置。
- E0-2/E0-3 完成后汇总出"TRIO↔端口映射表"，写入方案文档 §4，并定稿论文 PCIe 域表述。
