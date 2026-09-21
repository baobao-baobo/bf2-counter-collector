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
| D HTTP 下载+落盘 | BF2: wget --limit-rate=100M -O /tmp/dl.bin http://192.168.56.1:8000/rand1g.bin（fujian 起 http.server） | **NAD 主导**（Arm 收）+ CR 中度 + eMMC 写入入账；幅度稳健（100Mbps ≪ E2E-B 的 5Gbps，判决应不变） | 新协议 + 幅度稳健 |
| E Arm 主动发送 | BF2: iperf3 -c 192.168.56.1 -t 15 -b 5G（fujian iperf3 -s） | **边界场景**：arm/eswitch 顶点抬升；模型预期 nad 认领（Arm 边界入口和=出向流量）或如实暴露 Arm→主机出口缺口；tx≈0（p1 未涉） | 出口缺口签名 |

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
  -a "wget --limit-rate=100M -O /tmp/dl.bin http://192.168.56.1:8000/rand1g.bin" -b 0-3 -t 120
# fujian 收尾：Ctrl-C 停 http.server；BF2 收尾：rm /tmp/dl.bin
```
说明：`--limit-rate=100M` 把 1GB 下载拖到 ~80s（56.x 链路 ≫ eMMC 写速，不限速秒完）；
若 wget 不在，用 `curl --limit-rate 100M -o /tmp/dl.bin <同 URL>`。

### 负载 E（1 轮，Arm 主动发送，双端）
```bash
# fujian 终端（fujian 在 56.x 的地址，`ip a` 确认，默认 192.168.56.1）：
iperf3 -s -p 5205
# BF2 终端：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_armsend_run1.csv \
  -a "iperf3 -c 192.168.56.1 -p 5205 -t 15 -b 5G" -b 0-3 -t 30
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

## 记录表

| 轮 | CSV | 窗长（实际） | 判决 | L_p 窗口均值 (cr/ih/ib/wb/nad/nhd/tx) | 预期 | 结论 |
|---|---|---|---|---|---|---|
| A1 | | | | | cr | |
| A2 | | | | | cr | |
| A3 | | | | | cr | |
| B1 | | | | | cr | |
| C1 | | | | | cr | |
| D1 | | | | | nad | |
| E1 | | | | | 边界 | |
