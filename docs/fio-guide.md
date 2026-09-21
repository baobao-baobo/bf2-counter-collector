# fio（Flexible I/O Tester）系统学习文档

> **定位**：简历可写、面试可答的水平，不只是"会敲命令"。
> **使用环境**：本文命令以 Linux 为主（你的实验环境是 BF2 / 服务器）。Windows 上建议用 WSL 练习，不要用 Windows 原生版 fio。
> **配套工具**：`jq`（解析 JSON 结果）、`fio2gnuplot`（画延迟曲线）、`numactl`（绑 NUMA）。

---

## 0. 学习路径建议（按顺序）

1. 读 §1–§2：fio 是什么、装好
2. **精读 §3 核心概念**——面试考点集中在这里，逐条理解，最好边读边跑对应命令验证
3. 跑 §6 的每个场景，对照 §5 学会逐块读输出
4. 过一遍 §8 的陷阱清单（这是"做过真实验"和"背过命令"的分水岭）
5. 用 §9 的面试题自测，答不上来的回头补 §3

---

## 1. fio 是什么

- **fio（Flexible I/O Tester）是 Linux 存储性能测试的事实标准工具**，作者 Jens Axboe（Linux 块层（block layer）维护者）。存储厂商、云厂商、SSD 评测机构都在用。
- 它不是一个"文件拷贝测速器"，而是一个 **I/O 工作负载生成器（workload generator）**：你可以精确指定 I/O 模式（顺序/随机）、块大小、队列深度、读写比例、运行时长，fio 按这个模式向目标文件或裸设备发起 I/O，并统计吞吐（BW）、IOPS、延迟分布。
- 一句话理解它的工作方式：**fio 造出你想要的 I/O 压力 → 统计存储系统在这份压力下的表现**。所以"会设计测试"比"会敲命令"重要得多。

面试角度的一句话：*"fio 的意义在于让不同存储系统的性能可以被同一套标准工作负载公平对比。"*

---

## 2. 安装

```bash
# Debian / Ubuntu（你的 BF2、服务器大概率是这类）
sudo apt install -y fio

# CentOS / RHEL / Fedora
sudo yum install -y fio        # 或 dnf install -y fio

# 验证
fio --version
```

编译安装（需要最新特性如 io_uring 引擎、ZNS 支持时）：

```bash
sudo apt install -y build-essential libaio-dev libnuma-dev
git clone https://github.com/axboe/fio.git
cd fio && ./configure && make -j && sudo make install
```

**注意**：不要在生产盘/有数据的盘上直接跑写测试（见 §8 陷阱 #6）。

---

## 3. 核心概念（★ 面试重点，逐条吃透）

### 3.1 I/O 引擎（ioengine）

fio 通过不同的引擎与内核交互，这决定了 I/O 是"同步"还是"异步"：

| 引擎                    | 类型    | 说明                                                         |
| --------------------- | ----- | ---------------------------------------------------------- |
| `psync`               | 同步    | **默认引擎**。用 pread/pwrite 系统调用，一次只能有一个 I/O 在途                |
| `sync`                | 同步    | 用 read/write（维护文件偏移量），一般不用                                 |
| `libaio`              | 异步    | Linux 原生异步 I/O（`io_submit`），最常用，**必须配 direct=1 才是真异步**（见下） |
| `io_uring`            | 异步    | 新一代异步接口，提交/完成开销更低，高 IOPS 场景推荐（fio ≥ 3.16）                  |
| `mmap`                | 内存映射  | 通过 mmap 访问文件，测的是页缓存路径                                      |
| `null`                | 无 I/O | 不落盘，只测 fio 本身的开销上限（用来判断瓶颈是否在 fio 自身）                       |
| `net`                 | 网络    | 对 socket 做 I/O 负载（fio 也能测网络，但网络测试一般用 iperf3，见另一份文档）        |
| `libpmem` / `dev-dax` | 持久内存  | Intel Optane PMem 测试                                       |
| `rdma`                | RDMA  | RDMA 网络 I/O 负载                                             |

**关键知识点（面试常考）**：

- **libaio 在 direct=0（走页缓存）时，很多内核实现会退化成同步等待**——所以用 libaio 必须配 `--direct=1`，否则 iodepth 形同虚设。
- io_uring 相比 libaio 减少了系统调用次数（SQPOLL 模式甚至可完全在内核轮询），在高 IOPS（百万级）场景下 fio 本身的 CPU 开销更低、测得的结果更接近设备真实能力。

### 3.2 同步 / 异步 与 iodepth（队列深度）

这是整个 fio 里**最重要的一组概念**：

- **同步 I/O**：发起一个 I/O 后，必须等它完成才能发下一个。任一时刻在途 I/O 数 = 1。
- **异步 I/O**：可以一次性提交多个 I/O，不等待完成，由内核/设备慢慢完成。任一时刻在途的 I/O 数 = **iodepth（队列深度）**。
- **iodepth 只对异步引擎（libaio / io_uring）有意义**。同步引擎下 iodepth 恒为 1。
- 总在途 I/O 数 = `numjobs × iodepth`。

**为什么 SSD 需要深队列**：SSD 内部是大量 NAND 通道 + 多颗闪存芯片并行工作，队列深度太浅时设备"吃不饱"、内部并行度发挥不出来。所以 4K 随机测试通常用 iodepth 32~64。而机械盘（HDD）物理上只有一组磁头，天然串行，iodepth 大于 1 收益很小（NCQ 深度也很浅）。

> 面试题"iodepth 是什么"的满分答案：*"异步 I/O 的并发深度，即同一时刻在途的 I/O 请求数。它对应到设备层是 NVMe 的命令队列深度，决定了能否把 SSD 内部多通道并行度压满。同步 I/O 天然深度为 1。"*

### 3.3 Direct I/O（direct=1）

- 默认情况下 fio 走**页缓存（page cache）**：读会命中缓存（测的是内存速度），写会先写缓存由内核延迟刷盘（测的是内存 + 后台刷盘速度）——**测出来的根本不是设备的性能**。
- `--direct=1` 使用 `O_DIRECT` 标志：**绕过页缓存**，数据从用户态缓冲区直接 DMA 到设备。
- O_DIRECT 的要求：缓冲区地址、长度、文件偏移必须对齐（通常 512B/4K）。fio 会自动处理对齐，你不用管。
- **测设备真实性能，永远加 direct=1**。测"应用实际体验"（带缓存）的场景才去掉它，但那样测的是页缓存性能，两者必须分开报告。

### 3.4 顺序 / 随机 与块大小（bs）

- **顺序（seq）**：大块连续读写，瓶颈在设备带宽（GB/s）。
- **随机（rand）**：小块随机落点，瓶颈在每秒能处理多少条 I/O 命令（IOPS）。
- **块大小**：
  - `4K` —— 数据库/文件系统页的典型大小，**行业标准随机测试块**。数据库、虚拟化、OLTP 负载都是 4K 随机为主。
  - `64K/128K/1M` —— 大块顺序，模拟大文件读写、视频流。
- 随机分布默认均匀分布，可用 `--random_distribution=zipf:1.2` 模拟"热点数据"（少数块被频繁访问——真实负载特征，面试加分点）。

### 3.5 Job 与 numjobs

- **job**：一组完整的 I/O 参数（引擎、模式、块大小……）。一个 fio 任务可以包含多个 job（写在 job 文件里）。
- **numjobs**：把同一个 job **克隆 N 份**，各自独立线程（`--thread`）或进程并行执行，各自操作各自的目标文件（或同一文件的不同区域）。
- 为什么要多 job：单个 worker 受单核 CPU 限制（百万 IOPS 时 CPU 是瓶颈）；多 job 还能对应 NVMe 的多个硬件队列。
- `--group_reporting`：多 job 时把统计合并成一份报告（否则每个 job 单独报一份）。
- **注意**：`numjobs=4` 的 IOPS 报告是 4 个 worker 的**总和**（合并报告后），不要以为还要乘 4。

### 3.6 读写混合

- `rw=rw`：读写各 50%，顺序。
- `rw=randrw`：读写各 50%，随机。
- `--rwmixread=70`：混合模式中读占 70%（写 30%）。
- `--percentage_random=70`：混合模式中 70% 是随机、30% 是顺序。
- `rw=trim`：只发 TRIM/Discard 命令（测 SSD 回收能力，不产生数据流量）。
- `rw=trimwrite`：trim + 写混合。

### 3.7 测试时长控制

- `--size=100G`：每个文件写到 100G 为止。**容量驱动**——适合快速摸底。
- `--runtime=60 --time_based`：不管文件多大，**跑满 60 秒**。**时间驱动**——适合测稳态性能（面试时"怎么保证测得准"的答案之一）。
- `--ramp_time=10`：前 10 秒预热，不计入统计。设备刚启动时缓存状态、SSD 内部 GC 状态都没稳定。
- `--loops=3`：job 重复跑 3 轮；配合 `--stonewall` 让多个 job 串行执行（前一个完全结束才开始下一个），保证每个 job 独立、互不干扰。

### 3.8 三个时间：slat / clat / lat（★ 必考）

fio 统计每条 I/O 的三个时间：

| 名称       | 全称                 | 含义                 | 谁的性能                         |
| -------- | ------------------ | ------------------ | ---------------------------- |
| **slat** | submission latency | fio 决定发起 → 成功提交给内核 | fio 自身 + 系统调用开销（nsec~usec 级） |
| **clat** | completion latency | 提交给内核 → 完成         | **存储栈 + 设备的真实延迟（核心指标）**      |
| **lat**  | total latency      | slat + clat        | 应用感知的总延迟                     |

面试标准答案：*"slat 是提交延迟，反映测试端开销；clat 是完成延迟，反映内核存储栈和设备的真实性能，是我们要看的核心指标；lat 是两者之和，是应用真正感受到的延迟。"*

---

## 4. 常用参数速查表

### 基础参数

| 参数                        | 说明                                                            | 默认          |
| ------------------------- | ------------------------------------------------------------- | ----------- |
| `--name=xxx`              | job 名（必填，标识用）                                                 | 无           |
| `--filename=/dev/nvme0n1` | 目标设备（裸设备）或文件路径                                                | 用 name 生成文件 |
| `--directory=/mnt/test`   | 在指定目录生成测试文件                                                   | 当前目录        |
| `--size=100G`             | 每个文件的大小（K/M/G）                                                | 0（用满设备）     |
| `--runtime=60`            | 运行时长（秒）                                                       | 由 size 决定   |
| `--time_based`            | 与 runtime 配合：跑满时长而非写完 size                                    | 关           |
| `--ramp_time=10`          | 预热秒数，不计入统计                                                    | 0           |
| `--rw=read`               | I/O 模式：read/write/randread/randwrite/rw/randrw/trim/trimwrite | read        |
| `--bs=4k`                 | 块大小                                                           | 4k          |
| `--ioengine=libaio`       | I/O 引擎（§3.1）                                                  | psync       |
| `--iodepth=32`            | 队列深度（仅异步引擎）                                                   | 1           |
| `--direct=1`              | O_DIRECT，绕过页缓存                                                | 0           |
| `--numjobs=4`             | 克隆 N 个并行 worker                                               | 1           |
| `--thread`                | worker 用线程而非进程                                                | 进程          |
| `--group_reporting`       | 多 job 统计合并输出                                                  | 每个 job 单独输出 |
| `--output=result.txt`     | 结果写到文件                                                        | stdout      |
| `--output-format=json`    | 输出 JSON（§7.1）                                                 | normal      |
| `--eta`                   | 实时进度                                                          | 关           |

### 进阶参数

| 参数                                             | 说明                                      |
| ---------------------------------------------- | --------------------------------------- |
| `--rwmixread=70`                               | 混合读写中读占比 %                              |
| `--percentage_random=70`                       | 混合模式中随机占比 %                             |
| `--random_distribution=zipf:1.2`               | 随机分布：zipf（热点）/pareto/normal/gauss       |
| `--norandommap`                                | 随机 I/O 不保证遍历全 LBA（高 IOPS 场景省开销）         |
| `--randrepeat=0`                               | 每次运行用不同随机种子（多次测试取平均时用）                  |
| `--offset=1G`                                  | 只测设备从 1G 开始的区域（测 HDD 内外圈差异等）            |
| `--fsync=32`                                   | 每 32 次写后发一次 fsync（测持久化代价）               |
| `--end_fsync=1`                                | 测试结束时 fsync，确保写真正落盘再计时                  |
| `--verify=crc32c`                              | 写后读回校验数据完整性（crc32c/md5/sha256/sha1/...） |
| `--do_verify=1`                                | 只跑校验阶段（配合之前写的数据）                        |
| `--verify_fatal=1`                             | 校验失败立即停止                                |
| `--rate=200m` / `--rate_iops=1000`             | 限速（MB/s / IOPS）                         |
| `--latency_target=100 --latency_percentile=99` | 以 p99 延迟为目标自适应调整速率                      |
| `--cpus_allowed=0-3`                           | 绑 CPU 核                                 |
| `--numa_cpu_nodes=0 --numa_mem_policy=local`   | 绑 NUMA 节点                               |
| `--write_bw_log=pre / --write_lat_log=pre`     | 输出逐点带宽/延迟日志（画图用，§7.2）                   |
| `--log_avg_msec=1000`                          | 日志采样周期（ms）                              |
| `--loops=3 --stonewall`                        | 重复 3 轮 / job 间串行隔离                      |
| `--invalidate=1`                               | 开始前失效页缓存                                |
| `--buffer_compress_percentage=50`              | 写数据的可压缩比例（默认 50%，测带压缩的存储时重要）            |
| `--unlink=1`                                   | 测试完删除测试文件                               |
| `--fallocate=none`                             | 不预分配（写真实数据，测文件系统时更真实）                   |
| `--ss=iops:0.1% --ss_dur=300 --ss_interval=10` | SNIA 稳态测试（§7.4）                         |
| `--thinktime=500us`                            | 每条 I/O 之间"思考"500 微秒（模拟真实应用）             |
| `--zonemode=zbd --zonesize=2G`                 | ZNS SSD 分区测试                            |
| `--allow_mounted_write=1`                      | 允许写已挂载的设备（新版 fio 默认拒绝，慎用）               |

### Job 文件格式

参数多的时候写成 job 文件（`.fio`），可维护性和"专业感"都更好：

```ini
; ssd-suite.fio  —— 分号开头是注释
[global]
ioengine=libaio
direct=1
group_reporting=1
time_based=1
runtime=60
ramp_time=5
filename=/dev/nvme0n1

[seq-read-1m]
rw=read
bs=1m
iodepth=32

[rand-read-4k]
stonewall
rw=randread
bs=4k
iodepth=64
numjobs=4
```

运行：`sudo fio ssd-suite.fio`

---

## 5. 输出解读（★ 必练：逐块读懂一份结果）

一次 4K 随机读的典型输出（NVMe，iodepth=32，单 job）：

```
fio-3.35
Starting 1 process
randread: Laying out IO file (1 file / 10240MiB)
Jobs: 1 (f=1): [r(1)][100.0%][r=705MiB/s][r=181k IOPS][eta 00m:00s]
randread: (groupid=0, jobs=1): err= 0: pid=4242: Thu Sep 11 10:00:00 2026
  read: IOPS=181k, BW=706MiB/s (741MB/s)(82.8GiB/120001msec)
    slat (nsec): min=1312, max=48211, avg=2482.63, stdev=1117.30
    clat (usec): min=24, max=3736, avg=348.78, stdev=100.56
     lat (usec): min=26, max=3740, avg=351.26, stdev=100.56
    clat percentiles (usec):
     |  1.00th=[   44],  5.00th=[   51], 10.00th=[   55], 20.00th=[   61],
     | 30.00th=[  200], 40.00th=[  302], 50.00th=[  347], 60.00th=[  375],
     | 70.00th=[  392], 80.00th=[  404], 90.00th=[  412], 95.00th=[  424],
     | 99.00th=[  453], 99.50th=[  498], 99.90th=[  701], 99.95th=[  848],
     | 99.99th=[ 1303]
   bw (  MiB/s): min=  620, max=  725, per=100.00%, avg=705.72, stdev=18.40, samples=240
   iops        : min=158720, max=185600, avg=180664.32, stdev=4710.40, samples=240
  lat (usec)   : 50=0.05%, 100=0.48%, 250=33.21%, 500=99.47%, 750=99.95%, 1000=99.99%
  cpu          : usr=2.11%, sys=4.83%, ctx=383205, majf=0, minf=152
  IO depths    : 1=0.0%, 2=0.0%, 4=0.0%, 8=0.0%, 16=0.0%, 32=100.0%, >=64=0.0%
     submit    : 0=0.0%, 4=100.0%, 8=0.0%, 16=0.0%, 32=0.0%, 64=0.0%, >=64=0.0%
     complete  : 0=0.0%, 4=100.0%, 8=0.0%, 16=0.0%, 32=0.0%, 64=0.0%, >=64=0.0%
     issued rwt: total=21724800,0,0, short=0,0,0, dropped=0,0,0
     latency   : target=0, window=0, percentile=100.00%, depth=32
Run status group 0 (all jobs):
   READ: bw=706MiB/s (741MB/s), 706MiB/s-706MiB/s (741MB/s-741MB/s), io=82.8GiB (88.9GB), run=120001-120001msec

Disk stats (read/write):
  nvme0n1: ios=21724800/0, merge=0/0, ticks=7459991/0, in_queue=1233, util=100.00%
```

逐块解读：

| 输出块                                   | 含义                                           | 怎么用                                                              |
| ------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------- |
| `Jobs: ... [r=705MiB/s][r=181k IOPS]` | 实时进度行                                        | 观察测试是否稳定                                                         |
| `read: IOPS=..., BW=...`              | 全程平均 IOPS 与带宽                                | **对外报告的核心数字**                                                    |
| `slat (nsec)`                         | 提交延迟                                         | 应该远小于 clat；若 slat 异常大，说明 fio 侧/CPU 是瓶颈                           |
| `clat (usec)`                         | 完成延迟（min/max/avg/stdev）                      | **核心指标**，与下面 percentile 对照                                       |
| `lat (usec)`                          | 总延迟 = slat + clat                            | 应用视角                                                             |
| `clat percentiles`                    | 延迟分位数 p1~p99.99                              | 见下方说明                                                            |
| `bw` / `iops` 块                       | 逐秒采样带宽/IOPS 的 min/max/avg/stdev              | stdev 小 = 性能稳定；stdev 大 = 有波动（GC、缓存耗尽）                            |
| `lat (usec): 50=..., 250=...`         | 完成延迟累计分布直方图                                  | "99.47% 的 I/O 在 500μs 内完成"                                       |
| `cpu: usr/sys/ctx`                    | fio 占用的用户态/内核态 CPU、上下文切换                     | **判断 CPU 是否瓶颈**：sys 接近 100%/核 = CPU 先到极限                         |
| `IO depths: 32=100.0%`                | 实际达到的深度分布                                    | **验证 iodepth 真正生效**（全是 1=100% 说明异步没起作用）                          |
| `issued rwt`                          | 总共发出的 read/write/trim 数；short=读到文件尾提前结束的 I/O | 校验总量（IOPS × 时长 ≈ issued）                                         |
| `Run status group`                    | 合并汇总（group_reporting）                        | 报告用                                                              |
| `Disk stats: util=100.00%`            | **设备视角**：util=设备忙的时间占比，in_queue=平均排队请求数      | util=100% 且 IOPS 上不去 → 设备饱和；util 低但 IOPS 低 → 瓶颈在 fio/CPU/内核，不在设备 |

**percentile（分位数）怎么读**：把所有 I/O 的完成延迟从小到大排序，第 50 百分位 = 一半 I/O 比它快。例：上表中 `99.99th=[1303]` 意为 99.99% 的 I/O 在 1303μs 内完成，即**一万条 I/O 里只有一条超过 1.3ms**。

- **p50（中位数）**：典型延迟，报"延迟"时一般指它。
- **p99 / p99.9 / p99.99**：**尾延迟（tail latency）**。现代分布式系统一个请求要经过几十次存储访问，尾延迟会在整条链路上**放大**，所以 SSD 评测和面试都非常看重尾延迟。

**换算公式**：`IOPS × bs = 带宽`。例：181k × 4KiB ≈ 707MiB/s。小 bs 时瓶颈是"每秒处理命令数"（IOPS/CPU），大 bs 时瓶颈是"每秒搬多少字节"（带宽/DMA 通道）。

---

## 6. 典型场景模板（可直接运行）

> ⚠️ 所有写测试（write/randwrite/rw/trim）都会**摧毁目标上的数据**。裸设备测试前务必确认设备无数据。裸设备测试需要 root。

### 6.1 NVMe/SSD 标准四项（行业通用测试集）

```bash
# ① 顺序读
sudo fio --name=seq-read --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=read --bs=1M --iodepth=32 --numjobs=1 \
         --runtime=60 --time_based --group_reporting

# ② 顺序写
sudo fio --name=seq-write --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=write --bs=1M --iodepth=32 --numjobs=1 \
         --runtime=60 --time_based --group_reporting --end_fsync=1

# ③ 4K 随机读
sudo fio --name=rand-read-4k --filename=/dev/nvme0n1 --ioengine=io_uring --direct=1 \
         --rw=randread --bs=4k --iodepth=64 --numjobs=4 \
         --runtime=120 --time_based --group_reporting

# ④ 4K 随机写
sudo fio --name=rand-write-4k --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=randwrite --bs=4k --iodepth=64 --numjobs=4 \
         --runtime=120 --time_based --group_reporting --end_fsync=1
```

参考量级（Gen4 消费级 NVMe，仅供判断结果是否离谱）：顺序读写 3~7GB/s；4K 随机读 500K~1M IOPS、随机写 400K~800K IOPS；4K 随机读 p50 延迟约 50~100μs（Q1 单深度）、Q64 高深度下几百 μs。SATA SSD 顺序 ~550MB/s、4K 随机 ~90K IOPS。HDD 顺序 ~200MB/s、4K 随机 ~100~200 IOPS。

### 6.2 延迟专项（Q1T1：队列深度 1、单 job）

```bash
# 最小负载下的设备固有延迟（clat p50 即"设备延迟"）
sudo fio --name=lat-4k --filename=/dev/nvme0n1 --ioengine=io_uring --direct=1 \
         --rw=randread --bs=4k --iodepth=1 --numjobs=1 \
         --runtime=60 --time_based
```

### 6.3 混合读写

```bash
# 70% 读 + 30% 写的随机混合负载（数据库典型）
sudo fio --name=mix-7030 --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=randrw --rwmixread=70 --bs=4k --iodepth=32 --numjobs=4 \
         --runtime=120 --time_based --group_reporting
```

### 6.4 文件系统测试（而非裸设备）

```bash
# 在挂载点上测文件系统性能：注意测试文件要足够大（≥ 2×内存，防缓存全命中）
sudo fio --name=fs-rand-write --directory=/mnt/test --size=100G \
         --ioengine=libaio --direct=1 --rw=randwrite --bs=4k --iodepth=32 \
         --runtime=60 --time_based --group_reporting --unlink=1
```

裸设备测的是"硬件+块层"，文件系统测试多了文件系统的开销——两者都要测、分开报告，这是专业性体现。

### 6.5 HDD 注意事项

- 随机测试 **iodepth=1**（机械盘天然串行，深队列无意义还会引入排队延迟）；
- 顺序测试 bs=1M；
- HDD 外圈快、内圈慢（恒定角速度），可用 `--offset` 分段测不同区域；
- 不要对生产机械盘长时间随机写（加速老化）。

### 6.6 与你的 BF2 实验对接

你们的工作流是"用负载发生器制造压力 + 用 PMC 计数器采集"。fio 在这里的角色是**可控的负载发生器**：用上面任一模板产生确定性的 I/O 压力（例如固定 `--rate_iops=50000` 产生恒定强度负载），同时跑你们的采集程序。建议每次实验把 fio 的完整参数和 JSON 结果一并保存，论文里报告负载特征时（IOPS、块大小、读写比、队列深度）才有据可查。

---

## 7. 高级用法

### 7.1 JSON 输出 + jq 自动化

```bash
sudo fio --name=rand-read-4k --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=randread --bs=4k --iodepth=64 --numjobs=4 --runtime=60 --time_based \
         --group_reporting --output-format=json --output=result.json

# 提取关键指标
jq '.jobs[0].read | {iops: .iops, bw_mib: .bw, clat_avg_us: (.clat_ns.mean/1000),
     p50_us: (.clat_ns.percentile."50.000000"/1000),
     p99_us: (.clat_ns.percentile."99.000000"/1000),
     p9999_us: (.clat_ns.percentile."99.990000"/1000)}' result.json
```

（注意：percentile 的 key 是字符串，如 `"99.000000"`。JSON 化之后就能写脚本批量跑多组参数、汇总成表格——简历上"自动化性能测试"就来自这里。）

### 7.2 逐点日志与画图（看掉速/波动）

```bash
sudo fio --name=seq-write --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=write --bs=1M --iodepth=32 --runtime=300 --time_based \
         --write_bw_log=seq-write --write_lat_log=seq-write --log_avg_msec=1000

# 生成 seq-write_bw.1.log（带宽逐秒曲线）与 seq-write_lat.1.log（延迟曲线）
fio2gnuplot -b -g -t "seq-write" seq-write_bw.1.log   # 需要 gnuplot
```

延迟日志画出来就是延迟随时间/随 LBA 的曲线，SSD SLC 缓存耗尽、GC 触发的掉速一眼可见（这是面试里"你怎么发现掉速问题"的标准答案）。

### 7.3 数据完整性校验（verify）

```bash
# 阶段1：写 + 附带校验信息
sudo fio --name=v --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=write --bs=4k --iodepth=32 --verify=crc32c --verify_fatal=1 --size=10G

# 阶段2：读回校验
sudo fio --name=v --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=read --bs=4k --iodepth=32 --do_verify=1 --verify=crc32c --verify_fatal=1
```

写测试不仅要测速度，还要确认数据读回来是对的（存储领域叫"静默数据损坏"检测）。简历上写"完成数据完整性校验"是加分项。

### 7.4 稳态测试（SNIA PTS 规范）

SSD 性能会随使用状态变化（GC、缓存耗尽），SNIA（存储行业协会）规定了**稳态（steady state）测试方法**：

```bash
sudo fio --name=ss-test --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 \
         --rw=randwrite --bs=4k --iodepth=32 --numjobs=4 \
         --ss=iops:0.1% --ss_dur=300 --ss_interval=10 --time_based --runtime=3600
```

含义：每 10 秒采样一次 IOPS，当连续区间内 IOPS 变化率 < 0.1% 且持续 300 秒，即判定达到稳态。**知道 SNIA PTS 这个名字本身就是面试加分项**。

### 7.5 绑核 / 限速 / 其他

```bash
--cpus_allowed=0-3              # worker 只跑在 0-3 号核（避免核间迁移干扰）
--numa_cpu_nodes=0 --numa_mem_policy=local   # 绑 NUMA 节点（多路服务器测本地盘）
--rate=200m                     # 限速 200MB/s
--rate_iops=5000                # 限速 5000 IOPS（与你的 BF2 定强度负载实验直接相关）
--rw=trim                       # 测 TRIM 性能
--random_distribution=zipf:1.2  # 热点访问模式（模拟真实业务）
```

---

## 8. 测试方法论与常见陷阱（★ "真做过实验"和"背过命令"的分水岭）

1. **三层缓存**：`direct=1` 只绕过了页缓存；RAID 卡/HBA 还有自己的缓存；SSD 内部还有 DRAM/SLC 缓存。测设备真实性能要考虑全部三层。
2. **SSD SLC 缓存耗尽掉速**：消费级 SSD 顺序写前几十秒（或前几百 GB）是 SLC 缓存速度，耗尽后掉到 TLC 直写速度（常见从 3GB/s 掉到 ~500MB/s）。**短时间写测试会严重高估性能**——所以写测试至少要跑几十秒以上，并配合 §7.2 的逐点日志观察。
3. **GC 效应**：用过的盘（全盘写过随机数据）比新盘慢，因为写前要先做垃圾回收。**预置条件（preconditioning）**：正式测试前先用 `rw=write` 顺序写满全盘**至少一遍（SNIA 建议两遍容量）**，把盘打到稳态再测。
4. **CPU 瓶颈**：4K 随机百万 IOPS 时，单核 CPU 会先于设备成为瓶颈。判断方法：看输出 `cpu: sys=` 是否接近 100%/核。对策：numjobs 分散 + 绑核。**注意 NVMe 多队列**：`numjobs=1 + 高 iodepth` 只用一个硬件队列，IOPS 可能上不去；多 job 对应多队列才能打满。
5. **文件大小不够**：buffered 测试时文件必须 ≥ 2×内存，否则全命中页缓存；direct=1 时文件 ≥ 设备缓存大小即可，但为了结果稳定仍建议大文件 + 时间驱动（runtime）。
6. **数据安全（最重要）**：`randwrite/write/trim` 会摧毁目标上的所有数据。裸设备（`--filename=/dev/xxx`）前**务必确认设备无数据**；生产系统用测试文件（`--directory` + `--size`）而不是裸设备。**绝不在系统盘上做写测试**。新版 fio 对已挂载设备会拒绝写（除非 `--allow_mounted_write=1`），这是一道保护。
7. **iodepth 对同步引擎无效**：psync + iodepth=32 没有任何意义，fio 只跑深度 1。检查输出里 `IO depths` 分布验证。
8. **libaio 不配 direct=1 会退化成同步**（§3.1）——结果看起来"正常"但其实是假的。
9. **结果可重复性**：同类测试跑 3 次取中位数；不同运行之间用 `--randrepeat=0` 换随机种子。报告时注明测试条件（设备型号、固件、文件系统、参数），否则结果没有意义。
10. **设备忙时测试无意义**：测之前 `iostat -x 1` 确认设备空闲；测试中看 `Disk stats: util` 判断是否真的压到了设备。
11. **HDD 上深队列随机测试**：机械盘 iodepth>1 只会增加排队延迟，测不出真实性能，反而像在"折磨盘"。
12. **热节流（thermal throttling）**：长时间高负载测试中 SSD/整机降频导致后期掉速——所以长时间测试的 avg 会低于早期峰值，逐点日志能看出来。

---

## 9. 简历与面试（★ 自测）

### 简历写法参考

> **存储性能测试**：熟练使用 fio 进行 NVMe SSD 全项性能基准测试（顺序吞吐、4K 随机 IOPS、延迟分位数与尾延迟、SNIA 稳态测试），掌握预置条件、直写模式（O_DIRECT）、多队列并行压测等方法，通过 JSON 输出 + jq 实现批量测试自动化与结果分析。

### 高频面试题与参考答案

**Q1：怎么测一块 SSD 的 4K 随机写 IOPS？**
答：`fio --name=rand-write --filename=/dev/nvme0n1 --ioengine=libaio --direct=1 --rw=randwrite --bs=4k --iodepth=64 --numjobs=4 --runtime=120 --time_based --group_reporting`，然后讲清每个参数为什么这么设：libaio+direct 真异步、4K 是数据库页大小、深队列压满设备并行度、多 job 对应多硬件队列、时间驱动保证稳态。

**Q2：iodepth 是什么？为什么 SSD 要深队列？**
答：见 §3.2 满分答案。

**Q3：direct=1 起什么作用？**
答：O_DIRECT 绕过页缓存，数据直接 DMA 到用户缓冲区，测到的是设备真实性能；否则读命中缓存、写延迟刷盘，测的是内存性能。附带讲 O_DIRECT 的对齐要求。

**Q4：slat / clat / lat 的区别？**
答：见 §3.8。

**Q5：测顺序写为什么越测越慢？**
答：三个可能：SLC 缓存耗尽掉速、GC 触发、热节流。区分方法：看逐点带宽日志（--write_bw_log）掉速的时间点和形态。

**Q6：IOPS 和带宽是什么关系？**
答：IOPS × 块大小 = 带宽。小块的瓶颈是每秒命令处理能力（IOPS，受 CPU 和设备命令队列限制），大块的瓶颈是通道带宽（GB/s）。所以 4K 随机看 IOPS，1M 顺序看带宽。

**Q7：尾延迟（p99.9）为什么重要？**
答：大规模系统一次请求需要成百上千次存储 I/O，单条 I/O 的尾延迟在整个请求链路上被放大，用户感知的延迟由最慢的那几条 I/O 决定。

**Q8：怎么设计一套完整的 SSD 验收测试？**
答：① 预置条件（写满 1~2 倍容量）；② 顺序读写（1M）；③ 4K 随机读写（多队列深度）；④ Q1T1 延迟；⑤ 混合读写；⑥ 稳态测试（SNIA）；⑦ 数据完整性校验（verify）；⑧ 报告含参数、环境、多次取中位的结果。

---

## 10. 参考资源

- fio HOWTO（官方最全文档）：https://github.com/axboe/fio/blob/master/HOWTO.rst
- `man fio`
- SNIA Solid State Storage Performance Test Specification（稳态测试规范）
- Brendan Gregg《Systems Performance》存储章节（性能分析方法论）
- 你的实验环境：BF2（Ubuntu）上 `apt install fio` 直接可用，配合 PMC 计数器采集做负载发生器
