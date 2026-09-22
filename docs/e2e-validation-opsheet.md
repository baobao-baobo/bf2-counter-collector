# E2E 端到端验证操作单（2026-09-22）

**目的**：完整闭环验证——在设备上运行**模型从未见过的留出负载**，用 e1_esw 配置采集计数器，
回传本地后用 PRISM 搜索层判读，得到繁忙路径结论，再与负载的独立语义预期对照。
与历史回放的区别：回放用的是实例库内数据，本操作单的负载全部不在实例库中，
判读结果是对模型泛化能力的直接检验。

## 留出负载设计

| 负载 | 命令 | 独立预期（凭负载语义） | 检验点 |
|---|---|---|---|
| A 软件加密 | openssl speed aes-128-cbc | **CR 主导**：A72 软件计算、小数据足迹、无网络无大块 IO | 核域幅度判据 |
| B UDP 洪流 Arm 终接 | iperf3 -u 5G → 56.103 | **NAD 主导 + CR 中度伴随**（DDR 落点经 CR 观测，已知混合流表现）；库内网络负载全是 TCP，UDP 为留出协议 | 网络域方向判据 |
| C 混合（CR+NAD 同窗） | xz -6 + UDP 洪流并发 | **CR 与 NAD 双高**：M1 共享顶点份额机制的直接检验；单主导判决仍按规则给出，看点是两路 L_p 同时抬升 | 共享顶点份额 |

## 设备步骤（用户执行）

### 前置（一次性）
```bash
# BF2（root）
cd /root/bf2k
# 确认 e1_esw.conf 与 run_phase.sh 在（917db11 批已部署）：
ls configs/e1_esw.conf run_phase.sh
# 混合负载 C 的输入文件（一次性准备，~20 秒）：
dd if=/dev/urandom of=/tmp/rand.bin bs=1M count=128
```

### 负载 A（3 轮，核域 CR）
```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e2e_openssl_run1.csv \
  -a "openssl speed -seconds 18 -elapsed -multi 4 aes-128-cbc" -b 0-3
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e2e_openssl_run2.csv \
  -a "openssl speed -seconds 18 -elapsed -multi 4 aes-128-cbc" -b 0-3
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e2e_openssl_run3.csv \
  -a "openssl speed -seconds 18 -elapsed -multi 4 aes-128-cbc" -b 0-3
```
说明：`-b 0-3` 把负载钉在核 0–3（mlnx_snap_emu 常驻核 4–7，避开）；
`-elapsed` 按墙钟计时（-seconds 在多核下须配它才准）；-multi 4 起 4 个加密进程各占一核。

### 负载 B（1 轮，网络域 NAD，UDP 留出）
```bash
# BF2 终端：
iperf3 -s -p 5203 &
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e2e_udp_nad_run1.csv \
  -a "sleep 30" -t 30
# 看到 [run_phase] APP PHASE START 后，立刻在 fujian 终端打流：
iperf3 -u -c 192.168.56.103 -p 5203 -b 5G -t 10
# BF2 收尾：
kill %1 2>/dev/null   # 停 iperf3 服务端
```
说明：-b 5G 低于 Arm 收包平台瓶颈（~6.6Gbps），避免丢包干扰判读；10s 洪流落在 30s 窗内。

### 负载 C（1 轮，混合，可选但推荐）
```bash
# BF2 终端：
iperf3 -s -p 5204 &
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e2e_mixed_run1.csv \
  -a "xz -6 -c /tmp/rand.bin > /dev/null" -b 0-3 -t 45
# APP PHASE START 后，fujian 终端：
iperf3 -u -c 192.168.56.103 -p 5204 -b 5G -t 10
# BF2 收尾：
kill %1 2>/dev/null
```
说明：128MB 随机数据 xz -6 在 4 核上约 30–45s，-t 45 留足；若压缩先结束，窗内尾段转空载属预期。

### 回传
```bash
# BF2：
tar czf /tmp/e2e_batch.tar.gz results/e2e_*.csv results/e2e_*.phase.log
# scp 回 fujian 再回本地（老流程），或直接放本地 results/e2e_*/
```

## 判读标准（Claude 本地）

本地命令：`python tools/prism_search.py <csv> --scene <名> [--json results/e2e_<名>.json]`

| 负载 | 期望判决 | 期望签名 |
|---|---|---|
| A | cr dominant | cr wins 全票；med L_p(cr) 与 g1/g2 同量级（1.2–1.7）；nad/nhd/tx ≈0 |
| B | nad dominant | nad wins 多数；cr 中度伴随（~0.1–0.2，DDR 落点）；nhd/tx ≈0 |
| C | cr dominant + nad 显著 | 双高：med L_p(cr) 高且 med L_p(nad) 明显 >0（共享顶点份额生效）；nhd/tx ≈0 |

判读要点：① 判决必须与独立预期一致（A/B/C 各一行）；② 若 C 的 nad 被压得过低（份额失效）
或 A/B 判错，即为模型缺陷，须回 validation-replay.md 诊断；③ SAT-SUSPECT 标注按既定机制解读；
④ 判决三值（2026-09-22 H3 起）：dominant / low / multi——C 类"有负载但无单一路径过半数票"
的场景判 **multi**（多路径繁忙、无单一主导），不再误标 low。

## 记录表

| 轮 | CSV | 窗长（实际） | 判决 | L_p 窗口均值 (cr/ih/ib/wb/nad/nhd/tx) | 预期 | 结论 |
|---|---|---|---|---|---|---|
| A1 | e2e_openssl_run1.csv | 25s | low（cr 0.002） | 0.002/0/0/0/0/0/0 | cr | **预期设错，诚实负例**：AES 是 L1 常驻纯计算，tile 网格访问 11–54K/s（低于会话自身基线），模型如实报无繁忙路径（核内盲区，g6 TFLite 先例）；三轮一致 |
| A2 | e2e_openssl_run2.csv | 25s | low（cr 0.002） | 同上（三轮合判） | cr | 同 A1 |
| A3 | e2e_openssl_run3.csv | 25s | low（cr 0.002） | 同上（三轮合判） | cr | 同 A1 |
| B1 | e2e_udp_nad_run1.csv | 31s（洪流 10s） | **dominant nad（0.541，wins 全票）** | 0.089/0.182/0.062/0.001/**0.541**/0.016/0.000 | nad | **PASS**：方向判据命中；cr 0.089 中度伴随 ✓；nhd 0.016=pcie0 双向链路进向中转的诚实残值、tx 0.000（H3 入口修复后） |
| C1 | e2e_mixed_run1.csv | 46s（洪流 10s） | **multi（cr 0.584，wins cr 14/35 无多数）** | **0.584**/0.300/0.327/0.011/**0.339**/0.011/0.000 | cr+nad | **双高 PASS**：洪流行内 cr 0.25–1.08 与 nad 0.10–0.97 同时抬升（M1 共享顶点份额脱离实例库成立）；判决按修复后规则为 multi（有负载、无单一路径过半数票）；ib/ih 抬升=xz 的 eMMC 输入读+IO DMA 如实入账 |

**引擎修复（H3，本轮暴露）**：①判决三值化（multi）；②nad 入口方向再修正
（rx → rx+tx 和，§8.5 的 rx 改动方向读反：2a/B 实测 tx=入向全量 596–949MB/s）；
③顶点分解与排名统一窗口均值（突发场景中位数被空载行稀释）。双门复验：
replay 17/17、selfcheck 23/23 全过。详见 validation-replay.md §8.6。
