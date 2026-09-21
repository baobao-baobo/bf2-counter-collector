# E1-1 操作单（傻瓜式）：eSwitch 方向定标

> 对应方案文档 docs/e1-eswitch-split-experiment-plan.md §5。执行者：用户。
> 目的：用可控速率的 iperf3 流量，逐场景验证第 2 节的方向语义表（谁动谁不动），并顺带裁定 p1 端口统计的口径疑点。
> 角色记号：【宿主机】= 插着 BF2 的机器（fujian）；【BF2】= ssh 登录 192.168.100.2 的设备终端（提示符 root@localhost）。
> 【BF2】所有命令先 `cd /root/bf2k` 再执行；CSV 落在 `/root/bf2k/` 下。每场景采集 50 秒，流量只占其中 8 秒——**开始采集后尽快打流量（10 秒内），早打晚打都不影响判读**（各端口列自己会把流量段标出来）。
> 全程不需要懂原理，照着敲、照着记即可。CSV 回传后由 Claude 做列级判读。
> ⚠️ **2026-09-15 姿势更正（恢复 E1 前必读）**：场景 1a/1c 的服务端在 fujian 本机 → Linux local 表本地投递，流量根本不进 BF2（pipe 探针实测 p1 物理口只涨 54KB/期望 5.8GB；E0-1 的 34.5Gbps 同假象）。恢复时按 docs/pipe-collection-plan.md §5.4 改姿势：**服务端 IP 放 enp94s0f1np1（桥内 PF1 面）+ 客户端实验室机器（或 fujian 双 netns+macvlan）**。1b/2a/2b 不受影响。

---

## 0. 准备阶段（一次性）

**0.1 【Windows/用户】把最新代码推到 GitHub**（Claude 已提交 8ff40c4，含采集器扩展与 e1_esw.conf；推送由用户执行）：

```bash
cd /d/bf2-collector && git push
```

**0.2 【宿主机】拉取并部署到 BF2**：

```bash
cd /tmp/bf2k 2>/dev/null || git clone --depth 1 https://github.com/baobao-baobo/bf2-counter-collector /tmp/bf2k
cd /tmp/bf2k && git pull
bash deploy.sh -b          # 打包 git 跟踪文件 + 设备端 make
```

看到部署输出无报错即可。

**0.3 【BF2】确认配置与新增列就位**：

```bash
cd /root/bf2k
sudo ./code/collect_all --check-config -c configs/e1_esw.conf
# 期望：最后有 [net] enabled=true 且
#   interfaces: pf0hpf pf1hpf p1 en3f1pf1sf0 enp3s0f1s0
```

**0.4 【BF2】表头自检（5 秒快采，验证 10 个新列真的出现）**：

```bash
cd /root/bf2k
sudo ./code/collect_all -c configs/e1_esw.conf -d 5 -o e1_header_check.csv
head -1 e1_header_check.csv | tr ',' '\n' | tail -12
rm e1_header_check.csv
```

> 期望最后 12 行依次是：
> `net_rx_bytes, net_tx_bytes, pf0hpf_rx_bytes, pf0hpf_tx_bytes, pf1hpf_rx_bytes, pf1hpf_tx_bytes, p1_rx_bytes, p1_tx_bytes, en3f1pf1sf0_rx_bytes, en3f1pf1sf0_tx_bytes, enp3s0f1s0_rx_bytes, enp3s0f1s0_tx_bytes`。
> 若不是（例如还是只有 net_rx/net_tx 两列）→ 说明设备端二进制是旧的，回 0.2 重跑 `bash deploy.sh -b` 并检查 `make` 输出。

**0.5 【宿主机】确认 iperf3 存在**：`which iperf3 || sudo apt install -y iperf3`
（⚠️ 装包会触发 needrestart 重启 rshim，装完先 `ping 192.168.100.2` 确认链路再继续。）

---

## 1. p1 统计口径对照（顺带做，1a 前后各记一次）

> 背景：E1-0 发现 p1_rx 累计值小于 E0-1 B 注入量，怀疑 uplink 的 sysfs 统计口径与 vport 不同。用 1a 的流量做对照。

**1.1 【BF2】1a 打流量之前记录**（把输出抄进笔记）：

```bash
ethtool -S p1 | grep -iE "rx_bytes|tx_bytes"
cat /sys/class/net/p1/statistics/rx_bytes /sys/class/net/p1/statistics/tx_bytes
```

**1.2 跑完 1a 之后再看一次同样的两条命令，把两组数字记进笔记**（对比：sysfs 涨量更接近 ethtool 的哪个计数器，就是哪个口径）。

---

## 2. 场景 1a：wire→主机直通（NHD 定标，10 Gbps）——【宿主机】+【BF2】各一个终端

**2.1 【宿主机】给主机面网卡配临时 IP 并起服务端**：

```bash
sudo ip addr add 192.168.101.1/24 dev enp94s0f0np0
sudo ip link set enp94s0f0np0 up
iperf3 -s -p 5201 -B 192.168.101.1 -D
```

**2.2 【BF2】开采集（50 秒）**：

```bash
cd /root/bf2k
sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_1a.csv
```

屏幕开始每秒刷一行后，**立刻**切回宿主机执行 2.3。

**2.3 【宿主机】强制经业务网打流量**（E0-1 尝试 B 通路；限时 12 秒内完成 8 秒流量）：

```bash
sudo ip route add 192.168.101.1/32 dev eno1
timeout 12 iperf3 -c 192.168.101.1 -B 172.28.4.77 -t 8 -b 10G
sudo ip route del 192.168.101.1/32 dev eno1
```

> 笔记记录：连上没有、吞吐多少（期望 ≈9.4 Gbps）。

**2.4 【BF2】等采集自然结束**（约 50 秒），确认文件生成：

```bash
ls -l /root/bf2k/e1_1a.csv   # 存在且大小非零 = 成功
```

> 采集期间顺手观察屏幕：预期 **p1_rx_bytes、pf1hpf_tx_bytes** 两列明显跳动（每秒约 1.2e9），其余端口列接近 0。把"哪几列在动"记进笔记。

---

## 3. 场景 1c（可选）：PF0 面 hairpin 定标——复用 2.1 的服务端

**3.1 【BF2】开采集**：

```bash
cd /root/bf2k
sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_1c.csv
```

**3.2 【宿主机】立刻打流量**（**不要**加 /32 路由——走本地直连，E0-1 尝试 A 通路）：

```bash
timeout 12 iperf3 -c 192.168.101.1 -B 172.28.4.77 -t 8 -b 10G
```

> 预期：**pf0hpf_rx_bytes ≈ pf0hpf_tx_bytes** ≈ 1.2e9/秒，其余端口列 ≈ 0。

**3.3 【BF2】等采集结束**，`ls -l /root/bf2k/e1_1c.csv`。

**3.4 【宿主机】清理 1a/1c 现场**：

```bash
pkill iperf3
sudo ip addr del 192.168.101.1/24 dev enp94s0f0np0
```

---

## 4. 场景 1b（可选）：主机→wire 反向（UDP，10 Gbps）——需要实验室网里另一台机器

> 回程路由问题用 UDP 绕开（UDP 单方向不需要回程通路）。若手头没有实验室网机器，跳过本场景并在笔记注明。
> 实验室机器已就位（2026-09-15）：另一台服务器（另一张 BF2 的宿主机，与 fujian 互 ping 通）；其实验室网网卡用 `ip route get 172.28.4.77` 查。

**4.1 【实验室机器】起服务端**：`iperf3 -s -p 5203`

**4.2 【BF2】开采集**：

```bash
cd /root/bf2k
sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_1b.csv
```

**4.3 【宿主机】立刻打流量**（走 56.11 主机面进 BF2 的桥）：

```bash
sudo ip route add <实验室机器IP>/32 dev enp94s0f1np1
timeout 12 iperf3 -c <实验室机器IP> -p 5203 -u -b 10G -t 8
sudo ip route del <实验室机器IP>/32 dev enp94s0f1np1
```

> ⚠️ enp94s0f1np1（56.11）是别人在用的口，只打 8 秒。UDP 丢包不碍事——本场景只验证方向。
> 预期：**pf1hpf_rx_bytes ≈ p1_tx_bytes** ≈ 1.2e9/秒，Arm 镜像对 ≈ 0。

**4.4 【BF2】等采集结束**，`ls -l /root/bf2k/e1_1b.csv`。

---

## 5. 场景 2a/2b：56.x 管道双向（NAD 定标，10 Gbps）——【宿主机】+【BF2】各一个终端

**5.1 【BF2】起服务端（56.103，一次起好供 2a/2b 共用）**：

```bash
cd /root/bf2k
pkill iperf3 || true
/root/bf2k/bench/bin/iperf3 -s -p 5202 -B 192.168.56.103 -D
```

**5.2 【宿主机】确认 56.x 管道通**：`ping -c 2 192.168.56.103`

**5.3 场景 2a（host→Arm）**：

```bash
# 【BF2】开采集
cd /root/bf2k
sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_2a.csv

# 【宿主机】立刻打流量（fujian 已有 56.0/24 直连路由 via enp94s0f1np1）
timeout 12 iperf3 -c 192.168.56.103 -p 5202 -t 8 -b 10G
```

> 预期：**pf1hpf_rx_bytes ≈ enp3s0f1s0_rx_bytes ≈ en3f1pf1sf0_tx_bytes** ≈ 1.2e9/秒；p1 列 ≈ 0。
> （前向数据 + 反向 ACK 都在 56.x 管道里，ACK 占比小，不影响判读。）

**5.4 【BF2】等 2a 采集结束**（`ls -l /root/bf2k/e1_2a.csv`），然后开 2b：

**5.5 场景 2b（Arm→host，-R 反转）**：

```bash
# 【BF2】开采集
cd /root/bf2k
sudo ./code/collect_all -c configs/e1_esw.conf -d 50 -o e1_2b.csv

# 【宿主机】立刻打反向流量
timeout 12 iperf3 -c 192.168.56.103 -p 5202 -t 8 -b 10G -R
```

> 预期：**enp3s0f1s0_tx_bytes ≈ en3f1pf1sf0_rx_bytes ≈ pf1hpf_tx_bytes** ≈ 1.2e9/秒；p1 列 ≈ 0。

**5.6 【BF2】等 2b 采集结束**，清理：

```bash
ls -l /root/bf2k/e1_2b.csv
pkill iperf3
```

---

## 6. 全程记录表（照抄填空，回传时一起给 Claude）

| 场景                | 连上？吞吐 | BF2 屏幕哪几列在动（写列名） | 异常/备注             |
| ----------------- | ----- | ---------------- | ----------------- |
| 0.4 表头自检          | —     | —                | 新列是否出现            |
| 1.1 p1 ethtool 前值 | —     | —                | rx/tx 各计数器数值      |
| 1a                |       |                  | 打完后抄一遍 1.2 的 p1 值 |
| 1c（可选）            |       |                  |                   |
| 1b（可选）            |       |                  |                   |
| 2a                |       |                  |                   |
| 2b                |       |                  |                   |

> 屏幕每行有 60+ 列，不用全盯：重点看 `pf0hpf_*、pf1hpf_*、p1_*、en3f1pf1sf0_*、enp3s0f1s0_*` 这 10 列。

---

## 7. 回传清单

| 文件                     | 内容                  |
| ---------------------- | ------------------- |
| `/root/bf2k/e1_1a.csv` | NHD 定标（必回传）         |
| `/root/bf2k/e1_1c.csv` | PF0 hairpin 定标（若执行） |
| `/root/bf2k/e1_1b.csv` | NHD 反向定标（若执行）       |
| `/root/bf2k/e1_2a.csv` | NAD 前向定标（必回传）       |
| `/root/bf2k/e1_2b.csv` | NAD 反向定标（必回传）       |
| 第 6 节记录表               | 吞吐、跳动列、p1 前后计数      |

【宿主机】拉回（再按你平时的链路传到 Windows 发给 Claude）：

```bash
scp root@192.168.100.2:/root/bf2k/e1_1a.csv /tmp/
scp root@192.168.100.2:/root/bf2k/e1_1c.csv /tmp/
scp root@192.168.100.2:/root/bf2k/e1_1b.csv /tmp/
scp root@192.168.100.2:/root/bf2k/e1_2a.csv /tmp/
scp root@192.168.100.2:/root/bf2k/e1_2b.csv /tmp/
```

（文件不存在的说明该场景跳过，不用管。）

---

## 8. 判读标准（Claude 收到 CSV 后做的事，你只需提供数据）

- **1a**：p1_rx ≈ pf1hpf_tx ≈ 9.4 Gbps，Arm 镜像对与 pf0hpf ≈ 0 → NHD 判据成立；同时用 1.1/1.2 的前后计数裁定 p1 的 sysfs 统计口径。
- **1c**：pf0hpf rx≈tx ≈ 速率，其余 ≈ 0 → PF0 面 hairpin 确认。
- **1b**：pf1hpf_rx ≈ p1_tx ≈ 速率 → NHD 反向确认。
- **2a**：pf1hpf_rx ≈ enp3s0f1s0.rx ≈ en3f1pf1sf0.tx ≈ 速率，p1 ≈ 0 → NAD 前向确认。
- **2b**：enp3s0f1s0.tx ≈ en3f1pf1sf0.rx ≈ pf1hpf_tx ≈ 速率，p1 ≈ 0 → NAD 反向确认。
- **镜像自洽（每场景顺带核验）**：en3f1pf1sf0.rx ≈ enp3s0f1s0.tx、en3f1pf1sf0.tx ≈ enp3s0f1s0.rx（偏差 <5%）。
- 全部通过 → 方向语义表成立，进入 E1-2（应用矩阵）；有场景不通过 → 回传后 Claude 重审端口身份，必要时与师兄核对拓扑。
