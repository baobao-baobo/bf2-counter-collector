# 精确饱和标定操作单（P1.5 → Part 6）

版本 2026-09-17。用途：把 `configs/anchor_sat.conf` 里标记为 `obs-max
provisional` 的初值，替换为真饱和参考值；同时确认几个 `suspected`
的链路规格（p1 是否 25G、pcie0/1 的实际宽度）。**执行时间在第六部分
（设备实测验证），由用户自行在设备上执行**；执行前先做第五部分的探针
实验（MSS_NO_CREDIT 等），两者可以合并进一次上机。

## 0. 背景：现在缺什么

锚点表现状（2026-09-17 生成）：

| 类别                  | 状态                                                                              |
| ------------------- | ------------------------------------------------------------------------------- |
| bench-b* 真应力        | b4 已有：A72_ACCESS / HNF_REQUESTS / MEM_READS / MEM_WRITES                        |
| e0-dma 真应力          | E0-2/E0-3 已有：io_access 系                                                        |
| obs-max provisional | 其余全部（含 L3 全系列、pcie0/1、net 等）——只是"见过的最大值"下界                                      |
| suspected           | p1/pf1hpf/enp3s0f1s0 按 25G 名义值、Arm 收包 6.6 Gbps（已实测平台）                           |
| 失效待裁定               | A72_WRITE/RNF_REQUESTS/POC_READS/SMMU_TBU_MISS/TRIO×4/TRIOGEN×2 全系列从未动过（见探针操作单） |

本次标定的目标：**每类资源至少一个真饱和参考**；其余 obs-max 值能换
则换。

## 1. 前置准备

```bash
# bench 套件已在设备（/root/bf2k/bench/），含静态二进制与 run_bench.sh
cd /root/bf2k/bench
ls bin/            # stress-ng stream mbw memrand fio iperf3
# 采集器二进制放在 bench/ 下（run_bench.sh 会调用）
# p7（iperf3）先编辑 run_bench.sh 里的 IPERF_SERVER 为对端地址
```

每个方案跑 **3 次**（run 1..3），回传 `results/<id>_run<N>.csv` 与
`*_<bench>.txt`；本地后处理取中位。

## 2. 四个标定面

### 面一：内存面（MSS/DDR）—— 打满内存带宽

```bash
sudo ./run_bench.sh p3 1   # STREAM：顺序读写带宽
# 可选补充：bin/mbw 1000    # memcpy/dumb 带宽
```

- 采集配置 `bench_p3_stream.conf`：MEMORY_READS/WRITES、POC_*、
  MSS_NO_CREDIT、L3 EMEM_REQ/MISSES/EVICTIONS。
- **判读**：平台段（裁首尾各 5 行）MEMORY_READS+WRITES 之和 × 64 B
  与 stream.txt 报告的带宽（GB/s）对照；若 MEMORY_READS 平台值显著
  高于现值 131.2M/s，替换 anchor_sat。
- **同时观察 MSS_NO_CREDIT**：内存带宽打满若仍为 0，探针裁定更有
  把握（见 §4）。

### 面二：核/L2 面 —— 打满核与 L2 请求

```bash
sudo ./run_bench.sh p1 1   # stress-ng --cpu 8（纯计算，L1 内循环）
sudo ./run_bench.sh p5 1   # stress-ng --cache 8（cache 抖动，压 L2/L3）
sudo ./run_bench.sh p4 1   # memrand 1GB（随机访存，压 DIR/ALLOCATE/VICTIM）
```

- **p5 判读**：A72_ACCESS 平台值应与 b4 的 195.7M/s 同量级（±20%）；
  ALLOCATE/DIR_HIT/L3 HITS/MISSES 的平台值替换各自 obs-max。
- **p1 判读**：A72_ACCESS 应远低于 p5（b1 仅 2.3M/s——纯计算不产生
  tile 侧请求，这是计数器语义，不是故障）；重点看 HNF_REQUESTS。
- **p4 判读**：1GB 工作集 >> L3 → L3 MISS 率应 >90%（验证 miss 公式
  族在 memrand 下 n→1）；ALLOCATE/VICTIM 平台值替换 obs-max。
- **顺带观察**：REQ_BUF_EMPTY 在 p5 下是否显著下降（empty 公式族的
  首次真负载验证）。

### 面三：网口面 —— 确认链路规格 + 软件收包平台

先钉死两个规格：

```bash
ethtool p1 | grep -E "Speed|Duplex"     # p1 是否 25G（suspected）
lspci -vv | grep -B2 -A8 "LnkCap"       # pcie0/pcie1 的 gen×width
```

然后打流：

```bash
# 对端（56.x 主机或 helong BF2）先起服务：iperf3 -s
sudo ./run_bench.sh p7 1   # iperf3（编辑 IPERF_SERVER 后）
# 若要同时采 wire/eswitch 列（p1/pf1hpf/en3f1pf1sf0），
# 改用 e1_esw.conf 的采集器跑 iperf3 60s
```

- **判读**：net_rx 平台值应≈6.05 Gbps（历史平台，上限 6.6 Gbps）；
  net_tx 平台值替换 obs-max；pcie0/1 平台值替换 obs-max；wire 列平台
  值与 ethtool 链路规格对照（若 25G 且流量受 Arm 收包限制，wire 打
  不满属正常，此时 wire 的 C 用 ethtool 规格值即可）。
- pcie0/pcie1 的 C 值 = GT/s × 车道数 × 128/130（gen3: 7.88 Gbps/
  车道，gen4: 15.75 Gbps/车道）。

### 面四：eMMC 面 —— 确认 I/O 路径饱和

```bash
# fio 默认写 /tmp 是 tmpfs（内存路径，不是 eMMC）！
# 改 run_bench.sh 里 --filename 指向 eMMC 挂载点后：
sudo ./run_bench.sh p6 1   # fio 顺序读
```

- **判读**：IO_ACCESS 平台值应与 E0-3 的 ~704k req/s（43 MiB/s ÷
  64 B）同量级；IO_READS/IO_WRITE/TSO_WRITE 平台值替换 obs-max。
- 若设备有 NVMe，另跑一次 --filename=/dev/nvme0n1 测 NVMe 路径的
  IO_ACCESS 上限（论文可作对比）。

## 3. tilenet 补采（闭合缺口）

tilenet 3 列（CDN_REQ/DDN_REQ/NDN_REQ）在所有现有 CSV 中都不存在
（从未用 default.conf 采集过）。任选一次上机，用 default.conf 采集
配置跑一个 70s 空闲窗口 + 一个 60s 的 STREAM：

```bash
# 用 default.conf（含 tilenet/trio/smmu/L1 列）采集，跑法同 G 系列
```

回传后：tilenet 三列将获得 idle 与 obs-max 初值，缺口闭合（若值为 0，
与 trio/smmu 同样进 [unverified] 待探针）。

## 4. 与失效计数器探针的衔接

`docs/counter-failure-probe-opsheet.md` 负责裁定 A72_WRITE、
RNF_REQUESTS、POC_READS、SMMU_TBU_MISS、TRIO×4、TRIOGEN×2、
MSS_NO_CREDIT 是否会动。本操作单 §2 的四个面已顺带覆盖其中大部分
（p3 压 MSS_NO_CREDIT、p5 压 A72_WRITE/RNF、p6 压 TRIO/SMMU）——
**探针实验与标定同时跑**即可，不必单独上机。

## 5. 数据回传与锚点回填

1. 设备 `bench/results/` 全部 CSV + `*_<bench>.txt` 拷回本地
   `D:\bf2-collector\bench\results\`；
2. 本地跑 `python tools/extract_anchors.py sat` —— p1–p7 的 bench
   中位值自动纳入 [span]（provenance = bench-pN），并自动替换对应的
   obs-max 初值；
3. [cap] 表（ethtool/lspci 规格、net_rx 平台值）由我据回传结果手工
   更新；
4. 重跑第四部分的历史回放验证脚本，确认锚点替换后判读结论不变或
   变好（若变坏，回查该锚点）。

## 6. 判读标准总表

| 标定对象                          | 方法                 | 期望平台值                  | 替换谁                 |
| ----------------------------- | ------------------ | ---------------------- | ------------------- |
| tile_a72_access               | p5 cache           | ≈195.7M/s（±20%）        | 已是 bench-b4         |
| tile_hnf_requests             | p5 / p3            | ≥194M/s                | 已是 bench-b4         |
| tile_mem_reads/writes         | p3 STREAM          | 与 stream.txt 带宽÷64B 对照 | 已是 bench-b4         |
| tile_allocate/dir_hit/victim  | p4 memrand         | 显著高于 7 应用时值            | obs-max             |
| l3 hits/misses/alloc/evict    | p4/p5              | 平台值                    | obs-max             |
| l3 emem/cdn/ddn/rd/wr 管线      | p3                 | 平台值                    | obs-max             |
| tile_io_access                | p6 eMMC            | ≈704k/s（eMMC）/更高（NVMe） | obs-max             |
| tile_io_reads/write/tso_write | p6                 | 平台值                    | obs-max             |
| net_rx/tx                     | p7 iperf3          | rx≈6.05Gbps 平台         | 已是 measured/obs-max |
| pcie0/1                       | p7 + lspci 规格      | 与 LnkCap×128/130 对照    | obs-max             |
| wire/eswitch 列                | e1_esw 采集 + iperf3 | 与 ethtool 规格对照         | suspected 25G 确认    |
| tilenet 三列                    | default.conf 采集    | 首次观测                   | 缺口闭合                |
| MSS_NO_CREDIT 等失效列            | 各面对应压力             | 是否动                    | [unverified] 裁定     |

判读原则：**平台段均值三次取中位**；与期望值偏差 >30% 先查采集配置
再判"计数器语义与预期不符"，不硬改锚点。

## 7. 执行纪要（2026-09-18 定稿，一次上机清单）

执行顺序与预估（总 ~40 分钟）：

1. **规格钉死**（2 min）：`lspci -vv | grep -B2 -A8 "LnkCap"`（pcie0/1
   gen×width）。**p1 的 Speed 已定案 100G（9/18 M2 B4，ethtool 记录在
   案），跳过 ethtool**。
2. **编辑 run_bench.sh**（3 min）：
   - `IPERF_SERVER=192.168.56.11`（fujian 侧先 `iperf3 -s -D` 起服务）
   - p6 的 `--filename`：`df -h` 找 eMMC 挂载点（BF2 根分区通常在
     eMMC），指向 `/root/fio.tmp`；**预创建一次文件**（
     `dd if=/dev/zero of=/root/fio.tmp bs=1M count=4096`，约 1 分钟），
     避免 4G 文件创建吃进 60s 测量窗。
3. **四个面 ×3 次**（21 min，每面之间可穿插休息）：
   `p3, p1, p5, p4, p7, p6` 各 `sudo ./run_bench.sh <id> 1|2|3`
4. **面三反向 rx run**（~2 min，net_rx 平台复验 + pf1hpf 列）：
   BF2 `sudo ./collect_all -c configs/e1_esw.conf -d 70 -o results/e1_sat_rx.csv &`
   等 5s → fujian `iperf3 -c 192.168.56.103 -p 5202 -t 60 -b 10G`
   （BF2 已起 iperf3 -s -p 5202 -D；期望 rx≈6.05Gbps 平台）
5. **tilenet 补采**（3 min，default.conf 两窗）：
   idle 70s：`sudo ./collect_all -c configs/default.conf -d 70 -o results/tilenet_idle.csv`
   再起 STREAM 窗：同命令 `-o results/tilenet_stream.csv`，窗口内第 5 秒
   `bin/stream` 循环跑 60s。
6. **回传**：`bench/results/` 全部 csv+txt → 本地 `D:\bf2-collector\bench\results\`。

探针观察不做额外设备动作——Claude 在回传 CSV 上事后检查
MSS_NO_CREDIT/POC/A72_WRITE/RNF/TRIO/SMMU 等候选是否动；**不在任何
bench 配置里的候选（如 SMMU_TBU_MISS、triogen）维持 [unverified]，
走探针操作单单独裁定**（不阻塞标定判读）。
