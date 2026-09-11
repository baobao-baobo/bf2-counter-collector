# E0 实验操作单：TRIO/端口映射与 NHD 可观测性验证

> 对应方案文档 docs/path-counter-experiment-plan.md §4。执行者：用户（设备端 + 主机端）。采集配置：`configs/e0_trio_map.conf`（本地已过 `--check-config`）。
> 目的：① 确定 trio0/trio1 与 pcie0/pcie1 分别对应哪个端口（主机接口 / 网口 Arm 面）；② 确定 NHD（主机直通）流量是否对 Arm 侧 TLR 可见——此项直接决定论文 PCIe 域段落的最终表述。

## 0. 一次性部署（首次执行前）

1. 本仓库已含 `configs/e0_trio_map.conf`（随本次提交推送）；
2. fujian：`cd /tmp/bf2k && git pull`（或重新 clone），然后预检一次（x86 上无需 root 即可验证配置）：
   ```bash
   make && ./code/collect_all --check-config -c configs/e0_trio_map.conf
   ```
3. scp 到 BF2：`scp configs/e0_trio_map.conf root@192.168.100.2:/root/`（collect_all 二进制若设备上还没有本次最新版，同样 scp）；
4. 设备端确认 collect_all 可执行且配置通过：
   ```bash
   sudo ./collect_all --check-config -c e0_trio_map.conf
   ```

## 1. E0-1：NHD 流量（主机直通方向）

**生成 NHD 流量**：流量必须从网口进、经主机 PF 走 PCIe 到主机内存。若 fujian 上能看到 BF2 的主机面网卡（`ip a` 里除业务网卡外多出的一块，如 enp*），单机即可自环生成：

1. fujian 上给主机面网卡配一个临时 IP（若没有）：`sudo ip addr add 192.168.101.1/24 dev <host_pf_iface> && sudo ip link set <host_pf_iface> up`；
2. fujian 起服务端：`iperf3 -s -p 5201 -B 192.168.101.1 -D`（-B 绑定主机面网卡 IP，保证流量走该网卡出、经 BF2 网口自环回主机）；
3. **BF2 上开采集（50 s）**：
   ```bash
   sudo ./collect_all -c e0_trio_map.conf -d 50 -o e0_1_nhd.csv
   ```
   采集启动后约 5 s，在 fujian 上执行：
   ```bash
   iperf3 -c 192.168.101.1 -B <fujian_业务网卡IP> -t 10        # TX：fujian→网口→PCIe→主机
   sleep 5
   iperf3 -c 192.168.101.1 -B <fujian_业务网卡IP> -t 10 -R     # RX：主机→PCIe→网口→fujian
   ```
   （若无第二个 LAN 对端可作自环，改用局域网内另一台机器作 iperf3 对端，主机面网卡为必经之路即可。）

**要回答的问题**：pcie0 / pcie1 的 rx/tx 字节列是否增长？哪个涨、哪个方向涨？
- **都不涨** → 结论 NHD-A：Arm 侧 TLR 对主机直通流量不可见（论文按此口径定稿；NHD 实验改用主机侧观测）；
- **某个涨** → 结论 NHD-B：记录 pcieN 与方向，该 TRIO 即主机向 TRIO，NHD 获得 Arm 侧观测点。

## 2. E0-2：NAD 流量（网口 Arm 面）

**生成 NAD 流量**：fujian 与 BF2 本机网口（Arm 面 PF，即 ssh 用的 192.168.100.2 所在网卡）之间跑 iperf3：

1. **BF2 上开采集（50 s）**：
   ```bash
   sudo ./collect_all -c e0_trio_map.conf -d 50 -o e0_2_nad.csv
   ```
   采集启动后约 5 s，在 fujian 上执行（BF2 作服务端则先 `iperf3 -s -D`，或 BF2 作客户端均可；两个方向都要测）：
   ```bash
   iperf3 -c 192.168.100.2 -t 10        # fujian→BF2：网口 RX→DMA 写 Arm DRAM（NAD 主方向）
   sleep 5
   iperf3 -c 192.168.100.2 -t 10 -R     # BF2→fujian：Arm DRAM→DMA 读→网口 TX
   ```

**要回答的问题**：
- trio0 / trio1 的 TDMA_DATA_BEAT 哪个涨？→ NAD 的 TRIO 归属；
- pcie0 / pcie1 哪个同步涨？→ 与 E0-1 对照，得到"主机向 TRIO vs 网口 Arm 面 TRIO"的完整映射；
- triogen0/1 的 TX_DAT_AF / RX_DAT_AF 与 smmu0 的对应事件是否有信号（网格数据通道 FIFO 是否被触到）。

## 3. E0-3：eMMC DMA 对照（顺带确认板载存储是否经 PCIe）

1. 准备测试文件（**严禁对 /dev/mmcblk0 裸设备读写**——eMMC 是系统盘）：
   ```bash
   fallocate -l 2G /root/fio_testfile   # 大小看 df -h / 余量，1–2 GB 即可
   ```
2. **BF2 上开采集（35 s）**：
   ```bash
   sudo ./collect_all -c e0_trio_map.conf -d 35 -o e0_3_emmc.csv
   ```
   采集启动后约 5 s：
   ```bash
   bench/bin/fio --filename=/root/fio_testfile --rw=read --direct=1 --size=2G --bs=128k --time_based --runtime=25 --name=e0_emmc
   ```

**要回答的问题**：
- tile 的 IO_ACCESS 是否上升（预期：是——eMMC 控制器 DMA 走网格 RN-I）；
- trio/pcie 计数器是否响应（预期：不响应——eMMC 不在 PCIe Switch 下）；
- 若 trio/pcie 意外有信号 → 记录之，重新评估 eMMC 挂接位置。

## 4. 结果记录与回传

每个实验回传两样东西：

| 项目 | 内容 |
|---|---|
| CSV | `e0_1_nhd.csv` / `e0_2_nad.csv` / `e0_3_emmc.csv` |
| 笔记 | ① 网口/IP/接口名实况（fujian 主机面网卡名、BF2 网卡名）；② iperf3 实测吞吐（Gbps）；③ 肉眼观察：哪些计数器列在流量期间明显跳动 |

回传方式（既有链路反向）：`scp root@192.168.100.2:/root/e0_*.csv /tmp/` → fujian → Windows，或直接从 fujian 拉回。CSV 到手后我来做列级分析并给出 TRIO/端口映射结论。

## 5. 判读标准（预期结论预览）

- E0-2 中 trio 与 pcie 同涨的那个编号 = 网口 Arm 面 TRIO；
- E0-1 若 pcie 涨 = 主机向 TRIO（且涨的是另一个编号还是同一个，直接揭示"2 个 TRIO 如何分摊两个端口面"）；
- 三种可能结局与论文表述的对应关系，见方案文档 §4。
