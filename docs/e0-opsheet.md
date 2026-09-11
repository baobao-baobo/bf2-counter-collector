# E0 实验操作单（傻瓜式）：TRIO/端口映射与 NHD 可观测性验证

> 对应方案文档 docs/path-counter-experiment-plan.md §4。执行者：用户。
> 目的：① trio0/trio1 与 pcie0/pcie1 分别对应哪个端口（主机接口 / 网口 Arm 面）；② NHD（主机直通）流量是否对 Arm 侧 TLR 可见——直接决定论文 PCIe 域段落表述。
> 角色记号：【宿主机】= 插着 BF2 的机器（默认 fujian；若 BF2 插在跳板机，宿主机就是跳板机）；【BF2】= ssh 登录 192.168.100.2 的设备终端。
> 全程不需要懂原理，照着敲、照着记即可。CSV 回传后由 Claude 做列级分析。

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

**0.2 【宿主机】确认 iperf3 存在**（没有则装）：
```bash
which iperf3 || sudo apt install -y iperf3
```

**0.3 【宿主机】把配置传到 BF2**：
```bash
scp /tmp/bf2k/configs/e0_trio_map.conf root@192.168.100.2:/root/
```

**0.4 【BF2】确认配置与工具就位**：
```bash
sudo ./collect_all --check-config -c e0_trio_map.conf   # 输出应与宿主机一致
ls /root/bench/bin/iperf3 /root/bench/bin/fio           # 确认 bench 套件路径（下面命令按此路径写）
```
（若 bench 目录不在 /root/bench，用 `find / -name iperf3 2>/dev/null` 找到实际路径，后续命令替换。）

---

## 1. E0-1：NHD（主机直通方向）——需要【宿主机】+【BF2】各一个终端

**1.1 【宿主机】侦察两张网卡**：
```bash
ip a
```
找两个东西并**记进笔记**：
- `<业务IP>`：正在使用的、有 IP 的网卡地址（宿主机连交换机的网卡）；
- `<HOST_PF>`：多出来的那张网卡，通常没有 IP、状态 DOWN（名字常含 enp*/eno*）。
> 找不到 `<HOST_PF>` → 说明宿主机上看不到 BF2 的主机面，**跳过 1.2–1.4**，在笔记里写"宿主机无 BF2 主机面网卡"，E0-1 到此为止（这本身就是一个重要结论）。

**1.2 【宿主机】给主机面网卡配临时 IP 并起服务端**（`<HOST_PF>` 换成 1.1 记下的名字）：
```bash
sudo ip addr add 192.168.101.1/24 dev <HOST_PF>
sudo ip link set <HOST_PF> up
iperf3 -s -p 5201 -B 192.168.101.1 -D
```

**1.3 【BF2】开采集（50 秒）**：
```bash
sudo ./collect_all -c e0_trio_map.conf -d 50 -o e0_1_nhd.csv
```
屏幕开始每秒刷一行后，**立刻**切回宿主机执行 1.4（不要磨蹭）。

**1.4 【宿主机】打两个方向的流量**（`<业务IP>` 换成 1.1 记下的地址）：
```bash
iperf3 -c 192.168.101.1 -B <业务IP> -t 10
sleep 5
iperf3 -c 192.168.101.1 -B <业务IP> -t 10 -R
```
> 若第一条就连接失败/卡住（说明 BF2 上未配置主机直通桥），等 50 秒采完，笔记写"E0-1 无法生成 NHD 流量：eSwitch 无主机直通桥"，E0-1 到此为止。

**1.5 【BF2】等采集自然结束**，然后确认文件生成：
```bash
ls -l /root/e0_1_nhd.csv   # 存在且大小非零 = 成功
```

**1.6 【宿主机】清理现场**：
```bash
pkill iperf3
sudo ip addr del 192.168.101.1/24 dev <HOST_PF>
```

**笔记内容**：`<HOST_PF>` 名字、`<业务IP>` 地址、iperf3 打印的吞吐（Gbits/sec，TX 和 -R 各一个）、采集期间 BF2 屏幕上哪些列明显跳动。

---

## 2. E0-2：NAD（网口 Arm 面）——需要【宿主机】+【BF2】各一个终端

**2.0 【BF2】记录本机所有带 IP 的接口**（笔记用，用于后续判读）：
```bash
ip a | grep -B2 "inet "
```

**2.1 【BF2】起 iperf3 服务端（后台）**：
```bash
/root/bench/bin/iperf3 -s -p 5202 -D
```

**2.2 【BF2】开采集（50 秒）**：
```bash
sudo ./collect_all -c e0_trio_map.conf -d 50 -o e0_2_nad.csv
```
开始刷行后立刻切到宿主机执行 2.3。

**2.3 【宿主机】打两个方向流量**：
```bash
iperf3 -c 192.168.100.2 -p 5202 -t 10
sleep 5
iperf3 -c 192.168.100.2 -p 5202 -t 10 -R
```

**2.4 【BF2】等采集结束**，确认文件并关掉服务端：
```bash
ls -l /root/e0_2_nad.csv
pkill iperf3
```

**笔记内容**：iperf3 吞吐（两个方向）、BF2 屏幕上哪些列跳动（重点看 trio 的 TDMA_DATA_BEAT、pcie 的 rx/tx 字节列、`net` 软件计数列）。

---

## 3. E0-3：eMMC DMA 对照——【BF2】开两个终端（窗口 A 采集、窗口 B 打盘）

**3.1 【BF2】准备测试文件**（**严禁对 /dev/mmcblk0 裸设备读写——eMMC 是系统盘**）：
```bash
df -h /root | tail -1        # 看剩余空间，够 2G 就用 2G，不够改 1G
fallocate -l 2G /root/fio_testfile
ls -l /root/fio_testfile     # 应显示 2147483648
```

**3.2 【BF2 窗口 A】开采集（35 秒）**：
```bash
sudo ./collect_all -c e0_trio_map.conf -d 35 -o e0_3_emmc.csv
```

**3.3 【BF2 窗口 B】采集开始后 5 秒内执行 fio 直读**：
```bash
/root/bench/bin/fio --filename=/root/fio_testfile --rw=read --direct=1 --size=2G --bs=128k --time_based --runtime=25 --name=e0_emmc
```
> 只有一个 BF2 终端时可用后台写法：`sudo ./collect_all -c e0_trio_map.conf -d 35 -o e0_3_emmc.csv & sleep 5 && /root/bench/bin/fio ... && wait`（sudo 若问密码建议还是开两个窗口）。

**3.4 【BF2 窗口 A】等采集结束**，确认文件并删除测试文件：
```bash
ls -l /root/e0_3_emmc.csv
rm /root/fio_testfile
```

**笔记内容**：fio 打印的读带宽（BW=...MiB/s）、BF2 屏幕上哪些列跳动（预期：tile 的 IO_ACCESS 明显涨；trio/pcie 纹丝不动）。

---

## 4. 回传清单

| 文件 | 内容 |
|---|---|
| `/root/e0_1_nhd.csv` | E0-1 采集（若跳过了 1.2–1.4 就只回传笔记） |
| `/root/e0_2_nad.csv` | E0-2 采集 |
| `/root/e0_3_emmc.csv` | E0-3 采集 |
| 笔记 | 每步要求记录的内容（接口名/IP、吞吐、跳动列、任何异常现象） |

【宿主机】拉回（再按你平时的链路传到 Windows 发给 Claude）：
```bash
scp root@192.168.100.2:/root/e0_1_nhd.csv /tmp/
scp root@192.168.100.2:/root/e0_2_nad.csv /tmp/
scp root@192.168.100.2:/root/e0_3_emmc.csv /tmp/
```

## 5. 判读标准（Claude 收到 CSV 后做的事，你只需提供数据）

- **E0-2**：trio 与 pcie 同涨的编号 = 网口 Arm 面 TRIO；`net` 软件计数涨而 trio/pcie 不涨 → 流量走了管理通道（192.168.100.2 是 rshim 口），NAD 结论作废，需要换 ConnectX 口 IP 重测。
- **E0-1**：pcie 字节列涨 = 主机向 TRIO 可见（结论 NHD-B）；pcie 不涨 = NHD 对 Arm 侧 TLR 不可见（结论 NHD-A）。两种结论分别对应论文 PCIe 域段落的不同写法，见方案文档 §4。
- **E0-3**：tile IO_ACCESS 涨且 trio/pcie 平 = eMMC DMA 走 RN-I 不经 PCIe Switch（预期）；若 trio/pcie 意外有信号 → 重新评估 eMMC 挂接位置。
