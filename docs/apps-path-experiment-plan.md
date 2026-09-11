# 真实应用路径实验方案（PathFinder 风格）

> 状态：v0.1（2026-09-11 初稿，待用户审核应用顺序）
> 背景：E0 已闭环——TRIO↔端口映射表定稿、五条路径观测点全部实锤（e0-analysis.md）。
> 本方案接续 path-counter-experiment-plan.md 的 B/M 阶段，但**负载从本地合成工具换成
> 真实应用**（对标 PathFinder §5.1 的 77 应用谱：SPEC CPU2017 / PARSEC / SPLASH-2x /
> GAP / Redis+YCSB）。

## 1. 目标

用真实应用跑相位式采集，绘 **"应用 × 路径" 流量行为对比图**（PathFinder Fig 2 同构：
横轴 = 应用，系列 = 路径 CR/IH/IB/NAD/WB），支撑论文"路径流量分析"节：不同负载类型在
五条数据路径上的流量构成与行为差异。

## 2. 应用组（G1–G7）

| 组 | 应用 | 对标 PathFinder | 主导路径 | 关键观测 | 可行性/风险 |
|---|---|---|---|---|---|
| G1 | xz 压缩/解压（SPEC int 真身用例） | SPEC CPU2017 | CR + WB | A72_ACCESS、HNF_REQUESTS、L3 HITS/MISSES/EVICTIONS、MEMORY_*、VICTIM_WRITE | 低（autotools 静态） |
| G2 | GAPBS bfs/pr/cc（kron 图） | GAP | CR 随机访存（指针追逐）、LLC 高 miss | A72_ACCESS、HNF_*、L3 MISS/EVICT、MSS_NO_CREDIT | 中（cmake 交叉静态） |
| G3 | Redis server（Arm）+ 客户端（fujian，经 56.x 管道） | Redis+YCSB | NAD + CR + IH | pcie0/pcie1 字节、net、A72_ACCESS、IO_ACCESS | 低-中（redis make MALLOC=libc 静态；客户端 fujian 装 redis-tools） |
| G4 | SQLite 在 eMMC 真实读写（CLI + 负载脚本） | 存储类（PathFinder 无、自增） | IB/IH + CR | IO_ACCESS（E0-3 定标 735K/s 量级）、MEMORY_*、A72_ACCESS | 低（amalgamation 单文件静态） |
| G5 | PARSEC blackscholes（多线程纯计算） | PARSEC | 多核对称 CR | 各 tile A72_ACCESS、HNF、L3 | 低-中（pthread 小依赖） |
| G6 | TFLite 推理（MobileNet 级模型） | 现代负载补充 | CR + 连续内存流 | A72_ACCESS、MEMORY_READS/WRITES、L3 | 中-高（prebuilt 需 glibc ≥2.31 兼容；不行则弃 G6 或换方案） |
| G7 | 混合：Redis + SQLite 并发（含 RDB 持久化） | Case 4 干扰类 | 多路径并发 + 守恒校验 | 全量 | 依赖 G3/G4 二进制 |

NHD 不入表：Arm 应用不产生主机直通流量；E0-1 的 33 Gbps 数据作对照组。

## 3. 统一采集方案

- **配置**：`configs/app_full.conf` = paper52 全块轮换 + [pcie] TLR 字节 + [net] 软件
  计数；[trio] 禁用（E0-2 裁决：TDMA 计数器不计数网卡 DMA）。
- **相位模板**：5 s idle + 24 s app（+24 s 二相位）+ 5 s idle；每应用 3 次取中位；
  应用 taskset 对称绑核；tile 轮换 6 s / L3 轮换 8 s → 每相位每组 4 个 tile 样本、
  3 个 L3 样本。
- **归因管线**：run_phase.sh（相位计时起停）→ CSV → tools/split_path.py（相位差分 +
  入口比例 + 守恒校验：入口 = 命中 + 流出）→ 每路径流量向量。
- **路径 → 计数器映射**（app_full.conf 已全覆盖）：

  | 路径 | 入口/观测点 | 依据 |
  |---|---|---|
  | CR | A72_ACCESS/A72_READ/A72_WRITE、RNF_REQUESTS、HNF_REQUESTS、MEMORY_READS/WRITES | RN-F 发起 |
  | IH | IO_ACCESS/IO_READS/IO_WRITE/TSO_WRITE → HNF → MEMORY_* | RN-I 发起 |
  | IB | MEMORY_READS_BYPASS（专用）+ MSS 总量 − HNF 量守恒推断 | E0-3 + 守恒 |
  | WB | VICTIM_WRITE、L3 EVICTIONS | 脏行写回 |
  | NAD | pcie0/pcie1 入/出字节（TLR）、net_rx/tx | E0-2 实锤 |
  | 回压 | REQ_BUF_EMPTY、MSS_NO_CREDIT | 饱和证据 |

- **出图**：
  - 图1 **应用 × 路径流量柱**（主图，同构 PathFinder Fig 2）。注意跨路径单位口径：
    CR/IH/IB/WB 用事件/s（同单位可比），NAD 用字节/s（单独面板，或按基线归一化倍数
    并入同一图）。
  - 图2 每应用 L3 行为堆叠（HITS/MISSES/ALLOCATIONS/EVICTIONS，分 bank 合并）。
  - 图3 守恒校验面板（逐跳 流入 vs 命中+流出）。

## 4. 执行顺序（一个一个来）

1. **工具链**（本地，不动设备）：run_phase.sh + tools/split_path.py
   （path-counter-experiment-plan.md §8 里程碑 4，现激活）。
2. **G1 xz**——纯设备侧、零外部依赖、CR 主导路径单纯 → 校准工具链与归因管线。
3. **G3 Redis**——引入 NAD 与双机（fujian 客户端经 56.x 管道），PathFinder 同款 KVS。
4. G2 GAPBS → G4 SQLite → G5 blackscholes → G6 TFLite → G7 混合（Redis+SQLite 并发）。
- 每完成一组，向图1 添加一根 x 轴条目；G1–G7 全部完成后即得论文主图。
- 二进制准备与实验流水线并行：先备 G1（xz）+ G3（redis-server/redis-benchmark）静态
  二进制，其余按序。

## 5. 二进制准备（离线静态交叉编译，同 bench 套件流程）

| 应用 | 源码获取 | 静态编译要点 | 风险 |
|---|---|---|---|
| xz | tukaani.org（代理） | ./configure --host=aarch64-linux-gnu LDFLAGS=-static | 低 |
| redis | github releases（代理） | make MALLOC=libc LDFLAGS="-static" | 低-中（jemalloc 改 libc） |
| GAPBS | github（代理） | cmake toolchain + 静态 libstdc++ | 中（cmake 交叉） |
| sqlite3 | sqlite.org amalgamation | gcc -static 单文件 | 低 |
| blackscholes | PARSEC 源码 | pthread 直编 | 低-中 |
| TFLite | github prebuilt（代理） | 预编译 .so 需 glibc ≥2.31 兼容 | 中-高 |

- 设备 glibc 2.31：全部全静态最稳；fujian 侧 redis 客户端用
  `NEEDRESTART_MODE=l apt install redis-tools` 或 fujian 原生编译（防 needrestart
  重启 rshim，E0 教训）。

## 6. 注意

- GAPBS 图规模按 A72 性能调（kron 2^18–2^20），保证 24 s 相位内完成多次迭代。
- split_path.py 归因前提：应用独占相位窗口（与 PathFinder 快照绑定流一致）。
- 每个应用正式跑前先 10 s 试跑：确认稳态、无 swap、CSV 表头与 app_full.conf 一致。
