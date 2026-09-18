# bench/ — BF2 计数器基准测试套件（离线部署）

与论文 52 计数器配套的 benchmark 套件。所有二进制均为 **静态链接的
aarch64 ELF**，在 WSL 中用 `aarch64-linux-gnu-gcc` 交叉编译，不依赖
设备上的任何动态库版本，可直接拷贝运行。

## 目录结构

```
bench/
├── bin/                  # arm64 静态二进制（6 个，共 ~12 MB）
│   ├── stress-ng          # V0.22.00，CPU/cache 压力
│   ├── stream             # STREAM 带宽测试（OpenMP 8 线程）
│   ├── mbw                # v2.0，memcpy/dumb 带宽模式
│   ├── memrand            # 自研：随机访问指针追逐（替代 sysbench）
│   ├── fio                # 3.42，存储 I/O
│   └── iperf3             # 3.21，网络吞吐
├── configs/               # 采集配置（bench_p*.conf，分场景定向）
├── run_bench.sh           # 设备端一键运行脚本
├── src/                   # 源码（设备上有 gcc 也可原生重编）
└── README.md
```

## 部署到设备

1. 把整个 `bench/` 目录拷到设备（如 `/root/bench/`）；
2. 把设备上编译好的 `collect_all` 二进制放进 `bench/`；
3. `chmod +x run_bench.sh bin/*`；
4. p7（iperf3）需先编辑 run_bench.sh 里的 `IPERF_SERVER` 并确保对端
   （x86 主机/另一台机器）跑 `iperf3 -s`；对端也需要一份本套件里的
   iperf3（静态 aarch64）或自装。

## v1 横轴试运行（b1–b4，固定计数器组）

固定一组 tile 计数器（A72_ACCESS, MEMORY_READS, MEMORY_WRITES,
HNF_REQUESTS；单组无轮换，**每秒全采**），用 **4 个不同的 bench**
各跑 3 次（run 1..3）：

```bash
sudo ./run_bench.sh b1 1   # stress-ng --cpu 8（计算）
sudo ./run_bench.sh b2 1   # STREAM（混合顺序带宽）
sudo ./run_bench.sh b3 1   # memrand 1GB（随机访存）
sudo ./run_bench.sh b4 1   # stress-ng --cache 8（cache 抖动）
```

所有 b1–b4 共用 `configs/bench_v1_axis.conf`，产出
`results/b<N>_run<R>.csv`。

**第一张图（数据回传后本地画）**：x 轴 = b1..b4（4 根柱），
y 轴 = A72_ACCESS 每秒均值（裁首尾各 5 行后 `mean()`，3 次取
中位）。同批采集的 MEMORY_READS/WRITES、HNF_REQUESTS 留作后续
读写对比，不再重新跑。

## 运行矩阵

每个 bench 跑 **3 次**（run 1..3），后处理取中位数：

```bash
sudo ./run_bench.sh p1 1   # stress-ng --cpu 8（CPU 计算压力）
sudo ./run_bench.sh p3 1   # STREAM（内存顺序带宽）
sudo ./run_bench.sh p4 1   # memrand（随机访存，1GB 工作集）
sudo ./run_bench.sh p5 1   # stress-ng --cache 8（cache 抖动）
sudo ./run_bench.sh p6 1   # fio 顺序读（I/O 路径）
sudo ./run_bench.sh p7 1   # iperf3（网络，可选）
```

每次运行产出：
- `results/<id>_run<N>.csv` —— 采集数据（70 s ≈ 70 行）
- `results/<id>_run<N>_<bench>.txt` —— bench 自身输出（带宽/延迟数）

脚本时序：采集 70 s → 第 5 s 起跑 bench（60 s）→ 5 s 收尾。
**后处理裁掉 CSV 首尾各 5 行**，只留 bench 稳定段。

## 各 bench 与计数器对照

| 方案 | bench | 主要观察计数器（52 内） | 采集配置 |
|---|---|---|---|
| P1 | stress-ng --cpu | A72_ACCESS/READ/WRITE、HNF_REQUESTS、REQ_BUF_EMPTY、L3 请求管线 | bench_p1_cpu.conf |
| P3 | STREAM | MEMORY_READS/WRITES、POC_*、MSS_NO_CREDIT、L3 EMEM_REQ/MISSES/EVICTIONS | bench_p3_stream.conf |
| P4 | memrand 1GB | DIR_HIT、ALLOCATE、VICTIM*、L3 HITS/MISSES/ALLOCATIONS/EVICTIONS | bench_p4_memrand.conf |
| P5 | stress-ng --cache | 同 P4 | bench_p5_cache.conf |
| P6 | fio | IO_ACCESS/READS/WRITE、TSO_WRITE、RNF_REQUESTS + tilenet/trio/pcie（DMA 路径） | bench_p6_fio.conf |
| P7 | iperf3 | A72_*、IO_*、net_rx/tx_bytes（软件） | bench_p7_net.conf |

所有 bench 配置均为 2 组轮换（周期 2 s），每个计数器每 2 s 一个
样本，60 s 内 ~30 样本。

## memrand 用法（局部性实验）

```bash
bin/memrand -s 1024 -b 64 -d 60   # 1GB 工作集 >> LLC，近乎全 miss（默认方案）
bin/memrand -s 4    -b 64 -d 60   # 4MB 工作集，可装进 L3，局部性保留
bin/memrand -s 1024 -b 4096 -d 60 # 4KB 步长：跨页走，压 TLB
bin/memrand -s 512  -b 64 -d 30 -w  # 写模式（stride >= 16 才可用）
```

`sink` 行为保证不会被打乱优化；输出两行摘要（GB/s 与 ns/access）。

> ⚠️ **2026-09-17 写模式段错误已修**：旧版把 stamp 写进块首 8 字节，
> 覆盖了链表指针，第二次访问同一块时跳垃圾地址崩溃（`-w` 此前从未
> 上过设备，探针实验首秀暴露）。修复 = stamp 改写到偏移 8、写模式
> 要求 stride >= 16（源码已入库 bench/src/memrand.c；2026-09-18
> 设备原地 `gcc -O2 -static` 重建、写模式冒烟 31.8M 次/10s 通过，
> 新二进制 2604b059 已入库 bench/bin/memrand）。部署后 10 秒冒烟验证：
>
> ```bash
> taskset -c 0 bin/memrand -s 64 -b 64 -d 10 -w   # 应打印统计，不应段错误
> ```

## 后处理与出图（本地）

1. `tools/check_csv.py` 验证 CSV 节奏（`--period 2`）；
2. pandas：读 CSV → 裁首尾 5 行 → `resample('10s').mean()`（skipna）
   → 3 次运行取中位 → 每个 bench 一行汇总；
3. 画图（仿 PathFinder Figure 2 的视觉语言；分场景定向采集下，跨 bench
   对比只对多配置覆盖的计数器有效）：
   - **图 A（仿 2a/2d）**：A72_ACCESS 跨 P1–P7 归一化分组柱——唯一在
     每个 bench 配置中都采集的锚点计数器，横轴 = bench；
   - **图 B（仿 2c/2f）**：L3 HITS/MISSES/EVICTIONS 事件分解堆叠柱，
     P4/P5 同配置直接对比（随机访存 vs cache 抖动），P6 的 HITS/MISSES
     可并入；
   - **图 C（仿 2b/2e）**：MEMORY_READS+WRITES 带宽速率与 L2/L3 命中率，
     P3/P6 对比（顺序带宽 vs I/O 路径）；
   - **图 D**：各场景面板图——每个 bench 用自己配置内的计数器出
     1–2 张时间序列/事件分解图（背压类 MSS_NO_CREDIT 只在 P3、
     REQ_BUF_EMPTY 只在 P1 内出图）。

## 备注

- STREAM 默认数组 160MB（4 个 kernel × NTIMES=10），单次运行 2–5 s；
  run_bench.sh 循环 15 次保证覆盖 60 s 窗口，带宽值看 stream.txt。
  需要更长单次运行可在设备上用 `gcc -O3 -fopenmp -DSTREAM_ARRAY_SIZE=100000000`
  重编（src/stream.c）。
- fio 默认写 /tmp（tmpfs，测的是内存+I/O 软件路径）；设备若有 NVMe，
  把 run_bench.sh 里 `--filename` 指向 `/dev/nvme0n1` 以压 DMA/PCIe 路径。
- stress-ng 静态版裁剪了部分探测不到的特性（libaio/libcrypt 等），
  `--cpu/--cache/--vm/--matrix/--memrate` 核心压力源均可用；
  `stress-ng --help` 可查全部。
- sysbench 因离线交叉工具链无法跑 autotools + LuaJIT 而未包含，
  其 CPU 场景由 stress-ng --cpu 覆盖、随机访存场景由 memrand 覆盖
  （memrand 可控工作集/步长，更适合论文的局部性实验）。
