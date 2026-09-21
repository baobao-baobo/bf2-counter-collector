# E4 留出负载扩展批操作单（2026-09-22）

**目的**：扩大留出样本。E2E 三场（openssl/UDP/混合）证明了方向判据与共享份额机制
在实例库外成立，但正例仍少：CR 留出正例缺失（openssl 是 L1 纯计算，选错了负载）、
网络域只有 UDP 一个留出协议。本批引入 5 个新负载（7 轮），继续
"跑负载 → 采计数器 → 模型判读 → 对照独立语义"的闭环。

**批级验收**：7 轮中 ≥6 轮判决与预期一致，且任何不一致经诊断为已知边界或覆盖缺口
而非引擎缺陷 → 可用性证据扩充成立。E 场（边界场景）必录、不计入 PASS 计数。

**设计原则**：
1. **期望先行**：每个负载的独立预期在采集前写死（凭负载语义），判读时对照，不改题；
2. **覆盖铺开**：补 CR 留出正例、读写/只读对比、压缩族泛化、新协议复合、Arm 出向边界；
3. **不调参**：留出数据只用于判读。若暴露引擎缺陷，修复必须是通用规则修正，
   且过双门复验（replay 17/17 + selfcheck 23/23，见 docs/validation-replay.md §8.6 协议）。

## 负载表

| 负载 | 命令 | 独立预期（凭负载语义） | 检验点 |
|---|---|---|---|
| A 外部归并排序 ×3 | sort -S 4M /tmp/rand512.txt > /dev/null | **CR 主导**（归并计算 + DDR 读写）；IB/IH 中度（-S 4M 强制外部排序，临时文件落 eMMC）；WB 参与（写缓冲） | CR 留出正例 + 复现 |
| B 文本扫描 | for i in 1 2 3 4 5 6; do grep -c AAAAAAAA /tmp/rand512.txt; done | **CR 主导**（只读流）；WB 明显低于 A——与 A 形成读写对比 | 只读/读写对比 |
| C 压缩族泛化 | gzip -6 -c /tmp/rand512.txt > /dev/null | **CR 主导**（与库内 xz 同族不同应用）；WB 中度（输出缓冲） | 家族泛化 |
| D HTTP 下载+落盘 | BF2: wget --limit-rate=100M -O /tmp/dl.bin http://192.168.56.11:8000/rand1g.bin（fujian 起 http.server） | **NAD 主导**（Arm 收）+ CR 中度 + eMMC 写入入账；幅度稳健（100Mbps ≪ E2E-B 的 5Gbps，判决应不变） | 新协议 + 幅度稳健 |
| E Arm 主动发送 | BF2: iperf3 -c 192.168.56.11 -t 15 -b 5G（fujian iperf3 -s） | **边界场景**：arm/eswitch 顶点抬升；模型预期 nad 认领（Arm 边界入口和=出向流量）或如实暴露 Arm→主机出口缺口；tx≈0（p1 未涉） | 出口缺口签名 |

新名词首现：
- **外部归并排序**：数据量大到内存放不下时，sort 把中间结果写成磁盘临时文件再归并。
  A 用 `-S 4M` 强制该行为（最多用 4MB 内存），让 eMMC 路径（IB/IH）也进入签名。
- **边界场景**：负载的真实去向落在模型路径集合之外或边缘，判读结果用于刻画模型
  覆盖边界（论文边界段的实证素材），而非验证正确性。

## 设备步骤（用户执行）

### 前置（一次性，~3 分钟）
```bash
cd /root/bf2k
# 可用性检查（缺哪个告诉我，换场景）：
which sort grep gzip wget curl iperf3 python3 base64 dd
# 输入文件准备（512MB 带行文本，A/B/C 三负载共用；耗时约 1–3 分钟）：
base64 /dev/urandom | head -c 512M > /tmp/rand512.txt
ls -la /tmp/rand512.txt     # 应 ~537MB
```
说明：文件取 512MB 而非 128MB，是为了把每个负载的窗长撑到 15s 以上
（排序/扫描类应用太快，128MB 几秒跑完，采样行太少）。

### 负载 A（3 轮，CR 留出正例）
```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_sort_run1.csv \
  -a "sort -S 4M /tmp/rand512.txt > /dev/null" -b 0-3 -t 120
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_sort_run2.csv \
  -a "sort -S 4M /tmp/rand512.txt > /dev/null" -b 0-3 -t 120
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_sort_run3.csv \
  -a "sort -S 4M /tmp/rand512.txt > /dev/null" -b 0-3 -t 120
```
说明：`-t 120` 是上限，相位日志按实际 app 起止界定窗口（sort 结束即 app_end，
均值不会被尾部空载稀释）；512MB 外部归并单轮预计 20–30s。若单轮 <10s 告诉我，
加循环；临时文件 ~512MB 落在 /tmp（跑完自动删除），磁盘空间足够。

### 负载 B（1 轮，只读扫描）
```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_grep_run1.csv \
  -a "for i in 1 2 3 4 5 6; do grep -c AAAAAAAA /tmp/rand512.txt; done" -b 0-3 -t 120
```
说明：grep 是极快的扫描器（单遍 512MB 仅 1–2s），循环 6 遍把窗长撑到 ~15s；
若窗长仍 <10s 告诉我，加循环次数。

### 负载 C（1 轮，压缩族泛化）
```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_gzip_run1.csv \
  -a "gzip -6 -c /tmp/rand512.txt > /dev/null" -b 0-3 -t 120
```
说明：gzip 单线程 ~20–40MB/s，512MB 单轮约 15–25s，无需循环。

### 负载 D（1 轮，HTTP 下载+落盘，双端）
```bash
# fujian 终端（先起服务，~30s 准备）：
mkdir -p ~/http && dd if=/dev/urandom of=~/http/rand1g.bin bs=1M count=1024
cd ~/http && python3 -m http.server 8000
# BF2 终端：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_http_run1.csv \
  -a "wget --limit-rate=100M -O /tmp/dl.bin http://192.168.56.11:8000/rand1g.bin" -b 0-3 -t 120
# fujian 收尾：Ctrl-C 停 http.server；BF2 收尾：rm /tmp/dl.bin
```
说明：`--limit-rate=100M` 把 1GB 下载拖到 ~80s（56.x 链路 ≫ eMMC 写速，不限速秒完）；
若 wget 不在，用 `curl --limit-rate 100M -o /tmp/dl.bin <同 URL>`。

### 负载 E（1 轮，Arm 主动发送，双端）
```bash
# fujian 终端（fujian 56.x 地址=192.168.56.11，2026-09-22 已由 ip a 确认；早期笔误 56.1 即 D/E 首轮失败的根因）：
iperf3 -s -p 5205
# BF2 终端：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_armsend_run1.csv \
  -a "iperf3 -c 192.168.56.11 -p 5205 -t 15 -b 5G" -b 0-3 -t 30
# fujian 收尾：Ctrl-C
```

### 回传
```bash
# BF2：
tar czf /tmp/e4_batch.tar.gz results/e4_*.csv results/e4_*.phase.log
# scp 回本地（老流程）
```

## 判读标准（Claude 本地）

本地命令：`python tools/bfs_search.py <csv> --scene <名>`（多文件按逗号列）

| 负载 | 期望判决 | 期望签名 | 不一致时的处置 |
|---|---|---|---|
| A | dominant cr（三轮一致） | cr 0.3–1.5 量级；ib/ih 中度（外部排序临时文件）；wb 0.05–0.3；nad/nhd/tx ≈0 | 原始计数器逐列取证后按 §8.6 三类（引擎缺陷/覆盖缺口/预期设错）诊断 |
| B | dominant cr | cr 与 A 同量级；wb 明显低于 A（只读无写回） | 同上 |
| C | dominant cr | cr 与库内 xz 同族同量级或略低（base64 文本比随机二进制易压）；wb 中度 | 同上 |
| D | dominant nad | nad 明显抬升（低于 E2E-B 属预期，100Mbps vs 5Gbps）；cr 中度；eMMC 写入入账；nhd 残值 ~0.02（pcie0 进向中转） | 幅度稳健性 = 判决不随流量规模翻转 |
| E | 边界场景 | arm/eswitch 顶点抬升；判决预期 nad 认领（Arm 边界）或如实暴露出口缺口；tx≈0 | 签名入论文边界段（Arm→主机出口缺口的网络侧实证） |

批级判读要点：① 7 轮 ≥6 轮判决与预期一致即批级 PASS；② E 场按边界场景必录、
不计入 PASS；③ 任何不一致先原始计数器逐列取证（如 E2E 对 en3f1_tx 方向的取证），
再定三类，处置须过双门复验。

## 记录表（首轮判读，2026-09-22）

| 轮 | CSV | 窗长（实际） | 判决 | L_p 窗口均值 (cr/ih/ib/wb/nad/nhd/tx) | 预期 | 结论 |
|---|---|---|---|---|---|---|
| A1 | e4_sort_run1.csv | 37s | **dominant cr（0.216）** | 0.216/0.109/0.160/0.006/0/0/0 | cr | **PASS**：ib+ih=外部排序临时文件签名 ✓ |
| A2 | e4_sort_run2.csv | 41s | **dominant cr（0.227）** | 0.227/0.119/0.125/0.006/0/0/0 | cr | **PASS** |
| A3 | e4_sort_run3.csv | 39s | **dominant cr（0.232）** | 0.232/0.103/0.142/0.006/0/0/0 | cr | **PASS**（三轮偏差 0.016≤0.025 复现性门 ✓） |
| B1 | e4_grep_run1.csv | 6s | **dominant cr（0.431）** | 0.431/0.009/0.003/0.015/0/0/0 | cr | **PASS**（窗薄但信号强；ib 0.003 vs A 0.142=只读/读写对比成立 ✓） |
| C1 | e4_gzip_run1.csv | 58s | **low（cr 0.013）** | 0.013/0.002/0.001/0.001/0/0/0 | cr | **预期设错 → 诚实负例**（取证见下） |
| D1 | e4_http_run1.csv | 4s | low（cr 0.004） | 0.004/0/0/0/0/0/0 | nad | **执行失败**：窗内 net_rx 峰值 700 B/s=零流量（wget 未取到数据，疑 404/连接拒绝）；引擎报 low 如实；**须重跑** |
| E1 | e4_armsend_run1.csv | 4s | low（cr 0.002） | 0.002/0/0/0/0/0/0 | 边界 | **执行失败**：窗内 net_tx ~256–444 B/s=零流量（iperf3 未连上服务端）；引擎报 low 如实；**须重跑** |

### C1 取证（诚实负例 #2：gzip 足迹常驻 L2）

- gzip 单线程压 512MB 用时 58s=**8.8MB/s 输入率**（正常单线程量级），但核域计数器几乎无响应：
  tile_a72_access n=0.001（≈2× 会话背景）、l3 emem_rd≈0（读从未到达 L3）、
  emem_wr≈100K/s（≈6.4MB/s=压缩输出率，写回经 L3 流出）——全部自洽于
  **gzip 的 32KB 滑动窗口+哈希表常驻 L2**，逐字节网格流量极小。
- 族内对照（同引擎口径）：xz 输入仅 3.2MB/s 却 cr 0.584（a72 n=0.096、victim_write n=0.168）
  ——xz 的 8MB LZMA 字典打穿 L2，逐字节触 DRAM 多次；gzip 输入率高 2.7× 而网格压力低 ~45–96×。
- 结论：模型按**实测流量**判读而非按应用族标签——"压缩=核重"的族假设被推翻；
  与 E2E-A openssl 同族（L1/L2 常驻应用属观测盲区，模型不虚构压力）。
  本场不重跑，负例本身成立且有对照价值；若想补压缩族正例可用大字典应用
  （zstd --long / pigz ×4，需设备确认安装）。

### D/E 重跑（先对端自检再正式跑）

**首轮失败根因已定（2026-09-22）**：fujian 在 56.x 的地址是 192.168.56.11
（enp94s0f1np1，ip a 已确认），首轮命令误用 192.168.56.1（不存在）——
BF2 curl 报 "No route to host"（子网内 ARP 无应答），wget/iperf3 同因即退，
故两场 4s 零流量。本节目录与上表已全部改为 56.11。

负载 D（HTTP）：
```bash
# fujian：
ls -la ~/http/rand1g.bin     # 必须 ~1073741824 字节；缺则先：
# mkdir -p ~/http && dd if=/dev/urandom of=~/http/rand1g.bin bs=1M count=1024
cd ~/http && python3 -m http.server 8000
# BF2（先测连通）：
curl -I http://192.168.56.11:8000/rand1g.bin
# 期望 "HTTP/1.0 200 OK" + "Content-Length: 1073741824"；若 404/拒绝，
# 查 fujian 的 56.x 地址（ip a）与服务端输出；连通后正式跑：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_http_run2.csv \
  -a "wget --limit-rate=100M -O /tmp/dl.bin http://192.168.56.11:8000/rand1g.bin" -b 0-3 -t 120
rm -f /tmp/dl.bin
```
负载 E（Arm 主动发送）：
```bash
# fujian：
iperf3 -s -p 5205
# BF2（先测 3s 连通）：
iperf3 -c 192.168.56.11 -p 5205 -t 3
# 期望连接成功+~3s 发送统计；失败查 fujian 地址/端口/服务端输出；连通后正式跑
#（注意：TCP 下 -b 无效，去掉）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_armsend_run2.csv \
  -a "iperf3 -c 192.168.56.11 -p 5205 -t 15" -b 0-3 -t 30
```
回传：`tar czf /tmp/e4_re.tar.gz results/e4_http_run2.csv results/e4_http_run2.csv.phase.log results/e4_armsend_run2.csv results/e4_armsend_run2.csv.phase.log`
