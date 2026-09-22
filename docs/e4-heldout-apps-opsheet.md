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

本地命令：`python tools/prism_search.py <csv> --scene <名>`（多文件按逗号列）

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
| D2 | e4_http_run2.csv | 11s | **dominant nad（0.257）** | 0.238/0.209/0.091/0.008/**0.257**/0.009/0.000 | nad | **PASS**：下载 1GB 真实入窗（net_rx≈en3f1_tx≈110MB/s 逐行吻合）；cr 0.238 中度+ih 0.209=eMMC 写入入账 ✓；nhd 0.009 残值 ✓、tx 0.000 ✓；限速未生效（~880Mbps 而非 100Mbps，wget 版本/参数问题），反成意外收获——880Mbps 与 E2E-B 的 5Gbps 两种速率下判决同为 dominant nad=幅度稳健 ✓ |
| E2 | e4_armsend_run2.csv | 26s（洪流 ~16s） | **dominant ih（1.000）**，wins leader=nad | 0.467/1.000/0.284/0.002/0.980/0.426/0.000 | 边界 | **边界场景成立，证据达成**（取证见下） |

### E2 取证（Arm 出向洪流，边界场景实证）

- **物理事实**（原始计数器逐行）：en3f1_rx 662M–2.35GB/s/行（代表口 rx 完整承载
  Arm 出向洪流，与 pf1hpf_tx 0.88–3.17GB/s 同量级——2b 反向管 1.31G 先例一致，
  方向语义无漏计）；tile_io_reads 30–32M/s=TX DMA 读；tile_a72_access 52–59M/s
  =内核协议栈处理；p1_tx≈0（p1 未涉）✓。
- **引擎判读**：dominant ih（med 1.000）——ih 的 hnf 顶点=tile_io_reads@bench-p7
  （p7=Arm→主机管真应力锚点），物理方向正确：Arm→主机出口的 DMA 读侧满载；
  nad 0.980 紧随（en3f1_rx 全量入账 → arm/eswitch 顶点各 ≈0.6）；cr 0.467
  =TX DMA 读在核域可见（p7 修正笔记"TX DMA 读跳显 CR"的复现 ✓）；nhd 0.426
  =共享 eswitch/pcie0 顶点分摊+pf1hpf 出向（M1 份额机制如实分摊）。
- **SAT-SUSPECT 如实触发**：en3f1_rx=0.988、enp3s0f1s0_tx=0.946——锚点取自
  2b 反向管 obs-max（~1.6–1.9GB/s），本次峰值 2.35GB/s 略超 → 引擎如实标注
  "参照值可能取自负载自身观测下界"（与 xz/victim_write 同款提示，非缺陷）。
- **近平局如实呈现**：nad 0.980 vs ih 1.000 双高，逐行互有胜负——判决跟幅值
  （ih）、方向票跟多数（nad），两者都打印，不强行合流。
- **对边界问题的回答**：Arm→主机出口并非全无覆盖——TX DMA 读侧（ih，p7 管）
  与核侧（cr）都可见且满载；论文的缺口声明应精确化为"出口最后一程（主机内存
  写入侧）无计数器"。pcie0 出向放大（nhd 0.426）为已知"pcie0 双向链路"现象的
  出向案例。

### 批级结论（2026-09-22 定案）

可计分 6 轮（A×3+B+D×1 首轮作废后按 D2 计+C）：**5 PASS + 1 诚实负例**，
E2 边界场景证据达成（不计分）。引擎全程 **0 误判**：5 个 PASS 全部命中预期，
C 的 low 系预期强度设错（gzip 足迹常驻 L2，非引擎缺陷），D1/E1 两次执行失败
均如实报 low。留出验证证据从 E2E 的 3 场扩充至 8 场 8 应用（sort/grep/gzip/
HTTP/armsend + openssl/UDP/混合）。论文实验评估节留出段可据此扩写。

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

## E4 扩展：打穿访存链路（2026-09-22 新增，期望先行）

**背景**：C1 定案 gzip 足迹常驻 L2（诚实负例 #2），库内 xz（8MB 字典）cr 0.584。
用户要求再补一轮压缩实验，但这次要"打穿访存链路"而非"压完即丢"。
两个词的计数器语言定义：
- **打穿访存链路**：负载工作集大于 L2（1MB），核请求与逐出流量贯穿网格全链
  （tile_a72_access → hnf → L3 → victim_write 全线抬升），全程持续；
- **压完即丢**：单遍流式（C1 gzip：输入 8.8MB/s + 输出 6.4MB/s），窗口/哈希表
  常驻 L2，逐字节网格流量 ≈ 0，DRAM 只吃流式速率。
两个架构常数决定一切：**DEFLATE 窗口上限 32KB**（RFC 1951，gzip 无参数可调大）；
**LZMA2 字典 -6=8MB、-9=64MB**（xz 可调）。1MB L2 是分水岭——
gzip 任何配置都打不穿（多线程只把流式速率乘 N，归一化仍 <0.01），
xz -9 必然打穿。故 F1/F2 选 xz 为主力，G 为 gzip 同形对照。

### 负载 F1（xz -9 压缩，打穿主力）
```bash
cat /tmp/rand512.txt > /dev/null   # page cache 预热（剔除 eMMC 读混杂）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_xz9_run1.csv \
  -a "xz -9 -T4 -k /tmp/rand512.txt" -b 0-3 -t 600
```
说明：-9=64MB 字典 ×4 线程 ≈ 256MB 工作集 ≫ 1MB L2 → 匹配搜索逐字节触 DRAM、
字典行持续逐出（victim_write）。512MB 随机数据预计 60–300s（LZMA -9 每线程
0.5–2MB/s）。回退：OOM（~2.7GB 内存）→ -T2；旧版 xz 无 -T → 去 -T4；
>600s 未跑完 → 改 -6。

### 负载 F2（xz -t 回读校验，打穿第二段）
```bash
cat /tmp/rand512.txt.xz > /dev/null
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_xzt_run1.csv \
  -a "xz -t -T4 /tmp/rand512.txt.xz" -b 0-3 -t 600
```
说明：xz -t 全量解压校验但不写输出（压→写→回读→验全链路收尾），无输出写 →
wb ≈ 0，与 F1 的 wb 构成读写分离对照。预计 30–90s（解码快于压缩）。

### 负载 G（gzip 同形对照，期望 low——可选）
```bash
cat /tmp/rand512.txt > /dev/null
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/e4_gzip9_run1.csv \
  -a "gzip -9 -c /tmp/rand512.txt | sha256sum" -b 0-3 -t 120
```
说明：输出被 sha256sum 消费（非压完即丢），但 32KB 窗口仍常驻 L2——
**期望 low（cr ≈ 0.01–0.02，与 C1 一致），这个 low 就是正确结果**
（DEFLATE 窗口是架构硬上限，gzip 打不穿与调参无关）。价值：与 F1 同输入、
同消费形态的族内对照，坐实"逐字节网格流量是应用属性而非族标签"。

### 判读标准补充

| 负载 | 期望判决 | 期望签名 | 不一致时的处置 |
|---|---|---|---|
| F1 | dominant cr | cr ≥ 0.584（库内 xz 基线；4 线程聚合下 a72_access 顶点可能顶到锚点上界钳位 1.0，SAT-SUSPECT 允许且如实）；a72/hnf/victim_write 同抬升（64MB 字典逐出）；emem_rd 可见（≈输入率量级）；wb 中度 | 按 docs/validation-replay.md §8.6 三类（引擎缺陷/覆盖缺口/预期设错）诊断 |
| F2 | dominant cr | cr 中高（a72/hnf 驱动）；emem_rd 可见（=解码输出率量级）；wb ≈ 0（与 F1 对比） | 同上 |
| G | low（诚实负例） | cr ≈ 0.01–0.02（与 C1 同族同量级） | 若报 high 反而要逐列取证 |

回传：`tar czf /tmp/e4_xz_batch.tar.gz results/e4_xz9_run1.csv results/e4_xz9_run1.csv.phase.log results/e4_xzt_run1.csv results/e4_xzt_run1.csv.phase.log results/e4_gzip9_run1.csv results/e4_gzip9_run1.csv.phase.log`
