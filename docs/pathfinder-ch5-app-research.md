# PathFinder 第五章验证阶段：候选应用池调研（2026-09-22）

版本 2026-09-22。目的：为第五章验证阶段（对应 PathFinder Evaluation 的
案例化验证）调研**新应用候选池**。三项硬约束：① 应用须在已用工具/应用
**之外**（已用清单见 §2）；② 须能通过**运行参数**把 BF2 数据路径瓶颈从
一条路径转移到另一条（供后续出图）；③ 6 个 case × 每个 case 多个应用，
故池子要足够宽、跨度要足够大。

调研方法：本地（设备操作手册/bench 目录清点已用应用 + BF2 路径模型
对齐）→ 外部（逐候选验证 aarch64 可构建性、调参旋钮、性能跨度）。
数据源链接见文末 §8。

---

## 1. 转移目标：BF2 七路径模型回顾

瓶颈"转移"的可观察目标 = `configs/path_table.conf` 的 7 条路径：

| 路径 | 语义 | 典型点亮方式 |
| --- | --- | --- |
| cr | 核读（A72→HNF→L3→DDR） | 随机访存、cache miss 密集 |
| wb | 核写回（writeback） | 写入密集（排序、写文件） |
| ib | Arm 旁路 DMA 读（bypass HNF） | 顺序流式读、O_DIRECT、网卡 DMA |
| ih | 主机入向 DMA（主机→PCIe→DDR） | 主机洪流进卡 |
| nad | 网口→Arm 终接 | TCP/UDP 流量终接在 Arm 应用 |
| nhd | 网口→主机直通 | 两卡 p1 之间的穿透流 |
| tx | Arm→网口发送 | Arm 主动上行 |

对应 PathFinder 第五章的"路径转移"三型：**负载强度转移**（Case 3/4 的
流量比例阶梯）、**访问模式转移**（Case 5 的流间份额）、**配置转移**
（Case 7 的 TPP 开关）。本池每个候选都标注"哪个参数把瓶颈从哪条路径
搬到哪条路径"。

---

## 2. 已用应用清单（排除范围）

设备侧已跑过的（出自 `docs/apps-ops-manual.md`、`docs/e2e-validation-opsheet.md`、
`docs/e4-heldout-apps-opsheet.md`、`docs/saturation-calibration-execution.md`）：

- 主图七应用：xz（G1）、GAPBS-BFS（G2）、Redis（G3）、SQLite（G4）、
  blackscholes（G5）、TFLite（G6）、Redis+SQLite 混合（G7）
- 标定面：stress-ng（cpu/cache 两面）、STREAM、memrand、fio、iperf3
- E2E 留出：openssl、UDP 洪流、xz+UDP 混合
- E4 留出：sort、grep、gzip、HTTP 下载、Arm 主动发送

以下候选全部不在此清单内。两个"半例外"已标注：GAPBS 其余内核（同
套件异应用，§4.12）、zstd/lz4（压缩家族新工具，§4.8）。

---

## 3. 候选池总表

| # | 应用 | 点亮路径 | 转移旋钮（参数 → 转移方向） | 性能跨度 | 构建 |
| --- | --- | --- | --- | --- | --- |
| 1 | NPB EP | （诚实负例，纯 L1 计算） | class 只影响时长 | 计算型极低内存压力 | 源码+gfortran |
| 2 | NPB IS | cr（miss 主导）+wb | class S→A/B：工作集跨出 L2 | 键数 2¹⁶→2²⁵（512×） | 源码（C） |
| 3 | NPB MG | cr+wb（模板计算） | class S(32³ 缓存驻留)→A(256³ DRAM) | 网格量 512× | 源码+gfortran |
| 4 | NPB CG | cr（稀疏不规则） | class 行数 1400→14000 | 行数 10× | 源码+gfortran |
| 5 | NPB FT | ib+wb（流式 FFT+转置） | class 网格 64³→256×256×128 | 网格量 341× | 源码+gfortran |
| 6 | netperf | nad/tx/nhd | **方向旋钮** STREAM↔MAERTS＝nad↔tx；**报文旋钮** -m 64B↔64KB＝核绑定↔DMA 绑定 | pps/bps 2–3 个量级 | arm64 deb |
| 7 | sysbench | cr/wb/ib/ih+诚实负例 | 模式旋钮 cpu↔memory↔fileio；seq↔rnd；read↔write；buffered↔direct | 纯计算↔磁盘满压 | arm64 deb |
| 8 | LevelDB db_bench | wb/cr/ib/ih | **cache_size 阈值翻转**＝存储↔内存；benchmarks 切换＝wb↔cr↔ib | 命中↔未命中 ops 差 10× | 源码 cmake |
| 9 | sockperf | nad+cr（TCP/UDP）/ ib（RDMA） | **传输旋钮** TCP↔RDMA＝cr 有↔无；报文大小 | pps↔bps 跨度大 | arm64 deb |
| 10 | lmbench3 | cr（延迟链）/cr+wb（带宽） | 尺寸跨缓存边界＝L1/L2/DRAM 平台跳变；rd↔wr↔cp | 延迟 3↔150ns；带宽全谱 | 源码 gcc |
| 11 | HPCC GUPS | wb+cr miss（读写改随机） | 表尺寸 ¼–½ 内存；实例数×4＝份额分解 | 随机更新 GB 级/s | 源码/自写 |
| 12 | zstd/lz4 | cr（哈希表）+wb | level 1↔22（速度 10–20×）；--long 窗口字典 2¹⁰↔2²⁷ | 速度跨度最大 | 源码 gcc |
| 13 | iozone | ib/ih（eMMC） | -i 13 种模式矩阵；-I O_DIRECT；-r 记录大小 | 顺序↔随机差 1–2 量级 | 源码 make |
| 14 | DuckDB CLI | cr/ib（扫描）/cr+wb（join） | 查询类型 scan↔join；SET memory_limit | 扫描↔join 差 10×+ | aarch64 静态二进制 |
| 15 | GAPBS 其余内核 | cr 各型 | -s 图规模；内核 bc/cc/pr/sssp/tc 各异 | 图规模 2²⁶ 边 | 已在设备 |
| 16 | wrk（+nginx） | nad+cr / tx | 线程数/连接数/对象大小 | pps 跨度大 | 源码 gcc |

---

## 4. 逐候选详解

### 4.1 NPB（NAS Parallel Benchmarks 3.4）—— 一包五应用，跨度最大

NASA 经典 HPC 套件，含 5 个计算内核（EP/IS/MG/CG/FT）+ 3 个伪应用
（BT/SP/LU，可不取）。**class 参数（S/W/A/B/C…）就是工作集旋钮**：
class A≈B 的 4 倍、B≈C 的 4 倍，S 为最小快速测试档。

- **路径签名与转移**：
  - EP（高斯随机数对生成）：纯 L1 计算、内存几乎不动 → 与 openssl
    （E2E A）同型的**诚实负例**，且是"计算强度极端"的参照点；
  - IS（整型桶排序，C 实现）：随机键分布 → cr miss 主导（g2 BFS 同族
    签名）+ 排序阶段 wb 抬升；class A=2²³ 键 / max 2¹⁹，B=2²⁵ 键；
  - MG（三维多重网格模板）：class S 32³ 缓存驻留（cr 轻）→ class A
    256³ 网格必然 DRAM（cr+wb 双高）——**S↔A 即瓶颈从 L2 迁到 DDR**；
  - CG（稀疏共轭梯度）：不规则访存、非均匀 → cr miss 主导；
  - FT（三维 FFT）：三方向一维 FFT + 转置 → 大跨步顺序流 + 写入 →
    **ib（流式旁路读）+ wb** 候选，与 xz 的流式签名同族但模式不同。
- **性能跨度**：EP（GFLOPS 级、内存近零）↔ FT/IS（内存带宽饱和）——
  同套件内即覆盖"计算↔访存"全谱，L_p 跨度预计为全池最大。
- **构建**：官网 NAS 源码；`config/make.def` 指定编译器后
  `make <kernel> CLASS=<class>`。IS 是 C；EP/MG/CG/FT 是 **Fortran，
  设备需 gfortran**（待验证，见 §6-1）。串行版无 MPI 依赖。
- **对应案例角色**：提案 1（分类）、提案 3（工作集转移）。

### 4.2 netperf —— 方向旋钮 nad↔tx 的标准答案

HP 经典网络压测工具，客户端 `netperf` + 远端 `netserver` 守护进程
（控制连接 12865 端口）。

- **路径签名与转移**：
  - `TCP_STREAM`（客户端→服务端）＝服务端在 Arm = **nad**；
  - `TCP_MAERTS`（"STREAM"倒写，方向反转：服务端→客户端）＝
    Arm 主动发送 = **tx**——**同一个二进制、同一组参数，仅换测试名，
    瓶颈即在 nad↔tx 间翻转**，这是全池最干净的"方向转移"演示；
  - `TCP_RR`（请求-应答，`-- -r 64,64`）：小报文每包过核 → nad+cr
    双高（中断/拷贝绑定）；`-r` 从 64B 加到 64KB → 从核绑定转移到
    DMA 绑定（2–3 个量级的 pps↔bps 跨度）；
  - `UDP_STREAM`（`-- -m 1400`）：无流控洪流 → nad，实测将撞
    Arm 平台收包上限 ~6.6Gbps（我们 2a 已定案的平台瓶颈，非缺陷）；
  - 附加价值：两卡 p1 直连 100G——fujian 主机 ↔ helong 主机**穿过
    两张 BF2** 打流时，两侧同时点亮 **nhd**（穿透路径），可作 nhd
    的新应用证据（历史 nhd 证据只有 iperf3 一种）。
- **性能跨度**：64B RR 的 pps 极限 ↔ 64KB STREAM 的线速，吞吐跨度
  2–3 个量级。
- **构建**：Ubuntu/Debian **arm64 包已确认存在**（netperf 2.7.0，
  约 518KB），fujian 下载 deb → scp → `dpkg -i` 即可；源码 autoconf
  亦可。两端各装一份（客户端+服务端）。
- **对应案例角色**：提案 1（分类）、提案 2（方向翻转）、提案 6（机制）。

### 4.3 sysbench —— 一工具三模式，旋钮最多

多线程系统压测工具，`cpu`/`memory`/`fileio` 三模式即可覆盖三种路径族。

- **路径签名与转移**：
  - `cpu --cpu-max-prime=N`：素数计算 → L1 纯计算，**诚实负例**；
  - `memory --memory-oper=read|write --memory-access-mode=seq|rnd
    --memory-block-size=SIZE`：read↔write 翻转＝cr↔wb；seq↔rnd
    翻转＝流式↔miss 主导；block-size 从 1K 到 1G 扫过缓存层级；
  - `fileio --file-test-mode=seqrd|seqwr|rndrd|rndwr|rndrw|seqrewr
    --file-block-size=N --file-extra-flags=direct`：eMMC 路径（ib/ih
    家族）；**`direct` 标志（O_DIRECT）关掉页缓存 → 纯 DMA 旁路
    （ib）vs buffered 默认走页缓存（cr）**——"机制切换"旋钮的标准件；
    seqrd↔rndrd 翻转＝eMMC 顺序↔随机（1–2 个量级）；
  - `--file-io-mode=sync|async|mmap`：mmap 模式 = 缺页读（cr 家族）
    vs sync 模式 = read() 拷贝（cr+ib 混合）。
- **性能跨度**：cpu 模式（内存近零）↔ fileio rndwr 满压，跨度全池前列。
- **构建**：apt 包（Ubuntu jammy/noble manpage 在册），官方
  packagecloud 仓库有 **aarch64 二进制**。
- **对应案例角色**：提案 1（分类）、提案 4（模式转移）、提案 5（并发）。

### 4.4 LevelDB db_bench —— cache_size 阈值翻转＝存储↔内存

Google LevelDB 自带压测工具（`benchmarks/db_bench.cc`），键值库微
基准的行业标准。aarch64 可行性已被 ARM 服务器论文证实（Kunpeng 920
上跑 fillseq/fillrandom/readseq/readrandom，BDET 2021）。

- **路径签名与转移**：
  - `--benchmarks=fillseq`：顺序灌库 → wb 主导（写 eMMC）；
  - `fillrandom`：随机灌库 → eMMC 随机写 + wb（rndwr 同族）；
  - `readrandom` + `--cache_size` 小（< DB 尺寸）：随机读 miss →
    **ib/ih（eMMC 读）主导**；`--cache_size` 大（≥ DB 尺寸）：全命中
    → **cr 主导**——**同一个 benchmark、同一个库，仅 cache_size 翻
    过阈值，瓶颈即从存储搬到内存**，全池最精确的"阈值翻转"演示；
  - `--value_size`（默认 100B→可 4KB+）：改变每读的字节/未命中代价；
  - `--write_buffer_size`、`--compression`：次级旋钮；
  - 多实例：4 个 db_bench 各配不同 cache_size 并发 = 提案 5 的份额
    分解输入（PathFinder Case 5 的 MBW×4 同构）。
- **性能跨度**：readrandom 全命中↔全未命中 ops/s 差 ~10×。
- **构建**：cmake `-DLEVELDB_BUILD_BENCHMARKS=ON`，g++9.4 可编；
  对比基准依赖的 sqlite3/kytocabinet 可跳过（不编对比目标即可）。
  源码小（~几 MB），设备编译分钟级。
- **对应案例角色**：提案 1/3/4/5（全池最通用）。

### 4.5 sockperf —— 传输旋钮 TCP↔RDMA＝cr 有↔无

Mellanox 系高容量 ping-pong/吞吐压测工具，TCP 与 RDMA 双模式。
**arm64 deb 已在 Ubuntu universe 在册**（sockperf_3.7-1_arm64.deb，
~550KB）；aarch64 时间戳计数器支持已并入上游（PR #187，读
`cntvct_el0`）；学术论文实测 BlueField-2 DPU 上跑 sockperf UDP
ping-pong 基线 ~10.5µs（经 eSwitch）——**该工具在 BF2 上有直接
使用先例**。

- **路径签名与转移**：
  - `under-load` / `playback`（吞吐模式，TCP/UDP）：两卡 p1 对打
    → 收端 nad（TCP 终接）+ cr；发端 tx；
  - `ping-pong`（`--msg-size=64` 小报文）：pps 极限 → 每包过核，
    nad+cr 双高（Arm 中断/拷贝绑定）；
  - **RDMA 模式（RoCE，需两卡 p1 上配好无损）**：RDMA write 由
    ConnectX 硬件直写 DDR → **ib 点亮、cr 熄灭、nad 的 DDR 跳仍然
    在但核分量归零**——`--tcp` 与 `--rdma`（实际为 `--transport` 类
    参数）之间的切换 = "同一流量、cr 有↔无"的机制转移，PathFinder
    Case 7（TPP 改配置）在 BF2 上的最接近等价物；
  - 报文大小旋钮（64B↔64KB）同 netperf。
- **性能跨度**：ping-pong pps 极限 ↔ 吞吐线速，2 个量级+。
- **构建**：arm64 deb 直装；RDMA 模式依赖 libibverbs/librdmacm
  （DOCA 环境已含，待验证 §6-3）。无 RDMA 时 TCP/UDP 模式照样
  可用（价值不降级）。
- **对应案例角色**：提案 2（方向）、提案 6（传输机制切换）。

### 4.6 lmbench3 —— 缓存层级扫描的标准显微镜

经典微基准双件套：`lat_mem_rd`（指针追逐延迟链）+ `bw_mem`
（带宽）。源码极小，gcc 直编，arm 无移植障碍（纯 C）。

- **路径签名与转移**：
  - `lat_mem_rd size_mb stride`：按步长串指针环、逐级加大数组 → 延迟
    在 **L1（~3ns）/L2（7–14ns）/DRAM（~150ns，A72 量级）/TLB 失效**
    四个平台间阶梯跳变——**尺寸跨缓存边界即 cr 的"深度"转移**，且
    每个平台的跳变点直接标注缓存层级（与我们 L1/L2/L3 顶点对应）；
  - `bw_mem size`：`rd`↔`wr`↔`rdwr`↔`cp`（另有 fwd/frd/fcp 全字
    宽变体）——**读↔写翻转＝cr↔wb；读+写混合＝cr+wb 双点**；尺寸
    从缓存驻留加到 DRAM 级；
  - 单核 vs 多核并行（`-P`）＝并发度旋钮。
- **性能跨度**：延迟轴 3↔150ns（50×）；带宽轴覆盖全谱。
- **构建**：lmbench3 源码 tar，`make` 即得（设备 gcc 直编，分钟级）。
  运行时注意：默认运行时间短，需循环包装或加大 size 凑 30s 相位。
- **对应案例角色**：提案 3（工作集）、提案 4（读写模式）。

### 4.7 HPCC RandomAccess（GUPS）—— PathFinder Case 5 原版复刻

HPC Challenge 的 RandomAccess 内核：对占内存 ¼–½ 的 64 位字表做
随机地址流的读-改-写更新（GF(2) 本原多项式生成随机地址），指标
GUPS（每秒 10⁹ 次随机更新）。**PathFinder Case 5 用的正是 GUPS×4
并发做带宽份额分解**——原样复刻可获得直接的方法论对照。

- **路径签名与转移**：随机读-改-写 → **wb+cr miss 双满**，是全池
  最强的写路径压力源（我们的 wb 证据目前只有 sort/xz 一族）；
  表尺寸 ¼↔½ 内存＝工作集旋钮；实例数 1↔4＝争用旋钮。
- **性能跨度**：vs 计算型负例差 3–4 个量级的内存流量。
- **构建**：完整 HPCC 套件需要 MPI+BLAS（aarch64 已被 Fugaku/Spack
  证实可编，Ubuntu 有 hpcc 源码包）；**亦可只取 `randacc.c` 单 CPU
  核心**，或按 bench/src 惯例自写一个 ~50 行 GUPS（mbw/memrand 先例）
  ——自写版零依赖、参数可控，但"标准 benchmark"的论文说服力稍弱。
- **对应案例角色**：提案 5（份额分解，原版复刻）。

### 4.8 zstd / lz4 —— 压缩家族新工具，速度跨度最大

- zstd：`-1`…`-22` 级别 → 压缩速度跨度 10–20×；`--long=windowLog`
  （10–27）哈希表尺寸旋钮 → cr 工作集转移；`-T` 线程数。lz4 是另一
  端（极速流式，内存压力低、wb 主导）。
- **与已用工具关系**：xz/gzip 已用（G1/E4-C），zstd/lz4 是新工具、
  算法族不同——**半例外**，优先度低于前 7 项。
- 构建：源码 gcc 直编（设备现成）。
- **对应案例角色**：提案 3 备选（字典尺寸转移）。

### 4.9 iozone —— eMMC 13 模式矩阵（与 sysbench fileio 二选一）

- `-i 0..12`：顺序写/改写/顺序读/重读/随机读/随机写/倒读/记录改写/
  跨步读/fread/fwrite/随机混合/pwrite 家族，13 种模式；`-r` 记录大小、
  `-s` 文件大小、`-I` O_DIRECT、`-t` 多线程吞吐。
- aarch64：源码 `make linux-arm` 目标（NetBSD pkgsrc 有 aarch64 包）。
- **与 sysbench fileio 重叠**——建议二者取一（sysbench 因 apt 更省事
  优先；iozone 的模式矩阵更细，写 eMMC 深度分析时可用）。

### 4.10 DuckDB CLI —— 零构建的分析型数据库（列存扫描）

- **官方 aarch64 单文件 CLI 二进制**（GitHub releases
  `duckdb_cli-linux-aarch64.zip`）——fujian 下载 → scp 即用，全池
  构建成本最低；
- 分析型列存引擎，与已用的 SQLite（OLTP 行存）完全不同族：全表
  聚合扫描 = 顺序流（ib/cr）；hash join = cr+wb；`SET memory_limit`
  与 `SET threads` 为运行时旋钮 → **memory_limit 阈值翻转＝外溢
  ↔驻留**（与 db_bench cache_size 同构的第二个阈值翻转件）；
- 查询类型（scan↔join）切换 = 访问模式转移。

### 4.11 GAPBS 其余内核 —— 零构建的同套件补充（半例外）

设备上已有 GAPBS（g2 BFS 用），其余内核 bc/cc/pr/sssp/tc 零编译成本。
各内核访存特征不同（tc=计算+随机交、pr=随机、bc=遍历）。**同套件，
故标注为半例外**——仅当某 case 缺 1–2 个应用时作低成本填充。

### 4.12 wrk + nginx —— HTTP 族（低优先）

wrk（HTTP 压测，线程/连接/对象大小旋钮）+ nginx（worker/sendfile
旋钮，tx 路径）。E4 D2 已用过简易 HTTP 下载，本项为新工具，但价值
低于 netperf/sockperf，列为备选。

---

## 5. 六个 case 的构建建议（提案，最终由用户裁定）

对应 PathFinder 第五章的案例骨架（见本日第五章深析结论）：

| 提案 | 对应 PathFinder | 主题 | 应用组合（例） | 转移演示 |
| --- | --- | --- | --- | --- |
| 1 | Case 1 | 新应用路径分类 | EP / IS / FT / TCP_STREAM / db_bench-readrandom / sysbench-fileio-rndrd | 六应用六种路径签名，L_p 跨度最大 |
| 2 | Case 7（部分） | 方向翻转 | netperf STREAM↔MAERTS；sockperf 服务端/客户端互换 | 同二进制同参数仅换方向：nad↔tx |
| 3 | Case 3/6 | 工作集转移 | NPB class S↔B；lmbench 尺寸扫描；db_bench cache_size 小↔大；DuckDB memory_limit | 缓存驻留↔DRAM↔存储 三级转移 |
| 4 | Case 5（部分） | 访问模式转移 | sysbench seq↔rnd；lmbench rd↔wr↔cp；db_bench fillseq↔fillrandom | cr↔wb↔ib 三向翻转 |
| 5 | Case 4/5 | 并发争用与份额 | db_bench×4（异 cache_size）/ GUPS×4（异表尺寸）/ sysbench×4（异强度） | M1 份额分解 + 应用层带宽对照（0.998 式） |
| 6 | Case 6/7 | 机制切换与共置 | sockperf TCP↔RDMA；sysbench buffered↔direct；多应用共置 locality 漂移 | 传输/缓存机制切换＝路径整体转移 |

---

## 6. 待验证事项清单（设备侧，用户执行）

1. **gfortran 可用性**（NPB 的 EP/MG/CG/FT 是 Fortran；IS 是 C）：
   `which gfortran`；无则从 fujian 下载 arm64 deb 安装；
2. **apt/deb 路线**（netperf、sockperf、sysbench 的 arm64 包）：
   设备无外网 → fujian 下载 deb → scp → `dpkg -i`（老流程）；
3. **RDMA 库**（sockperf RDMA 模式）：`ldconfig -p | grep ibverbs`；
   RoCE 在两张 BF2 的 p1 之间是否就绪（无损配置），未就绪则 sockperf
   先只跑 TCP/UDP 模式（价值不降级）；
4. **eMMC 余量**（LevelDB/sysbench fileio/DuckDB 数据文件，GB 级）：
   `df -h <EM>`；
5. **运行时长校准**（相位 30–35s 惯例）：netperf `-l 30`、sockperf
   `--time`、sysbench `--time=30`、db_bench `--num` 调大到 30s、
   NPB 选 class 使时长 30s（S 档通常太短，A 档起步）、lmbench 循环
   包装、GUPS 表尺寸调时长。

---

## 7. 结论

- 候选池 16 项，**Tier 1 六件**（NPB、netperf、sysbench、LevelDB
  db_bench、sockperf、lmbench3）全部满足三约束：新工具、aarch64
  路线已确认、至少一个参数旋钮能制造路径级瓶颈转移；
- 转移旋钮共四类：**方向**（STREAM↔MAERTS）、**阈值**
  （cache_size/memory_limit 跨缓存边界）、**模式**（seq↔rnd、
  rd↔wr、buffered↔direct）、**传输**（TCP↔RDMA）——六案例提案
  各取所需；
- 最硬的对照件：GUPS×4（PathFinder Case 5 原版复刻）与 sockperf
  的传输切换（Case 7 的 BF2 等价物）。

---

## 8. 数据源

- NPB：[NPB 官方](http://www.nas.nasa.gov/Software/NPB/)；
  [问题规模与参数（NPB 3.4，CSDN）](https://blog.csdn.net/qtm_gitee/article/details/161111768)
- LevelDB：[DeepWiki LevelDB 性能基准](https://deepwiki.com/google/leveldb/5.3-performance-benchmarking)；
  [db_bench 编译指南（CSDN）](https://blog.csdn.net/weixin_48791852/article/details/147306467)；
  [Kunpeng 920 上 LevelDB 优化论文（ACM BDET 2021）](https://dl.acm.org/doi/fullHtml/10.1145/3474944.3474946)
- sockperf：[Ubuntu universe sockperf arm64 包](http://mirrors.ptisp.pt/ubuntu/pool/universe/s/sockperf/)；
  [BlueField DPU 上 sockperf/pipe 性能论文（li2024）](https://shenjiaxing.github.io/pdf/2024/li2024performance.pdf)；
  [XLIO 文档（BlueField 容器内 sockperf 用例）](https://docs.nvidia.com/networking/display/nvidia-accelerated-io-xlio-documentation-rev-3-60.60.pdf)
- HPCC：[HPCC 官方](http://icl.cs.utk.edu/hpcc/)；[Ubuntu hpcc 源码包](https://packages.ubuntu.com/hu/source/plucky/s390x/hpcc)；
  [Fugaku/RIKEN Spack 上 hpcc aarch64 构建清单](https://spack-mirror.r-ccs.riken.jp/oss/public/packages/view/4553)
- netperf：[netperf 手册（Debian arm64 包页）](https://manpages.debian.org/unstable/netperf/netperf.1.en.html)；
  [netperf 2.7.0 文档 PDF](https://sources.debian.org/data/non-free/n/netperf/2.7.0-0.1/doc/netperf.pdf)
- lmbench3：[lat_mem_rd 手册（Ubuntu）](https://manpages.ubuntu.com/manpages/trusty/lat_mem_rd.8.html)；
  [lmbench 内存层级分析（DeepWiki）](https://deepwiki.com/intel/lmbench/3.1-memory-hierarchy-analysis)
- sysbench：[sysbench 手册（Ubuntu jammy）](https://manpages.ubuntu.com/manpages/jammy/man1/sysbench.1.html)
- DuckDB：[官方安装页](https://duckdb.org/docs/installation/index.html)（aarch64 CLI zip）；
  [conda-forge duckdb-cli linux-aarch64](https://anaconda.org/channels/conda-forge/packages/duckdb-cli/files)
- iozone：[iozone 官方](http://www.iozone.org/)；[NetBSD pkgsrc iozone aarch64](http://rsync.netbsd.org/pub/pkgsrc/current/pkgsrc/benchmarks/iozone/index.html)
