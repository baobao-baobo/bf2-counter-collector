# E0 实验操作单（傻瓜式）：TRIO/端口映射与 NHD 可观测性验证

> 对应方案文档 docs/path-counter-experiment-plan.md §4。执行者：用户。
> 目的：① trio0/trio1 与 pcie0/pcie1 分别对应哪个端口（主机接口 / 网口 Arm 面）；② NHD（主机直通）流量是否对 Arm 侧 TLR 可见——直接决定论文 PCIe 域段落表述。
> 角色记号：【宿主机】= 插着 BF2 的机器（fujian）；【BF2】= ssh 登录 192.168.100.2 的设备终端（提示符 root@localhost）。
> **【BF2】所有命令先 `cd /root/bf2k` 再执行**——采集引擎在 `/root/bf2k/code/collect_all`，配置在 `/root/bf2k/configs/`，bench 二进制在 `/root/bf2k/bench/bin/`，CSV 落在 `/root/bf2k/` 下。全程不需要懂原理，照着敲、照着记即可。CSV 回传后由 Claude 做列级分析。

---

## 0. 准备阶段（一次性，之后不用重复）

**0.1 【宿主机】拉取最新仓库并预检**
```bash
cd /tmp/bf2k 2>/dev/null || git clone --depth 1 https://github.com/baobao-baobo/bf2-counter-collector /tmp/bf2k
cd /tmp/bf2k && git pull
make
./code/collect_all --check-config -c configs/e0_trio_map.conf
```
看到 `fixed: triogen0=TX_DAT_AF triogen1=RX_DAT_AF`、`rx_merged=1 tx_merged=1` 即通过。

**0.2 【宿主机】确认 iperf3 存在**（没有则装；⚠️ 装包会触发服务重启，见 0.2 尾注）：
```bash
which iperf3 || sudo apt install -y iperf3
```
> ⚠️ fujian 上 apt 装包会触发 needrestart 自动重启 rshim/networkd 服务，导致 BF2 链路中断。若中断：`sudo ip link set <接口> up && sudo ip addr add 192.168.100.1/24 dev <接口>`（接口名用 `ip a` 找），必要时 `sudo systemctl restart rshim`。**装完包务必先 `ping 192.168.100.2` 确认链路活着再继续。**

**0.3 【宿主机】把配置传到 BF2**（目标路径 = 设备端仓库的 configs 目录）：
```bash
scp /tmp/bf2k/configs/e0_trio_map.conf root@192.168.100.2:/root/bf2k/configs/
```

**0.4 【BF2】确认配置与工具就位**：
```bash
cd /root/bf2k
sudo ./code/collect_all --check-config -c configs/e0_trio_map.conf   # 输出应与宿主机一致
ls bench/bin/iperf3 bench/bin/fio                                    # 确认 bench 二进制存在
```

---

## 1. E0-1：NHD（主机直通方向）——需要【宿主机】+【BF2】各一个终端

**1.1 【宿主机】侦察网卡**（2026-09-11 实况已指认，直接可用；若与实况不符以 `ip a` 为准）：
- `<业务IP>` = **eno1 = 172.28.4.77**（实验室业务网）
- `<HOST_PF>` = **enp94s0f0np0**（BF2 主机面网卡，Mellanox MAC 08:c0:eb 开头，DOWN 空闲）
- ⚠️ 不碰：enp94s0f1np1（BF2 另一口，已被占用 192.168.56.11）、tmfifo_net0（rshim 管理口 192.168.100.1）、docker0/flannel/cni0/veth（k8s 容器网）、enp175s*（另一张 Dell 网卡）

**1.2 【宿主机】给主机面网卡配临时 IP 并起服务端**：
```bash
sudo ip addr add 192.168.101.1/24 dev enp94s0f0np0
sudo ip link set enp94s0f0np0 up
iperf3 -s -p 5201 -B 192.168.101.1 -D
```

**1.3 【BF2】开采集（50 秒）**：
```bash
cd /root/bf2k
sudo ./code/collect_all -c configs/e0_trio_map.conf -d 50 -o e0_1_nhd.csv
```
屏幕开始每秒刷一行后，**立刻**切回宿主机执行 1.4（不要磨蹭）。

**1.4 【宿主机】依次尝试两条通路**（每条限时 12 秒，50 秒窗口足够）：
```bash
# 尝试 A：直连 HOST_PF（本地直连路由优先，SYN 直接从主机面进 BF2；即使连不上，SYN 也过了 PCIe，TX 侧字节计数必然动）
timeout 12 iperf3 -c 192.168.101.1 -B 172.28.4.77 -t 8

# 尝试 B：强制经业务网出去（真正的 NHD 通路：eno1→实验室交换机→BF2 网口→eSwitch→主机面→PCIe 回 fujian）
sudo ip route add 192.168.101.1/32 dev eno1
timeout 12 iperf3 -c 192.168.101.1 -B 172.28.4.77 -t 8
sudo ip route del 192.168.101.1/32 dev eno1
```
> 笔记记录：A 和 B 各是**连上（记吞吐）还是超时**。A 连上 = eSwitch 把包反射回主机面；B 连上 = BF2 网口接在实验室网络且 eSwitch 有主机直通桥（真正的 NHD 流量）。A/B 都超时 → 笔记写"无法生成完整 NHD 流量"（A 的 SYN 仍可回答 PCIe TX 可见性）。

**1.5 【BF2】等采集自然结束**，然后确认文件生成：
```bash
ls -l /root/bf2k/e0_1_nhd.csv   # 存在且大小非零 = 成功
```

**1.6 【宿主机】清理现场**：
```bash
pkill iperf3
sudo ip addr del 192.168.101.1/24 dev enp94s0f0np0
```

**笔记内容**：A/B 两条尝试的连上与否与吞吐、采集期间 BF2 屏幕上哪些列明显跳动（重点 pcie 的 rx/tx 字节列）。

---

## 2. E0-2：NAD（网口 Arm 面）——需要【宿主机】+【BF2】各一个终端

> ⚠️ 2026-09-11 实况修正：BF2 的 192.168.100.2 是 **rshim 管理口**（fujian 侧 tmfifo_net0），对它的流量不走 ConnectX 网口，**不能生成 NAD**。真正的 NAD 目标 = BF2 上 ConnectX 网口（Arm 侧）的 IP，先做 2.0 找出它。

**2.0 【BF2】侦察网口**：
```bash
ip a | grep -B2 "inet "
```
找 ConnectX 网口（名字常含 enp/oob_net/p0p1，MAC 前缀多为 00:1a:ca / 08:c0:eb / 00:02:c9 等 NVIDIA/Mellanox 段）。**把完整输出贴给 Claude 指认目标 IP**（拿不准就贴，别猜）。目标 IP 记为 `<NAD_IP>`。

**2.1 【BF2】起 iperf3 服务端（后台）**：
```bash
/root/bf2k/bench/bin/iperf3 -s -p 5202 -B <NAD_IP> -D
```

**2.2 【BF2】开采集（50 秒）**：
```bash
cd /root/bf2k
sudo ./code/collect_all -c configs/e0_trio_map.conf -d 50 -o e0_2_nad.csv
```
开始刷行后立刻切到宿主机执行 2.3。

**2.3 【宿主机】打两个方向流量**（`<NAD_IP>` 与 fujian 同网段即可直连；跨网段先 `ip route` 或问 Claude）：
```bash
iperf3 -c <NAD_IP> -p 5202 -t 10
sleep 5
iperf3 -c <NAD_IP> -p 5202 -t 10 -R
```

**2.4 【BF2】等采集结束**，确认文件并关掉服务端：
```bash
ls -l /root/bf2k/e0_2_nad.csv
pkill iperf3
```

**2.5 对照组 E0-2b（可选但建议做，验证判读标准）**：把 2.1 的服务端换成绑在 192.168.100.2 上（rshim 口），重复 2.2–2.4，输出 `e0_2b_rshim.csv`。预期：`net` 软件计数涨、trio/pcie 纹丝不动。

**笔记内容**：`<NAD_IP>` 及其接口名、iperf3 吞吐（两个方向）、BF2 屏幕上哪些列跳动（重点 trio 的 TDMA_DATA_BEAT、pcie 的 rx/tx 字节列、`net` 软件计数列）。

---

## 3. E0-3：eMMC DMA 对照——【BF2】开两个终端（窗口 A 采集、窗口 B 打盘）

**3.1 【BF2】准备测试文件**（**严禁对 /dev/mmcblk0 裸设备读写——eMMC 是系统盘**）：
```bash
df -h /root | tail -1        # 看剩余空间，够 2G 就用 2G，不够改 1G
# ⚠️ 必须写真实数据！fallocate 创建的文件读取时不落盘（E0-3 第一轮教训：mmcblk0 ios=0）
/root/bf2k/bench/bin/fio --filename=/root/fio_testfile --rw=write --direct=1 --size=2G --bs=128k --name=warmup
ls -l /root/fio_testfile     # 应显示 2147483648
```

**3.2 【BF2 窗口 A】开采集（35 秒）**：
```bash
cd /root/bf2k
sudo ./code/collect_all -c configs/e0_trio_map.conf -d 35 -o e0_3_emmc.csv
```

**3.3 【BF2 窗口 B】采集开始后 5 秒内执行 fio 直读**：
```bash
/root/bf2k/bench/bin/fio --filename=/root/fio_testfile --rw=read --direct=1 --size=2G --bs=128k --time_based --runtime=25 --name=e0_emmc
```
> 只有一个 BF2 终端时可用后台写法：`sudo ./code/collect_all -c configs/e0_trio_map.conf -d 35 -o e0_3_emmc.csv & sleep 5 && /root/bf2k/bench/bin/fio ... && wait`（sudo 若问密码建议还是开两个窗口）。

**3.4 【BF2 窗口 A】等采集结束**，确认文件并删除测试文件：
```bash
ls -l /root/bf2k/e0_3_emmc.csv
rm /root/fio_testfile
```

**判读前自查**：fio 输出末尾的 `Disk stats` 行，`mmcblk0: ios=` 必须是数千以上、`BW` 在 100–400 MB/s 量级——才说明真的读盘了；若 `ios=0` 且 BW 上 GB/s，本轮作废重跑 3.1。

**笔记内容**：fio 打印的读带宽（BW=...MiB/s）、BF2 屏幕上哪些列跳动（预期：tile 的 IO_ACCESS 明显涨；trio/pcie 纹丝不动）。

---

## 4. 回传清单

| 文件 | 内容 |
|---|---|
| `/root/bf2k/e0_1_nhd.csv` | E0-1 采集（若跳过了 1.2–1.4 就只回传笔记） |
| `/root/bf2k/e0_2_nad.csv` | E0-2 采集（NAD 目标 IP 由 2.0 侦察 + Claude 指认） |
| `/root/bf2k/e0_2b_rshim.csv` | E0-2b 对照组（rshim 口，若执行） |
| `/root/bf2k/e0_3_emmc.csv` | E0-3 采集 |
| 笔记 | 每步要求记录的内容（接口名/IP、吞吐、跳动列、任何异常现象） |

【宿主机】拉回（再按你平时的链路传到 Windows 发给 Claude）：
```bash
scp root@192.168.100.2:/root/bf2k/e0_1_nhd.csv /tmp/
scp root@192.168.100.2:/root/bf2k/e0_2_nad.csv /tmp/
scp root@192.168.100.2:/root/bf2k/e0_2b_rshim.csv /tmp/
scp root@192.168.100.2:/root/bf2k/e0_3_emmc.csv /tmp/
```

## 5. 判读标准（Claude 收到 CSV 后做的事，你只需提供数据）

- **E0-2**：trio 与 pcie 同涨的编号 = 网口 Arm 面 TRIO；若目标是 rshim 口（192.168.100.2），`net` 软件计数涨而 trio/pcie 不涨 = 管理通道对照组，与 E0-2b 预期一致。
- **E0-1**：pcie 字节列涨 = 主机向 TRIO 可见（结论 NHD-B）；pcie 不涨 = NHD 对 Arm 侧 TLR 不可见（结论 NHD-A）。两种结论分别对应论文 PCIe 域段落的不同写法，见方案文档 §4。
- **E0-3**：tile IO_ACCESS 涨且 trio/pcie 平 = eMMC DMA 走 RN-I 不经 PCIe Switch（预期）；若 trio/pcie 意外有信号 → 重新评估 eMMC 挂接位置。
