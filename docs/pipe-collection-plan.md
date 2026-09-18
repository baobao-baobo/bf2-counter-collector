# pipe 采集执行方案：eSwitch 数据流管道观测

日期：2026-09-15
状态：执行方案（E1 实验暂停，本方案优先；E1 的 e1-1/e1-2 操作单留存，pipe 跑通后恢复）
进度：M0 §5.1 勘察已执行并判读（2026-09-15，见 §5.0）；§5.2 P1 测试与 §5.3 P1b 探针均已执行 → **两轮测试流量都没进 p1（打流姿势错误，非统计问题）**；§5.4 正确姿势重测首轮已执行 → **三端 tcpdump 定案：p1 不在 172.28.4.x 这张二层网**（fujian eno1 自答 ARP 陷阱 + p1 零 10.99.99 包）→ 方案 A/B 姿势全废；**56.x 计数测试已执行（9/15 晚）→ P1+P1b 双复活**（规则 n_bytes 8.74GB ≈ tc in_hw 硬件计数 ≈ vport 增量，三层一致、offload 统计回流成立）→ pipe 走 P1 零安装路线；M2 采集器 tools/collect_pipe.sh + §5.5 验证操作单已交付待部署；**师兄答复 p1 接线（9/15）：fujian/helong 两台独立服务器、各一张 BF2、彼此相连** → 两张 BF2 的上联口即服务器间互联通路 → **§5.6 Part A 已执行：ping 零丢包 → 两卡 p1 同二层网确认；Part B 已执行：NHD 流量首次真实进 p1（tc in_hw 13.1GB=software 0≈rx_bytes_phy 增量三层一致）→ P1b 在 p1 成立**；P1 规则计数被 grep 引号坑吃掉（collect_pipe.sh 同类隐患已修），2 分钟补验操作单已给（重打 5s + 完整 dump，期望 n_bytes≈6.5GB）
来源：师兄建议"用 dataflow pipe 采集 eSwitch 转发出去的流量"；术语溯源见第 2 节

## 1. 背景与目标

- **问题**：NHD（wire→主机直通）流量不经过 Arm 根复合体，PMC 计数器不可见（E0-1 实测零响应）；现有 eSwitch 端口 sysfs 计数器（E1 方案）能看到字节数，但 pf1hpf 单口混合 NHD 与 56.x 两类流量，只能靠组合判据间接区分。
- **pipe 的价值**：eSwitch 的硬件 match-action 管线（pipe）可以对流量做**硬件级分类计数**——按入口端口、按 L4 端口号匹配，直接给出每一类流量的字节/包计数。这是论文归因三法中"专用计数器直读"在 eSwitch 上的实现。
- **目标输出**：Arm 侧一个每秒一行的 CSV（与 collect_all 并列），含各流量类的硬件字节计数；与 E1 的 sysfs 组合判据交叉对照（偏差 <5% 为验收线），并成为论文"Arm 侧直接观测 NHD"的证据。

## 2. pipe 是什么，以及三种实现路径

"pipe" 是 DOCA Flow 的核心术语：**eSwitch 硬件管线**（创建 pipe = 定义匹配域与动作，插入 entry = 具体规则，entry 可挂 counter 资源，用 `doca_flow_resource_query_entry()` 查询硬件累计字节/包数）。但**同一思想的实现有三条路径，成本相差很大**：

| 路径                   | 机制                                                                                                   | 成本                                        | 输出                      | 定位               |
| -------------------- | ---------------------------------------------------------------------------------------------------- | ----------------------------------------- | ----------------------- | ---------------- |
| **P1 OVS 流表计数**      | ovsbr1 加 `in_port=…,actions=NORMAL` 计数规则；offload 到 eSwitch 后 `ovs-ofctl dump-flows` 的 n_bytes 就是硬件计数 | **零安装**（设备已有 OVS+offload）                 | 每入口端口字节数                | **首选**，先验证       |
| **P2 DOCA Flow 计数管** | 自写小程序 `collect_pipe.c`（参考官方 bifurcated-driver 应用）：pipe + entry counter + 每秒查询写 CSV                   | 需 DOCA SDK（NVIDIA 账号下载）+ 交叉编译             | 每类（入口端口/L4 端口号）字节数，粒度最细 | 正统 pipe，P1 不够细时上 |
| **P3 镜像管**           | OVS mirror 到 SF（DOCA Flow Inspector 官方服务），Arm 用 DPDK 收真包                                             | 重（SF 配额 + DPDK 环境 + Arm 收包带宽瓶颈 ~6.6 Gbps） | 真包（可 5-tuple/落盘）        | 兜底/图 3 升级证据      |

- P1 与 P2 都是**纯计数**（零 Arm 带宽成本，每秒一次查询），与现有计数器框架同构；
- P1 的关键问题只有一个：**offload 流的 n_bytes 是否随硬件实时更新、刷新周期多长**——M0 实测直接回答；
- 三条路径不互斥：P1 先行，P2 需要时叠加，P3 视论文证据需求再说。

## 3. 里程碑与验收标准

| 里程碑                                | 内容                                                               | 验收                                                                  |
| ---------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------- |
| **M0 设备勘察**（§5 操作单，已执行 2026-09-15） | eSwitch/OFED/DOCA/OVS offload/SF 状态 + **P1 即时可行性测试**             | 已判读：**P1 失败**（offload 统计不回流，9.31GB 流量只计到 30KB）→ P1b 探针（§5.3）→ 否则 P2 |
| **M1 工具链准备**（仅 P2 需要）              | DOCA SDK 下载（Windows 代理）→ WSL aarch64 交叉编译参考应用                    | 交叉编译产物在设备上能跑起来                                                      |
| **M2 最小 pipe 计数**                  | P1：计数规则轮询脚本；P2：最小 pipe+entry counter 程序                          | iperf3 10G 打流时计数 ≈ 流量（±5%）                                          |
| **M3 分类 pipe**                     | 入口端口分类（wire/host/Arm 三面）+ L4 端口分类（iperf3 5201 / redis 6379/6380） | NHD 与 56.x 分类结果与 E1 组合判据一致                                          |
| **M4 与 E1 汇合**                     | pipe CSV 与 collect_all 并列跑同一批 N1 流量                              | 论文方法定稿（pipe 计数 vs sysfs 判据对照表）；E1 恢复                                |

## 4. 目标输出形态（M2 之后）

`pipe.csv`（每秒一行，与 collect_all 的 CSV 时间对齐，同窗口协议）：

```
timestamp,pipe_p1_bytes,pipe_p1_pkts,pipe_pf1hpf_bytes,pipe_pf1hpf_pkts,pipe_arm_bytes,pipe_arm_pkts,...
```

判读对照（以 E1-1 已核实的组合判据为准）：

- NHD 场景：`pipe_p1_bytes` ≈ iperf3 速率；`pipe_pf1hpf` ≈ 0（同场景 pf1hpf_rx≈0）
- NAD 前向：`pipe_pf1hpf_bytes` ≈ 速率、`pipe_p1` ≈ 0
- 每类净流量 = 窗口均值 − idle 基线（沿用 G 系列口径）

## 5. M0 设备勘察操作单（傻瓜式，现在就可以执行）

> 角色：【宿主机】= fujian；【BF2】= ssh 192.168.100.2（root@localhost）。所有 BF2 命令先 `cd /root/bf2k` 以外的裸命令即可（本步与仓库无关）。全程 ~15 分钟，不含 P1 打流测试。

### 5.0 勘察结果（2026-09-15 已执行，已判读）

- 双口 eswitch 均 **switchdev**（pci/0000:03:00.0 与 03:00.1，inline-mode none encap enable）；OVS `hw-offload="true"` → **P1 硬件前提全满足**。
- 现有流表仅一条 catch-all：`priority=0, actions=NORMAL`，n_packets=506M / n_bytes=137.7GB（时长 ≈130 天）→ **统计回流已存在**；聚合口径可直接轮询该规则增量，per-port 需加 in_port 规则（用 priority=1，见 §5.2.2 修正）。
- BF2 Arm 上已装**完整 DOCA + DPDK**（/opt/mellanox/doca：applications/samples/include/lib/tools；/opt/mellanox/dpdk）→ **P2 很可能无需下载 SDK，设备原生 gcc 直接编译**（待确认 bifurcated-driver-model 参考应用是否随附，见 §5.2 第 3 步补查）。
- **SF 存在**：auxiliary mlx5_core.sf.2/3（enp3s0f0s0 / enp3s0f1s0）+ representor 双 SF（en3f0pf0sf0 / en3f1pf1sf0）→ P3 基础设施可用（无 mlxdevm 工具，用 `devlink port show` 即可）。
- 端口清单：03:00.0 = port 0（p0/pf0hpf）、03:00.1 = port 1（p1/pf1hpf），mlx5_core 5.8-1.1.2 / 固件 24.46.1006。

### 5.1 状态勘察（照敲照抄，输出抄进笔记）

```bash
# A. eSwitch 与驱动
devlink dev eswitch show
ofed_info -s 2>/dev/null || modinfo mlx5_core 2>/dev/null | grep -E "^version"
ethtool -i p1 | grep -E "driver|version|bus"

# B. DOCA / DPDK 环境（P2 可行性；没有就说明设备没装，正常）
ls /opt/mellanox/doca 2>/dev/null; ls /opt/mellanox/dpdk 2>/dev/null; ls /opt/mellanox/ 2>/dev/null

# C. OVS 与 offload 状态（P1 可行性）
ovs-vsctl get Open_VSwitch . other_config
ovs-ofctl dump-flows ovsbr1

# D. SF 配额（P3 可行性；报错/空输出 = 无 SF 工具，正常）
mlxdevm sf show 2>/dev/null || echo "no mlxdevm"
devlink port show 2>/dev/null | head -40

# E. 网卡 PCI 地址（P2 用；记下两个 Mellanox 设备的地址）
lspci | grep -i mellanox
```

**记录表（回传用）**：

| 命令              | 关键输出（照抄）                       |
| --------------- | ------------------------------ |
| A eswitch show  | mode 是否为 switchdev、multiport 值 |
| A ofed/modinfo  | OFED 版本号                       |
| B /opt/mellanox | doca/dpdk 目录是否存在               |
| C other_config  | 是否有 hw-offload=true            |
| C dump-flows    | 现有流表条目数（有无既有计数规则）              |
| D sf/port show  | SF 是否可用                        |
| E lspci         | 两个 Mellanox PCI 地址             |

### 5.2 P1 即时可行性测试（核心，~10 分钟）

**目的**：验证"ovsbr1 加计数规则 → dump-flows 的 n_bytes 是否跟随硬件流量更新"。

**5.2.1 【BF2】记录现有流表与计数基准**：

```bash
ovs-ofctl dump-flows ovsbr1 > /tmp/flows_before.txt
cat /tmp/flows_before.txt
```

**5.2.2 【BF2】加一条只计数、不改变转发行为的规则**（⚠️ 用 priority=1：现有 catch-all 已占 priority=0，同优先级两条规则胜负未定义，必须用更高优先级确保我们的规则匹配 p1 流量；NORMAL 动作 = 桥原有转发，路径不变）：

```bash
ovs-ofctl add-flow ovsbr1 "table=0,priority=1,in_port=p1,actions=NORMAL"
ovs-ofctl dump-flows ovsbr1 | grep "in_port=p1"   # 期望 n_packets=0
```

**5.2.3 【宿主机】起 iperf3 服务端**（复用 E1-1 1a 姿势，8 秒 10G）：

```bash
sudo ip addr add 192.168.101.1/24 dev enp94s0f0np0
sudo ip link set enp94s0f0np0 up
iperf3 -s -p 5201 -B 192.168.101.1 -D
sudo ip route add 192.168.101.1/32 dev eno1
timeout 12 iperf3 -c 192.168.101.1 -B 172.28.4.77 -t 8 -b 10G
sudo ip route del 192.168.101.1/32 dev eno1
```

**5.2.4 【BF2】打完后立刻 + 10 秒后各看一次**（in_port=p1 规则与 catch-all 两行都看，catch-all 的 n_bytes 是第二条判读线）：

```bash
ovs-ofctl dump-flows ovsbr1
sleep 10
ovs-ofctl dump-flows ovsbr1
```

**5.2.5 【宿主机】清理**：

```bash
pkill iperf3
sudo ip addr del 192.168.101.1/24 dev enp94s0f0np0
```

**5.2.6 【BF2】删除测试规则**（务必删，恢复原状）：

```bash
ovs-ofctl del-flows ovsbr1 "in_port=p1"   # 勿带 priority：del-flows 的 OF1.0 匹配语法不认（实测报 unknown keyword priority）
ovs-ofctl dump-flows ovsbr1 > /tmp/flows_after.txt
diff /tmp/flows_before.txt /tmp/flows_after.txt
# 期望：in_port=p1 规则行消失；catch-all 行只有 n_packets/n_bytes 统计数字变化（打过流量必然），无结构性差异
```

**判读（Claude 收到输出后做）**：

- n_bytes 涨了且增量 ≈ 8s × 10Gbps ≈ 9.4 GB（或同一量级）→ **P1 可行**，M2 直接走 P1；
- n_bytes 涨了但增量明显偏小/延迟超过 10s → P1 统计刷新周期太粗，测出刷新间隔后评估（配 `stats-update-interval` 或转 P2）；
- n_bytes 纹丝不动 → 该流 offload 后统计不回流（无 hw-offload 或规则未 offload），转 P2。

### 5.3 P1 测试结果与 P1b 补救探针（2026-09-15）

**实测结果（P1 判死）**：iperf3 9.31GB / 8s / 10G / 0 重传（宿主侧干净 NHD）；但 BF2 上新规则仅计到 **30,285 B / 311 包**，catch-all 仅 +177KB，10 秒后再看无迟到统计 → OpenFlow 规则 n_bytes **不回流 offload 后的硬件转发流量**（只计慢路径残渣，占比 0.0003%）。对应 §5.2 判读第三分支。

**附带发现**：`ovs-ofctl del-flows` 不接受 priority 关键字（OF1.0 匹配语法，add 可用 del 不可用，实测报 `unknown keyword priority`）→ 删除改用 `del-flows "in_port=p1"`（§5.2.6 已修正）。

**P1b 补救探针（零成本，决定走 P1b 还是 P2）**：offload 条目本身带硬件计数器，绕过 OVS 软件统计直接读。**按编号执行；标 ⏱ 的两步要连续（中间别停），其余任意慢。判读不依赖"打完立刻"——第 5 步的 60 秒二次 dump 兜底统计延迟，第 6 步的物理口对照兜底"流量没打进去"。**

```bash
# 【BF2】0. 记 p1 物理口计数前值（用于第 6 步对照：验证流量确实过了 p1）
ethtool -S p1 | grep -iE "rx_bytes|tx_bytes"
```

```bash
# 【BF2】1. 删残留规则（上一轮 del 失败遗留）
ovs-ofctl del-flows ovsbr1 "in_port=p1"        # 若报错，试 ovs-ofctl del-flows ovsbr1 in_port=8
ovs-ofctl dump-flows ovsbr1                   # 期望只剩 catch-all 一行

# 【BF2】2. DOCA 补查（上一轮第 3 步漏回传）
ls /opt/mellanox/doca/applications/
ls /opt/mellanox/doca/samples/ | head -40
ls /opt/mellanox/doca/infrastructure/ 2>/dev/null | head -20

# 【BF2】3. 打流前基线
ovs-dpctl dump-flows | head -40
tc -s filter show dev p1 ingress | head -40
tc -s filter show dev pf1hpf ingress | head -40
```

```bash
# 【宿主机】4. ⏱ 起服务端后立刻打 5 秒流（这段 6 行连续敲，别停）
sudo ip addr add 192.168.101.1/24 dev enp94s0f0np0
sudo ip link set enp94s0f0np0 up
iperf3 -s -p 5201 -B 192.168.101.1 -D
sudo ip route add 192.168.101.1/32 dev eno1
timeout 9 iperf3 -c 192.168.101.1 -B 172.28.4.77 -t 5 -b 10G
sudo ip route del 192.168.101.1/32 dev eno1
```

```bash
# 【BF2】5. ⏱ iperf 一结束立刻 dump 一次（offload 条目空闲 ~10s 后会被 OVS 删掉，必须在它活着时看）；
#        再等 60 秒 dump 第二次（兜底：若统计只是延迟刷新，第二次会爆发）
ovs-dpctl dump-flows | head -40
tc -s filter show dev p1 ingress | head -60
tc -s filter show dev pf1hpf ingress | head -60
sleep 60
ovs-dpctl dump-flows | head -40
tc -s filter show dev p1 ingress | head -60
tc -s filter show dev pf1hpf ingress | head -60

# 【BF2】6. 记 p1 物理口计数后值（与第 0 步前值之差应 ≈ +9.3GB：证明流量确实过了 p1，排除"没打进去"）
ethtool -S p1 | grep -iE "rx_bytes|tx_bytes"
```

```bash
# 【宿主机】7. 清理
pkill iperf3
sudo ip addr del 192.168.101.1/24 dev enp94s0f0np0
```

**判读预告**：第 6 步 p1 前值后值差 ≈ +9.3GB（流量有效）且 tc 过滤器出现 GB 级字节统计 → **P1b 复活**（轮询 tc 计数 = 纯 Arm 读硬件计数器，零带宽成本，每 5 元组可按 L4 端口分类）；p1 有 +9.3GB 但 tc/dpctl 都没有 → **转 P2**（DOCA Flow 计数管，设备已装 DOCA）；p1 没涨 → 流量姿势问题，回查打流命令。

### 5.4 探针结果（2026-09-15 已执行）：第三分支命中——打流姿势错误

**实测**：第 0/6 步 p1 物理口对照：`rx_bytes_phy` 只涨 **+54,224 B**（期望 +5.8GB）、vport rx +68,964 B → **5.82GB 流量根本没进 p1**。tc 里只有 OVS 的 IPv6 组播镜像与 ARP 洪泛软件条目（全部 `not_in_hw`），无任何真实流量条目。

**根因**：192.168.101.1 配在 fujian 本机（enp94s0f0np0），Linux 路由 **local 表优先级 0，本机地址永远本地投递**，`ip route add 192.168.101.1/32 dev eno1` 不可能覆盖 → client/server 同机时 iperf3 全程内核本地投递，从未离开 fujian。**E0-1 的 34.5 Gbps "NHD" 是同一假象**（其 CSV net 列全程仅 60–894 B/s 背景即铁证）；E0-2（56.103 非本机）与 E0-3 真实，G 系列真实。

**影响**：

- "P1 判死"撤销——前两轮测试没有真实流量，offload 统计回流问题**未决**；
- E0-1 经验证据作废："NHD 对 Arm 不可见"理论结论不变（架构上 eSwitch→主机 PF 不经 Arm RC），但实证需正确姿势重做（论文 CRITICAL #2 措辞暂不落笔）；
- E1-1 的 1a/1c 与 E1-2 的 N1（服务端 enp94s0f0np0 + 同机客户端）同样作废，恢复 E1 前须改姿势；1b/2a/2b 不受影响。

**正确 NHD 姿势**（服务端 IP 必须落在桥内 PF1 面 enp94s0f1np1，客户端不得同机）：

- **首选 A（客户端已就位：另一台服务器，2026-09-15 确认）**：用户手头还有一台服务器（另一张 BF2 的宿主机），与 fujian 互 ping 通、那张 BF2 的 p1 link=yes → 它就是"实验室机器"。N2 对称设计：fujian `ip addr add 10.99.99.1/24 dev enp94s0f1np1`；另一台服务器 `ip route get 172.28.4.77` 查 lab 网卡 `<dev>` → `sudo ip addr add 10.99.99.2/24 dev <dev>` → `ping -c 3 10.99.99.1`。数据/回程全经 p1↔pf1hpf。
  - **ping 通 = 姿势成立**（ARP 已穿过 BF2 p1→桥→PF1），直接进下方重测组合；
  - ping 不通 → 先 `ip addr | grep 10.99.99` 看两步 IP 是否配上（**helong 侧 10.99.99.2 没配的话 ping 会走默认网关、100% 丢包**）；仍不通 → 静态邻居兜底（**键必须是 10.99.99.x，不是 172.28.4.x；MAC 取对方接口的，不是本机的**）：helong `sudo ip neigh replace 10.99.99.1 lladdr <fujian enp94s0f1np1 的 MAC> dev eno1`，fujian `sudo ip neigh replace 10.99.99.2 lladdr <helong eno1 的 MAC> dev enp94s0f1np1`；再不通 → 三端 tcpdump 三角定位（见下），判定 p1 到底接在哪张二层网。
  - 客户端现场事实（9/15 晚实测）：helong 的 lab 网卡 = **eno1（172.28.4.85，仅 100Mb/s）**→ iperf 期望按 ~94Mbps 缩小（-t 20 ≈ +0.23GB）；helong 自己也插着 BF2（有 enp94s0f1np1）。fujian 上另有第二张 BF2 卡（enp175s0f0np0/enp175s0f1np1，均 DOWN）——与本次实验无关。
  - 附带发现：另一张 BF2 的 p1 link=yes——若也接同一实验室二层网，以后可做 BF2↔BF2 eSwitch 对打实验（M3 之后考虑）。（9/15 师兄确认两台服务器相连，此通路成立，见 §5.6。）

**烟测不通时的三端三角定位**（helong 上 `ping -f -c 100 10.99.99.1` 连发的同时，三端各抓 ARP 10 秒）：

```bash
# fujian：eno1 能否看到 10.99.99.1 的 ARP 请求？（helong↔fujian 是否同广播域）
sudo timeout 10 tcpdump -i eno1 -nn -e arp | grep -i 10.99.99
# BF2：p1 能否看到？（helong 的广播域是否与 p1 的二层网重叠）
timeout 10 tcpdump -i p1 -nn -e arp | grep -i 10.99.99
#   （BF2 没装 tcpdump 就 ping -f -c 1000 前后 ethtool -S p1 对照 rx 增量）
# fujian：PF1 能否看到？（桥是否把广播从 p1 洪泛到 pf1hpf）
sudo timeout 10 tcpdump -i enp94s0f1np1 -nn -e arp | grep -i 10.99.99
# fujian：顺手对照——p1 上出现过的 MAC 44:4c:a8:56:ca:7c（STP 源）是否在 eno1 邻居表
ip neigh | grep -i 44:4c:a8:56:ca:7c
```

> 定位：eno1 见、p1 不见 → **p1 不在 172.28.4.x 这张网**（拓扑真相；找师兄问 p1 接的是哪台交换机）；p1 见、PF1 不见 → 桥不洪泛广播（OVS 配置问题）；PF1 见、ping 仍不通 → 回程问题（查 rp_filter / arp_ignore）。

**三角定位结果（9/15 晚已执行）→ p1 不在 172.28.4.x 这张网，方案 A/B 姿势全废**：

- eno1 上：helong 的 who-has 10.99.99.1 到了，且 **fujian 自己用 eno1 的 MAC（0c:c4:7a:fd:e4:3a）回答了**——多宿主机 arp_ignore=0 陷阱，任何在 eno1 广播域里的客户端都会把流量发给 fujian 本机、绕开 BF2；
- pf1 上：只有 fujian 自己发出的 who-has 10.99.99.2（无应答）——fujian 的回程确实走 PF1（姿势设计正确），但对方不在；
- **p1 上：10 秒抓到 19-20 个 ARP 包全是背景流量，grep 10.99.99 零匹配** → helong 的广播域与 p1 的二层网不重叠（p1 上另有 STP BPDU 源 44:4c:a8:56:ca:7c 与 NVIDIA 卡 IPv6 组播 b8:59:9f:b7:ba:b5/bc，疑似另一张 BF2 所在的数据网）。

**改道（不依赖 p1 拓扑，直接裁决 pipe 机制，现在就可执行）**：P1/P1b 三问本质是"OVS 流表/tc 硬件计数能否反映 eSwitch 转发流量"，用已实证真实的 56.x 管道（pf1hpf↔SF，E0-2/G 系列）同样可测：

```bash
# 【BF2】1. 前值
ethtool -S pf1hpf | grep -iE "rx_bytes|tx_bytes"
# 【BF2】2. 加计数规则（56.x 业务不受影响：NORMAL 语义与 catch-all 相同）
ovs-ofctl add-flow ovsbr1 "table=0,priority=1,in_port=pf1hpf,actions=NORMAL"
# 【fujian】⏱ 打流 10s（服务端未起则先在 BF2 上 /root/bf2k/bench/bin/iperf3 -s -p 5202 -B 192.168.56.103 -D）
iperf3 -c 192.168.56.103 -p 5202 -t 10 -b 10G
# 【BF2】⏱ 打完后立刻 + 60s 后各一遍
ovs-ofctl dump-flows ovsbr1
tc -s filter show dev pf1hpf ingress | head -80
ovs-dpctl dump-flows | head -40
# 【BF2】5. 后值 + 删规则
ethtool -S pf1hpf | grep -iE "rx_bytes|tx_bytes"
ovs-ofctl del-flows ovsbr1 "in_port=pf1hpf"
```

> 判读：新规则 n_bytes ≈ +11.7GB（10s×9.4Gbps）→ **P1 复活**（OVS 流表计数可用）；tc 出现 `in_hw` 条目带 GB 级统计 → **P1b 复活**；都没有 → 转 P2。p1 口的 NHD 数字等 p1 接线搞清楚（问师兄 p1 接的是哪台交换机；helong 那张 BF2 若与 p1 同网，以后用它当客户端做 BF2↔BF2 对打）。

### 5.5 56.x 计数测试结果（2026-09-15 晚已执行）→ P1+P1b 双复活 + M2 验证操作单

**实测**（in_port=pf1hpf 规则 + iperf3 56.103 `-t 10 -b 10G`，总流量 8.74GB）：

- 新规则 `n_bytes=8,737,979,679 / 5,771,497 包`（24s 时点）→ **P1 复活**：OVS 流表计数如实反映 eSwitch 转发流量；
- tc pf1hpf ingress 出现 **`in_hw`** flower 条目（dst_mac 02:8c:0e:26:ae:d7 → redirect en3f1pf1sf0），`Sent hardware=8,737,978,641 B、software=0` → **P1b 复活**，全程硬件转发；
- vport_rx_bytes 增量 = 8,737,992,755 ≈ 规则 n_bytes（差 0.00015%）→ **三层计数一致，offload 统计回流成立**；
- 规则 n_bytes 比 tc 多 1,038B/26 包 = 建连/拆连慢路径残渣，可忽略；etholt `rx_bytes` 全程 0 = 流量没走软件网卡，纯 HW 转发。

**坑与纪律**：同 match 的 `add-flow` 是**替换**（计数清零）——本轮执行时第二次 add-flow 把计数清了，最后 dump 只剩 74 包背景（判读不受影响，但 M2 跑窗口期间**不得重复加规则**）；本轮规则已删，设备已恢复干净。

**M2 最小采集器**（`tools/collect_pipe.sh`，**9/17 已按 P1 补验改造**）：口径分层——wire 口（`-w` 列表，默认 p1）读 sysfs 物理口计数（rx_packets/rx_bytes，对 NHD 100% 可靠）；Arm 面口（pf1hpf/en3f1pf1sf0）仍挂 priority=1 计数规则、每秒 `dump-flows` 轮询 n_packets/n_bytes（对 Arm 终接流量 100% 成立）。bash、ASCII、设备端直跑；OVS 规则退出时自动删。⚠️ 部署用 fujian `git pull` + `bash deploy.sh`（deploy 只传 git 跟踪文件——改造版须先提交推送）。

**M2 验证操作单（9/18 修订：完整自含流程，两路打流在同一 50s 窗口内，无需再看 §5.6）**：

1. 部署：【fujian】`git pull` + `bash deploy.sh`（只传 git 跟踪文件、路径保持原样）；【BF2】`chmod +x /root/bf2k/tools/collect_pipe.sh && bash -n /root/bf2k/tools/collect_pipe.sh`（应无输出）。
2. 服务端准备：【BF2】Arm 侧服务端若已停：`/root/bf2k/bench/bin/iperf3 -s -p 5202 -D`；【fujian】`sudo ip addr add 10.99.99.1/24 dev enp94s0f1np1` + `iperf3 -s -B 10.99.99.1 -p 5201 -D`。
3. 客户端准备：【helong 的 BF2】（helong 上 `ssh root@192.168.100.2`）`ip addr add 10.99.99.3/24 dev p1`（报已存在则跳过）；`ping -c 2 10.99.99.1` 确认通路（不通 → 回 §5.6 Part A 兜底）。
4. 起采集 60s：【BF2】`cd /root/bf2k && sudo ./tools/collect_pipe.sh -d 60 -o pipe_m2.csv`（两路 10+5s 留足余量；9/18 首轮用 -d 50 时第二路起在窗口尾部被截断）
5. ⏱ 第一路（屏幕开始刷行后）：【fujian】`iperf3 -c 192.168.56.103 -p 5202 -t 10 -b 10G`
6. ⏱ 第二路（第一路结束后立刻）：【helong 的 BF2】`/root/iperf3 -c 10.99.99.1 -B 10.99.99.3 -t 5 -b 10G`
7. 等 60s 自然结束，【BF2】`ls -l /root/bf2k/pipe_m2.csv` 非零。（可选双侧对照：同窗再跑 `sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_m2_check.csv`，与主验收二选一即可。）
8. 回传 `pipe_m2.csv`（fujian 中转 scp）。

判读（Claude）：pf1hpf 列增量 ≈ 8.7GB（OVS 规则口径，Arm 终接流量 100% 成立，对应第 5 步 10s 段）；p1 列 = sysfs 物理口 rx 增量，第 6 步 5s 段 ≈ 5.8GB、第 5 步段应 ≈ 背景（56.x 流量不经 p1）；en3f1pf1sf0 ≈ 背景 → **M2 验收通过** → M3（L4 分类 pipe：加 5201/6379/6380 端口）或直接恢复 E1 并行采集。
- **备选 B（无实验室机器）**：fujian 单机双 netns + macvlan（不动 56.11/eno1 原有配置）：

```bash
sudo ip link add srv0 link enp94s0f1np1 type macvlan mode bridge
sudo ip link add cli0 link eno1 type macvlan mode bridge
sudo ip netns add srv; sudo ip netns add cli
sudo ip link set srv0 netns srv; sudo ip link set cli0 netns cli
sudo ip netns exec srv ip addr add 192.168.101.1/24 dev srv0
sudo ip netns exec srv ip link set srv0 up
sudo ip netns exec srv ip route add default dev srv0
sudo ip netns exec cli ip addr add 172.28.4.99/24 dev cli0   # 99 换成没人用的地址
sudo ip netns exec cli ip link set cli0 up
sudo ip netns exec cli ip route add 192.168.101.1 dev cli0
sudo ip netns exec srv iperf3 -s -p 5201 -B 192.168.101.1 -D
sudo ip netns exec cli ping -c 2 192.168.101.1   # 通 = 姿势成立
sudo ip netns exec cli timeout 12 iperf3 -c 192.168.101.1 -B 172.28.4.99 -t 8 -b 10G
```

清理（B 用）：`sudo ip netns del srv; sudo ip netns del cli`（链随 netns 一起删）。

**重测组合（三台机器各一个终端；一次回答三个问题：流量是否真到、offload 统计是否回流、tc 有无 in_hw 硬件计数）**：

```bash
# 【fujian】0. 起服务端
sudo ip addr add 10.99.99.1/24 dev enp94s0f1np1
iperf3 -s -B 10.99.99.1 -p 5201 -D

# 【另一台服务器】0'. 配客户端（先 ip route get 172.28.4.77 拿 <dev>；
#     再 ethtool <dev> | grep Speed 记速率，定 -b：≥10G 用 -b 10G，1G 用 -b 1G）
sudo ip addr add 10.99.99.2/24 dev <dev>
ping -c 3 10.99.99.1                    # ⏱ 通 → 继续；不通 → 见上方兜底

# 【BF2】1. 前值
ethtool -S p1 | grep -iE "rx_bytes|tx_bytes"
# 【BF2】2. 加计数规则
ovs-ofctl add-flow ovsbr1 "table=0,priority=1,in_port=p1,actions=NORMAL"
# 【另一台服务器】⏱ 立刻打流 8s
iperf3 -c 10.99.99.1 -B 10.99.99.2 -t 8 -b 10G
# 【BF2】⏱ 打完后立刻（10 秒内）
ovs-ofctl dump-flows ovsbr1                            # 3. 看新规则的 n_packets/n_bytes
tc -s filter show dev p1 ingress | head -80
ovs-dpctl dump-flows | head -40
# 【BF2】4. 60s 后再看
sleep 60
ovs-ofctl dump-flows ovsbr1
tc -s filter show dev p1 ingress | head -80
# 【BF2】5. 后值
ethtool -S p1 | grep -iE "rx_bytes|tx_bytes"
# 【BF2】6. 删规则
ovs-ofctl del-flows ovsbr1 "in_port=p1"

# 收尾：【fujian】pkill iperf3; sudo ip addr del 10.99.99.1/24 dev enp94s0f1np1
#       【另一台服务器】sudo ip addr del 10.99.99.2/24 dev <dev>
```

**判读预告**：p1 后值-前值 ≈ +9.3GB（10G 时）/ +0.93GB（1G 时）才算流量有效（否则姿势仍不对，查 ping 与打流命令）；dump-flows 新规则 n_bytes ≈ 同量级 → **P1 复活**（OVS 流表计数可用）；tc 出现 `in_hw` 条目带 GB 级统计 → **P1b 复活**（轮询 tc 硬件计数）；都没有 → 转 **P2**。

### 5.6 p1 接线答案与 helong BF2 p1 客户端测试（2026-09-15 师兄答复后更新）

**师兄答复（Task #33）**：fujian 与 helong 是两台独立服务器，各部署一张 BF-2，**两台服务器之间是连着的** → p1 接线之谜的答案：两张 BF-2 的上联口（p1）即两台服务器之间的互联通路（直连或经同一交换机；p1 上抓到的 STP BPDU 源 44:4c:a8:56:ca:7c 应即该链路经过的交换机）。

**证据比对**：helong 活动 BF2 的 p1 = `08:c0:eb:d1:fc:e7`（ip link show 实测），而 p1 上抓到的 NVIDIA 组播源是 `b8:59:9f:b7:ba:b5/bc`（与 fujian enp175 卡的 `b8:59:9f:b7:ba:ac/ad` 同段）→ 组播源不是 helong 的活动卡，疑似某张第二卡的上联口；10 秒窗口抓不到 08:c0:eb 不等于不同网，**以 Part A 的 ping 实测为准**。

**含义**：若 Part A 通过，helong 的 BF2 p1 即我们寻找的 NHD 客户端——BF2↔BF2 线缆流量成立，E1 的 NHD 柱（N1 定标/N2 应用级）与 E0-1 重证全部解锁，且绕开 helong eno1 的 100Mb/s 瓶颈（其 p1 的链路速率由 Part A 第 1 步 ethtool 确认，10G+ 则可满速定标）。

**Part A 烟测（~5 分钟，无需部署，现在就可执行；只回答"两张 p1 是否同一二层网"）**：

```bash
# 0.【fujian】清理上轮毒邻居 + 起服务端
sudo ip neigh del 172.28.4.85 dev eno1 2>/dev/null
sudo ip addr add 10.99.99.1/24 dev enp94s0f1np1
iperf3 -s -B 10.99.99.1 -p 5201 -D

# 0'.【helong】清毒邻居与上轮残留 IP
sudo ip neigh del 172.28.4.77 dev eno1 2>/dev/null
sudo ip addr del 10.99.99.2/24 dev eno1 2>/dev/null

# 1.【helong 的 BF2】（从 helong 登录：ssh root@192.168.100.2）
ethtool p1 | grep -iE "Speed|Link detected"   # 记速率（判 -b 用）
ip addr add 10.99.99.3/24 dev p1
ping -c 3 10.99.99.1                            # 期望 0% 丢包

# 2.【我们的 BF2】在 helong 敲 ping 之前先开好抓包
timeout 10 tcpdump -i p1 -nn -e arp | grep -i 10.99.99
```

> 判读：ping 通 + 我们 p1 抓到 who-has 10.99.99.1 → **两张 p1 同二层网，拓扑确认，进 Part B**；ping 不通 → 笔记记录现象回传（可能交换机端口有 VLAN/隔离，需再问师兄交换机配置；顺手查我们的 p1 是否在 ovsbr1：`ovs-vsctl show`）。
> 清理（暂不进 Part B 时）：helong 的 BF2 `ip addr del 10.99.99.3/24 dev p1`；fujian `pkill iperf3; sudo ip addr del 10.99.99.1/24 dev enp94s0f1np1`。

**Part B 满速计数（Part A 通过后执行；与 Task #30 M2 验收同一窗口做最省事）**：

```bash
# 1.【helong 的 BF2】拿 iperf3（静态 aarch64，链：Windows → fujian → helong → helong 的 BF2）
#   【Windows】把 D:\bf2-collector\bench\bin\iperf3 按平时链路 scp 到 fujian:/tmp/
#   【fujian】scp /tmp/iperf3 huaz@helong:/tmp/
#   【helong】scp /tmp/iperf3 root@192.168.100.2:/root/

# 2.【我们的 BF2】开采集（仅 p1 一口；collect_pipe.sh 已部署则用它，否则用下方手动组合块）
cd /root/bf2k
sudo ./tools/collect_pipe.sh -d 50 -o pipe_p1.csv -p p1

# 3.【helong 的 BF2】屏幕开始刷行后 ⏱（10 秒内）打流
/root/iperf3 -c 10.99.99.1 -B 10.99.99.3 -t 10 -b 10G   # -b 按 Part A 记的 Speed 定

# 4.【我们的 BF2】等 50s 自然结束，回传 pipe_p1.csv
```

> 判读：p1_bytes 流量段增量 ≈ 实际吞吐字节（iperf3 报告的实际吞吐折算，10 秒 ≈ 12–13GB，Part B 实测 13.1GB）→ **p1 口物理口计数成立（NHD 采集口径定案）**；p1_bytes ≈ 0 → 流量没进 p1（桥/姿势问题），回传笔记。
> ⚠️ 纪律：同 §5.5——窗口期间不得重复加规则（collect_pipe.sh 退出自动清理，无需手动删）。
> ⚠️ 打招呼：这是两台服务器之间的互联链路，打满速流之前跟师兄确认链路上没有他人业务（我们抓包只见 BPDU/组播背景，仍以口头确认为准）。

**Part B 结果（9/15 已执行）→ NHD 流量首次真实进 p1、P1b 在 p1 成立**：helong BF2 的 iperf3 客户端跑成功（我们 BF2 上那条 `/root/iperf3: No such file or directory` 是敲错机器，无影响），10 秒流量：tc p1 ingress 的 offload 条目 **`in_hw`、Sent hardware=13,109,035,891 B / 8,679,123 包、software=0**（src_mac 08:c0:eb:d1:fc:e7 = helong BF2 p1，dst=我们 PF1，redirect pf1hpf）≈ ethtool `rx_bytes_phy` 增量 13,109,346,818（差 0.0024% = 背景）→ **tc 硬件计数与物理口三层一致，与 56.x 同构；wire 速率 ≈10.5Gbps（链路疑似 25G，p1 Speed 待用户补记）**。**P1 规则计数未读到**：两次 `dump-flows | grep "in_port=p1"` 空输出 = OVS 输出带引号（`in_port="p1"`）grep 不匹配，规则本身在表里（非装失败）→ 补验见下。附带：tc 里 LLDP drop 条目 src 44:4c:a8:56:ca:7c = 段上那台交换机确实存在；一条 33 天前的 ARP 镜像规则（src 08:c0:eb:d1:fa:97 持续 ARP 192.168.2.x）就是 p1 背景 ARP 的来源（另一张 BF2 卡的 Arm 侧）。

**P1 规则补验（2 分钟，同一通路重打 5 秒即可）**：

```bash
# 【我们的 BF2】加规则并确认在表
ovs-ofctl add-flow ovsbr1 "table=0,priority=1,in_port=p1,actions=NORMAL"
ovs-ofctl dump-flows ovsbr1 | grep -E 'in_port="?p1"?'   # 期望 priority=1,in_port="p1" 行、n_packets=0
# 【helong 的 BF2】⏱ 打流 5s
/root/iperf3 -c 10.99.99.1 -B 10.99.99.3 -t 5 -b 10G
# 【我们的 BF2】⏱ 打完后立刻：完整 dump（不加 grep），整段输出回传
ovs-ofctl dump-flows ovsbr1
# 【我们的 BF2】删规则
ovs-ofctl del-flows ovsbr1 "in_port=p1"
```

> **补验实测（9/17 已执行）→ P1 对 NHD 判死（新形态），P1b/物理口仍成立**：helong 全速 10Gbps×5s=5.82GB（接收端 5.82GB 全额），规则只计 n_bytes=1,317,933,394 ≈ **22.6%**；且 1.32GB÷10Gbps ≈ **1.1 秒** = 恰好流量开始后前 ~1.1s 的量——规则计数在 ~1.1s 处冻结。机理：规则加表后先走内核 datapath（计数正常）→ **offload 完成、eSwitch 硬件直转接管 wire→host** → 流量不再经内核 → dump-flows 统计停止增长、**HW 段统计不回流 OVS**。旁证：①前窗 1.33GB = 同次测试软件段 1.32GB + 19min 背景 16.4MB（≈114 pkt/s，与 catch-all 背景 116 pkt/s 吻合）；②iperf 第 2 秒 48 次重传 + cwnd 856→732KB 下探 = offload 切换瞬间丢包。
> **定性修正**：§5.5 的"三层一致/统计回流成立"是在 **Arm 终接流量**（56.x，全部经内核）下测的，推广到 NHD 是错误外推。**P1（OVS 规则轮询）只对 Arm 终接流量（NAD）成立；对 NHD 只计 offload 前软件段（实测 ~23%，随 offload 延迟浮动）**。不受影响：Part B 的 tc in_hw=13.1GB 证明 **tc 能读硬件计数（P1b 对 NHD 成立）**；E1 每口 sysfs 列（物理口/vport 计数器）与 NAD/NHD 主图数据全部有效。附带数据点：**offload 编程延迟 ≈1.1s**，期间 10Gbps 经 Arm 内核慢路径转发未丢包。
> **修复（2026-09-17 已改造 collect_pipe.sh，待提交部署）**：wire 口（p1）改读 sysfs 物理口计数（/sys/class/net/p1/statistics/rx_bytes，已裁定=rx_bytes_phy）；Arm 面口保留 OVS 规则作互校；M2 验收标准改 p1 列 ≈5.8GB；M3 的 L4 分类 NHD 侧不可信 → 留 P2/P3。

**手动组合块（collect_pipe.sh 未部署时替代第 2 步，其余同上）**：

```bash
ethtool -S p1 | grep -iE "rx_bytes|tx_bytes"          # 前值
ovs-ofctl add-flow ovsbr1 "table=0,priority=1,in_port=p1,actions=NORMAL"
# … helong 的 BF2 打流 10s …
ovs-ofctl dump-flows ovsbr1 | grep -E 'in_port="?p1"?' # n_bytes 增量 ≈ 流量（⚠️ 带引号输出，勿用 grep "in_port=p1"）
sleep 60
ovs-ofctl dump-flows ovsbr1 | grep -E 'in_port="?p1"?' # 兜底迟刷
ethtool -S p1 | grep -iE "rx_bytes|tx_bytes"          # 后值 ≈ +11.6GB
ovs-ofctl del-flows ovsbr1 "in_port=p1"
```

## 6. M1/M2 技术要点（P2 路线，M0 判定需要时才动工）

- **参考代码**：NVIDIA 官方 DOCA Bifurcated Driver Model Reference Application——它就是"ingress 分类 pipe（按 port_id 匹配）+ egress pipe + **per-entry counter** + `doca_flow_resource_query_entry()` 查询"的最小完整模式，改造它比从零写靠谱得多。
- **改造目标 `code/collect_pipe.c`**（ASCII-only，同项目规范）：初始化 DOCA Flow → 取 switch port（p1/pf1hpf/SF 三个）→ 建 ingress pipe（match = port_id）→ 每口一个 entry（动作 NORMAL 语义 = forward，挂非共享 counter）→ `doca_flow_entries_process` → 主循环每秒 `query_entry` 读字节/包数写 CSV。
- **编译**：DOCA/DPDK 已装在设备上（M0 §5.0）→ **优先设备原生编译**（gcc 9.4 已确认可用，链接 /opt/mellanox/doca/lib）；仅当参考应用源码不在设备上时，才需 Windows 下载 SDK（NVIDIA 开发者账号，免费）提取样例源码 scp 过去。
- **与 OVS 共存**：pipe 程序与 ovsbr1 的 offload 流共用 eSwitch 管线；Flow Inspector 官方服务与 OVS 共存，说明可行，但 M2 验证时先用空载窗口跑、确认不打乱现有 56.x 管道。
- **L4 分类（M3）**：entry match 增加外层 L4 协议/目的端口（iperf3 5201、redis 6379/6380）→ 应用级字节计数，直接分离 56.x 他人背景。

## 7. 风险与回退

| 风险                   | 回退                                                                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------------------- |
| P1 统计不回流/刷新太慢        | **已证伪（9/15 晚 56.x 实测）**：pf1hpf 规则 n_bytes 8.74GB ≈ tc in_hw 硬件计数 ≈ vport 增量，三层一致 → P1 为主路线；若后续个别端口不回流再启用 P1b/P2 |
| DOCA SDK 下载需账号/许可    | 用户注册 NVIDIA 账号（免费）；或问师兄实验室是否已有 SDK/现成 pipe 工具                                                                   |
| P2 与 OVS offload 冲突  | 只在实验窗口启动 pipe 程序；或用官方 Flow Inspector（与 OVS 共存的设计）                                                               |
| pipe 计数与 sysfs 判据对不上 | 回 E1-1 定标数据重审方向语义；必要时请师兄现场对口径                                                                                   |
| 加计数规则影响转发性能          | 规则用 NORMAL 动作 + 仅高出 catch-all 一级的优先级（纯计数不改路径）；性能敏感实验时删除规则                                                       |

## 8. 待确认清单

1. **师兄口径复核**：下次见到师兄确认他说的 pipe 是 OVS 流表（P1）还是 DOCA Flow（P2）——**P1 已于 9/15 晚实测可行（OVS 流表计数与 tc 硬件计数/vport 三层一致）**，成本低一个量级，优先采用；仍需当面确认师兄所指是否即此层；
2. **NVIDIA 开发者账号**：若 M0 判定需要 P2，用户是否已有/愿意注册（用于下载 DOCA SDK）；
3. **E1 恢复时机**：pipe 跑通（M2 验收）后恢复 E1（e1-1 定标 + e1-2 应用矩阵照原操作单执行，pipe CSV 与之并列采集）。
