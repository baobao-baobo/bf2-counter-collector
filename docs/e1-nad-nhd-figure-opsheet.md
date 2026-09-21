# NAD/NHD 主图操作单（专为一图，E1 系列精简版）

> ✅ 2026-09-15 已出图：fig/e1_nad_nhd.png（全部数据块验证通过；tools/verify_nad_nhd.py 可复现判读与数据文件生成）。

> 目的：为"NAD/NHD eSwitch 去向分解图"采集全部数据块。数据块与判读同 docs/e1-eswitch-split-experiment-plan.md §5 主图（图 2），但姿势全部按 9/15 实证修正：E1-1 的 1a/1c/N1 旧姿势作废（服务端本机地址陷阱），wire 侧客户端 = **helong BF2 的 p1**（9/15 已实证：ping 零丢包 + iperf3 13.1GB 全程硬件转发）。
> 角色：【宿主机】= fujian；【我们的 BF2】= 从 fujian `ssh root@192.168.100.2`；【helong 的 BF2】= 从 helong `ssh root@192.168.100.2`（注意区分！）。
> 前置：设备已部署每口列扩展采集器（8ff40c4）与 `configs/e1_esw.conf`（E1-1 §0 已做过；未做过先跑 e1-1 的 §0）。
> 总时长：必跑块（N0/N1/G3/G7）约 60 分钟含准备；选跑 N2 再加约 20 分钟。
> 节奏纪律：每个采集窗口 50s，**开始采集后尽快打流（10 秒内）**；CSV 落在 `/root/bf2k/` 下。
> ⚠️ 打招呼：N1/N2 要打满速流量，打之前跟师兄确认两台服务器间的互联链路上没有他人业务。

## 0. 准备（一次性，~5 分钟）

**0.1 【我们的 BF2】确认采集器与配置就位**：

```bash
cd /root/bf2k
sudo ./code/collect_all --check-config -c configs/e1_esw.conf
# 期望 [net] enabled=true 且 interfaces: pf0hpf pf1hpf p1 en3f1pf1sf0 enp3s0f1s0
```

**0.2 把两个静态二进制传到 helong 的 BF2**（链：我们 BF2 → fujian → helong → helong 的 BF2；Part B 已传过 iperf3 则只需传 redis-benchmark）：

```bash
# 【fujian】从我们 BF2 拉
scp root@192.168.100.2:/root/bf2k/bench/bin/iperf3 /tmp/
scp root@192.168.100.2:/root/bf2k/apps/bin/redis-benchmark /tmp/
# 【fujian】传 helong
scp /tmp/iperf3 /tmp/redis-benchmark huaz@helong:/tmp/
# 【helong】传 helong 的 BF2
scp /tmp/iperf3 /tmp/redis-benchmark root@192.168.100.2:/root/
```

## 1. N0：NAD 定标线（56.x 双向，2 个窗口，~5 分钟）

**1.1 【我们的 BF2】起服务端（2a/2b 共用）**：

```bash
cd /root/bf2k
pkill iperf3 || true
/root/bf2k/bench/bin/iperf3 -s -p 5202 -B 192.168.56.103 -D
```

**1.2 【宿主机】确认管道**：`ping -c 2 192.168.56.103`

**1.3 场景 2a（host→Arm）**：

```bash
# 【我们的 BF2】开采集
sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_n0_2a.csv
# 【宿主机】屏幕开始刷行后立刻（10 秒内）：
timeout 20 iperf3 -c 192.168.56.103 -p 5202 -t 15 -b 10G
# 【我们的 BF2】等自然结束（约 50 秒），ls -l /root/bf2k/e1_n0_2a.csv
```

> ⚠️ 实测（9/15）：2a 只有 ~6 Gbps（15s 平台 3.7-7.6 Gbps 震荡、均值 6.05）——不是慢启动截断，是 **Arm 收包瓶颈**（pipe-collection-plan P3 注 ~6.6 Gbps），与 2b 的 10.4 Gbps 构成 NAD 双向不对称。`-t 15` 保留（平台更长更稳）。

**1.4 场景 2b（Arm→host，-R 反转）**：同上，输出换 `e1_n0_2b.csv`，宿主机命令加 `-R`。

**1.5 收尾**：【我们的 BF2】`pkill iperf3`。

## 2. N1：NHD 定标线（wire→主机，4 速率，~10 分钟）

**2.1 【宿主机】配辅助 IP 并起服务端**（若 Part B 收尾已清）：

```bash
sudo ip addr add 10.99.99.1/24 dev enp94s0f1np1
iperf3 -s -B 10.99.99.1 -p 5201 -D
```

> `RTNETLINK answers: File exists` = 地址已在（P1 补验残留），无害，跳过 add 即可。
> 起服务端前先 `pgrep -af iperf3`：若已有 `-B 10.99.99.1 -p 5201` 的服务端在听 → 直接复用，**不要重复起**（会 bind 冲突）；想重起先 `pkill iperf3`。

**2.2 【helong 的 BF2】配客户端地址并验证**（Part B 姿势，已实证）：

```bash
ip addr add 10.99.99.3/24 dev p1
ping -c 2 10.99.99.1        # 期望 0% 丢包
```

**2.3 逐速率跑（速率 = 1 / 5 / 10 / 20 Gbps，各一跑；换两处字面即可）**：

```bash
# 【我们的 BF2】开采集（50 秒）
sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_n1_1g.csv

# 【helong 的 BF2】屏幕开始刷行后立刻（10 秒内）：
timeout 12 /root/iperf3 -c 10.99.99.1 -B 10.99.99.3 -t 8 -b 1G

# 【我们的 BF2】等自然结束，ls -l /root/bf2k/e1_n1_1g.csv
```

> 后三跑：`-b 5G`/`-b 10G`/`-b 20G`，输出换 `e1_n1_5g.csv`/`e1_n1_10g.csv`/`e1_n1_20g.csv`。
> ⚠️ 20G 单流上不去（吞吐远低于 19 Gbps）就加 `-P 4` 重打一次，笔记记录两种写法的吞吐。
> 每跑笔记：速率、实际吞吐、BF2 屏幕哪几列在动（预期 p1_rx、pf1hpf_tx）。

## 3. G3：Redis-Arm（NAD 应用柱，3 跑，~10 分钟）

**3.1 【我们的 BF2】起 redis 服务（56.103）**：

```bash
pkill -x redis-server || true
apps/bin/redis-server --bind 192.168.56.103 --port 6379 --save "" \
    --appendonly no --protected-mode no --daemonize yes
apps/bin/redis-cli -h 192.168.56.103 ping        # 期望 PONG
```

**3.0 【宿主机】确认 redis-benchmark 已装**（⚠️ 9/15 实测：fujian 默认没有，三跑全空）：

```bash
which redis-benchmark || sudo apt install -y redis-server redis-tools
```

> ⚠️ 装包可能触发 needrestart 重启 rshim，装完先 `ping -c 2 192.168.100.2` 确认链路再继续。

**3.2 【宿主机】确认管道**：`ping -c 2 192.168.56.103`

**3.3 三连跑**：

```bash
# 【我们的 BF2】
sudo ./run_phase.sh -c configs/e1_esw.conf -o e1_g3_run1.csv -t 40 -a "sleep 40"

# 屏幕出现 APP PHASE START 后（约启动 5s），立即在【宿主机】上：
redis-benchmark -h 192.168.56.103 -p 6379 -t set,get -n 3000000 -c 64 -d 128 -q
```

> run2/run3 换 `-o e1_g3_run2.csv` / `e1_g3_run3.csv`。
> 笔记：benchmark 吞吐、屏幕跳动列（预期 pf1hpf 两列、Arm 镜像对两列；p1 ≈ 0）。

**3.4 收尾**：`apps/bin/redis-cli -h 192.168.56.103 shutdown nosave`

## 4. G7：Redis+SQLite 混合（NAD 应用柱，3 跑，~10 分钟）

**4.1 【我们的 BF2】先起 redis**（同 3.1 两条命令）。

**4.2 三连跑**（纪律：每次 run_phase 之前单独 rm 并确认删净）：

```bash
# 【我们的 BF2】删旧库（DB 在根分区 = eMMC，勿放 /tmp）：
rm -f /root/bf2k/g7.db*
ls -l /root/bf2k/g7.db*     # 必须报 No such file；否则没删干净，停！
sudo ./run_phase.sh -c configs/e1_esw.conf -o e1_g7_run1.csv -t 40 \
    -a "apps/bin/sqlite3 /root/bf2k/g7.db < apps/sqlite_workload.sql"

# 屏幕出现 APP PHASE START 后，立即在【宿主机】上：
redis-benchmark -h 192.168.56.103 -p 6379 -t set,get -n 3000000 -c 64 -d 128 -q
```

> 每跑结束看 sqlite 输出：.timer 应 INSERT ~20s 起步、全程 ~32s 自然退出；若见 "table t already exists" / "UNIQUE constraint failed" → 跑在旧库上，该跑作废、重 rm 重跑。
> run2/run3 换 `-o e1_g7_run2.csv` / `e1_g7_run3.csv`。

**4.3 收尾**：`apps/bin/redis-cli -h 192.168.56.103 shutdown nosave`；顺手 `rm -f /root/bf2k/g7.db*`。

## 5. N2（选跑）：Redis-Host（NHD 应用柱，3 跑，~20 分钟）

> 与 G3 同一负载、server 位置翻转（fujian 当 server、wire 侧客户端进来）——主图 NHD 应用柱。
> 通路：helong BF2（客户端）→ 其 p1 → 交换机 → 我们 p1 → ovsbr1 → pf1hpf → fujian 的 enp94s0f1np1（redis-server）。

**5.1 【宿主机】起 redis-server（若 N1 后 10.99.99.1 还在则跳过配 IP）**：

先确认 redis 已装（fujian 默认没有）：

```bash
which redis-server || sudo apt install -y redis-server
```

> ⚠️ 装包可能触发 needrestart 重启 rshim，装完先 `ping -c 2 192.168.100.2` 确认链路再继续。
> apt 装的默认实例绑 6379，与我们显式绑 10.99.99.1:6380 不冲突，不用管它。

```bash
sudo ip addr add 10.99.99.1/24 dev enp94s0f1np1
redis-server --bind 10.99.99.1 --port 6380 --save "" \
    --appendonly no --protected-mode no --daemonize yes
redis-cli -h 10.99.99.1 -p 6380 ping        # 期望 PONG
```

> `RTNETLINK answers: File exists` 同样无害，跳过 add 即可（理由同 §2.1）。

**5.2 【helong 的 BF2】确认地址与通路**（若 N1 后还在则跳过）：

```bash
ip addr add 10.99.99.3/24 dev p1
ping -c 2 10.99.99.1
ls -l /root/redis-benchmark      # 不存在 → 先按 §0.2 补传（别开采集窗口），再冒烟：
/root/redis-benchmark -h 10.99.99.1 -p 6380 -t ping -n 3 -q
```

**5.3 三连跑（时序同 G3）**：

```bash
# 【我们的 BF2】
sudo ./run_phase.sh -c configs/e1_esw.conf -o e1_n2_run1.csv -t 40 -a "sleep 40"

# 屏幕出现 APP PHASE START 后，立即在【helong 的 BF2】上：
/root/redis-benchmark -h 10.99.99.1 -p 6380 -t set,get -n 3000000 -c 64 -d 128 -q
```

> ⚠️ 9/15 实测：开窗前必须先冒烟（`-t ping -n 3 -q`），并把 benchmark 的**完整输出**（吞吐或报错原文）抄进笔记——首轮三跑只有 37 Mbps 涓流、疑似连不上，无输出无法定位。

> run2/run3 换 `-o e1_n2_run2.csv` / `e1_n2_run3.csv`。
> 笔记：benchmark 吞吐、屏幕跳动列（预期 p1_rx、p1_tx、pf1hpf_rx、pf1hpf_tx 四列都动——数据与回程都过桥；Arm 镜像对 ≈ 0）。

**5.4 收尾**：

```bash
# 【宿主机】
redis-cli -h 10.99.99.1 -p 6380 shutdown nosave
sudo ip addr del 10.99.99.1/24 dev enp94s0f1np1
# 【helong 的 BF2】
ip addr del 10.99.99.3/24 dev p1
```

## 6. 回传清单

| 文件                                                  | 内容                                 |
| --------------------------------------------------- | ---------------------------------- |
| `/root/bf2k/e1_n0_2a.csv` `/root/bf2k/e1_n0_2b.csv` | NAD 定标线（必回传）                       |
| `/root/bf2k/e1_n1_{1g,5g,10g,20g}.csv`              | NHD 定标线（必回传）                       |
| `/root/bf2k/e1_g3_run{1,2,3}.csv` + `.phase.log`    | NAD 应用柱（必回传）                       |
| `/root/bf2k/e1_g7_run{1,2,3}.csv` + `.phase.log`    | NAD 应用柱含背景（必回传）                    |
| `/root/bf2k/e1_n2_run{1,2,3}.csv` + `.phase.log`    | NHD 应用柱（若执行）                       |
| 笔记                                                  | 各跑吞吐、跳动列、异常（20G 是否用 -P 4、N2 跳过原因等） |

【宿主机】拉回（再按平时链路传到 Windows 放 `D:\bf2-collector\results\`）：

```bash
mkdir -p /tmp/bf2k/results
scp root@192.168.100.2:/root/bf2k/e1_n0_*.csv /tmp/bf2k/results/
scp root@192.168.100.2:/root/bf2k/e1_n1_*.csv /tmp/bf2k/results/
scp root@192.168.100.2:/root/bf2k/e1_g3_run*.csv* /tmp/bf2k/results/
scp root@192.168.100.2:/root/bf2k/e1_g7_run*.csv* /tmp/bf2k/results/
scp root@192.168.100.2:/root/bf2k/e1_n2_run*.csv* /tmp/bf2k/results/
```

（文件不存在的说明该场景跳过，不用管。）

## 7. 判读标准（Claude 收到 CSV 后做的事，你只需提供数据）

- **N0 2a**：pf1hpf_rx ≈ enp3s0f1s0.rx ≈ en3f1pf1sf0.tx ≈ 6 Gbps（Arm 收包瓶颈 ~6.6 Gbps 平台，非慢启动）；p1 ≈ 0 → NAD 前向定标。
- **N0 2b**：enp3s0f1s0.tx ≈ en3f1pf1sf0.rx ≈ pf1hpf_tx ≈ 10.4 Gbps → NAD 反向定标。
- **N1**：各速率下 p1_rx ≈ pf1hpf_tx ≈ 标定速率、Arm 镜像对 ≈ 0 → NHD 定标线；吞吐对速率画线（线性？20G 受限？）。
- **G3**：pf1hpf_rx ≈ enp3s0f1s0.rx ≈ en3f1pf1sf0.tx ≈ 0.10 Gbps、p1 ≈ 0 → NAD 应用柱（Arm redis 单线程限速，历史 G 系列 0.12-0.13 Gbps 同量级）。
- **G7**：同 G3 叠加 sqlite 背景，≈ 0.09-0.10 Gbps（略低于 G3 = sqlite 争抢 Arm CPU 的真实信号；IO/IB 信号在 tile 列，不影响去向分解）。
- **N2**：p1_rx ≈ pf1hpf_tx ≈ 0.075 Gbps（helong BF2 Arm 客户端限速）；p1_tx ≈ pf1hpf_rx ≈ 0.023 = SET 阶段 ACK 回程（benchmark 3M+3M 全程 ~90s，40s 窗口只覆盖 SET 阶段）；Arm 镜像对 ≈ 0 → NHD 应用柱。
- **主图产出**：每负载窗口均值 − **仅 pre-idle 5s 基线**（benchmark 跑满 40s 窗口后仍继续 ~50s，post-idle 被污染，不可当基线）→ eSwitch→Host 分量 = pf1hpf_tx、eSwitch→Arm 分量 = en3f1pf1sf0_tx；样式沿用既定规范（双系列 #B2172B / #F5A682、log y 10^{-4}..10^{1}、零值柱 floor 2e-4 画可见小桩）。
