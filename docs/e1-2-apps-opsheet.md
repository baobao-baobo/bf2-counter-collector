# E1-2 操作单（傻瓜式）：应用矩阵（主图数据采集）

> 对应方案文档 docs/e1-eswitch-split-experiment-plan.md §5（E1-2 表）。执行者：用户。
> 前置：E1-1（docs/e1-1-calibration-opsheet.md）已跑完且判读通过。
> 目的：给主图（eSwitch 去向分解）采齐各负载的窗口数据——NAD 柱（G3/G7 重跑）、NHD 柱（N1 定标线 + N2 应用级）、N0 定标线（直接复用 E1-1 2a/2b，不重跑）。
> 角色记号：【宿主机】= fujian；【BF2】= ssh 192.168.100.2（root@localhost）；【实验室机器】= 实验室网里另一台装有网线的机器（N2 用）。
> 全部沿用 `configs/e1_esw.conf`（带 10 个每口字节列）；CSV 落在 `/root/bf2k/` 下。
> 节奏纪律：G3/G7/N2 用 `run_phase.sh` 相位协议（5s 空载 + 40s 应用 + 5s 空载，自动写 `.phase.log`）；N1 用固定 50s 窗口（同 E1-1）。**跑前先 `cd /root/bf2k`。**
> ⚠️ **2026-09-15 姿势更正（恢复 E1 前必读）**：§1 N1 作废——服务端 192.168.101.1 在 enp94s0f0np0 + 同机客户端 → Linux local 表本地投递，流量不进 BF2。恢复时改：服务端 IP 放 **enp94s0f1np1**（同 §2 N2 的 10.99.99.1 设计）+ 客户端实验室机器（或 fujian 双 netns+macvlan，见 docs/pipe-collection-plan.md §5.4）。N2/G3/G7 不受影响。

---

## 0. 准备（一次性；若 E1-1 之后设备没动过，跳到 §1）

**0.1** 确认 E1-1 已判读通过（方向语义表成立）。
**0.2 【BF2】** `sudo ./code/collect_all --check-config -c configs/e1_esw.conf` 通过（`interfaces: pf0hpf pf1hpf p1 en3f1pf1sf0 enp3s0f1s0`）。

---

## 1. N1：wire→主机多速率 iperf3（NHD 定标线，4 个速率 × 8 s）——【宿主机】+【BF2】

**1.1 【宿主机】起服务端（一次，供 4 个速率共用）**：

```bash
sudo ip addr add 192.168.101.1/24 dev enp94s0f0np0
sudo ip link set enp94s0f0np0 up
iperf3 -s -p 5201 -B 192.168.101.1 -D
```

**1.2 逐速率跑**（速率 = 1 / 5 / 10 / 20 Gbps，各一跑；每跑流程完全一样，换两处字面）：

```bash
# 【BF2】开采集（50 秒）
sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_n1_1g.csv

# 【宿主机】屏幕开始刷行后立刻（10 秒内）：
sudo ip route add 192.168.101.1/32 dev eno1
timeout 12 iperf3 -c 192.168.101.1 -B 172.28.4.77 -t 8 -b 1G
sudo ip route del 192.168.101.1/32 dev eno1

# 【BF2】等采集自然结束（约 50 秒），ls -l /root/bf2k/e1_n1_1g.csv 确认非零
```

> 后面三跑把 `-b 1G` 换成 `-b 5G`、`-b 10G`、`-b 20G`，`-o e1_n1_1g.csv` 换成 `e1_n1_5g.csv`、`e1_n1_10g.csv`、`e1_n1_20g.csv`。
> ⚠️ 20G 若单流上不去（吞吐远低于 19 Gbps），**加 `-P 4` 重打一次**（`timeout 12 iperf3 -c 192.168.101.1 -B 172.28.4.77 -t 8 -b 20G -P 4`），笔记记录两种写法的吞吐。
> 每跑笔记：速率、实际吞吐、BF2 屏幕哪几列在动（预期 p1_rx、pf1hpf_tx）。

**1.3 【宿主机】清理**：

```bash
pkill iperf3
sudo ip addr del 192.168.101.1/24 dev enp94s0f0np0
```

---

## 2. N2：Redis-Host（fujian 当 server，wire 侧客户端进来）三连跑

> 与 G3 是同一负载、仅 server 部署位置翻转——主图 NHD 柱的对照组。
> 通路设计（对称 10.99.99.x 辅助网段，数据与回程都只能走 BF2 桥）：
> 实验室机器(10.99.99.2) → L2 → BF2 p1 → ovsbr1 → pf1hpf → 主机 PF1(10.99.99.1, redis-server 在 fujian)。
> 若 E1-1 的 1b 被跳过而本场景打不通，先补跑 1b 定位（回程/桥问题）。

**2.1 【宿主机】装并起 redis-server（一次性）**：

```bash
sudo NEEDRESTART_MODE=l apt install -y redis-server
sudo ip addr add 10.99.99.1/24 dev enp94s0f1np1
redis-server --bind 10.99.99.1 --port 6380 --save "" \
    --appendonly no --protected-mode no --daemonize yes
redis-cli -h 10.99.99.1 -p 6380 ping        # 期望 PONG
```

> 给 enp94s0f1np1 加的只是一个未使用网段的辅助 IP，不影响 56.11 原有业务。

**2.2 【实验室机器】配辅助 IP 并验证连通**：

> 实验室机器已就位（2026-09-15）：另一台服务器（另一张 BF2 的宿主机，与 fujian 互 ping 通）。它的 `<eth接口名>` 用 `ip route get 172.28.4.77` 查；建议先跑 docs/pipe-collection-plan.md §5.4 的烟测（ping 10.99.99.1 + 重测组合）确认通路，再回来跑 N2。

```bash
sudo ip addr add 10.99.99.2/24 dev <eth接口名>     # 用 ip route get 172.28.4.77 找到连实验室网的口
ping -c 3 10.99.99.1
```

- ping 通 → 进 2.3。
- ping 不通 → ARP 过不去（罕见）：先试静态邻居：
  ```bash
  # 【宿主机】查 PF1 口 MAC：
  ip link show enp94s0f1np1 | grep ether      # 形如 08:c0:eb:xx:xx:xx
  # 【实验室机器】写死邻居：
  sudo ip neigh replace 10.99.99.1 lladdr 08:c0:eb:xx:xx:xx dev <eth接口名>
  ping -c 3 10.99.99.1
  ```
  再不通 → 笔记记录现象，跳过 N2（回传后与师兄核对桥配置）。
- 没有实验室机器可用 → 跳过 N2，笔记注明。

**2.3 三连跑（时序同 G3）**：

```bash
# 【BF2】先起采集（sleep 占位，相位由 run_phase 管理）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o e1_n2_run1.csv -t 40 -a "sleep 40"

# 屏幕出现 APP PHASE START 后（约启动 5s），立即在【实验室机器】上：
redis-benchmark -h 10.99.99.1 -p 6380 -t set,get -n 3000000 -c 64 -d 128 -q
```

> benchmark 约 30-40s，落在 40s 应用窗口内；run2/run3 同样节奏。
> 笔记：benchmark 吞吐、BF2 屏幕跳动列（预期 p1_rx、p1_tx、pf1hpf_rx、pf1hpf_tx 四个都动——数据与回程都过桥）。
> 实验室机器若装了 YCSB：用与 G3 客户端相同的 YCSB 工作负载替代 redis-benchmark（更对称），笔记注明用的哪种客户端。

**2.4 收尾（三跑全部结束后）**：

```bash
# 【宿主机】
redis-cli -h 10.99.99.1 -p 6380 shutdown nosave
sudo ip addr del 10.99.99.1/24 dev enp94s0f1np1
# 【实验室机器】
sudo ip addr del 10.99.99.2/24 dev <eth接口名>
```

---

## 3. G3 Redis-Arm 重跑（NAD 柱，e1_esw.conf 版）三连跑

**3.1 【BF2】起服务（若 E1-1 后已关）**：

```bash
pkill -x redis-server || true
apps/bin/redis-server --bind 192.168.56.103 --port 6379 --save "" \
    --appendonly no --protected-mode no --daemonize yes
apps/bin/redis-cli -h 192.168.56.103 ping        # 期望 PONG
```

**3.2 【宿主机】确认管道**：`ping -c 2 192.168.56.103`

**3.3 三连跑（与 G 系列同节奏，仅配置换成 e1_esw.conf、输出换名）**：

```bash
# 【BF2】
sudo ./run_phase.sh -c configs/e1_esw.conf -o e1_g3_run1.csv -t 40 -a "sleep 40"

# 屏幕出现 APP PHASE START 后，立即在【宿主机】上：
redis-benchmark -h 192.168.56.103 -p 6379 -t set,get -n 3000000 -c 64 -d 128 -q
```

> run2/run3 换 `-o e1_g3_run2.csv` / `e1_g3_run3.csv`。
> 笔记：benchmark 吞吐、屏幕跳动列（预期 pf1hpf 两列、Arm 镜像对两列；p1 ≈ 0）。

**3.4 收尾**：`apps/bin/redis-cli -h 192.168.56.103 shutdown nosave`

---

## 4. G7 Redis+SQLite 混合重跑（NAD 柱，含背景）三连跑

**4.1 【BF2】先起 redis**（同 §3.1 两条命令）。

**4.2 三连跑**（**纪律：每次 run_phase 之前单独 rm 并确认删净**）：

```bash
# 【BF2】删旧库（DB 在根分区 = eMMC，勿放 /tmp）：
rm -f /root/bf2k/g7.db*
ls -l /root/bf2k/g7.db*     # 必须报 No such file；否则没删干净，停！
sudo ./run_phase.sh -c configs/e1_esw.conf -o e1_g7_run1.csv -t 40 \
    -a "apps/bin/sqlite3 /root/bf2k/g7.db < apps/sqlite_workload.sql"

# 屏幕出现 APP PHASE START 后，立即在【宿主机】上：
redis-benchmark -h 192.168.56.103 -p 6379 -t set,get -n 3000000 -c 64 -d 128 -q
```

> 每跑结束看一眼 sqlite 输出：.timer 应 INSERT ~20s 起步、全程 ~32s 自然退出；若见 "table t already exists" / "UNIQUE constraint failed" 或 .timer 全是毫秒级 → 跑在旧库上，该跑作废、重 rm 重跑。
> benchmark 尾部满速流量可能落在 post-idle（同 G 系列，无需重采，出图分段处理）。
> run2/run3 换 `-o e1_g7_run2.csv` / `e1_g7_run3.csv`。

**4.3 收尾**：`apps/bin/redis-cli -h 192.168.56.103 shutdown nosave`；顺手 `rm -f /root/bf2k/g4.db*` 腾 eMMC。

---

## 5. 回传清单

| 文件 | 内容 |
|---|---|
| `/root/bf2k/e1_n1_1g.csv` `/root/bf2k/e1_n1_5g.csv` `/root/bf2k/e1_n1_10g.csv` `/root/bf2k/e1_n1_20g.csv` | NHD 定标线（必回传） |
| `/root/bf2k/e1_n2_run{1,2,3}.csv` + `.phase.log` | NHD 应用级（若执行） |
| `/root/bf2k/e1_g3_run{1,2,3}.csv` + `.phase.log` | NAD 柱（必回传） |
| `/root/bf2k/e1_g7_run{1,2,3}.csv` + `.phase.log` | NAD 柱含背景（必回传） |
| 笔记 | 各跑吞吐、跳动列、异常（N2 跳过原因、20G 是否用了 -P 4 等） |

【宿主机】拉回（再按平时链路传到 Windows 放 `D:\bf2-collector\results\`）：

```bash
mkdir -p /tmp/bf2k/results
scp root@192.168.100.2:/root/bf2k/e1_n1_*.csv /tmp/bf2k/results/
scp root@192.168.100.2:/root/bf2k/e1_n2_run*.csv* /tmp/bf2k/results/
scp root@192.168.100.2:/root/bf2k/e1_g3_run*.csv* /tmp/bf2k/results/
scp root@192.168.100.2:/root/bf2k/e1_g7_run*.csv* /tmp/bf2k/results/
```

（文件不存在的说明该场景跳过，不用管。）

---

## 6. 判读标准（Claude 收到 CSV 后做的事，你只需提供数据）

- **N1**：各速率下 p1_rx ≈ pf1hpf_tx ≈ 标定速率、Arm 镜像对 ≈ 0 → NHD 定标线（图 1）；实际吞吐对速率画线（是否线性、20G 是否受限）。
- **N2**：p1_rx ≈ pf1hpf_tx ≈ 请求方向速率 且 p1_tx ≈ pf1hpf_rx ≈ 回程方向速率（对称桥通路成立）→ 主图 NHD 柱；Arm 镜像对 ≈ 0。
- **G3**：pf1hpf_rx ≈ enp3s0f1s0.rx ≈ en3f1pf1sf0.tx ≈ NAD 速率、p1 ≈ 0 → 主图 NAD 柱。
- **G7**：同上叠加 sqlite 背景（IO/IB 信号在 tile 列，不影响 eSwitch 去向分解）。
- **N0**：直接复用 E1-1 的 e1_2a/e1_2b（NAD 10G 定标）。
- 主图（图 2）产出：每负载窗口均值（净流量）→ eSwitch→Host 分量 = NHD 方向各口净流量和、eSwitch→Arm 分量 = Arm 镜像对净流量和；样式沿用既定规范（双系列 #B2172B / #F5A682 等）。
