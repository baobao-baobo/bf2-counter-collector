# Task #40 饱和标定——一次上机执行单

版本 2026-09-18。本文是从 `docs/saturation-calibration-opsheet.md` §7
展开的**独立执行单**：只讲设备上怎么跑，判读标准与锚点回填逻辑见主
操作单 §6/§5。执行人：用户；Claude 不连设备，判读在回传后本地做。

**总时长预估 ~40 分钟，一次上机跑完。**

---

## 1. 前置准备（fujian + BF2，~5 分钟）

### 1a. fujian 终端

```bash
iperf3 -s -D          # p7 的服务端（若已起会报端口占用，跳过即可）
```

### 1b. BF2 终端——套件在位检查

```bash
ls /root/bf2k/bench/run_bench.sh /root/bf2k/bench/collect_all
ls /root/bf2k/bench/configs/bench_p{1,3,4,5,6,7}*.conf
```

缺任何一个 → fujian 上 `cd /tmp/bf2k && git pull && bash deploy.sh` 后重查。

### 1c. BF2 终端——补两个配置（④⑤步要用，bench/configs 里没有）

```bash
cp /root/bf2k/configs/e1_esw.conf /root/bf2k/bench/configs/
cp /root/bf2k/configs/default.conf /root/bf2k/bench/configs/
ls /root/bf2k/bench/configs/e1_esw.conf /root/bf2k/bench/configs/default.conf   # 确认
```

---

## 2. 规格钉死（~2 分钟）

```bash
lspci -vv | grep -B2 -A8 "LnkCap"
```

✅ **已执行（9/18，双侧 lspci）**：

- BF2 Arm 侧：五条内部链全 Gen4 x16、LnkSta 全 ok → **pcie1（Arm 子系统）
  [cap] = 16GT/s×16×128/130 ≈ 252 Gbps ≈ 31.5 GB/s**。
- fujian 宿主侧（复核修正）：VPD 坐实 BlueField-2 100GbE 双口型号；BF2 四函数
  **LnkCap 16GT/s x16 但 LnkSta `8GT/s (downgraded)` x16 → 主机面链路实际
  Gen3 x16 → pcie0 [cap] = 8GT/s×16×128/130 ≈ 126 Gbps ≈ 15.75 GB/s**
  （宿主平台/riser 是 Gen3 时代，其余槽位同证）。pcie0/pcie1 分档回填
  anchor_sat.conf + extract_anchors.py 硬编码表。
- p1=100G（M2 B4）同步回填 wire 系 [cap]：p1/pf1hpf/pipe:p1 由 25G-suspected
  → 12.5 GB/s。

**跳过 ethtool p1**——p1 Speed 已定案 100G（9/18 M2 B4 记录在案）。

---

## 3. 编辑 run_bench.sh（~3 分钟）

```bash
cd /root/bf2k/bench
vi run_bench.sh     # 或 sed 改，共两处
```

**改动 1（第 31 行）**——iperf3 服务端地址：

```sh
IPERF_SERVER=192.168.56.11
```

**改动 2（p6 的 fio 行）**——`--filename=/tmp/fio.tmp` 是 tmpfs（内存
路径，不经过 eMMC），改到 eMMC 挂载点。先查挂载点：

```bash
df -h | grep -E "mmc|/$"     # BF2 根分区通常在 eMMC 上
```

把 `--filename=` 改到该挂载点下，例如 `--filename=/root/fio.tmp`。

**改动 3（预创建 fio 文件，~1 分钟）**——避免 4G 文件创建吃进 60s
测量窗：

```bash
dd if=/dev/zero of=/root/fio.tmp bs=1M count=4096
ls -l /root/fio.tmp     # 应 4294967296 字节
```

---

## 4. 四标定面 ×3 次（~21 分钟，核心步骤）

每个面跑 3 次（run 1..3），**每轮 70 秒勿打断**（采集器 -d 70，bench
在第 5 秒启动跑 60 秒，尾留 5 秒；判读时裁首尾各 5 行）。

| 面      | bench              | 打什么           | 采集配置                  | 期望平台值（判读用）                                        |
| ------ | ------------------ | ------------- | --------------------- | ------------------------------------------------- |
| 一 内存   | p3 STREAM          | 顺序读写带宽        | bench_p3_stream.conf  | MEMORY_READS+WRITES×64B 对照 stream.txt 带宽          |
| 二 核    | p1 stress-ng cpu   | 纯计算（L1 内）     | bench_p1_cpu.conf     | A72_ACCESS 远低于 p5（b1 仅 2.3M/s，语义如此非故障）            |
| 二 L2   | p5 stress-ng cache | cache 抖动      | bench_p5_cache.conf   | A72_ACCESS ≈195.7M/s（±20%）；ALLOCATE/DIR_HIT/L3 系列 |
| 二 随机   | p4 memrand 1GB     | 随机访存          | bench_p4_memrand.conf | L3 miss 率 >90%；ALLOCATE/VICTIM 平台值                |
| 三 网    | p7 iperf3          | BF2→fujian 打流 | bench_p7_net.conf     | net_tx 平台值（net_rx 另由第 5 步复验）                      |
| 四 eMMC | p6 fio             | 顺序读 I/O       | bench_p6_fio.conf     | IO_ACCESS ≈704k req/s（E0-3 平台）                    |

```bash
cd /root/bf2k/bench

# 面一 内存 ×3
sudo ./run_bench.sh p3 1
sudo ./run_bench.sh p3 2
sudo ./run_bench.sh p3 3

# 面二 核/L2/随机 ×3
sudo ./run_bench.sh p1 1
sudo ./run_bench.sh p1 2
sudo ./run_bench.sh p1 3
sudo ./run_bench.sh p5 1
sudo ./run_bench.sh p5 2
sudo ./run_bench.sh p5 3
sudo ./run_bench.sh p4 1
sudo ./run_bench.sh p4 2
sudo ./run_bench.sh p4 3

# 面三 网 ×3（fujian 的 iperf3 -s 已在 1a 起好）
sudo ./run_bench.sh p7 1
sudo ./run_bench.sh p7 2
sudo ./run_bench.sh p7 3

# 面四 eMMC ×3
sudo ./run_bench.sh p6 1
sudo ./run_bench.sh p6 2
sudo ./run_bench.sh p6 3
```

每轮结束自查：`ls -l results/ | tail -3` 应有 `pN_runM.csv` + 对应
`.txt` 两个新文件。

---

## 5. 面三反向 rx 复验（~3 分钟）

net_rx 平台（Arm 收包 ~6.05Gbps，上限 6.6Gbps）与 pf1hpf 列复验。
**时序关键：先起采集器，看到开始刷行后再回 fujian 打流。**

```bash
# BF2 终端（先确保 Arm 侧服务端在跑）：
sudo /root/bf2k/bench/bin/iperf3 -s -p 5202 -D
cd /root/bf2k/bench
sudo ./collect_all -c configs/e1_esw.conf -d 70 -o results/e1_sat_rx.csv
```

```bash
# fujian 终端（BF2 刷行开始后立即）：
iperf3 -c 192.168.56.103 -p 5202 -t 60 -b 10G
```

等 BF2 70s 自然结束。判读：net_rx 平台 ≈6.05Gbps；pf1hpf/p1/en3f1pf1sf0
列同窗采集（wire 列与 ethtool 100G 规格对照，打不满属正常——Arm 收包
是瓶颈）。

✅ **9/22 复验通过（sat_results_0918_1319.tar.gz 重传版，81 列）。**
根因修复确认：`make && cp code/collect_all bench/collect_all` 后每口
十列齐全（旧版二进制早于 8ff40c4 特性）。判读（负载窗原始值均值，
CSV 存的是逐秒增量勿再差分）：pf1hpf_rx **803MB/s=6.43Gbps**=干净
入流量（10G 流×平台瓶颈，与历史 6.05–6.6Gbps 一致）；en3f1pf1sf0_tx
805MB/s + enp3s0f1s0_rx 790MB/s = Arm 侧镜像对双计数（2× 伪影在每口
级分解）；p1 收/发=0（wire 静默 ✓）；pcie1_rx 843MB/s=6.75Gbps（旧批
同窗同为 846MB/s，两批负载一致——此前"787M/s"与"1.23Gbps 弱载"均为
本地二次差分误算）；net_rx 1594≈pf1hpf+enp3 之和、net_tx 807≈sf0_tx
（过滤求和内部自洽 ✓）。

---

## 6. tilenet 补采（~4 分钟）

tilenet 三列（CDN_REQ/DDN_REQ/NDN_REQ）从未进过 CSV，用 default.conf
补两个窗口闭合缺口：

```bash
cd /root/bf2k/bench

# 窗 1：70s 空闲（什么都不跑，纯背景）
sudo ./collect_all -c configs/default.conf -d 70 -o results/tilenet_idle.csv

# 窗 2：70s 窗口 + STREAM 压 60s（时序同第 5 步）
# 60 循环 = 满窗占空比（p3 教训 2026-09-20：15 循环只有 ~25% 占空）
sudo ./collect_all -c configs/default.conf -d 70 -o results/tilenet_stream.csv &
sleep 5
i=0; while [ $i -lt 60 ]; do bin/stream >> results/tilenet_stream.txt; i=$((i+1)); done
```

✅ **9/22 12:25 重跑通过——§6 闭合（tilenet_stream3 批）。**
窗口 12:25:09–12:26:08（-d 60，59 行，ts 4717 一秒采样缺口无碍）：
前 41 行 a72_access 177.0–195.6M/s（均值 **180.4M/s**，与 p3 三轮
均值 179.3M/s 差 0.6%）、cpu 94.4–99.0% = STREAM 满窗占空；第 42 行
（12:25:51）起回落到 snap 基态（0.5–1.2M/s、cpu 50.2–51.0）——循环
尾段落窗内 ~41.5s 后自然结束。**tilenet cdn/ddn/ndn 59/59 全零
（含 41 行满负载行）→ 内存负载不触网络 tile，闭合证据达成。**
附带产出：snap 未停（用户未 kill），满载行 a72 仍精确复现 p3
179.3M/s → **p3 锚点本就含此背景、无需修正**（lstart 检查作罢）。
txt 3960 行 = 120 个 run（60 循环 × 2 次执行，>> 追加无害）。防呆
块存档如下（已实证有效）：

```bash
cd /root/bf2k/bench
sudo -v                                    # 只提示一次密码，之后整段不再询问
pkill -9 stress-ng 2>/dev/null; pkill -9 stream 2>/dev/null; sleep 1
( i=0; while [ $i -lt 60 ]; do bin/stream >> results/tilenet_stream3.txt; i=$((i+1)); done ) &
echo "LOOP_STARTED"                        # 看到这行 = loop 已在后台开跑
sleep 10
sudo ./collect_all -c configs/default.conf -d 60 -o results/tilenet_stream3.csv
ls -l results/tilenet_stream3.*
```

60 循环 ≈ 132s+（受 50% 背景拖慢只会更长），窗口 [10,70]s 必然落在
循环内、两边各留 ≥60s 余量。整段一次粘贴；看到 `LOOP_STARTED` 后
**等 70s 让 collect 自然结束**（中途不要敲任何命令）；再等 txt 停止
增长（paste 后约 3 分钟）才执行第 7 节回传。

**50% 背景进程定位（9/22 ps 回传已定案）**：`mlnx_snap_emu -m 0xf0
-u --mem-size 1200`（PID 4471，PPID 1）**399% CPU = 核 4–7 满转**，
开机常驻的 NVIDIA SNAP 仿真守护进程（非 stress-ng——pkill 杀不掉、
9/20 23:57 即存在由此解释；ovs-vswitchd 仅 0.5%，kubelet/containerd/
collectx 各 ≤1%）。签名自洽：4 核紧循环忙轮询 → l1d 3.3G/s、a72_access
~1M/s（纯计算型）。**对 §6 判读无影响**：STREAM 179M/s ≫ 1M/s 基线，
且 snap 钉核 4–7、STREAM 跑核 0–3 互不抢——不杀也可直接重跑。若要
更干净窗口：`systemctl list-units | grep -i snap` 找单元名后
`sudo systemctl stop <单元名>`（实验不用 SNAP 仿真盘，停掉安全；
`ps -o pid,lstart,args -p 4471` 若早于 9/20 22:46 则 p3 锚点本就含
此背景、无需修正）。

判读目标：窗内 a72_access ≈150–180M/s（p3 经验）且 tilenet 三列仍 0
→ 内存负载不触网络 tile = 闭合证据。

---

## 7. 回传（~3 分钟）

BF2 → fujian 中转 → 本地（scp 照 M2 回传链路）。

```bash
# BF2 上收拢（首选，与 9/18 批同流程）：
cd /root/bf2k/bench && sudo ./run_sat_all.sh collect sat_results_0918_1319
# 预期输出 "gathered 40 files"（36 旧 + e1_sat_rx + tilenet×2 + tilenet_stream.txt）。
# 9/21 实际 39 且缺 txt —— 39=36+3 本身即 stream 循环未执行的旁证
# 再打包回传（或照 M2 scp 链路逐个传）：
tar czf sat_results_0918_1319.tar.gz sat_results_0918_1319/
# fujian 上拉取，再转到本地（解包位置随意，告知 Claude 即可）
```

**回传清单（36+ 文件）**：

| 内容      | 文件                                                                      |
| ------- | ----------------------------------------------------------------------- |
| 面一      | p3_run{1,2,3}.csv + p3_run{1,2,3}_stream.txt                            |
| 面二      | p1/p5/p4 各 run{1,2,3}.csv + 对应 _stressng.txt/_stressng.txt/_memrand.txt |
| 面三      | p7_run{1,2,3}.csv + p7_run{1,2,3}_iperf.txt                             |
| 面四      | p6_run{1,2,3}.csv + p6_run{1,2,3}_fio.txt                               |
| rx 复验   | e1_sat_rx.csv                                                           |
| tilenet | tilenet_idle.csv + tilenet_stream.csv + tilenet_stream.txt              |
| 规格      | lspci LnkCap 输出（第 2 步贴文本）                                               |

---

## 8. 回传后（Claude 本地判读，你知情即可）

1. `extract_anchors.py sat` —— p1–p7 平台段中位值自动纳入锚点表
   （provenance=bench-pN），替换对应 obs-max 初值
2. [cap] 表按 lspci 结果手工更新（GT/s×车道×128/130）
3. 重跑 replay_validate 回放验证，确认锚点替换后判读结论不变坏
4. 探针候选事后裁定：MSS_NO_CREDIT/POC/A72_WRITE/RNF/TRIO 在回传 CSV
   上检查是否动；不在任何 bench 配置里的（SMMU_TBU_MISS/triogen）维持
   [unverified]，走探针操作单单独裁定
5. 出判读报告（±20% 对照；偏差 >30% 先查采集配置、不硬改锚点）

---

## 9. 补跑批次：p4/p5 扩展重跑 + D2 七应用（~20 分钟，9/22 新增）

背景：9/20 锚点回填后 SAT-SUSPECT 仍有残余 obs-max 锚点，p4/p5 扩展
配置（本批次前新提交）可给真应力值替换；D2 七应用重跑带 e1_esw 每口
列（历史主图无每口列），是 PRISM 实例表"新场景协议"的设备输入。命令
全部自包含，一次上机跑完。

### 9a. 前置（本地线待批准 + fujian，~5 分钟）

扩展配置在本地，**必须先提交推送**（deploy.sh 只传 git 跟踪文件）：

```bash
# Claude 本地（待批准后执行）：
git add bench/configs/bench_p4_memrand.conf bench/configs/bench_p5_cache.conf \
    bench/run_bench.sh tools/prism_search.py docs/
git commit -m "extend p4/p5 bench configs; add prism_search layer; sync run_bench device fixes"
git push
```

```bash
# fujian 上（拉新配置并部署）：
cd /tmp/bf2k && git pull && bash deploy.sh
```

run_bench.sh 本地版已含设备侧全部修复（IPERF_SERVER=56.11、p3 60 循环、
fio /root/fio.tmp），部署覆盖设备旧文件无损。**设备自检新配置已到**：

```bash
grep -c TOTAL_WR_DATA_IN /root/bf2k/bench/configs/bench_p4_memrand.conf   # 期望 ≥1
grep -c A72_READ /root/bf2k/bench/configs/bench_p5_cache.conf             # 期望 ≥1
```

### 9b. Part A：p4/p5 扩展重跑 ×3（~8 分钟）

```bash
cd /root/bf2k/bench
sudo ./run_bench.sh p4 1
sudo ./run_bench.sh p4 2
sudo ./run_bench.sh p4 3
sudo ./run_bench.sh p5 1
sudo ./run_bench.sh p5 2
sudo ./run_bench.sh p5 3
ls -l results/p4_run*.csv results/p5_run*.csv   # 6 对 CSV+txt，时间戳为本批
```

覆盖设备上 9/18 旧 p4/p5 CSV 属预期（旧版已回传本地并入锚点，本地
bench/results/ 存档在案）。判读目标：新增 l3cache 组（p4 写系 4 计数、
p5 读系 4 计数）满载窗平台值 → 替换 SAT-SUSPECT 残余 obs-max 锚点。

### 9c. Part B：D2 七应用重跑（~10 分钟，e1_esw 每口列）

输出统一命名 `results/d2_gN_run1.csv`（**勿用 gN_runM**——设备 results/
下会覆盖历史主图 CSV；本地回放按原名匹配，d2_ 前缀不干扰）。

一次性前置检查（2 分钟）：

```bash
cd /root/bf2k
ls -lh /tmp/g1.dat /tmp/bs_in.txt                  # 缺则：
# dd if=/dev/urandom of=/tmp/g1.dat bs=1M count=1024 status=progress
# apps/bin/inputgen 5000000 /tmp/bs_in.txt
PYTHONPATH=/root/bf2k/apps/pylib python3 -c \
    "import tflite_runtime.interpreter; print('g6 ok')"
pkill -x redis-server || true
apps/bin/redis-server --bind 192.168.56.103 --port 6379 --save "" \
    --appendonly no --protected-mode no --daemonize yes
apps/bin/redis-cli -h 192.168.56.103 ping         # 期望 PONG
```

七连跑（每跑 ~50s，跑完看 `[run_phase] done` 的 app 时长）：

```bash
cd /root/bf2k

# g1 xz（跑前 pgrep -a xz 确认无残留压缩）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g1_run1.csv -t 40 \
    -a "apps/bin/xz -c -9 -T 8 /tmp/g1.dat > /dev/null"

# g2 bfs
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g2_run1.csv -t 40 \
    -a "apps/bin/bfs -g 20 -n 8"

# g3 redis（看到 APP PHASE START 后，fujian 上立即执行下行打流）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g3_run1.csv -t 40 -a "sleep 40"
# fujian: redis-benchmark -h 192.168.56.103 -p 6379 -t set,get -n 3000000 -c 64 -d 128 -q

# g4 sqlite（每跑前删库保证冷库）
rm -f /root/bf2k/g4.db*
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g4_run1.csv -t 40 \
    -a "apps/bin/sqlite3 /root/bf2k/g4.db < apps/sqlite_workload.sql"

# g5 blackscholes
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g5_run1.csv -t 40 \
    -a "for i in 1 2 3 4 5 6 7 8; do apps/bin/blackscholes 8 /tmp/bs_in.txt /dev/null; done"

# g6 tflite（N 用校准值，见 apps-ops-manual.md §12.2）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g6_run1.csv -t 40 \
    -a "PYTHONPATH=/root/bf2k/apps/pylib python3 /root/bf2k/apps/tflite_bench.py \
        /root/bf2k/apps/mobilenet_v1_1.0_224_quant.tflite 8 N"

# g7 sqlite+redis（先删库并确认删净；APP PHASE START 后 fujian 打流同 g3）
rm -f /root/bf2k/g7.db*
ls -l /root/bf2k/g7.db*    # 必须 No such file；否则就是没删干净，停！
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g7_run1.csv -t 40 \
    -a "apps/bin/sqlite3 /root/bf2k/g7.db < apps/sqlite_workload.sql"
```

g3/g7 双端时序同 apps-ops-manual.md §4.3/§13：屏幕出现 **APP PHASE
START**（约启动 5s）后立即在 fujian 打流。g7 每跑结束看 sqlite .timer：
INSERT ~20s 起步、全程 ~32s 自然退出；秒级报错 = 旧库残留，该跑作废。
如需中位数稳定性可补 run2/run3（同命令换名，+17 分钟，可选）。

### 9d. 回传（~3 分钟）

```bash
# BF2 上收拢：
cd /root/bf2k && tar czf d2_batch.tar.gz bench/results/p4_run*.csv \
    bench/results/p5_run*.csv bench/results/p4_run*_memrand.txt \
    bench/results/p5_run*_stressng.txt results/d2_*.csv results/d2_*.csv.phase.log
# 转回本地（照 M2 链路），解包位置告知 Claude
```

清单：p4/p5 各 3 对 CSV+txt（12 文件）+ d2_g1..g7 各 1 CSV + 1 相位
日志（14 文件）。

### 9e. 回传后（Claude 本地判读）

1. `extract_anchors.py sat` 再生成（p4/p5 真应力入锚点，provenance=
   bench-p4/p5，替换残余 obs-max）；
2. 三关重验：replay_validate 17/17 + prism_search --selfcheck 23/23；
3. D2 七应用逐个跑 `prism_search.py`（每场景 1 CSV = 1 窗口）→ 判决入
   PRISM 实例表（新场景协议）；与主图历史判决对照（g1/g2/g5/g6→cr、
   g3/g7→nad、g4→ib/ih 同量级）；
4. 出三段式判读报告。

---

## 常见坑速查

| 坑                     | 规避                         |
| --------------------- | -------------------------- |
| fio 落在 tmpfs（不压 eMMC） | 第 3 步已改 --filename + 预创建   |
| 4G 文件创建吃进测量窗          | 第 3 步 dd 预创建一次             |
| p7 服务端没起              | 1a 先 iperf3 -s -D          |
| 70s 窗口被打断             | 每轮等 run_bench.sh 自己结束再跑下一轮 |
| 打流与采集不同窗              | 第 5/6 步时序：先起采集器→刷行→再打流     |
| 结果目录混乱                | 每轮结束 `ls -l results/       |


### 9f. 修复重跑块（2026-09-22 首轮回传判读失败后的补救，~25 分钟）

首轮 26 文件已判读（docs/validation-replay.md §8）：**p4×3、p5×3 全部
因 `bin/memrand`、`bin/stress-ng` 缺执行位（Permission denied）作废**；
**d2_g6 因命令中 N 占位符未替换、应用 1 秒退出作废**；**d2_g3/d2_g7 的
fujian 侧 redis-benchmark 未打出流量（net_rx 全零）作废**。g1/g2/g4/g5
四场景有效（判读见 §8）。以下块照顺序执行后按 9d 重新打包回传（同名
覆盖即可）。

```bash
# (1) 恢复 bench 二进制执行位（9/22 deploy 抹掉了 x 位；apps/bin 不受影响）
chmod +x /root/bf2k/bench/bin/*
ls -l /root/bf2k/bench/bin/          # 应显示 -rwxr-xr-x 开头

# (2) 清理可能残留的孤儿进程（上次 ^C 中断的 xz 可能仍在跑）
pgrep -a xz; pgrep -a bfs; pgrep -a python3; pgrep -a memrand
# 有输出则 pkill -9 -x <名字>，然后重查应为空

# (3) p4/p5 重跑 ×3（每次 ~80 秒，共 ~8 分钟）
cd /root/bf2k/bench
sudo ./run_bench.sh p4 1
sudo ./run_bench.sh p4 2
sudo ./run_bench.sh p4 3
sudo ./run_bench.sh p5 1
sudo ./run_bench.sh p5 2
sudo ./run_bench.sh p5 3
# 冒烟判读：txt 不再是 Permission denied——
head -5 results/p4_run1_memrand.txt     # 应显示 memrand 统计
tail -3 results/p5_run1_stressng.txt    # 应显示 stress-ng 完成统计

# (4) g6 先校准再跑（N 为校准迭代数，占位符不能用）：
cd /root/bf2k
PYTHONPATH=/root/bf2k/apps/pylib python3 apps/tflite_bench.py     apps/mobilenet_v1_1.0_224_quant.tflite 8 100
# 看输出 per_iter=X ms → N = 35000 / X（目标 app 时长 30-35s），代入下条：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g6_run1.csv -t 40     -a "PYTHONPATH=/root/bf2k/apps/pylib python3 /root/bf2k/apps/tflite_bench.py         /root/bf2k/apps/mobilenet_v1_1.0_224_quant.tflite 8 N"
# 验收：相位日志 app_end-app_start 应为 ~30-35s（首轮是 1s）

# (5) g3 重跑 + fujian 侧基准（本机见 APP PHASE START 后立即执行）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g3_run1.csv -t 40 -a "sleep 40"
# fujian: redis-benchmark -h 192.168.56.103 -p 6379 -t set,get -n 3000000 -c 64 -d 128 -q
# 验收：benchmark 输出有真实 req/s 数字（首轮失败=没跑或连不上）

# (6) g7 重跑 + fujian 侧基准（同 g3；sqlite 窗口 ~31s，基准在窗口内即可）
rm -f /root/bf2k/g7.db*
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/d2_g7_run1.csv -t 40     -a "apps/bin/sqlite3 /root/bf2k/g7.db < apps/sqlite_workload.sql"

# (7) 重新打包（同名覆盖）
cd /root/bf2k && tar czf d2_batch.tar.gz bench/results/p4_run*.csv     bench/results/p5_run*.csv bench/results/p4_run*_memrand.txt     bench/results/p5_run*_stressng.txt results/d2_*.csv results/d2_*.csv.phase.log
```

回传后本地判读：p4/p5 入锚点再生成（provenance=bench-p4/p5 换掉
SAT-SUSPECT 自引用 om）→ 三关重验（replay 17/17 + selfcheck 23/23，
selfcheck B 的 p4/p5 面当前因本地旧 CSV 被覆盖而阻塞，回传即恢复）→
d2_g3/g6/g7 入 PRISM 实例表。


## §9 收尾（2026-09-22 晚，§9f 修复重跑完成）

§9f 全部执行完毕并验证通过：执行位已修（chmod +x）、孤儿清理（无残留进程，系统
python3 两条为 blueman/dts_watcher 服务非噪声）、p4/p5 ×3 重跑、g6 标定
（100 轮 13.674s → 单轮 136.74ms → N=256 → 36s 满窗）、g3/g7 双端 Redis 重跑、
重打包 d2_batch.tar.gz 回传。本地判读：26 文件全部有效，七场景 med 复现历史，
三关全绿（详见 docs/validation-replay.md §8.4）。§9 目标达成，本执行单闭环。
遗留可选项：p4/p5 扩展配置（D4 行）、D3 0x5d/0x72 语义专项。
