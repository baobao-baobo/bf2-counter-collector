# 应用×路径×计数器矩阵 与 计数器状态清单

版本 2026-09-22。回答两个问题：① 每个应用打在哪些路径的哪些计数器上；
② 目前全部计数器的锚点状态清单（真上限 / 观测下界 / 失效悬置）。
数据源：`configs/anchor_sat.conf`（提取自 9/20 回填）、`configs/path_table.conf`、
`tools/bfs_search.py` 逐场景分解（本日批量运行）、`docs/counter-failure-probe-opsheet.md`
§10 判死定论、`tools/replay_validate.py` 回放闸。

---

## 1. 应用×路径×计数器矩阵

判定来自回放闸结论（`replay_validate.EXPECT`）；"点亮计数器"= 该场景下
`bfs_search` 顶点分解的 argmax 计数器（顶点←计数器，n 为归一化压力值）。

### 1.1 七应用主图（G1–G7）

| 应用              | 负载特征                          | 主路径（判定）                           | 判据    | 点亮顶点←argmax 计数器（n）                                                   | 次路径/备注                                                             |
| --------------- | ----------------------------- | --------------------------------- | ----- | -------------------------------------------------------------------- | ------------------------------------------------------------------ |
| G1 xz           | 全核压缩 1GB 随机数据（-9 -T 8，55–65s） | **cr 主导**（med 1.64）               | 幅度    | hnf←a72_access(0.25)；l3←emem_wr_req(0.26)；mss←victim_write(**0.91**) | **ib 次强 0.85**：bypass 占比 45%；SAT-SUSPECT×5（victim_write 真实贴近饱和非缺陷） |
| G2 bfs          | 图遍历随机访存（20 图×8 线程）            | **cr 主导**（med 1.20）               | 幅度    | hnf←a72_access(0.28)；l3←emem_wr_req(0.07)；mss←victim_write(0.38)     | L3 miss 显著（随机访存）；ib 0.41                                           |
| G3 redis        | Arm 终接 set/get 64 并发          | tile 面 cr 低位(0.18)；**wire 面 nad** | 方向    | arm←net_rx(6.05Gbps 平台)；eswitch←en3f1pf1sf0_tx；wire←p1_rx            | NAD 的 DDR 跳计入 cr（混合流，模型 §8.5）；net_rx cap=6.6Gbps 实测常数              |
| G4 sqlite       | eMMC 冷库读写（.timer，~32s）        | **cr 低位**(0.11)                   | 幅度    | hnf←a72_access(0.01)；io 系活动（io_access/io_write）                      | IO_ACCESS 历史平台 ~735K/s；memory_reads_bypass→**ib**（Bypass 通道）       |
| G5 blackscholes | 8 核对称计算（循环 8×）                | **cr 主导**（med 0.20）               | 幅度    | hnf←a72_access(0.01)；l3←emem_wr_req(0.03)                            | 多核共享 L3；pcie/net 近零                                                |
| G6 tflite       | 推理+权重流式读入（~30s）               | **cr 低位**(0.07)                   | 幅度    | hnf/l3/mss 全低幅                                                       | 权重流式→l3 miss；IO 低                                                  |
| G7 sqlite+redis | 混合多路径并发                       | **cr**(0.34) + wire 面 nad         | 幅度/方向 | hnf←a72_access(0.06)；mss←victim_write(0.09)                          | e1_g7 wire 面为低载平局先例（cr 36:nad 35）                                  |

### 1.2 E1 标定/梯度系列（路径标定证据）

| 场景                      | 主路径（判定）                      | 点亮顶点←计数器                                                          |
| ----------------------- | ---------------------------- | ----------------------------------------------------------------- |
| e1_g3（redis wire 面）     | **nad**（方向判）                 | arm←net_rx；eswitch←en3f1pf1sf0_tx；wire←p1_rx                      |
| e1_g7（混合 wire 面）        | nad 低载平局                     | 同上形态，无多数得票                                                        |
| e1_n2（iperf NHD 方向）     | **nhd**（方向判）                 | wire←p1_rx；eswitch←en3f1pf1sf0_tx；pf1hpf←pf1hpf_tx；pcie0←pcie0_tx |
| e1_n0_2a（host→Arm 定标）   | **nad**（方向判，6.05Gbps 平台）     | pcie0←pcie0_tx（主机管）；arm←net_rx；eswitch←en3f1pf1sf0_tx             |
| e1_n0_2b（反向定标）          | **nad**（方向判，pcie0 主机管）       | 同上反向                                                              |
| e1_n1_1g/5g/10g/20g（梯度） | **nad**（方向判）                 | arm←net_rx；eswitch←en3f1pf1sf0_tx；wire←p1_rx                      |
| e0_3_emmc（fio）          | **全低位诚实负例**（ih 期望，eMMC 无计数器） | hnf←io_access(0.02)——覆盖缺口"看不到"                                    |

### 1.3 sat 六面（锚点标定源，实例集 B）

| 面                  | 判定                  | 点亮顶点←argmax 计数器（n）                                                   |
| ------------------ | ------------------- | -------------------------------------------------------------------- |
| p1 stress-ng cpu   | cr 弱面（L_p 0.03）     | hnf←a72_read(0.01)；l3←rd_req_in(0.01)——纯计算不压内存                       |
| p3 STREAM          | **cr 主导**（med 2.12） | hnf←a72_access(0.68)；l3←evictions(0.99)；mss←mem_writes(0.88)         |
| p4 memrand         | cr（miss 族）          | hnf miss(0.38)；l3 miss(0.46)——随机访存打 miss 效率                          |
| p5 stress-ng cache | **cr 主导**（med 1.73） | hnf←a72_access(0.79)；l3←allocations(0.72)；mss←victim_write(**1.00**) |
| p6 fio eMMC        | ih 负例（低位）           | hnf←io_write(0.05)；l3←wr_comp(0.03)                                  |
| p7 iperf3 上传       | cr（**TX DMA 读跳**）   | hnf←**io_reads(1.00)**——NIC DMA 内存读=TX 速率；出口不可观察（9/22 修正）            |

---

## 2. 计数器状态清单（76 锚点条目）

### 2.1 真实负载上限（25 条）

**真应力 13 条**（bench 满载窗均值，须压过 1.02× 一切观测值才计，obs-max
地板规则；值为该计数器的饱和率锚点）：

| 计数器                         | 真上限值     | 标定面                          |
| --------------------------- | -------- | ---------------------------- |
| tile_allocate               | 63.99M/s | p5 cache thrash              |
| tile_io_reads               | 30.17M/s | p7 iperf3（NIC DMA 读=TX 速率互证） |
| tile_victim_write           | 4.32M/s  | p5 cache thrash              |
| l3half0/1_evictions         | 24.76M/s | p3 STREAM                    |
| l3half0/1_total_emem_rd_req | 38.0M/s  | p3 STREAM                    |
| l3half0/1_total_emem_wr_req | 49.75M/s | p3 STREAM                    |
| l3half0/1_total_wr_dbid_ack | 13.79M/s | p7（写 DBID 确认，TX 流 L3 写管）     |
| l3half0/1_total_wr_req_in   | 13.79M/s | p7                           |

**推导 1 条**：tile_io_access = 43.89M/s = io_reads+io_write 饱和值之和
（access=reads+write 守恒，p7 实证 29.24M=29.04M+201K）。

**实测常数 2 条**：en3f1pf1sf0_tx_bytes = 825MB/s（Arm 面 representor 收口
上限=Arm 收包瓶颈）；net_rx_bytes = 825MB/s = 6.6Gbps（2a 平台 6.05Gbps
实测外推）。

**物理上限 9 条**（cap 族，链路规格/实测）：

| 计数器                | 上限        | 来源                                               |
| ------------------ | --------- | ------------------------------------------------ |
| p1_rx/tx_bytes     | 12.5GB/s  | ethtool p1 Speed=100G（9/18 M2 B4）                |
| pf1hpf_rx/tx_bytes | 12.5GB/s  | 同上（wire 系）                                       |
| pipe:p1_bytes      | 12.5GB/s  | 同上（OVS 规则计数口径）                                   |
| pcie0_rx/tx_bytes  | 15.75GB/s | fujian lspci：LnkSta 8GT/s downgraded=实际 Gen3 x16 |
| pcie1_rx/tx_bytes  | 31.5GB/s  | 双侧 lspci：Arm 子系 Gen4 x16                         |

### 2.2 可观测最大上限（38 条，obs-max 下界——**非饱和值**）

语义：任何已采集中见到的最大速率，是饱和值的**下界**；顶点读数 n≈1 时
SAT-SUSPECT 机制内建警示。p4/p5 扩展重跑（执行单 §9b）的目标就是再换掉
其中一部分。

**tile 域 12 条**：

| 计数器               | obs-max  | 计数器                      | obs-max |
| ----------------- | -------- | ------------------------ | ------- |
| tile_a72_access   | 258.6M/s | tile_memory_reads_bypass | 30.2M/s |
| tile_a72_read     | 66.5M/s  | tile_poc_fail            | 8.05M/s |
| tile_hnf_requests | 256.3M/s | tile_poc_success         | 8.05M/s |
| tile_io_write     | 13.7M/s  | tile_poc_writes          | 16.1M/s |
| tile_mem_reads    | 169.6M/s | tile_tso_write           | 13.7M/s |
| tile_mem_writes   | 59.5M/s  | tile_victim              | 51.8M/s |

**L3 域 24 条**（l3half0/l3half1 成对，值≈half0）：

| 计数器                   | obs-max | 计数器        | obs-max |
| --------------------- | ------- | ---------- | ------- |
| allocations           | 26.5M/s | rd_req_out | 25.4M/s |
| total_cache_rd_res_in | 14.4M/s | rd_res_in  | 26.4M/s |
| total_cdn_req_in      | 41.1M/s | wr_comp    | 9.0M/s  |
| total_ddn_req_in      | 9.3M/s  | wr_data_in | 9.0M/s  |
| total_emem_rd_res_in  | 25.9M/s | wr_req_out | 5.9M/s  |
| rd_data_out           | 64.8M/s | rd_req_in  | 32.4M/s |

**net 域 2 条**：enp3s0f1s0_tx_bytes = 1.32GB/s；net_tx_bytes = 2.95GB/s
（≈23.6Gbps，早于每口列时代的无过滤求和，9/22 起判读以每口列为准）。

### 2.3 失效与悬置（13 条）

**判死 4 条**（探针定论，`counter-failure-probe-opsheet.md` §10，已排除出
paper52，论文各带标注）：

| 计数器               | 编码   | 失效性质                                      | 论文处理                                      |
| ----------------- | ---- | ----------------------------------------- | ----------------------------------------- |
| tile_a72_write    | 0x71 | **不响应**：五轮写洪流全 ~66/s 背景，观测点未接 A72 写流量     | 写流量改 A72_ACCESS−A72_READ 反推（E3 定量验证）      |
| tile_rnf_requests | 0x4a | **配置缺失**：RNF_SEL 未编程（驱动无入口、寄存器不公开）        | 附录注明；信息由 HNF_REQUESTS(0x45)+A72_ACCESS 覆盖 |
| tile_poc_reads    | 0x53 | **不触发**：排他乒乓 50M 次/eMMC DMA/网络 DMA 三类读全 0 | 标注"排他读洪流与 DMA 读实测均不触发"                    |
| tile_mss_nocredit | 0x67 | **不触发**：10.3GB/s 带宽硬顶（4→8 线程零增益坐实）恒 0     | 标注"任何实测负载下不触发"                            |

**零值悬置 9 条**（[unverified]，模型保留但不出仲裁；非判死）：

| 计数器                                          | 状态    | 说明                            |
| -------------------------------------------- | ----- | ----------------------------- |
| pf0hpf_rx/tx_bytes                           | 端口无流量 | pf0hpf 从未使用（无 NAD/NHD 流经）     |
| smmu_tbu_miss                                | 不触发   | 所有 DMA 实测未发生 TBU miss         |
| trio_dma_beats / rt_af / pbuf_af / wrq_empty | 零值悬置  | TDMA 计数器不计数网卡 DMA（E0-2 裁定）    |
| triogen_tx/rx_dat_af                         | 零值悬置  | mesh FIFO AF 从未触发（smmu 顶点旁证位） |

**旁注（不属于 76 条锚点）**：计算族计数器无 sat 锚点、自带公式——
tile_req_buf_empty@empty（活，p1 面 2.2G 时钟率基线）、hnf @miss:hit=
dir_hit/allocate、l3 @miss:hit=hits/(hits+misses)（p4/p7 的效率兜底）；
tilenet 块三列（default.conf 窗）9/22 满载证据 59/59 全零——内存负载
不触网络 tile，其信息由 L3 侧 *_cdn/ddn/ndn_req_in 覆盖。

---

## 3. 汇总

| 状态      | 条目                        | 占比  | 语义                          |
| ------- | ------------------------- | --- | --------------------------- |
| 真实负载上限  | 25（13 真应力+1 推导+2 实测+9 物理） | 33% | 饱和锚点可信，直接标定 n=1             |
| 可观测最大上限 | 38                        | 50% | 下界，n≈1 读数需 SAT-SUSPECT 谨慎判读 |
| 失效/悬置   | 13（4 判死+9 零值悬置）           | 17% | 判死出模型；悬置保留不出仲裁              |

真上限率 33%（剔除悬置后 40%）；p4/p5 扩展重跑完成后真应力净增 8 组
（执行单 §9b：p4 写系 4 计数 + p5 读系 4 计数，2026-09-20 配置扩展
已入库 917db11）。
