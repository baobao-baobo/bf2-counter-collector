# BlueField-2 计数器分类表（bf2-counter-collector）

## 修订记录

| 日期         | 说明                                            |
| ---------- | --------------------------------------------- |
| 2026-08-06 | 初版，按独立采集程序记录                                  |
| 2026-08-13 | 修订为 collect_all.c 与 unified bf2-collector 对照表 |
| 2026-08-14 | 精简为 collect_all.c 专用：计数器分类总表，与采集工具配套          |
| 2026-08-14 | 工具改为配置文件驱动：采集范围与频率由 `configs/default.conf` 等 INI 配置 |
| 2026-09-10 | 新增 L3 轮换模式（`[l3cache] groupN`）与 `configs/paper52.conf`：精确采集论文 52 计数器 |

依据文档：NVIDIA BlueField-2 Performance Monitoring Counters v4.15.0 (GenBF2+)。事件编码已从源码硬编码迁移至事件目录 `code/catalog.c`（`--list-events` 可查看全量）。

## 1 采集工具 collect_all

实现代码：`code/`（collect_all.c + config.c + catalog.c）。单进程整合采集器，每 tick 输出一行 CSV，覆盖全部关注硬件块。**采集哪些 counter、以什么频率采集，全部由 INI 配置文件决定**（`-c configs/default.conf`；不带 `-c` 使用内置默认，即下文的已验证基线）。tile HNF 每 tile 仅 4 个计数器槽，采用多组事件轮换（时分复用）；l3cache 每 half 4 个槽，默认平面模式（≤4 事件持续采集），配置 `groupN` 时同样进入轮换模式（与 tile 同规则）；其余硬件块持续采集。

- 构建（设备本机）：`make`；交叉编译：`make CROSS=aarch64-linux-gnu-`
- 运行：`sudo ./collect_all -c configs/default.conf [-i 秒] [-d 秒] [-o 文件] [-q]`
- 信息命令（无需 root、不碰硬件）：
  - `./collect_all --list-events`：打印全量事件目录（编码 + 默认列名）
  - `./collect_all --dump-config`：打印默认配置模板（与 configs/default.conf 一致）
  - `./collect_all -c FILE --check-config`：校验 FILE 并打印解析结果，exit 0/1
- 输出：宽表 CSV，每 tick 一行；实测设备 42 列（列数随发现的硬件块数量变化）。默认配置完整列清单：

```
timestamp,tile_group,
tile_a72_access,tile_mem_reads,tile_mem_writes,tile_mss_nocredit,
tile_dir_hit,tile_allocate,tile_victim_write,
tilenet_cdn_req,tilenet_ddn_req,tilenet_ndn_req,
trio_dma_beats,trio_rt_af,trio_pbuf_af,trio_wrq_empty,
smmu_tbu_miss,smmu_tx_dat_af,smmu_rx_dat_af,
triogen_tx_dat_af,triogen_rx_dat_af,
l3half0_hits,l3half0_misses,l3half1_hits,l3half1_misses,
pcie0_rx_bytes,pcie0_tx_bytes,pcie1_rx_bytes,pcie1_tx_bytes,
l1d_access,l1d_miss,l1i_access,l1i_miss,
cpu_util_pct,cpu_min_pct,cpu_max_pct,cpu_avg_pct,
mem_total_kb,mem_used_kb,mem_util_pct,
net_rx_bytes,net_tx_bytes
```

其中 `timestamp`（秒级 Unix 时间戳）与 `tile_group`（0/1，标记该行 tile 计数器所属轮换组）为元数据列，非计数器。

### 1.1 配置要点

- `[global] interval`：基础 tick（秒），每 tick 一行；`duration=0` 永久。
- 各块 `interval` 必须为 global 的整数倍（0/缺省 = 继承）；未采样 tick 该块列留空（pandas 读为 NaN），每段窗口（含首个）恰为 k 个 tick。
- `[tile] groupN`：轮换组，每组 ≤4 事件且组间等长；首个 `groupN` 键清空默认组。
- `[l3cache] groupN`：与 tile 同规则的轮换模式（组连续、等长、每组 ≤4）；轮换模式下所有 `_BANK0/_BANK1` 事件对合并成每 half 一列（如 `total_cdn_req_in`），且 bank 对必须编在同一组（resolve 校验）；输出另加 `l3_group` 标记列。论文 52 计数器完整配置见 `configs/paper52.conf`（tile 6 组 + l3cache 8 组）。
- 各块首个 `events=`/`registers=` 键清空该块默认列表；未出现则保持默认。
- L3 同时选 HITS_BANK0+BANK1 → 合并列 `l3half{i}_hits`（MISSES 同理）；PCIe 同选 IN_P/NP/C_BYTE_CNT → 合并 `pcie{i}_rx_bytes`（OUT_ 同理），PKT 寄存器永不合并且逐寄存器列。
- `[gic]` 默认禁用：SMGEN 事件与 SMMU 共用，gic 目录尚未实机验证，确认 `gicN` 命名后再启用。
- 完整 schema 与全部可用事件见 `configs/default.conf` 注释与 `--list-events`。

## 2 计数器分类总表

按资源类别分类。验证状态说明：已验证 = 2026-08-13 实机 60 s 采集通过；待负载验证 = 无负载段为 0 属预期，尚未在负载下证明功能正常。

| 分类    | 硬件块                | 事件编码        | 事件名称                   | 含义                                      | 采集机制           | CSV 字段                                                 | 时间覆盖 | 验证    |
| ----- | ------------------ | ----------- | ---------------------- | --------------------------------------- | -------------- | ------------------------------------------------------ | ---- | ----- |
| 计算    | CPU（/proc/stat）    | —           | —                      | 全核与逐核利用率                                | 软件             | cpu_util_pct / cpu_min_pct / cpu_max_pct / cpu_avg_pct | 100% | 已验证   |
| 计算    | Cortex-A72 PMU     | 0x40        | L1D_ACCESS             | L1 D-cache 读访问次数                        | ARM PMU        | l1d_access                                             | 100% | 已验证   |
| 计算    | Cortex-A72 PMU     | 0x42        | L1D_MISS               | L1 D-cache 读未命中次数                       | ARM PMU        | l1d_miss                                               | 100% | 已验证   |
| 计算    | Cortex-A72 PMU     | 0x14        | L1I_ACCESS             | L1 I-cache 读访问次数                        | ARM PMU        | l1i_access                                             | 100% | 已验证   |
| 计算    | Cortex-A72 PMU     | 0x01        | L1I_MISS               | L1 I-cache 读未命中次数                       | ARM PMU        | l1i_miss                                               | 100% | 已验证   |
| L2 缓存 | Tile HNF           | 0x5d        | A72_ACCESS             | A72 集群访问请求总量，两组轮换共有基准                   | 机制一            | tile_a72_access                                        | 100% | 已验证   |
| L2 缓存 | Tile HNF           | 0x61        | DIR_HIT                | 目录命中次数                                  | 机制一            | tile_dir_hit                                           | 50%  | 已验证   |
| L2 缓存 | Tile HNF           | 0x6f        | ALLOCATE               | 目录项分配次数（近似未命中）                          | 机制一            | tile_allocate                                          | 50%  | 已验证   |
| L2 缓存 | Tile HNF           | 0x4e        | VICTIM_WRITE           | 脏牺牲行写回次数                                | 机制一            | tile_victim_write                                      | 50%  | 已验证   |
| 内存    | Tile HNF           | 0x4c        | MEMORY_READS           | 发往 MSS 的读请求数                            | 机制一            | tile_mem_reads                                         | 50%  | 已验证   |
| 内存    | Tile HNF           | 0x4d        | MEMORY_WRITES          | 发往 MSS 的写请求数                            | 机制一            | tile_mem_writes                                        | 50%  | 已验证   |
| 内存    | Tile HNF           | 0x67        | MSS_NO_CREDIT          | 信用不足无法发往 MSS 的周期数（内存控制器背压）              | 机制一            | tile_mss_nocredit                                      | 50%  | 已验证   |
| 内存    | L3 Cache           | 0x17 + 0x18 | HITS_BANK0/1           | 两 bank 命中次数之和                           | 机制一（enable 门控） | l3half{i}_hits                                         | 100% | 已验证   |
| 内存    | L3 Cache           | 0x19 + 0x1a | MISSES_BANK0/1         | 两 bank 未命中次数之和（触发 EMEM 访问）              | 机制一（enable 门控） | l3half{i}_misses                                       | 100% | 已验证   |
| 内存    | 系统（/proc/meminfo）  | —           | —                      | 内存总量、已用、利用率                             | 软件             | mem_total_kb / mem_used_kb / mem_util_pct              | 100% | 已验证   |
| 互联    | tilenet            | 0x12        | CDN_REQ                | 控制数据网络请求（缓存一致性流量）                       | 机制一            | tilenet_cdn_req                                        | 100% | 待负载验证 |
| 互联    | tilenet            | 0x13        | DDN_REQ                | 数据数据网络请求（I/O 数据搬运）                      | 机制一            | tilenet_ddn_req                                        | 100% | 待负载验证 |
| 互联    | tilenet            | 0x14        | NDN_REQ                | 非缓存数据网络请求（MMIO 与驱动访问）                   | 机制一            | tilenet_ndn_req                                        | 100% | 待负载验证 |
| I/O   | TRIO               | 0xa1        | TDMA_DATA_BEAT         | Arm 内存至 PCIe 的 DMA 数据拍（每拍 32 字节）        | 机制一            | trio_dma_beats                                         | 100% | 待负载验证 |
| I/O   | TRIO               | 0xa8        | TDMA_RT_AF             | PCI DMA 读请求在途队列接近满                      | 机制一            | trio_rt_af                                             | 100% | 待负载验证 |
| I/O   | TRIO               | 0xa9        | TDMA_PBUF_MAC_AF       | Arm 内存读缓冲过满，等待 PCIe 访问                  | 机制一            | trio_pbuf_af                                           | 100% | 待负载验证 |
| I/O   | TRIO               | 0xaa        | TRIO_MAP_WRQ_BUF_EMPTY | PCIe 写事务缓冲为空                            | 机制一            | trio_wrq_empty                                         | 100% | 待确认   |
| I/O   | SMMU               | 0x0e        | TBU_MISS               | TBU 未命中次数（IOMMU 页表遍历开销）                 | 机制一            | smmu_tbu_miss                                          | 100% | 待负载验证 |
| I/O   | SMMU               | 0x0f        | TX_DAT_AF              | Mesh 写数据通道 FIFO 接近满（TRIO 至 Arm 内存方向）    | 机制一            | smmu_tx_dat_af                                         | 100% | 待负载验证 |
| I/O   | SMMU               | 0x10        | RX_DAT_AF              | Mesh 读数据通道 FIFO 接近满（Arm 内存至 TRIO 方向）    | 机制一            | smmu_rx_dat_af                                         | 100% | 待负载验证 |
| I/O   | triogen0           | 0x0f        | TX_DAT_AF              | TRIO0 至 Arm 内存的 Mesh 写拥塞                | 机制一            | triogen_tx_dat_af                                      | 100% | 待负载验证 |
| I/O   | triogen1           | 0x10        | RX_DAT_AF              | Arm 内存至 TRIO1 的 Mesh 读拥塞                | 机制一            | triogen_rx_dat_af                                      | 100% | 待负载验证 |
| I/O   | PCIe TLR           | —           | IN/OUT P/NP/C 字节寄存器    | 入站/出站 posted、non-posted、completion 字节之和 | 机制二            | pcie{i}_rx_bytes / pcie{i}_tx_bytes                    | 100% | 已验证   |
| 网络    | 系统（/sys/class/net） | —           | —                      | 各非回环接口收发字节之和                            | 软件             | net_rx_bytes / net_tx_bytes                            | 100% | 已验证   |

## 3 采集机制

| 机制          | 接口                                      | 计数方式                          | 适用硬件块                                 |
| ----------- | --------------------------------------- | ----------------------------- | ------------------------------------- |
| 机制一（可编程计数器） | bfperf hwmon 的 eventN / counterN 文件     | 写事件编码复位并开始计数，周期读后差分           | tile HNF、tilenet、TRIO、SMMU、triogen、L3 |
| 机制二（统计寄存器）  | bfperf hwmon 的具名寄存器文件                   | 硬件自启动累计，程序启动读一次基线后差分          | PCIe TLR                              |
| ARM PMU     | perf_event_open 系统调用                    | 每核独立计数，内核复用（multiplexing）自动校正 | Cortex-A72 L1 缓存                      |
| 软件          | /proc/stat、/proc/meminfo、/sys/class/net | 周期读后差分                        | CPU、内存、网络                             |

## 4 数据说明

- 计数均为单调累计值，窗口增量按差分法计算，即 delta = cur − prev；首次采样仅作基线。
- 机制一写入 eventN 会复位该计数器，重编程后的第一次读取作基线丢弃。
- 同一硬件块多实例（tile、tilenet、trio、L3 half、PCIe 块、CPU 核）求和后输出一行。
- Tile HNF 轮换组（默认）：G0 = A72_ACCESS + MEMORY_READS + MEMORY_WRITES + MSS_NO_CREDIT；G1 = A72_ACCESS + DIR_HIT + ALLOCATE + VICTIM_WRITE。A72_ACCESS 编入两组（覆盖 100%），其余 6 事件覆盖 50%。tile_group 列标记当前行所属组。
- 未覆盖事件字段留空（读取为 NaN），不以 0 占位，因 0 为真实计数值（例如 mss_nocredit=0 表示无背压）。
- L3 enable 门控：enable=1 复位并启动全部计数器，enable=0 冻结；冻结后的读数即为窗口增量，无需差分。L3 窗口与 tile 轮换窗口对齐。无 enable 文件的 half 自动退化为差分模式。
- L3 轮换模式（groupN 配置）：冻结 → 读当前组 → **冻结期间**编程下一组 → enable 重启；每个窗口恰覆盖一个组，完整轮换周期 = 组数 × tick。合并列（bank 对）在当前窗口有值，其余组列留空（NaN），`l3_group` 列标记当前组。
- 后处理建议：pandas resample('60s').mean()（skipna）自动得到覆盖加权均值。

## 5 验证状态

| 项                            | 状态    | 说明                                                   |
| ---------------------------- | ----- | ---------------------------------------------------- |
| 整体格式                         | 已验证   | 2026-08-13 实机 60 s：首行无假 0、NaN 分布正确、轮换交替、42 列对齐、时间戳连续 |
| tile / L3 / L1 / PCIe / 软件指标 | 已验证   | 速率与 8/10 历史测试交叉一致                                    |
| trio / tilenet / triogen     | 待负载验证 | 无负载段为 0 属预期；需在 DMA 或网络负载下复测                          |
| trio_wrq_empty 语义            | 待确认   | 无 DMA 负载下实测恒为 0，与官方文档"缓冲为空"的语义矛盾                     |
| 配置驱动改造                      | 待设备验收 | 单元测试 170/170 + 交叉编译零警告已通过；设备验收清单见 acceptance-checklist.md |
