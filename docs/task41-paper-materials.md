# Task #41 论文化素材包（2026-09-22）

论文模型章节 + CRITICAL 项的事实与数字清单。供论文化时逐条取用；
**论文正文须由用户改写（无 AI 痕迹要求），本包只保证事实与数字准确**。
事实出处：docs/e0-opsheet.md §6、docs/validation-replay.md、docs/counter-failure-probe-opsheet.md §10、
docs/saturation-calibration-opsheet.md、docs/bf2-bottleneck-queueing-model.md。

---

## 1. CRITICAL #2：E0-1 NHD 重证（证据链，可落笔）

### 1.1 背景（论文须交代的波折）

- 9/15 之前：E0-1 曾测得 34.5Gbps "NHD"，后被判**假象**——打流姿势错误：iperf3
  服务端地址 192.168.101.1 是 fujian 本机地址，Linux local 路由表（优先级 0）使
  本机地址永远本地投递，`ip route add /32 dev eno1` 无法覆盖 → 流量从未进 BF2。
  铁证：E0-1 CSV 的 net 列全程仅背景。**E0-1 经验证据作废、实证重做**；"NHD 对
  Arm 不可见"的理论结论不受影响（eSwitch→主机 PF 不经 Arm RC 是架构事实）。
- 9/15 师兄答复 p1 接线：fujian/helong 是两台独立服务器、各插一张 BF2、p1 上联口
  彼此相连 → BF2↔BF2 对打解锁（Part A ping 零丢包=同二层网；Part B tc in_hw
  13.1GB=software 0 → NHD 首次真实进 p1）。

### 1.2 F1 三轮实证（2026-09-22，helong BF2 p1 → fujian BF2 p1，iperf3 10G）

| 轮 | p1_rx 体积 | 速率 | pf1hpf_tx/p1_rx | pf1hpf_rx（ACK） | pcie0_tx 预→洪 | pcie1_rx 洪 | en3f1rx | 引擎洪流行投票 |
|---|---|---|---|---|---|---|---|---|
| 1 | 13.108GB/11s | 9.53Gbps | 1.0000 | 20.8MB | 13KB/s→1.519GB/s | 1.39GB/s | 0 | nhd 11/11 |
| 2 | 13.058GB/10s | 10.45Gbps | 1.0000 | 20.4MB | 14KB/s→1.517GB/s | 1.39GB/s | 0 | nhd 10/10 |
| 3 | 8.936GB/7s | 10.21Gbps | 1.0000 | 14.1MB | 0→1.518GB/s | 1.39GB/s | 0 | nhd 7/7 |

判读（论文要点，逐条）：
1. **p1_rx 与 pf1hpf_tx 逐轮 1:1**：wire 进 = 主机向投递出，NHD 直通桥签名成立。
2. **NHD-B 定案**：pcie0_tx 从背景 13KB/s 跳到满载 1.52GB/s → 主机向流量对 Arm
   侧 TLR（PCIe 事务层寄存器）可见，即 NHD 走"网口→eSwitch→主机 PF→PCIe"路径，
   与 E0-1 待答问题②"主机向流量是否对 Arm 侧 TLR 可见"的答案 = **可见**。
3. **Arm 软件零参与**：en3f1pf1sf0_rx 恒 0（Arm 面 representor 收包），
   enp3s0f1s0_tx=0（主机上行 representor 无流量）→ 纯 OVS p1↔pf1hpf 硬件桥。
4. **已知模型缺口（论文须标注）**：pcie1_rx 满载 1.39GB/s —— NHD 洪流在 Arm 子
   系统 PCIe 链路级全程可见（eSwitch→Arm RC 桥接的硬件级中转）。**用户 9/22 裁定：
   pcie1 顶点不挂 nhd**，保持 NAD 专属；论文 PCIe 域段落须注明"pcie1_rx 计数含
   NHD 中转成分，解读时注意"。
5. **引擎跨数据集一致**：修复后 BFS 引擎对三份新数据判 nhd 全票，洪流行 med
   L_p：nhd=0.306 / nad=0.044 / tx=0.000，与 e1_n1_10g 同签名同读数。
6. run2 峰值 10.45Gbps 受 `-b 10G` 参数封顶（fujian p1 = 100000Mb/s，ethtool
   9/18 记录）；helong 侧链路速率未测（不影响结论）。

### 1.3 G6 计数口径双轮差分（NHD 的 OVS 规则计数器满血证明）

| 轮 | 规则增量 | iperf3 发送 | 占比 | 窗口 |
|---|---|---|---|---|
| 1 | 6.461GB | 5.73 GiB = 6.153GB | 105.0% | 16.9s |
| 2 | 6.565GB | 5.82 GiB = 6.249GB | 105.1% | 27.4s（零重传） |

- 两轮窗宽差 62%、占比几乎不动 → 增量与洪流成正比，非窗口背景。
- **+5% 分解**：iperf3 报应用层载荷（1460B/段），规则计 L2 以太网帧（1514B/帧），
  1514/1460 = **103.7% 协议开销**；剩余 1.3% = ACK 回流 + 窗口背景。第二轮包数
  自洽：4,494,698 ≈ 数据段 4,280,258 + 21 万背景。
- **口径定案**：NHD 金标准 = 物理口 sysfs（p1_rx，M2 已验证）；OVS catch-all
  规则计数器 = NAD/NHD 双满血第二口径。
- **论文义务**：9/17 曾定案"规则对 NHD 只计 ~23% 软件段"，现被推翻（配置漂移：
  OVS/tc 重启后硬件统计回流开启，tc 过滤器 "installed 2 sec"）→ 凡引用 9/17
  "23%"处须加配置漂移脚注。

---

## 2. 模型章节素材（三层索引 BF2-PF）

### 2.1 机制（docs/bf2-bottleneck-queueing-model.md §4.6）

- **L1 归一化**：计数器速率 → n∈[0,1]，六公式族：span（空闲→饱和跨度线性）、
  cap（速率÷容量，操作分析利用率律）、af（Almost-Full 占比 = P(Q≥θ)）、
  empty（1−空占比 = 忙占比）、drops、miss（1−命中率，效率兜底）。
- **L2 顶点融合**：v_j = max(顶点内各计数器 n)，证据分级仲裁（压力 > 忙闲 > 频率
  > 效率）只作 provenance 标注、不作权重。
- **L3 路径累加**：L_p = Σ v_j；共享顶点按 M1 入口比例拆份额（share =
  本路径入口速率 ÷ 该顶点全部属主路径入口速率之和；份额地板 SHARE_EPS=1000 B/s
  均分，防入口列 NaN 轮转时 ε 背景入口独得份额 1.0）。
- **L_p 语义须如实表述**：归一化压力指数（无量纲、可跨顶点/路径加总），**不是
  在途请求数/队列长度**——与 PathFinder ΣQ 的排队论语义同构但手段是经验归一化。

### 2.2 路径表（configs/path_table.conf）

七路径 = 论文六类分类 CR/IH/IB/WB/NAD/NHD + TX（P8 补充）：
- **CR**（核读）：A72→L1→HNF(L2)→tilenet→L3→MSS→DDR；入口 tile_a72_access。
- **IH**（IO→DDR）：DMA→SMMU→tilenet→HNF→L3→MSS→DDR；入口 tile_io_access；
  P3 主机腿共享 pcie0 在 NAD/NHD 域报告。
- **IB**（IO 旁路）：MEMORY_READS_BYPASS 跳过 HNF/L3 直达 MSS；**论文标注：IB
  非 I/O 专属**（xz 45% / Redis 81% bypass 占比）。
- **WB**（L3 逐出写回子路径）：victim_write→L3→MSS，附属于 CR/IH。
- **NAD**（网→Arm）：eSwitch→Arm 软件→DDR；入口 en3f1pf1sf0_rx（Arm 面
  representor 收包，源无关：主机管腿+wire 腿都覆盖）；顶点 pcie0/pcie1/arm/
  eswitch/wire。
- **NHD**（网→eSwitch→主机）：wire(p1)→eSwitch→pf1hpf→pcie0；入口 pf1hpf_tx。
- **TX**（Arm→网）：入口 p1_tx；顶点 arm/eswitch/wire。

### 2.3 锚点表（configs/anchor_idle.conf + anchor_sat.conf）

- 空闲 87 列；饱和 [span]44 + [cap]13 + [unverified]13。
- **obs-max 地板规则**：sat = max(bench 应力窗均值, 历史观测最大值)——bench 必须
  压过 1.02× 一切观测值才算真应力，否则 obs-max 下界胜出（防弱面降级回归）。
- **provenance 标记**：bench-bN 真应力（trimmed 5s 头尾、3 轮中位数）/ obs-max
  下界 / derived（io_access=io_reads+io_write，p7 实证精确）/ measured / suspected。
- 关键容量：[cap] p1 12.5GB/s（100G ethtool 实测）、pcie0 15.75GB/s（**LnkSta
  8GT/s downgraded = 实际 Gen3 x16**，fujian lspci 9/18）、pcie1 31.5GB/s
  （Gen4 x16）、arm representor 825MB/s（6.6Gbps 平台瓶颈，2a 实测 6.05Gbps 定
  性=Arm 收包瓶颈非慢启动）。
- 饱和标定：p3 STREAM a72_access 三轮均值 179.3M/s（离散 0.2%）；p5 cache 最强
  （a72_access ×234、L3 hits ×386）；p7 网络满载 **io_reads×64B ≈ net_tx 精确互证**
  （29.04M×64=1.858GB/s vs 1.86GB/s，NIC DMA 内存读 = TX 速率）。

### 2.4 验证矩阵（docs/validation-replay.md）

- **M4 历史回放 17/17**：xz/BFS→CR、Redis→NAD 双侧、iperf NHD→NHD、主机管道
  2a/2b→NAD、e0_3 全低诚实。空载关 ≤0.15（L3 span 单样本轮换噪声，已核实到
  计数器级）。
- **自检 23/23**：analyze_bottleneck --selfcheck（xz n_A72=0.342 手算==引擎）+
  PRISM 实例集 A 17/17 + B 6/6。
- **D2 七应用重跑复现**：g1 1.640/g2 1.265/g3 0.177/g4 0.120/g5 0.195/g6 0.070/
  g7 0.315，与历史 med 差 ≤0.025；g3/g7 流量 8.6Mbps≪历史 100Mbps 而 med 几乎
  不动 = 幅度判据对负载强度稳健。
- **SAT-SUSPECT 收敛**：g1×5/g2×1/e1_n0_2b×1——victim_write 换 p5 真应力仍标记
  = xz 真实贴近饱和（非缺陷）；残余 obs-max 取自负载自身峰值 = 自引用下界。
- **低载平局条款**：e1_g7 cr 36 : nad 35 一票之差（真 io 锚点拉低 nad 边际行）
  → 无多数即"无强主导"。
- **方向投票下限**：argmax L_p < 0.02 的行弃权（离线 ε 票实测 ≤0.003）。
- §6 tilenet 闭合：满载 41 行（a72 均值 180.4M/s=p3 差 0.6%）下 tilenet
  cdn/ddn/ndn 59/59 全零 → 内存负载不触网络 tile。

### 2.5 计数器探针产出（docs/counter-failure-probe-opsheet.md §10）

- **四失效（排除出 paper52，各带标注）**：0x71 A72_WRITE 不响应（五轮写洪流全
  ~66/s 背景）；0x4a RNF_SEL 未编程（网络 DMA 41.3M/s 对照恒 0）；0x53 不触发
  （排他乒乓+eMMC+网络三类读全 0）；0x67 不触发（10.3GB/s 带宽硬顶）。
- **E3 正面产出（论文可用）**：双槽同事件 1:1（0x5d 96.24 vs 96.24M/s，0.5‰ 内）
  = sysfs 直控链路精度金标准；**写流量反推公式 0x5d−0x72 定量成立**（差值 24.0M/s
  ÷8 实例 = 3.0M/s ≈ 快核写模式实测 2.89M/s；0x72=数据读+walk、0x5d=数据读+写
  +walk、walk≈2.2 次/访问）；VICTIM(0x70) 活计数器（13.5M/s 脏行淘汰）；0x5d
  语义=MPKI 类随机压力、不反映顺序带宽（Part 6 验证）。
- **offload 编程延迟 ≈1.1s**（期间 Arm 内核慢路径扛 10G 未丢包）。

---

## 3. 论文 CRITICAL 标注清单（落笔时逐项核对）

| # | 标注 | 出处 |
|---|---|---|
| 1 | 凡引用 9/17"OVS 规则只计 23%"处加配置漂移脚注（现 105% 满血） | e0-opsheet §6 / G6 |
| 2 | pcie1_rx 计数含 NHD 硬件级中转成分（pcie1 顶点不挂 nhd 的裁定） | e0-opsheet §6 |
| 3 | IB bypass 非 I/O 专属（xz 45%/Redis 81%） | 9/14 出图定稿 |
| 4 | L_p = 归一化压力指数，非队列长度/在途请求数 | 模型 §4.6 |
| 5 | 四失效计数器（0x71/0x4a/0x53/0x67）排除理由各带标注 | 探针 §10 |
| 6 | 背压计数器仅覆盖约半数转发点（范围裁定：PFAnalyzer-only 路径级繁忙度） | 模型 9/17 修订 |
| 7 | SAT-SUSPECT 残余 = obs-max 自引用下界（非缺陷） | validation-replay §7 |
| 8 | NHD-B：主机向流量对 Arm 侧 TLR 可见（pcie0_tx 1.52GB/s） | F1 |
| 9 | Arm 6.6Gbps 收包平台瓶颈（2a=6.05Gbps 实测） | NAD/NHD 主图 |
| 10 | e1_g7 低载平局条款（cr 36:nad 35 一票） | validation-replay |

## 4. 图表现状

- 已定稿 31 张：fig/ 并排 28（G 系列应用×路径 10 + 二批 18）+ 堆叠 3（l3_lookups、
  l3_rd_chain、tile_mem_reads_stack）+ e1_nad_nhd.png（NAD/NHD 主图）。样式规范
  见 9/14 定稿记录（配色/坐标轴/key 位置）。
- 模型自身可能还需：路径图示意（P1–P8 拓扑，设计手册有文字版可转矢量图）、
  三层索引机制示意图、F1/G6 表格（§1 本包已备）。
