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

## 待办

- **E0-2**（NAD）：需先在 BF2 `ip a` 侦察 ConnectX 网口（Arm 侧）IP，由 Claude 指认目标后再打 iperf3；rshim 口测试作对照组 E0-2b。
- E0-2 完成后汇总出"TRIO↔端口映射表"（当前状态：主机面=不经任何 TRIO（E0-1）；eMMC=不经 TRIO/PCIe（E0-3）；pcie0=rshim 管理链路假说（5s 心跳逐行同步）；pcie1=Arm↔NIC 假说待 E0-2 证实），写入方案文档 §4，并定稿论文 PCIe 域表述。
