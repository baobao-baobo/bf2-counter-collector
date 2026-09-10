# bf2-counter-collector 总体架构文档

> 面向想深入理解本项目的学习者。本文介绍项目的目标、整体架构、各模块职责、
> 运行时数据流与关键设计决策；逐函数/逐行的代码级讲解见
> [code-walkthrough.md](code-walkthrough.md)，事件编码总表见
> [collected-events.md](collected-events.md)。

---

## 1. 项目定位

**一句话：** 在 NVIDIA BlueField-2 DPU（SmartNIC）上，按 INI 配置文件指定的
计数器（counter）与采样频率，持续采集硬件/软件性能指标并输出 CSV 的工具。

**它解决什么问题：**

- 研究目标是用深度学习时序模型预测 BF2 各资源的负载（30 个过去时间步预测
  5 个未来时间步，1 分钟粒度），训练数据需要**多资源、细粒度（1 秒级）、
  长时运行**的指标采集。
- BF2 上有上百个硬件计数器（tile HNF、tilenet、TRIO、SMMU、L3 cache、
  PCIe TLR、ARM PMU……），但此前各个采集程序是独立的小工具，事件编码硬编码
  在源码里，改采集范围必须重新编译。
- 师兄验收后提出的要求：**做成配置文件的形式——配置采集哪些 counter、
  配置采样频率，然后运行采集**，并配套 GitHub 仓库。
  本项目（`bf2-counter-collector`）即是对这一要求的完整实现。

**一条硬性验收标准（parity gate）：** 内置默认配置必须逐字节复现此前在
BF2 实机上验证过的 42 列 CSV 表头（`all_test.txt` 第 2 行）。
代码单元测试中有一条专门的断言来保证这一点（见 §7）。

---

## 2. 硬件背景速览（读懂本项目所需的最小知识）

### 2.1 计数器的四个来源

| 来源 | 机制 | 接口 | 本项目中的块 |
|---|---|---|---|
| mlxbf-pmc **机制 1**（可编程计数器） | 向 sysfs 的 `eventN` 写事件编码（hex）→ 计数器**清零并开始**统计该事件；读 `counterN` 得到累计值 | `/sys/class/hwmon/hwmonX/`（name=`bfperf`）下的 `tileN/ tilenetN/ trioN/ smmuN/ triogenN/ l3cachehalfN/ gicN` 目录 | tile、tilenet、trio、smmu、triogen、l3cache、gic |
| PCIe TLR **机制 2**（只读统计寄存器） | 寄存器自开机起持续累计、**从不复位**；只能读，无法清零 | `pcieN/` 目录下的命名文件（如 `IN_P_BYTE_CNT`） | pcie |
| **ARM PMU**（CPU 硬件事件） | `perf_event_open()` 系统调用，每核一组 fd | 内核 perf 子系统 | l1（L1D/L1I 访问与缺失） |
| **软件指标** | 读 proc/sys 虚拟文件 | `/proc/stat`、`/proc/meminfo`、`/sys/class/net` | cpu、mem、net |

### 2.2 三个必须知道的硬件约束

1. **tile 只有 4 个计数器槽**，但想要的事件有 22 个 → 只能**分时轮换**
   （time-division multiplexing）：把事件分成若干"组"（每组 ≤4 个），
   各组交替占用这 4 个槽。
2. **tilenet 实机只验证过 3 个槽**（`event0`–`event2`），所以 catalog 里
   该块的 `max_slots=3`，配置超过 3 个事件会报错。
3. **L3 cache 计数器有 `enable` 门控文件**：写 1 → 计数器清零并开始计数；
   写 0 → 冻结。这天然支持"整窗口统计"（见 §4.2）。

事件编码与语义的完整清单（167 个目录条目、中文分类表）见
`docs/collected-events.md`。

### 2.3 工程环境约束

- BF2 设备是 aarch64 Ubuntu 20.04，gcc 9.4，glibc 2.31；
- **源码必须是纯 ASCII**（设备 gcc 不支持 UTF-8 源文件）——所有 .c/.h 注释
  都用英文书写；
- 设备上**不允许引入任何第三方库**——项目只用 libc（含 `-lm -lrt`）；
- 交叉编译（x86 主机 + `aarch64-linux-gnu-gcc`）只用于**验证代码可编译**
  （交叉工具链链接的是更高版本 glibc，产物不能在设备上运行），实机构建
  由用户在设备上执行；
- 工作流约定：**设备部署由用户自行完成**，开发侧只做本地开发与验证。

---

## 3. 总体架构：三层 + 周边工具

```
                         ┌────────────────────────────────────────┐
                         │             用户界面                    │
                         │  configs/default.conf   （INI 配置）    │
                         │  configs/multirate.conf （多速率示例）   │
                         └───────────────┬────────────────────────┘
                                         │ 解析 + 校验 + resolve
                         ┌───────────────▼────────────────────────┐
                         │          config 层（策略层）            │
                         │  config.c/.h                           │
                         │  · 手写 INI 解析器（零依赖）            │
                         │  · 校验：槽位上限/倍数关系/重复等       │
                         │  · resolve：名字→编码、k 因子、         │
                         │    合并列、tile 轮换 mask/slot          │
                         └───────┬───────────────────────┬────────┘
                 事件编码查询 │                       │ 解析后的配置
                         ┌─────▼──────┐         ┌──────▼──────────┐
                         │ catalog 层 │         │   engine 层      │
                         │（知识层）   │         │  collect_all.c   │
                         │ catalog.c/.h│        │  · 设备发现       │
                         │ · 167 个   │         │  · 三种采样机制   │
                         │   事件条目  │         │  · 多速率调度     │
                         │ · 列名覆盖  │         │  · CSV 行写出    │
                         └────────────┘         └──────┬──────────┘
                                                      │
                                      ┌───────────────▼────────────┐
                                      │       硬件 / 内核           │
                                      │  mlxbf-pmc sysfs · perf    │
                                      │  /proc · /sys/class/net    │
                                      └────────────────────────────┘

          ┌──────────────┬─────────────────┬──────────────────────┐
          │ test_config.c │ tools/check_csv │  Makefile ×2         │
          │ 主机单元测试  │ 输出不变量检查  │  (根 + code/)         │
          │ (170 断言)    │ (CSV 节奏校验)  │  构建/交叉编译/测试   │
          └──────────────┴─────────────────┴──────────────────────┘
```

### 3.1 三层各自的职责

| 层 | 文件 | 回答的问题 | 类比 |
|---|---|---|---|
| **catalog（知识层）** | `code/catalog.c/.h` | 这块硬件**存在哪些**事件？编码是多少？CSV 列名是什么？ | 词典 |
| **config（策略层）** | `code/config.c/.h` | 用户**想采哪些**事件、**什么频率**？配置是否合法？ | 需求说明书 + 翻译官 |
| **engine（执行层）** | `code/collect_all.c` | **怎么**在硬件上把配置变成一行行 CSV？ | 工人 |

**依赖方向严格单向**：`engine → config → catalog`。engine 不直接知道任何
事件编码——它只消费 config 层 resolve 好的 `bf2_config_t`；
config 层不硬编码任何编码——它通过 catalog 查询。三层都不依赖外部库。

### 3.2 为什么分成三层而不是一个文件

初版采集器是单文件、事件编码全部硬编码。改成三层后：

1. **改采集范围/频率 = 改配置文件**，二进制不用重编译（师兄的核心要求）；
2. **事件知识与采集逻辑解耦**——将来查 PMC 文档补充新事件，只需在
   catalog.c 加一行，所有配置立即能用；
3. **策略层可独立测试**——parser/resolve/列规则全是纯函数，可以在 x86
   主机上跑 170 条单元测试，不必依赖硬件。

---

## 4. 核心概念与机制

### 4.1 tick 与多速率调度

- **tick** 是全局最小时间单位，长度 = `[global] interval` 秒（默认 1s），
  每个 tick 输出一行 CSV。
- 每个块可以有自己的 `interval`（秒），它必须 ≥1 且是全局 interval 的
  **整数倍**；`interval = 0` 或缺省 = 继承全局。
- resolve 时为每块计算 **k 因子**：`k = 块interval / 全局interval`。
- 块的采样条件：**`tick % k == k - 1`**。

为什么是 `k-1` 而不是 `0`？因为窗口从编程时刻算起：

```
k=1:  tick 0 ✓  1 ✓  2 ✓ ...            （每行采样，= 原 1s 行为）
k=2:  tick 0 ✗  1 ✓  2 ✗  3 ✓ ...
      └─ 窗口0 从 init 编程算起，到 tick 1 采样时恰好跨 2 个 tick ─┘
k=5:  tick 0..3 ✗  4 ✓  5..8 ✗  9 ✓ ...
```

这样**每个窗口（包括第一个）都恰好覆盖 k 个 tick**，空列节奏干净整齐。
（若用 `tick % k == 0`，第一个窗口只有 1 个 tick 长，是个"残缺窗口"。）

### 4.2 三种采样机制

| 机制 | 适用块 | 窗口生命周期 | 读值语义 |
|---|---|---|---|
| **轮换（rotating）** | tile | 采样 tick **末尾**：把下一组事件写入 4 个槽（写入即清零），立即读一次作为基线；下个采样 tick 读差 | 差值（delta），组内事件跨 k 个 tick 累计 |
| **门控（gated）** | l3cache | 采样 tick **开头**：写 enable=0 冻结 → 读值 → 写 enable=1 重启（写 1 自动清零） | **读到的值本身就是整个窗口的增量**；无 enable 文件的 half 退化为普通差分 |
| **门控 + 轮换** | l3cache（groupN 配置） | 冻结 → 读当前组 → **冻结期间**编程下一组 → enable 重启；每个窗口 = 一个轮换组 | 与门控相同；组内事件跨窗口累计，列掩码决定该窗口填哪些列 |
| **差分（delta）** | tilenet、trio、smmu、triogen、pcie、l1、cpu、mem、net | 初始化时编程一次 + 读基线；每次采样读当前值 | `cur - prev`（计数器回绕时钳为 0） |

三种机制的共同点：**窗口边界严格对齐采样 tick**，非采样 tick 完全不碰
该块的硬件（不读、不写、不编程），所以块多速率不会互相污染窗口。

### 4.3 空字段（NaN）语义

块未被采样的 tick，其所有列**留空**（CSV 中就是连续的 `,,`）。
这是有意设计：

- 空 ≠ 0。0 是真实计数值（如"这 1 秒没有内存写"），空表示"本行没测"。
- pandas 读入时自动把这些空字段变成 `NaN`，后续 `resample` 聚合时
  天然忽略缺失值。

tile 块在未采样行还有一层：**tile_group 列也留空**（不写"上一组"的组号，
因为上一组并没有继续测）。

### 4.4 设备发现（presence）

引擎启动时**扫描硬件目录**（`opendir` + 目录名模式匹配）确定每块硬件
实际存在多少实例（4 个 tile？2 个 l3cachehalf？2 个 pcie？……），
结果汇总进 `bf2_presence_t`。CSV 表头**在发现之后**才生成：

- 配置启用了、硬件存在 → 该块的列出现（实例数决定列数，如 2 个 pcie 块
  → `pcie0_*` 和 `pcie1_*` 两组列）；
- 配置启用但硬件缺失 → WARN + 该块列整体省略（tile 例外：tile 启用却
  发现不了 → 直接 ERROR 退出，因为 tile 是核心资源）；
- 配置禁用 → 列不出现，硬件也不碰。

同一二进制在不同规格的 BF2 上运行会自动适配列布局。

### 4.5 列名规则（为什么不能机械推导）

CSV 列名 = `<块前缀>_<后缀>`，后缀由 `catalog_colname()` 决定：
有显式 `colname` 覆盖就用它，否则取事件名的小写。四个覆盖必须存在，
否则无法复现已验证的 42 列：

```
TDMA_DATA_BEAT      → dma_beats     （而不是 tdma_data_beat）
MEMORY_READS        → mem_reads     （而不是 memory_reads）
MSS_NO_CREDIT       → mss_nocredit
TRIO_MAP_WRQ_BUF_EMPTY → wrq_empty
```

另有两条**合并规则**：

- **L3 配对**：同时选了 `HITS_BANK0` + `HITS_BANK1` → 合并成一列
  `l3half{i}_hits`（两 bank 求和），`MISSES_*` 同理；只选一个 bank 则
  保持原名单列。**轮换模式下对所有 `_BANK0/_BANK1` 对生效**：合并列名 =
  去掉 bank 后缀的小写基名（`TOTAL_CDN_REQ_IN` 两 bank → `total_cdn_req_in`），
  且 bank 对必须编在**同一个组**（resolve 校验，否则报错）。
- **PCIe 字节三连**：同时选了 `IN_P/NP/C_BYTE_CNT` → 合并成 `pcie{i}_rx_bytes`，
  `OUT_*` → `pcie{i}_tx_bytes`；PKT 寄存器永不合并且逐寄存器成列。

### 4.6 parity gate（验收基线）

8/13 实机验证过的 42 列表头逐字节保存在单元测试里（`test_config.c` 的
`parity_line` 字符串），任何修改导致默认配置渲染的表头与之不一致，
`make test` 立即红灯。这是"默认行为不许悄悄变"的保险丝。

---

## 5. 运行时数据流

### 5.1 启动序列（main 中的顺序即代码顺序）

1. **解析 CLI**（`-c/-i/-d/-o/-q/--list-events/--dump-config/--check-config`）；
   三个信息命令不碰硬件、不需要 root，直接打印后退出。
2. **装载配置**：`catalog_init()` → `config_set_defaults()` →
   有 `-c` 则 `config_parse_file()`（逐行解析，错误带 `文件:行号` 并退出）→
   应用 CLI 覆盖项（-i/-d/-o）→ `config_resolve()`（全部校验 + 填派生字段）。
3. **打开输出**：配置了 `output` 就开文件并设为**行缓冲**（崩溃/被杀时
   已写完的行不丢），否则 stdout。
4. **安装信号处理器**：SIGINT/SIGTERM 只把 `g_running` 置 0，主循环感知。
5. **硬件初始化（按固定顺序）**：
   - 有任一硬件块启用 → `find_bfperf()` 找 hwmon 目录（找不到 → ERROR 退出）；
   - tile：发现 → 编程组 0 → 读基线（tile 启用而发现失败 → ERROR）；
   - tilenet/trio/smmu/triogen/l3cache/pcie/gic：各自发现 + 编程 +
     基线；缺失只 WARN；
   - l3cache 额外 `l3_enable_all()`——写 enable=1 清零并启动窗口 0；
   - L1 PMU：`pmu_init()` 开 fd → 立刻读一次基线（`/proc` 的
     perf_event_paranoid 过高时 WARN 并跳过，不影响其他块）；
   - cpu/net：读初值作基线。
6. **`fill_presence()` + `config_write_csv_header()`**——先发现、后表头。
7. 进入主循环。

### 5.2 主循环（每个 tick 内部的动作顺序）

```
while (g_running) {
    duration 到点？ → break
    sleep(全局interval)，被信号打断 → break        ← 中断时不写半行

    计算 12 个采样标志 s_xxx = (tick % k_xxx == k_xxx - 1)
      （BLK_K 宏：禁用块的 k 强制为 1，避免 %0 除零）

    ① L3：   s_l3 时 freeze → 读当前组 →（轮换配置：
              冻结期间编程下一组 + 无 enable half 读基线）
              → enable                               ← 门控机制
    ② tile： s_tile 时读当前组差值                    ← 轮换机制
    ③ 常驻块：tilenet/trio/smmu/gic/triogen/pcie 差值
    ④ PMU 差值（跨核求和）
    ⑤ 软件：cpu（总体+min/max/avg）、mem、net
    ⑥ 时间戳：clock_gettime(CLOCK_REALTIME)
    ⑦ 按固定列序写行（行写出器与表头生成器严格镜像）
    ⑧ fflush（行缓冲 → 立即落盘）
    ⑨ s_tile 时轮换：gid = (gid+1) % n_groups
       → 编程下一组 → 读基线                    ← 新 tile 窗口从此开始
    tick++
}
清理：l3_disable_all()、pmu_shutdown()、fclose
```

要点：

- **L3 的 freeze/read/enable 夹在窗口边界上**，与 tile 窗口在时间上对齐；
- tile 的"编程下一组"放在**采样 tick 的末尾**，而不是下个 tick 的开头——
  这样睡眠期间计数器已经在为新窗口计数，窗口不丢时间；
- 所有未采样块的行写出器一律输出空字段（`row_*` 的 `sampled` 参数）；
- 行写出器顺序 = 表头顺序 = 固定规范序
  （tile → tilenet → trio → smmu → triogen → l3 → pcie → l1 → cpu →
  mem → net → gic），保证 CSV 列对齐。

### 5.3 异常路径

- **SIGINT/SIGTERM**：`g_running=0` → `sleep_sec_interruptible` 返回 -1 →
  不写行直接退出 → 清理代码照常执行（L3 冻结、PMU 关闭、文件关闭）→
  CSV 不会出现半行。
- **sysfs 读/写失败**：每类失败只打印一次 WARN（`g_read_warned`/
  `g_write_warned` 去重），返回 0 继续跑，不因瞬时故障崩掉。
- **计数器回绕**（32 位计数器 + 大窗口）：`cur < prev` 时差值钳为 0，
  避免输出天文数字。文档注明"大 k 窗口可能回绕"这一已知限制。

---

## 6. 配置系统设计

### 6.1 Schema

```ini
[global]
interval = 1            # 基础 tick（秒），每 tick 一行
duration = 0            # 0 = 永久
output   = collector.csv # 空 = stdout

[tile]                  # 轮换块：最多 8 组，每组 ≤4 个且组间等长
enabled  = true
interval = 1            # 秒；必须为 global interval 的整数倍（0=继承）
group0   = A72_ACCESS, MEMORY_READS, MEMORY_WRITES, MSS_NO_CREDIT
group1   = A72_ACCESS, DIR_HIT, ALLOCATE, VICTIM_WRITE

[tilenet]  events = CDN_REQ, DDN_REQ, NDN_REQ        # ≤3（实机验证上限）
[trio]     events = TDMA_DATA_BEAT, ...               # ≤4
[smmu]     events = TBU_MISS, TX_DAT_AF, RX_DAT_AF    # ≤3
[triogen]  # 只有 enabled/interval；事件固定 triogen0=TX_DAT_AF, triogen1=RX_DAT_AF
[l3cache]  events = HITS_BANK0, HITS_BANK1, MISSES_BANK0, MISSES_BANK1
           # 平面模式（≤4 事件/半区）；或轮换模式（与 [tile] 同规则）：
           #   group0 = CYCLES, TOTAL_RD_REQ_IN, HITS_BANK0, HITS_BANK1
           #   group1 = MISSES_BANK0, MISSES_BANK1, ALLOCATIONS_BANK0, ALLOCATIONS_BANK1
           # 轮换模式下 bank 对合并列（见 §4.5），输出另加 l3_group 标记列；
           # 论文 52 计数器的完整配置见 configs/paper52.conf（8 组 × 4）
[pcie]     registers = IN_P_BYTE_CNT, ..., OUT_C_BYTE_CNT   # ≤12
[l1]       events = L1D_ACCESS, L1D_MISS, L1I_ACCESS, L1I_MISS  # 纯输出过滤
[cpu] [mem]  # enabled/interval；[net] 另有可选 interfaces = eth0, ...
[gic]      enabled = false        # 实机未验证，默认关
```

### 6.2 关键语义

- **替换语义**：`[tile]` 中第一个 `groupN=` 键会**清空全部默认组**
  （所以只写 `group0` 就是单组轮换）；各块第一个 `events=`/`registers=`
  清空该块默认列表。未出现的列表保持默认。
- **大小写不敏感**：section/key/布尔/事件名全部不区分大小写，
  解析后规范化为大写（`A72_Read`、`IO_Reads` 这类文档驼峰名也能用）。
- **鲁棒性**：CRLF、UTF-8 BOM、行内 `#`/`;` 注释、重复键报错、
  未知 section/key/事件报错（带 `文件:行号` 并提示 `--list-events`）。
- **CLI 覆盖**：`-i/-d/-o` 覆盖配置文件的对应全局项；覆盖后的值仍要
  通过 resolve 校验（比如 `-i 2` 会让 interval=3 的块报"非倍数"错误）。

### 6.3 三个信息命令（都不碰硬件、非 root 可跑）

| 命令 | 输出 |
|---|---|
| `--list-events` | 完整事件目录（167 条，按块分组，含编码与列名） |
| `--dump-config` | 默认配置模板（与 `configs/default.conf` 内容一致） |
| `-c FILE --check-config` | 解析 + resolve 的结果回显（错误则在 resolve 前就退出） |

---

## 7. 构建与测试体系

| 层 | 命令 | 说明 |
|---|---|---|
| 设备原生构建 | `make`（设备上 gcc 9.4） | 唯一有真实运行价值的产物 |
| 主机交叉验证 | `make CROSS=aarch64-linux-gnu-` | 只验证"能编译"，不验证"能跑" |
| **主机单元测试** | `make test` | `test_config`（HOSTCC 编译）跑 **170 条断言**，其中包含 parity gate |
| 输出不变量 | `python3 tools/check_csv.py out.csv [--period N --cols ...]` | 表头唯一、行宽一致、时间戳单调、**NaN 填充节奏** = `tick % k == k-1` |
| 实机验收 | `docs/acceptance-checklist.md` | 用户在设备上执行：表头逐字节 diff、多速率节奏、SIGINT 干净退出等 |

测试的价值分层：

- **单测**覆盖全部"纯逻辑"（解析、resolve、列规则、表头渲染）——不需要硬件；
- **check_csv.py** 覆盖运行时产物的结构不变量——设备跑一次多速率配置，
  就能验证引擎的调度逻辑与设计一致；
- **parity gate** 保证默认行为不回退。

---

## 8. 关键设计决策与理由（约束驱动）

| 决策 | 理由 |
|---|---|
| 手写 INI 解析器，不用任何库 | BF2 上零依赖；INI 足够表达"块 → 事件列表 + 频率" |
| 频率 = 全局 tick + 每块整数倍 | 保证所有块窗口边界可对齐；非倍数直接报错，语义简单 |
| `tick % k == k-1` 采样条件 | 首窗口即完整 k tick，NaN 节奏干净（详见 §4.1） |
| tile 轮换"采样 tick 末编程下一组 + 基线" | 睡眠期间计数器已在计数，窗口不丢时间；组大小强制相等防止残留槽位绑旧事件静默报错 |
| L3 门控 + enable-less 差分回退 | 有 enable 的 half 用硬件清零语义（值=窗口）；没有的 half 自动退化 |
| PCIe 逐寄存器差分 | 机制 2 寄存器不能清零，必须显式基线；合并列只对字节三连生效 |
| 空字段 = 未采样（≠0） | 0 是真实计数；pandas 直接读成 NaN 便于聚合 |
| 先发现、后表头 | 列布局随硬件实例数自动适配；缺失块列省略 + WARN |
| 三个信息命令独立于采集 | 用户不需要 root、不需要硬件就能查事件/模板/校验配置 |
| SIGINT 不写半行 | sleep 可中断 + 行只在 tick 末尾统一写出 |
| 源文件纯 ASCII | 设备 gcc 9.4 不支持 UTF-8 源 |
| 主机测试 + 交叉编译 + 实机验收三层验证 | BF2 不在开发环境里；分层让每层缺陷尽早暴露 |

---

## 9. 目录布局

```
bf2-collector/
├── Makefile                  # 根：委托 code/（all/test/clean）
├── README.md                 # 面向 GitHub 的英文说明
├── LICENSE                   # MIT
├── .gitignore / .gitattributes
├── configs/
│   ├── default.conf          # = 已验证 42 列基线（output=collector.csv）
│   └── multirate.conf        # 多速率示例：tile/l3 k=2，tilenet k=5
├── code/
│   ├── Makefile              # collect_all + test_config 构建规则
│   ├── catalog.h / catalog.c # 事件目录（167 条目 + 列名覆盖 + 生成式 DIAG）
│   ├── config.h / config.c   # INI 解析 + 校验 + resolve + 表头生成
│   ├── collect_all.c         # 采集引擎（发现/采样/主循环/CSV 写出）
│   └── test_config.c         # 主机单元测试（170 断言，含 parity gate）
├── docs/
│   ├── collected-events.md   # 事件分类总表（中文，按资源类别）
│   ├── acceptance-checklist.md # 实机验收清单（用户执行）
│   ├── architecture.md       # ← 本文
│   └── code-walkthrough.md   # 逐模块/逐函数代码详解
└── tools/
    └── check_csv.py          # CSV 不变量 + NaN 节奏检查器
```

---

## 10. 推荐学习路线

1. **README.md** —— 5 分钟了解用途、42 列布局、CLI；
2. **本文** —— 建立整体心智模型（三层、tick/k、三种机制、NaN 语义）；
3. **code/catalog.h → catalog.c** —— 最小、最独立的模块，先看懂"数据长什么样"；
4. **code/config.h → config.c** —— 核心数据结构 + 策略层全部逻辑，
   对照 `configs/default.conf` 和 `--dump-config` 输出理解；
5. **code/collect_all.c** —— 最大的一块，按 §5 的"启动序列 → 主循环"
   两条主线读，其余函数是被这两条主线调用的积木；
6. **code/test_config.c** —— 反过来印证你对 config/catalog 的理解
   （每个测试都在断言一个设计规则）；
7. **tools/check_csv.py + docs/acceptance-checklist.md** —— 理解验证体系；
8. 进阶：docs/collected-events.md（事件语义）+ PMC 官方文档（编码来源）。

配套的逐行讲解在 [code-walkthrough.md](code-walkthrough.md)。
