# bf2-counter-collector 代码逐行详解

> 本文是 [architecture.md](architecture.md) 的配套文档：按文件、按函数讲解每一段
> 代码的含义、每个变量与参数的角色，以及在整体系统中的作用。
> 建议先读 architecture.md 建立心智模型，再按本文顺序逐模块精读源码。
> 行号以当前仓库版本为准；文中的"L123"指对应文件第 123 行。

---

## 目录

- [阅读地图](#0-阅读地图)
- [Part 1  code/catalog.h —— 事件目录接口](#part-1)
- [Part 2  code/catalog.c —— 事件目录实现](#part-2)
- [Part 3  code/config.h —— 配置数据结构](#part-3)
- [Part 4  code/config.c —— INI 解析与 resolve](#part-4)
- [Part 5  code/collect_all.c —— 采集引擎](#part-5)
- [Part 6  code/test_config.c —— 主机单元测试](#part-6)
- [Part 7  Makefile（根 + code/）](#part-7)
- [Part 8  configs/*.conf](#part-8)
- [Part 9  tools/check_csv.py](#part-9)

---

## 0. 阅读地图

三个 C 模块的依赖是单向的：

```
collect_all.c（引擎）
    │  include
    ├── config.h ── config.c ──┐
    │              │           │ include
    └── catalog.h ─┴── catalog.c
test_config.c 独立编译，include config.h + catalog.h（不碰引擎）
```

数据流向（一条主线贯穿全程）：

```
default.conf 文本
   │  config_parse_file()      词法/语法解析，填 bf2_config_t 的"原始"字段
   ▼
bf2_config_t（enabled/interval/事件名列表……）
   │  config_resolve()         校验 + 查 catalog 填编码 + 计算 k/mask/合并列
   ▼
bf2_config_t（所有派生字段就绪 = 引擎唯一的输入）
   │  collect_all.c 消费：发现硬件 → 编程计数器 → 主循环采样
   ▼
CSV（表头由 config_write_csv_header 生成，行由 row_* 系列生成，二者严格镜像）
```

阅读顺序建议：Part 1 → 2（最小模块）→ 3 → 4（核心数据结构与策略）
→ 5（引擎，最大）→ 6（用测试反证理解）→ 7/8/9（构建与配套）。

---

## Part 1

### `code/catalog.h` —— 事件目录接口（48 行）

**文件职责**：声明"事件目录"模块的对外接口。整个文件只定义数据结构与
函数签名，没有任何逻辑。

#### 数据结构

```c
#define CATALOG_NAME_MAX 48   // 最长事件名（含 NUL 结尾）
```

**`catalog_event_t`（L18–22）—— 一个事件的三个属性：**

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | `const char *` | 规范名（全大写），如 `"A72_ACCESS"`；配置文件中用户写的大小写最终都要归到这里 |
| `colname` | `const char *` | CSV 列名后缀；**为 NULL 时** = 取 name 的小写。非 NULL 就是"显式覆盖"，见 Part 2 的 `catalog_colname()` |
| `code` | `unsigned int` | 硬件事件编码（16 进制数字）。写进 sysfs `eventN` 的就是它；PCIe 寄存器/L1 固定事件的 code 是 0（占位，无实义） |

**`catalog_block_t`（L24–29）—— 一个硬件块的元信息：**

| 字段 | 含义 |
|---|---|
| `block` | 配置文件中该块 section 的名字（`"tile"`、`"l3cache"`……） |
| `dir_prefix` | hwmon 下目录名的前缀（`"tile"` → 目录 `tile0/`……；l3cache 的目录前缀是 `"l3cachehalf"`；l1 是空串——PMU 不经过 sysfs） |
| `max_slots` | 机制 1 可编程槽位数上限（tile 4、tilenet 3、trio 4、smmu 3、gic 4）；**0 = 该块不是可编程计数器**（triogen/pcie/l1） |
| `events` | 指向以 `{NULL, NULL, 0}` 结尾的事件数组 |

#### 接口函数（L32–47）

```c
void catalog_init(void);              // 构建"生成式"事件（tilenet DIAG 家族），幂等
extern catalog_block_t g_catalog[];   // 全部 9 个块的表
extern const int g_catalog_n;

const catalog_block_t *catalog_find_block(const char *name);       // 按块名查（大小写不敏感）
const catalog_event_t *catalog_find_event(const char *block,
                                          const char *name);       // 按块+事件名查
const char *catalog_colname(const catalog_event_t *ev);            // 解析 CSV 列名后缀
int catalog_list_events(FILE *fp);                                 // --list-events 的打印函数
```

要点：`g_catalog` 是 `extern`（不是 static）——因为 test_config.c 不需要它，
而引擎和 `--list-events` 需要；对外只暴露 const 指针，目录本身不可被改写。

---

## Part 2

### `code/catalog.c` —— 事件目录实现（313 行）

**文件职责**：保存从官方《BlueField-2 Performance Monitoring Counters》
文档提取的全部非保留事件编码，并提供查询与列名解析。这是全项目**唯一**
写有硬件编码的文件。

#### 2.1 tile HNF 事件表（L18–41）

```c
static const catalog_event_t tile_events[] = {
    { "HNF_REQUESTS",        NULL,           0x45 },
    { "MEMORY_READS",        "mem_reads",    0x4c },
    { "MEMORY_WRITES",       "mem_writes",   0x4d },
    { "VICTIM_WRITE",        NULL,           0x4e },
    ...
    { "MSS_NO_CREDIT",       "mss_nocredit", 0x67 },
    ...
    { "A72_READ",            NULL,           0x72 },  /* doc: A72_Read  */
    { "IO_READS",            NULL,           0x74 },  /* doc: IO_Reads  */
    { NULL, NULL, 0 }     // ← 哨兵，所有表都以它结尾
};
```

观察点：

- 共 22 个事件，全部来自 PMC 文档的 Tile HNF 段，保留编码 0x45–0x74 中
  **文档标注为非保留**的部分（保留编码不能编程，必须跳过；0x4a
  RNF_REQUESTS 位于保留值之间但非保留，2026-09-10 论文对照核实后补回。
  本 Part 的行号引用以 2026-09-10 版源码为准，后续增删事件会使其偏移）；
- 四个带显式 `colname` 的条目（`mem_reads`/`mem_writes`/`mss_nocredit`
  以及 trio 里的 `dma_beats`/`wrq_empty`）是**为复现已验证的 42 列
  表头而存在**——机械小写会得到 `memory_reads`，与历史输出不一致；
- 注释里的 `doc: A72_Read` 说明：文档原文是驼峰拼写，这里规范成
  `A72_READ`（配合配置解析器的大小写规范化，用户写哪种都行）。

#### 2.2 tilenet 的 51 个事件——一半是"生成"出来的（L44–96）

tilenet 有三个网络（CDN 控制数据网 / DDN 数据网 / NDN 非缓存数据网），
文档对每个网络给了一张 type-major 的 DIAG 表：`OUT_OF_CRED`/`EGRESS`/
`INGRESS` 三种类型 × N/S/E/W/C 五个方向 + 一个 `CORE_SENT`。
三个网络结构完全相同，只是基址不同（CDN 0x15 起、DDN 0x25 起、
NDN 0x35 起，间隔 0x10）。手写 3×16 = 48 行没有意义，所以**用代码生成**：

```c
#define TN_NETS  3
#define TN_DIAGS 48
#define TN_TOTAL (3 + TN_DIAGS)        // 51：3 个 REQ + 48 个 DIAG

static char s_tn_names[TN_TOTAL][CATALOG_NAME_MAX];   // 名字的存储（生成式名字不能是字符串字面量）
static catalog_event_t s_tn_events[TN_TOTAL + 1];     // 事件表本体（多 1 放哨兵）
```

`s_tn_names` 是关键技巧：静态表里的 `name` 通常指向字符串字面量，但生成
的名字必须存到内存里，所以单独开一块 `char[51][48]` 作为名字仓库，
事件表的 `name` 指针指进去。

`build_tn_events()`（L59–96）逐段：

1. **幂等保护**（L69–70）：`if (s_tn_events[0].name != NULL) return;`
   —— 因为每个查询函数开头都会调 `catalog_init()`，重复调用必须无副作用。
2. **3 个 REQ 事件**（L72–78）：`CDN_REQ`/`DDN_REQ`/`NDN_REQ`，
   编码 0x12/0x13/0x14，排在数组最前面。
3. **三层循环生成 45 个 DIAG**（L79–88）：

   ```c
   for (n = 0; n < TN_NETS; n++)          // 网络：CDN/DDN/NDN
       for (t = 0; t < 3; t++)            // 类型：OUT_OF_CRED/EGRESS/INGRESS
           for (d = 0; d < 5; d++)        // 方向：N/S/E/W/C
               name = "%s_DIAG_%s_%s";    // 例：CDN_DIAG_C_INGRESS
               code = 0x15u + 0x10u*n + 5u*t + d;
   ```

   编码公式即文档布局：网络基址 0x15/0x25/0x35；类型每档占 5 个编码
   （5 个方向）；方向每档 1。`0x15u` 的 `u` 后缀保证无符号算术。
4. **每网络 1 个 CORE_SENT**（L89–93）：`%s_DIAG_CORE_SENT`，
   编码 `0x24u + 0x10u*n`（0x24/0x34/0x44——恰在各自网络块的第 16 位）。
5. **写哨兵**（L95）：`s_tn_events[e].name = NULL;`

这个"表驱动 + 生成"的写法是理解整个 catalog 的精髓：**凡是文档里有规律
的数据，用公式生成比手抄更不容易错**，而且单测里抽验了 12 个生成事件
的编码（见 Part 6 `test_catalog_spots`）。

#### 2.3 其余五张静态表（L101–204）

- **trio_events（L101–119）**：16 个非保留事件（0xa2/a3/a6/a7 保留）。
  注意 `TDMA_DATA_BEAT` 的 colname 覆盖为 `dma_beats`、
  `TDMA_RT_AF` → `rt_af`、`TDMA_PBUF_MAC_AF` → `pbuf_af`、
  `TRIO_MAP_WRQ_BUF_EMPTY` → `wrq_empty`——同样服务于 42 列兼容。
- **smgen_events（L124–129）**：3 个事件，**smmu 和 gic 共用**这张表
  （SMGEN 是同一类硬件，文档编码相同）。
- **triogen_events（L134–138）**：只有 2 个固定事件；注意这张表几乎不被
  引擎使用（triogen 的编程在引擎里硬编码了 0x0f/0x10），它主要服务于
  `--list-events` 展示与校验。
- **l3cache_events（L145–176）**：30 个非保留事件，0x01–0x1e
  （0x01 CYCLES 时间戳于 2026-09-10 补回）。
- **pcie_events（L179–193）**：12 个机制 2 统计寄存器，`code` 全是 0——
  因为机制 2 不是"写编码编程"，而是"读命名文件"（文件名即事件名）。
- **l1_events（L198–204）**：ARM PMU 的 4 个事件，code 是
  perf 的 config 编码（0x40/0x42/0x14/0x01），**仅供信息展示**；
  引擎实际用 `PERF_TYPE_HW_CACHE` 方式开事件（见 Part 5 §5.10），
  不消费这里的 code。

#### 2.4 块表 g_catalog（L209–220）

```c
catalog_block_t g_catalog[] = {
    { "tile",     "tile",        4, tile_events },
    { "tilenet",  "tilenet",     3, s_tn_events },  /* slots 0-2 verified */
    { "trio",     "trio",        4, trio_events },
    { "smmu",     "smmu",        3, smgen_events },
    { "triogen",  "triogen",     0, triogen_events }, /* fixed, not configurable */
    { "l3cache",  "l3cachehalf", 4, l3cache_events },
    { "pcie",     "pcie",        0, pcie_events },    /* mechanism 2 registers */
    { "l1",       "",            0, l1_events },      /* ARM PMU, fixed */
    { "gic",      "gic",         4, smgen_events },   /* 3 valid SMGEN events */
};
const int g_catalog_n = (int)(sizeof(g_catalog) / sizeof(g_catalog[0]));
```

要点：

- `dir_prefix` 与 `block` 不同的只有 l3cache（目录名是 `l3cachehalfN`）；
- `max_slots=3` 的 tilenet 注释记录了**实机验证边界**——超过 3 个槽
  从未在真机上验证过，所以配置层拒绝 >3；
- gic 复用 smgen 表，槽位上限 4（但表里只有 3 个事件）；
- `g_catalog_n` 由编译器算出来，加块时不会漏改。

#### 2.5 查询函数（L225–269）

**`catalog_init()`（L225–228）**：只做一件事——`build_tn_events()`。
每个查询入口都先调它，保证首次查询前生成表已就绪（幂等）。

**`catalog_find_block(name)`（L230–239）**：线性扫 9 个块，
`strcasecmp` 比较（**大小写不敏感**——用户写 `[TILE]` 也能匹配）。
找不到返回 NULL。

**`catalog_find_event(block, name)`（L241–254）**：先找块，再线性扫
该块事件表直到哨兵，`strcasecmp` 匹配。找不到返回 NULL。
这是 resolve 阶段的"事件名 → 编码"查询入口。

**`catalog_colname(ev)`（L256–269）**：

```c
static char buf[CATALOG_NAME_MAX];      // 静态缓冲：结果下次调用会被覆盖
if (ev->colname != NULL) return ev->colname;    // 有覆盖直接用
for (i = 0; ev->name[i] && i < CATALOG_NAME_MAX-1; i++)
    buf[i] = (c >= 'A' && c <= 'Z') ? c - 'A' + 'a' : c;   // 大写→小写
buf[i] = '\0';
return buf;
```

返回静态缓冲意味着**调用方必须立即复制**（config.c 里就是马上
`snprintf` 进 `cfg_event_t.colname`，从不在别处保存这个指针）。

#### 2.6 --list-events 的打印（L274–312）

`list_block()` 对每个事件打印一行：有 code 的打印
`0x%02x  NAME  -> block_colname`，code=0 的打印 `reg  NAME  -> ...`。
`catalog_list_events()` 遍历全部 9 个块，最后补打三个软件块的说明
（cpu/mem/net 没有硬件事件），并输出事件总数。
注意该函数把每块的 `dir_prefix` 和槽位数也打印出来，方便用户对照硬件。

---

## Part 3

### `code/config.h` —— 配置数据结构（136 行）

**文件职责**：定义配置系统的全部常量、数据结构与 API。理解本项目的
一半工作就是理解这些结构体。

#### 3.1 容量常量（L18–27）

```c
#define CFG_NAME_MAX      48   // 事件/接口名上限
#define CFG_PATH_MAX      512  // 输出路径上限
#define CFG_ERR_MAX       512  // 错误信息缓冲上限
#define CFG_GROUPS_MAX    8    // tile 轮换组数上限
#define CFG_SLOTS_MAX     4    // 机制 1 可编程槽位上限
#define CFG_REGS_MAX      12   // PCIe 寄存器数上限
#define CFG_IFACES_MAX    8
#define CFG_L3_COLS_MAX   32
#define CFG_TILE_COLS_MAX 32
#define CFG_PCIE_UNITS_MAX 14
```

这些上限都是"硬件现实 + 工程余量"：tile 硬件只有 4 槽所以
`CFG_SLOTS_MAX=4`；PCIe 目录里总共 12 个寄存器所以 `CFG_REGS_MAX=12`；
tile 最多 8 组 × 4 事件，去重后最多 32 个唯一列名所以
`CFG_TILE_COLS_MAX=32`。L3 同理：最多 8 组 × 4 事件，bank 对合并后
最多 32 列，所以 `CFG_L3_COLS_MAX=32`（平面与轮换两种列视图共用
这个上限）。

#### 3.2 `cfg_event_t`（L29–33）—— 配置侧的一个事件

```c
typedef struct {
    char name[CFG_NAME_MAX];    // 规范大写事件名（解析时填）
    char colname[CFG_NAME_MAX]; // CSV 列名后缀（resolve 时从 catalog 复制过来）
    int code;                   // 事件编码（resolve 时从 catalog 复制过来）
} cfg_event_t;
```

与 catalog 的 `catalog_event_t` 的区别：catalog 里是 `const char *`
指向共享表，而配置侧是**每个事件自己存一份**（`char[]` 数组），因为
配置是运行时可变的数据。`colname`/`code` 在 resolve 之前是空的——
这就是"原始配置"与"解析后配置"的分界。

#### 3.3 `cfg_col_t`（L35–38）—— L3 的一列

```c
typedef struct {
    char name[CFG_NAME_MAX];
    int ev0, ev1;   /* ev1 == -1: 单事件列 */
} cfg_col_t;
```

`ev0`/`ev1` 是 `events[]` 数组的**下标**：合并列（如 `hits`）指向两个
bank 事件；`ev1 == -1` 表示这列只对应一个事件（如 `hits_bank0`）。

**`cfg_l3_rot_col_t`（L3 轮换模式的列视图）**：与 tile 的
`cfg_tile_col_t` 完全同构——`mask`（bit g = 第 g 组提供这一列）+
`n_slots[8]`/`slot[8][4]`（该事件在各组占用的槽位号）。差别在于合并
语义：bank 对（`HITS_BANK0`+`HITS_BANK1`）合并成**一列占两个槽位**
（`n_slots[g]=2`，`slot[g][0..1]` 指向两 bank），单 bank 事件占一个
槽位。轮换模式存进 `l3_rot_cols[]`，平面模式仍用 `l3_cols[]`——
resolve 时二选一填充。

#### 3.4 `cfg_tile_col_t`（L40–44）—— tile 的一列（轮换感知）

```c
typedef struct {
    char name[CFG_NAME_MAX];
    int mask;                 /* bit g = 第 g 组提供这一列 */
    int slot[CFG_GROUPS_MAX]; /* 该事件在各组里占的槽位号 */
} cfg_tile_col_t;
```

这是 tile 轮换的**输出视图**：默认配置里 7 个唯一事件 = 7 列。
例如 `a72_access` 列：`mask = 0b11`（两组都有）、`slot[0]=0`、
`slot[1]=0`（都在各自组的 0 号槽）；`dir_hit` 列：`mask = 0b10`
（只有 1 组有）、`slot[1]=1`。采样组 g 时：`mask & (1<<g)` 为真才
输出，值取 `delta[slot[g]]`。

#### 3.5 `cfg_block_t`（L46–72）—— 一个配置块（最核心的结构）

| 字段 | 类型 | 含义 |
|---|---|---|
| `enabled` | int | 该块是否启用（默认 1） |
| `interval` | int | 块采样间隔（秒）；**0 = 继承全局** |
| `overridden` | int | 本块出现过 `events`/`registers`/`groupN` 键 → 默认列表已被替换（见 4.5 替换语义） |
| `n_groups` / `n_group_ev[8]` / `groups[8][4]` | | **tile 专用**：组数、每组事件数、各组事件（二维数组 [组][槽]） |
| `n_events` / `events[4]` | | **非轮换块**的事件列表（tilenet/trio/smmu/l3/l1/gic） |
| `n_regs` / `regs[12]` | | **pcie 专用**：机制 2 寄存器列表 |
| `n_ifaces` / `ifaces[8][48]` | | **net 专用**：接口过滤列表（0 = 全部） |
| `k` | int | **resolve 填充**：`interval / 全局interval`，即采样周期（tick 数） |
| `l1_sel[4]` | int | **resolve 填充**：L1 四个固定事件各自的"是否输出"标志（L1 的配置是纯输出过滤） |
| `rx_merged` / `tx_merged` | int | **resolve 填充**：PCIe 收/发字节三连是否合并成单列 |
| `rx_idx[3]` / `tx_idx[3]` | int | **resolve 填充**：三个寄存器在 `regs[]` 里的下标（-1 = 缺） |
| `n_l3_cols` / `l3_cols[32]` | | **resolve 填充**：L3 平面模式输出列（合并后） |
| `n_l3_rot_cols` / `l3_rot_cols[32]` | | **resolve 填充**：L3 轮换模式输出列（bank 对合并后的 mask/slot 视图） |
| `n_tile_cols` / `tile_cols[32]` | | **resolve 填充**：tile 输出列（唯一事件名视图） |

一个 `cfg_block_t` 同时容纳三种块的形状（轮换/列表/寄存器），不用的
字段留 0——用"结构体复用"换取"12 个块统一处理"的代码简洁性。

#### 3.6 `bf2_config_t`（L74–80）—— 整份配置

```c
typedef struct {
    int interval;                /* 全局 tick（秒，≥1） */
    int duration;                /* 总时长（秒，0=永久） */
    char output[CFG_PATH_MAX];   /* 输出路径（空=stdout） */
    cfg_block_t tile, tilenet, trio, smmu, triogen, l3cache,
                pcie, l1, cpu, mem, net, gic;
} bf2_config_t;
```

**全局单例**：引擎里就一个 `static bf2_config_t g_cfg;`。

#### 3.7 `bf2_presence_t`（L83–88）—— 设备发现结果

每块两个字段：`n_xxx`（发现到几个实例）与 `have_xxx`（是否可用）。
表头生成器以它为准：`配置启用 && have` 才输出该块列。

#### 3.8 API 一览（L91–125）

```c
void config_set_defaults(bf2_config_t *cfg);                    // 填内置默认（=42 列基线）
int  config_parse_file(const char *path, bf2_config_t *cfg,
                      char *errbuf, size_t errsz);              // 解析 INI，0/-1
int  config_resolve(bf2_config_t *cfg, char *errbuf, size_t errsz); // 校验+填派生字段
int  config_dump_template(FILE *fp);                            // 打印默认配置模板
int  config_dump_resolved(FILE *fp, const bf2_config_t *cfg);   // 打印 resolve 结果
int  config_write_csv_header(FILE *fp, const bf2_config_t *cfg,
                            const bf2_presence_t *p);           // 生成 CSV 表头
int  config_pcie_units(const bf2_config_t *cfg, int kind[], int idx[],
                      int max_units);                           // PCIe 列构成
void config_pcie_unit_name(const bf2_config_t *cfg, int kind, int idx,
                           char *buf, size_t bufsz);            // PCIe 列名
#define CFG_L1_CANON "L1D_ACCESS","L1D_MISS","L1I_ACCESS","L1I_MISS"  // L1 规范顺序
```

注意 `CFG_L1_CANON`（L124–125）是一个**宏展开成四个字符串字面量**的
技巧，配合数组初始化 `static const char *canon[4] = { CFG_L1_CANON };`
（config.c L799）正好得到一个 4 元素数组。L1 的列顺序是固定的
（PMU 开事件的顺序），与用户配置的书写顺序无关。

---

## Part 4

### `code/config.c` —— INI 解析与 resolve（1398 行）

**文件职责**：把文本配置变成可执行的 `bf2_config_t`。分四大段：
helpers（L21–158）→ 默认配置（L163–245）→ 解析器（L250–578）→
resolve（L583–899）→ 打印与表头（L904–1188）。

#### 4.1 字符串与数值 helpers

**`trim(char *s)`（L21–33）**：原地去首尾空白。注意最后一行
`if (p != s) memmove(s, p, n + 1);`——只有真的跳过前导空白才搬移
（`memmove` 处理重叠区间；`n+1` 把结尾 NUL 一起搬）。

**`canon_name(char *s)`（L35–41）**：小写字母原地转大写。配置里
`a72_access` 和 `A72_ACCESS` 经过它都变成 `A72_ACCESS`。

**`copy_name(dst, src)`（L45–53）**：截断拷贝（超过 47 字符截断）。
注释解释了它为什么存在：同一 struct 的两个 `char[]` 字段之间
`strncpy`/`snprintf` 会触发 gcc 的 `-Wrestrict` 误报，用独立的
helper 规避。学习点：**编译器警告零容忍**，但采用"隔离误报"的方式
而不是关警告。

**`parse_int(s, out)`（L55–66）**：`strtol` 严格解析——必须**整个字符串**
都是数字（`*end == '\0'`），且不超 int 范围。`"12abc"`、空串都返回 -1。

**`parse_bool(s, out)`（L68–81）**：接受 `true/yes/on/1` 与
`false/no/off/0`（大小写不敏感），其余报错。宽容输入、严格校验。

**`parse_event_list(v, dst, max, where, errbuf, errsz)`（L84–120）**：

```c
snprintf(buf, sizeof(buf), "%s", v);                    // 复制到 1KB 缓冲
for (tok = strtok_r(buf, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
    trim(tok);                                          // 去空白
    if (*tok == '\0')  → "empty list entry" 错误         // 防 "A,,B"
    if (n >= max)      → "too many entries" 错误
    canon_name(tok);                                    // 规范大写
    if (strlen(tok) >= CFG_NAME_MAX) → 名字过长错误
    snprintf(dst[n].name, ...); dst[n].code = 0; dst[n].colname[0] = '\0';
    n++;
}
return n;                                               // 成功返回条目数
```

要点：

- `strtok_r` 是线程安全版 `strtok`（`save` 保存游标）——这也是
  Makefile 里 `-std=gnu11` 的原因之一（严格 ISO C 没有 `strtok_r`）；
- 必须先复制到局部 `buf` 再切，因为 `strtok` 会改写输入字符串，
  不能直接切 `cfg` 里的内存或 const 字符串；
- 新条目的 `code=0、colname=""` 是"尚未 resolve"的标记；
- 错误信息带 `where`（`文件:行号`），定位精确到行。

**`parse_iface_list`（L123–158）**：与上面几乎相同，两个差别——
**保留大小写**（接口名如 `enp3s0f1s0` 是大小写敏感的）不做
`canon_name`；名字上限 15（Linux `IFNAMSIZ-1`，注释标明出处）。

#### 4.2 内置默认配置（L163–245）

**`block_init(b)`**：`memset` 清零 + `enabled=1`——**所有块默认启用**
（`gic` 例外，稍后单独关掉）。

**`ev_set(dst, name)`**：填一个 `cfg_event_t` 的名字字段（code/colname
留空待 resolve）。

**`config_set_defaults(cfg)`（L176–245）**：`memset` 全清 →
`interval=1, duration=0, output=""` → 12 个块全部 `block_init` →
`gic.enabled = 0`（实机未验证，默认关）→ 逐块填入**与 8/13 实机验证
完全一致的事件集**：

- tile 两组各 4 事件（A72_ACCESS 两组都有 = 100% 覆盖率）；
- tilenet 3、trio 4、smmu 3、l3cache 4（bank0/1 的 hits+misses）、
  pcie 6 寄存器（IN/OUT × P/NP/C 字节）、l1 4 个事件。

这个函数就是"parity gate"的数据来源：默认表头必须复现历史 42 列，
改这里就等于改基线，单测会拦。

#### 4.3 解析器的编号系统（L250–308）

```c
#define SEC_GLOBAL 0 ... #define SEC_GIC 12     // 13 个 section 编号
#define KEY_ENABLED 0 ... #define KEY_DURATION 6
#define KEY_GROUP0 8                             // groupN 的 key id = 8 + N
```

**key id 编码技巧**：`groupN` 的 id 直接取 `8 + N`（N=0..7），这样
`seen_key[kid]` 查重数组（16 个）每个 groupN 有独立槽位；N≥8 时
`kid` 越界 → 置 -1 → 走"unknown key"错误，顺带防住了数组越界。

**`sec_index(name)`（L281–289）**：大小写不敏感地查 section 名。
**`block_of(cfg, sec)`（L291–308）**：section 编号 → 对应 `cfg_block_t *`
的指针。**`SEC_GLOBAL` 没有块**（返回 NULL），在 dispatch 里单独分支。

#### 4.4 `config_parse_file()`（L310–578）—— 逐段精读

**签名**：`path`（INI 文件路径）、`cfg`（**必须在调用前先用
`config_set_defaults` 初始化**——解析只覆盖用户写过的键）、
`errbuf/errsz`（错误信息输出）。返回 0 成功 / -1 失败。

**局部状态**（L316–320）：

```c
int lineno = 0;        // 行号（错误定位）
int first_line = 1;    // 首行 BOM 处理标记
int cur_sec = -1;      // 当前 section（-1 = 还没进入任何 section）
int seen_sec = 0;      // 已见 section 位掩码（防重复 section）
int seen_key[16];      // 当前 section 已见 key 位掩码（防重复键）
```

**主循环逐行处理**（L329–575），每行的流水线：

1. **行长检查**（L336–343）：`fgets` 读满 1023 字节且没有换行 →
   该行超长，报错退出（否则后面处理的是被截断的行，错误会变得
   莫名其妙）。
2. **BOM 剥离**（L344–349）：只在**第一行**检查 UTF-8 BOM
   （`EF BB BF`），有则 `memmove` 掉。`first_line` 保证只查一次。
3. **注释剥离**（L350–355）：`trim` → `strpbrk(line, "#;")` 找第一个
   行内注释符（`#` 或 `;`），截断 → 再 `trim` → 空行跳过。
   注意：先 trim 再找注释，所以行首 `#` 整行注释也成立。
4. **section 行**（L359–390）：以 `[` 开头 → 找 `]`（缺则错）→
   trim 内部 → `sec_index` 查名（未知报错）→ `seen_sec` 位检查
   （重复 section 报错）→ 重置 `seen_key` → 更新 `cur_sec`。
5. **key 行**（L392–444）：
   - 还没进 section 就出现 key → 报错；
   - `strchr(line, '=')` 找等号（没有则报"expected key = value"）；
   - 切分并 trim 出 `key` 和 `val`，`where = "文件:行号"`；
   - **key 识别**（L415–435）：`enabled/interval/events/registers/
     interfaces/output/duration` 逐一 `strcasecmp`；`groupN` 用
     `strncasecmp(key,"group",5)` + `key[5]` 是数字 + `key[6]=='\0'`
     精确匹配（`group10`、`groups` 都不算）；全不中 → "unknown key"。
6. **dispatch（[global] 分支）**（L445–474）：只接受
   `output`（长度 <512）、`interval`、`duration`；其他键一律
   "unknown key in [global]"。
7. **dispatch（块分支）**（L475–574）：
   - **每 section 的键白名单**（L480–500）：
     `enabled/interval` 所有块通用；`groupN` 仅 tile / l3cache；
     `events` 仅 tilenet/trio/smmu/l3/l1/gic；`registers` 仅 pcie；
     `interfaces` 仅 net。白名单之外的键 → 带 section 名的错误。
     （所以 `[cpu] events=...` 或 `[triogen] events=...` 是错误——
     这些块没有可配置事件。）
   - **重复键检查**（L501–508）：`seen_key[kid]` 已置位 → 报错。
     INI 语义里同 section 同键出现两次是笔误，宁可报错。
   - **解析各键**（L510–573）：`enabled` → parse_bool；
     `interval` → parse_int；`groupN`/`events`/`registers` →
     `parse_event_list`（上限分别为 4/4/12，错误原样上抛）；
     `interfaces` → `parse_iface_list`。
   - **替换语义的实现**（L527–531、542–545、554–557）：

     ```c
     if (!b->overridden) {        /* 第一个 groupN/events/registers 键 */
         b->n_groups = 0;         /* 清空默认组 / 默认列表 */
         memset(b->n_group_ev, 0, sizeof(b->n_group_ev));
         b->overridden = 1;
     }
     ```

     这是 architecture.md §6.2"替换语义"的代码实现：**第一个**列表键
     清默认，之后的列表键**追加**（tile 里 group0/group1/... 各自独立
     不冲突；但注意 flat 块的 `events` 键只有一个——重复即报错，
     所以 flat 块不存在"追加"路径）。

错误处理风格统一：**一处错误立即返回 -1**（`ret=-1; break;`），
errbuf 里是完整的 `文件:行号: 原因`，main 直接打印退出。不做"收集
所有错误"，因为配置错了第一条就足以定位。

#### 4.5 resolve 阶段（L583–899）

resolve 的输入是"解析后的配置"（名字列表），输出是"可执行配置"
（编码、k、mask、合并列全部就绪）。

**`find_ev(evs, n, name)`（L583–591）**：在已规范化的列表里找事件
下标。**注意这里是 `strcmp`（大小写敏感）**——因为 parse 阶段已经
canon 过大写，这里可以放心用精确匹配。

**`resolve_list(blk, evs, n, errbuf, errsz)`（L593–619）**：对列表中
每个事件：

```c
const catalog_event_t *ce = catalog_find_event(blk, evs[i].name);
if (ce == NULL) → "[blk] event 'xxx' not in catalog (see --list-events)"
for (j < i) if 重名 → "[blk] duplicate event 'xxx'"       // 列表内查重
evs[i].code = ce->code;                                   // 填编码
snprintf(evs[i].colname, ..., catalog_colname(ce));       // 立即复制列名
```

未知事件 → 报错并提示 `--list-events`（好错误信息的范例）。

**`resolve_k(blk, b, ginterval, ...)`（L621–640）**：

```c
if (b->interval == 0) { b->k = 1; return 0; }   // 0 = 继承全局 → k=1
if (b->interval < 0) → "interval must be >= 0"
if (b->enabled && b->interval % ginterval != 0) → "must be a multiple"
b->k = b->interval / ginterval;
```

要点：**禁用块跳过倍数检查**（禁用了就不会采样，interval 无意义）；
但 k 仍会算出（整数除法），引擎侧用 BLK_K 宏把禁用块 k 强制为 1
（见 Part 5 §5.14），双保险。

**`resolve_block(blk, b, max_slots, ginterval, ...)`（L642–653）**：
三个动作的流水线：resolve_list → 槽位上限检查（`n_events > max_slots`
报错）→ resolve_k。所有"非轮换"块共用。

**`resolve_tile(b, ...)`（L655–730）**——最复杂的 resolve，两步：

第一步（L660–698）逐组校验并填编码：

- 组必须连续（`group0` 空 → "groups must be contiguous"）；
- 组大小必须相等（防残留槽位静默绑旧事件）；
- 组内查重；
- 查 catalog 填 code/colname。

第二步（L700–728）**构建列视图**：遍历所有组的所有事件，按
`colname`（不是 name！）去重聚合成列：

```c
col->mask |= 1 << g;    // 第 g 组有这个事件 → 掩码该位置位
col->slot[g] = s;       // 并记录它在第 g 组的槽位号
```

产出的 `tile_cols[]` 就是行写出器 `row_tile` 直接消费的视图
（见 Part 5 §5.12）。默认配置：7 列，`a72_access` 的 mask=3，
其余 6 列 mask 各为 1 或 2。

**`resolve_l3(b)`（L742–777）**：决定 L3 输出列（合并规则）：

```c
h0=HITS_BANK0 下标, h1=HITS_BANK1 下标, m0/m1=MISSES_*
if (h0>=0 && h1>=0) → 合并列 "hits"（ev0=h0, ev1=h1）
else 单列用事件自己的 colname（"hits_bank0"）
// MISSES 同理
// 其余未配对事件按配置顺序追加为单列
```

`used[]` 数组保证配对过的事件不会在收尾循环里重复输出。
列序固定：hits 相关在前、misses 其次、其余按用户书写顺序。

**`resolve_l3_rot(b)`（轮换模式，`n_groups > 0` 时取代 resolve_l3）**：
把 L3 的轮换组解析成 `l3_rot_cols[]`。校验规则与 tile 轮换完全同源：
组连续、组大小相等（1..4）、查 catalog、组内查重——**外加一条 bank
配对规则**：若选了某事件的 `_BANK0`，其 `_BANK1` 必须编在**同一个组**
（反之亦然），否则报 `"...must be in the same group"`。原因是合并列
要在同一窗口内同时读两个 bank 的值，拆到不同组则永远凑不齐一个窗口。

列构建（helpers：`bank_base_len` 取去除 `_BANK0/_BANK1` 后缀的基名、
`bank_partner` 找配对的另一 bank、`group_has`/`any_group_has` 查事件
编在哪些组）：

- 两 bank 都在 → 一列，`name` = 基名小写（`copy_name_lower`，
  如 `TOTAL_CDN_REQ_IN` → `total_cdn_req_in`），每组占 2 个槽位
  （bank0、bank1 各自的槽位号都记进 `slot[g][]`）；
- 只选一个 bank → 一列，`name` = 该事件的 colname（`hits_bank0`），
  占 1 个槽位；
- 非 bank 事件 → 一列，占 1 个槽位。

列序 = 配置书写顺序，去重按 `colname`（与 tile 一致）。`mask` 由
`any_group_has` 聚合：重复编入多组的事件（如 paper52.conf 里 HITS
对在 G5+G7）自然得到多位掩码。

**`resolve_pcie(b)`（L779–795）**：找 IN_P/NP/C 与 OUT_P/NP/C 三个
寄存器在 `regs[]` 里的下标（缺的 -1）；**三者全在**才置
`rx_merged`/`tx_merged`。合并是"全有或全无"，不存在部分合并。

**`resolve_l1(b)`（L797–804）**：`l1_sel[i] = (canon[i] 在 events 里)`。
L1 配置**只是输出过滤**——PMU 永远开 4 个固定事件，选中才输出列。

**`config_resolve(cfg, ...)`（L806–899）**——总入口，固定顺序：

1. 全局校验：`interval >= 1`、`duration >= 0`；
2. tile：组数 1..8、组连续、组大小 1..4 且相等（与 resolve_tile
   内的检查有重叠——入口处先做**结构**校验，resolve_tile 再做
   **内容**校验，职责分层）；
3. `resolve_k("tile")` → `resolve_tile`；
4. `resolve_block("tilenet", 3)` → trio(4) → smmu(3) → l3cache(4)；
   l3cache 若配置了 groupN（轮换模式），紧接着调 `resolve_l3_rot`
   （只依赖编码已 resolve，不必等到最后）；
5. pcie：resolve_list（regs 列表）→ 数量 ≤12 → resolve_k；
6. l1(4)、gic(4)；
7. `resolve_k` 单独处理 triogen/cpu/mem/net（没有事件列表的四个块）；
8. 最后 `resolve_pcie → resolve_l1`（依赖列表已 resolve，必须放最后）；
   `resolve_l3` 只对平面模式调用（`n_groups == 0` 时），轮换模式已在
   第 4 步走 `resolve_l3_rot`。

顺序即依赖：**先填编码，再算派生视图**。

#### 4.6 打印函数（L904–1072）

**`config_dump_template(fp)`（L904–976）**：整段输出 `configs/
default.conf` 的精确文本（一个超长 `fprintf`，带规则注释）。
`--dump-config` 与 default.conf 必须一致，单测 `test_template_roundtrip`
验证"模板 → 解析 → resolve → 表头"全链路等于 parity 基线。

**`dump_block`/`dump_event_list`**：`--check-config` 的排版辅助。
**`config_dump_resolved(fp, cfg)`（L1000–1072）**：把 resolve 后的
一切打印出来：全局项、每块 enabled/interval/k、tile 各组事件与列视图
（`tile_xxx  groups: 0@slot0 1@slot0`）、L3 列、PCIe merge 标志、
L1 选择向量、net 接口列表。**这是理解 resolve 结果最直观的工具**——
学本项目时强烈建议 `./collect_all -c configs/default.conf --check-config`
跑一遍对着看。

#### 4.7 PCIe 列构成与 CSV 表头（L1077–1188）

**`config_pcie_units(cfg, kind, idx, max)`（L1077–1108）**：产出每个
PCIe 块的输出列清单，`kind`/`idx` 是并行数组：

| kind | 含义 | idx |
|---|---|---|
| 0 | 合并的收字节列（`rx_bytes`） | -1（不需要下标，行写出时用 rx_idx[]） |
| 1 | 单个寄存器列 | `regs[]` 下标 |
| 2 | 合并的发字节列（`tx_bytes`） | -1 |

顺序：`rx_bytes` → 收方向单个寄存器（合并时跳过三元组）→
`tx_bytes` → 发方向单个寄存器。注意合并只作用于 BYTE 三连——
PKT 寄存器即使三个全配了也**永不合并**（它们不是三元组定义的一部分），
这是用"是否在 rx_idx 三元组里"判断的，而不是名字模式。

**`config_pcie_unit_name`（L1110–1119）**：kind→列名映射，
单寄存器列直接用 resolve 好的 `colname`。

**`config_write_csv_header(fp, cfg, p)`（L1121–1188）**——
表头生成器，**与引擎的行写出器严格镜像**（两边顺序必须一致，否则
CSV 错列）：

```
timestamp
[tile]   tile_group, tile_<col>...          （col 来自 tile_cols）
[tilenet] tilenet_<colname>...
[trio]    trio_...
[smmu]    smmu_...
[triogen] triogen_tx_dat_af, triogen_rx_dat_af   （固定 2 列）
[l3cache] 每个 half × 每列: l3half{i}_<name>
[pcie]    每个块 × config_pcie_units: pcie{i}_<name>
[l1]      按 l1_sel 过滤的固定 4 列（l1d_access...）
[cpu]     cpu_util_pct, cpu_min_pct, cpu_max_pct, cpu_avg_pct
[mem]     mem_total_kb, mem_used_kb, mem_util_pct
[net]     net_rx_bytes, net_tx_bytes
[gic]     gic_<colname>...
```

每个块的条件都是 `cfg->x.enabled && p->have_x`——**配置与硬件双条件**，
缺一则该块列整体消失。`p->n_pcies` 等实例数决定列组数量。
默认配置 + 4 tile/2 l3half/2 pcie/8 core 的 BF2 = 42 列。

---

## Part 5

### `code/collect_all.c` —— 采集引擎（1755 行）

**文件职责**：唯一接触硬件与内核的模块。结构：文件头设计文档 →
常量 → 全局状态 → sysfs helpers → 各块"发现/编程/读取"三件套 →
PMU → 软件指标 → 行写出器 → main。按"启动主线"和"主循环主线"
两条线索去读最省力。

#### 5.1 文件头注释（L1–56）

本身就是一份精炼设计文档：Purpose（12 块及其机制）、Timing model
（tick/k/`tick%k==k-1`、三种机制的窗口语义）、实测开销数据
（4 次写 ~0.22ms、16 次读 ~1ms，每秒 60 读 + 16 写 ≈ 窗口的 0.5%）、
Build & run 示例。**读代码前先读它**。

#### 5.2 常量（L78–91）

```c
#define MAX_TILES 8 ... MAX_IFACES 8   // 各硬件实例数上限
#define MAX_PATH_LEN 512               // 路径缓冲（注释给出最坏情况推导）
#define FDS_PER_CORE 4                 // 每核 PMU fd 数
```

BF2 实际是 4 tile/2 pcie/2 l3half，上限给足余量。`MAX_PATH_LEN` 的
512 来自：两个最长 255 字符的路径段拼 `"%s/%s"` 再加 NUL。

#### 5.3 全局状态（L97–167）—— 逐组解释

```c
static volatile sig_atomic_t g_running = 1;   // 运行标志（信号处理器改它）
static int  g_quiet = 0;                      // -q 静默
static bf2_config_t   g_cfg;                  // ★ 解析好的配置（全引擎唯一输入）
static bf2_presence_t g_pres;                 // 发现结果（喂给表头生成器）
```

`volatile sig_atomic_t` 是标准写法：信号处理器（L103）只做
`g_running = 0`，主循环轮询它。`sig_atomic_t` 保证对这个变量的
读写是原子的，不会撕裂。

**每块的状态数组（L106–149）**——统一的三件套模式：

| 状态 | tile 例 | 含义 |
|---|---|---|
| 计数 | `g_ntiles` | 发现到几个实例 |
| 可用标志 | `g_have_tile` | 初始化是否成功（决定列/采样标志） |
| 路径表 | `g_tile_path[8][512]` | 每个实例的 sysfs 目录路径 |
| 前值表 | `g_tile_prev[8][4]` | 每个实例每个槽的**上次读数**（差分用） |

例外：

- `g_tile_gid`（L108）：tile 当前活动轮换组；
- `g_l3_has_enable[]`（L137）：L3 每 half 是否有 enable 文件
  （决定门控 vs 差分模式）；
- `g_pcie_prev[4][12]`：PCIe 按**寄存器**（12）存前值；
- PMU：`g_fds[32][4]`（fd）+ `g_pmu_prev[32][4]`（前值）+
  `g_ncores` + `g_pmu_ok`；
- CPU：`g_cpu_prev_total[8]`（整机 8 个时间分量）+
  `g_cpu_prev_core[32][8]`（每核）+ `g_cpu_ncores`；
- 网络：`g_ifname[8][32]`、`g_if_prev_rx/tx[8]`、`g_nifaces`。

`prev` 系列是差分机制的载体：**每个 (实例, 槽) 一个前值**，
采样时 `cur - prev` 即窗口增量。

#### 5.4 sysfs helpers（L174–274）

**`file_read_all(path, buf, bufsz)`（L177–187）**：读整个文件到缓冲
（NUL 结尾）。两个细节：`fread` 失败时保存 errno 到 fclose 之后
（fclose 可能改 errno），返回 -1 并**保留真实 errno** 供警告信息用。

**`sysfs_write(path, val)`（L189–198）**：`fopen "w"` + `fprintf`。
sysfs 写入语义：向 hwmon 文件写字符串即向驱动下发命令。

**`sysfs_exists(path)`（L200–206）**：只探测存在性（`fopen "r"`）。

**`read_counter(dir, idx)`（L208–221）**：拼 `dir/counterN` 读出数值。
`strtoull(buf, NULL, 0)` 第三参 0 = 自动识别进制（sysfs 计数器是
十进制，但 base 0 也接受 0x 前缀，兼容性好）。失败时**只警告一次**
（`g_read_warned` 去重）并返回 0——不因单次读失败退出，让数据里
出现异常值也比整进程崩掉好。

**`read_reg(dir, name)`（L224–237）**：机制 2 的读法——文件名即
寄存器名。

**`write_event(dir, idx, code)`（L240–252）**：

```c
snprintf(path, ..., "%s/event%d", dir, idx);
snprintf(hex, ..., "0x%x", code);       // 编码必须是 hex 文本
sysfs_write(path, hex);
```

机制 1 的编程：**写 eventN 立即清零计数器并绑定新事件**。这个
"写即清零"的硬件语义是整个轮换与 L3 门控设计的基础。

**`program_block_events(paths, nblocks, evs, nev)`（L255–262）**：
把同一事件列表编程进多个实例目录（如 4 个 tile 都要绑组 0）。
`paths` 参数是 `char (*)[MAX_PATH_LEN]`——指向行宽 512 的二维
数组，等价 `char paths[][512]`。

**`sleep_sec_interruptible(sec)`（L266–274）**：

```c
struct timespec rem = { .tv_sec = sec, .tv_nsec = 0 };
while (nanosleep(&rem, &rem) == -1 && errno == EINTR) {
    if (!g_running) return -1;    // 被信号打断且要退出 → 通知调用方
}
return 0;
```

`nanosleep` 第二参 `rem` 返回剩余时间；被信号中断时 `EINTR`，循环
续睡剩余部分；若信号是 SIGINT/SIGTERM（`g_running==0`）则立即
返回 -1。**主循环据此退出且不写半行**。

#### 5.5 find_bfperf（L280–298）

扫描 `/sys/class/hwmon/hwmon0..15`，读每个的 `name` 文件，剥掉
换行后与 `"bfperf"` 比较。找到返回该目录路径——后面所有块目录都
挂在这个目录下。`mlxbf-pmc` 驱动没加载时找不到 → main 报
"bfperf not found. mlxbf-pmc loaded?" 退出。

#### 5.6 发现函数族——目录名模式匹配（L304–503）

四个 `discover_*` + 一个通用 `discover_named`，套路完全一致：

```c
DIR *d = opendir(base);                    // 打开 hwmon 目录
while ((entry = readdir(d)) && n < MAX) {
    if (strncmp(entry->d_name, "tile", 4) != 0) continue;   // 前缀
    if (entry->d_name[4] < '0' || entry->d_name[4] > '9') continue;  // 一位数字
    if (entry->d_name[5] != '\0') continue;   // ★ 精确一位数：排除 tile10、tilenet0
    snprintf(path, ..., "%.400s/%.16s", base, entry->d_name);  // 拼完整路径
    n++;
}
```

三个学习点：

1. **精确匹配的写法**：`"tile"` 前缀 + 第 5 字符是数字 + 第 6 字符
   是 NUL，三重条件把 `tilenet0`（第 5 字符是 `n`）和 `tile10`
   （第 6 字符是 `0`）都排除——目录枚举永远别假设只有想要的条目。
2. **`%.400s`/`%.16s` 精度限定**：gcc 对 `snprintf` 里 `%s` 会假设
   来源可能撑满目标数组，触发 `-Wformat-truncation` 警告；显式精度
   告诉编译器"来源最多这么长"，警告消失且更安全。
3. `readdir` 的 `d_name` 在下一次调用时会被复用，所以必须立刻
   复制（这里是立刻 `snprintf` 进路径表）。

**tile 发现**（L304）→ `MAX_TILES=8`；**tilenet**（L369，前缀 7 字符，
数字在第 8 位）→ 8 个；**trio**（L417，第 5 位数字——`triogen0`
第 5 位是 `g` 被排除）；**`discover_named`**（L465）把 smmu/gic 的
相同逻辑参数化（prefix 字符串 + 上限 + 目标数组）。

**`init_smmu`/`init_gic`（L487–503）**：发现 + 编程一步到位，返回
实例数（0 = 硬件上没有，调用方 WARN）。smmu 的实例数**决定列还是
不列**：发现 0 个 → `have_smmu=0` → 表头和行都省略该块。

#### 5.7 轮换块 tile（L304–363）

**`program_tile_group(gid)`（L330–335）**：组大小从配置读
（`g_cfg.tile.n_group_ev[gid]`），编程该组事件到每个 tile 的槽 0..n-1。
**写 eventN = 清零 + 绑定**，所以编程动作本身就是"开启新窗口"。

**`read_tile_baseline()`（L338–345）**：编程后立刻读一次当前值存进
`g_tile_prev`。为什么？因为编程与下次采样之间计数器已在计数，
若把编程瞬间当 0 起点，首窗口会**多算**编程到采样之间的时间。
基线读法让窗口严格从"编程后的那个瞬间"开始。注意它读的是
`g_tile_gid` 当前组——组切换时也要重读基线（main 循环末尾）。

**`read_tile_delta(delta_out)`（L348–363）**：

```c
for j: delta_out[j] = 0;                       // 先清零
for 每个 tile i, 每个事件 j:
    cur = read_counter(paths[i], j);
    d = (cur >= prev[i][j]) ? cur - prev[i][j] : 0;   // 回绕钳 0
    prev[i][j] = cur;
    delta_out[j] += d;                         // ★ 跨 tile 求和
```

输出是**按槽位下标**的数组（长度 = 当前组事件数），行写出器再经
`tile_cols` 的 `mask/slot` 映射到列。

#### 5.8 常驻差分块（tilenet/trio/smmu/gic/triogen）

**tilenet/trio**（L369–459）：与 tile 完全同构，但**没有轮换**——
`program_*` 只在初始化调一次，之后每个采样 tick 做 `cur-prev`。
**`read_multi_delta`（L505–521）** 把 smmu/gic 的差分逻辑通用化：
传路径表、实例数、事件数、前值表、输出数组即可。

**triogen（L539–564）** 是特殊的一个：

```c
const unsigned int codes[2] = { 0x0f, 0x10 };   // 事件是固定的，不来自配置
for (i = 0; i < 2; i++) {
    snprintf(path, ..., "%.502s/triogen%d", base, i);   // 直接按编号探测
    if (!sysfs_exists(path)) continue;                  // 不存在的跳过
    snprintf(g_triogen_path[g_ntriogens], ...);
    write_event(path, 0, codes[i]);                     // 用 0 号槽编程
    g_ntriogens++;
}
```

triogen 的目录就是 `triogen0`/`triogen1` 本身（不是 `triogenN` 里
再含槽位），每个目录用 slot 0 编程。事件固定（0x0f = TX_DAT_AF、
0x10 = RX_DAT_AF），所以配置里没有 `events` 键。
已知边角：若硬件上只有 triogen1 存在，它会被存进下标 0、值会被
输出到 `tx_dat_af` 列（列名按"两个都在"假设写死）——实机 BF2 两个
都在，此边角不影响使用，但读代码时值得留意。

#### 5.9 L3——门控机制（L570–667）

**`discover_l3halves`（L570–594）**：目录前缀是 `l3cachehalf`
（catalog 的 `dir_prefix`），并且**每发现一个 half 就探测它有没有
`enable` 文件**，记入 `g_l3_has_enable[]`——门控与差分的分支在
初始化时就定好。

**`l3_enable_all/l3_disable_all`（L603–632）**：对所有**有 enable
文件**的 half 写 1/0。写 1 的硬件语义：**清零并开始**；写 0：冻结。
只碰有 enable 的 half。

**`read_l3_baseline`（L635–643）**：只对**没有 enable**的 half 读前值
（差分模式需要）。

**`read_l3_vals(vals)`（L650–667）**——L3 读值核心：

```c
if (g_l3_has_enable[i]) {
    vals[i][j] = cur;                    // 有 enable：cur 就是窗口增量
} else {
    vals[i][j] = (cur >= prev) ? cur - prev : 0;   // 无 enable：差分
    prev = cur;
}
```

为什么有 enable 时"值即窗口"？因为主循环在采样 tick 开头先
`l3_disable_all()` 冻结再读，而上次采样结束时刚写过 enable=1
（那次写 1 已清零）——所以当前读数恰好 = 本窗口累计值。
**这是硬件语义配合调用时序的经典设计**，也是 L3 多速率"天然支持"
的原因：非采样 tick 完全不碰 L3，计数器跨 k 个 tick 持续累计。

**轮换模式（`n_groups > 0`）的增量**：

- `program_l3_group(gid)`（与 `program_tile_group` 同构）：把第 gid
  组的 `n_group_ev[gid]` 个事件编程进每个 half 的槽 0..n-1。
  平面模式的 `program_l3halves()` 在轮换模式下即分支到它；
- `read_l3_baseline/read_l3_vals` 的事件数不再是 `n_events`，而是
  **当前组**的 `n_group_ev[g_l3_gid]`（`g_l3_gid` 全局变量 = L3 当前
  活动组，镜像 tile 的 `g_tile_gid`）；
- 主循环的 s_l3 块扩展为四步：`l3_disable_all()` 冻结 → 读当前组
  （记下 `l3_gid = g_l3_gid` 供行写出用）→ **冻结期间**
  `g_l3_gid = (g_l3_gid+1) % n_groups; program_l3_group(g_l3_gid);`
  并对无 enable 的 half 重读基线（差分模式在换组后需要新前值）→
  `l3_enable_all()` 重启（写 1 清零）。
  **轮换在冻结窗口内完成**，所以组切换不丢计数时间；enable 写 1
  的清零语义天然保证新组从 0 起。

#### 5.10 PCIe——机制 2（L673–719）

**`discover_pcie_blocks`**：前缀 `pcie`。**`read_pcie_baseline`**
（L694–703）：初始化时把全部 (块, 寄存器) 的当前值读进前值表。
**为什么 PCIe 必须显式基线而机制 1 不用**：机制 2 寄存器自开机
累计、写不了也清不了，不读基线的话第一行会输出开机以来的天文
数字。**`read_pcie_delta`（L705–719）**：逐 (块, 寄存器) 差分，
回绕钳 0。注意 delta 按寄存器存，**合并发生在行写出层**（row_pcie
把 rx 三连相加），数据层与展示层分离。

#### 5.11 ARM PMU（L725–864）

**`perf_event_open(attr, pid, cpu, group_fd, flags)`（L725–730）**：
glibc 没有这个函数的包装，直接用
`syscall(__NR_perf_event_open, ...)` 调系统调用（这就是
`#define _GNU_SOURCE` 与 `<sys/syscall.h>` 的用途）。

**`make_attr(attr, cache_type, cache_op, cache_result)`（L732–748）**：

```c
attr->type   = PERF_TYPE_HW_CACHE;
attr->config = cache_type | (op << 8) | (result << 16);
```

perf 的 cache 事件编码 = 三级字段拼进 64 位 config。其余标志：
`disabled=1`（打开后先别计数，初始化完统一 RESET+ENABLE）、
`exclude_hv=1`（排除 hypervisor）、
`read_format = TOTAL_TIME_ENABLED | TOTAL_TIME_RUNNING`——这个
read_format 决定 `read()` 返回 24 字节（值+使能时长+运行时长），
供后面做多路复用缩放。

**`pmu_init()`（L750–809）**：

1. 读 `/proc/sys/kernel/perf_event_paranoid`，>1 时警告
   （普通用户开不了内核级计数，需要 sudo）；
2. `sysconf(_SC_NPROCESSORS_CONF)` 拿核数，上限 32；
3. 定义 4 个 attr：L1D 读访问 / L1D 读缺失 / L1I 读访问 /
   L1I 读缺失（顺序即 CSV 列顺序）；
4. **每核打开一个 4 fd 的组**（L782–803）：

   ```c
   int leader = -1;
   for j in 0..3:
       fd = perf_event_open(&attrs[j], -1, c, leader, 0);
       // pid=-1 表示"系统范围、绑定 CPU c"；group_fd=leader 表示同组
       if (fd < 0) { 关闭本核已开的 fd; break; }
       g_fds[c][j] = fd;
       if (j == 0) leader = fd;     // 第一个 fd 成为组长
   if (g_fds[c][0] >= 0) { cores_ok++; RESET; ENABLE; }   // 成功才启用
   ```

   组的意义：四个事件在 PMU 硬件计数器上**同进同退**（不会互相
   挤占导致不同时段只计一部分）。某核失败 → 该核整组关闭，
   其他核不受影响；全部失败才返回 -1。

**`read_pmu(cpu, idx)`（L811–829）**：

```c
struct { unsigned long long val, enabled, running; } buf;
read(fd, &buf, sizeof(buf));          // 一次读 24 字节
if (buf.running > 0 && buf.enabled > buf.running)
    buf.val = buf.val * buf.enabled / buf.running;   // 多路复用缩放
```

当 PMU 硬件计数器不够用、内核在事件间切换时，`running < enabled`，
缩放把部分时段的值按比例外推。注释即理由。

**`pmu_shutdown`（L831–844）**：DISABLE + close 全部 fd。
**`read_pmu_delta(delta_out)`（L846–864）**：跨核求和（跳过失败核），
每核每事件 `cur-prev` 回绕钳 0。

#### 5.12 软件指标（L870–1099）

**CPU（L870–986）**：

- `/proc/stat` 格式：首行 `cpu  各8分量`（整机），其后
  `cpuN 各8分量`（每核）。8 分量 = user/nice/system/idle/iowait/
  irq/softirq/steal。
- `cpu_init`（L870）把整机与每核当前值存入 `prev` 系列；
- `cpu_total`（L906）：8 分量求和；`cpu_busy`（L911）：
  **求和但跳过 v[3]（idle）**——利用率 = busy/total；
- `cpu_sample`（L918）：重读 → 整机利用率（`dt_busy/dt_total`，
  钳 0..100，`dt_total==0` 防除零）→ 每核利用率算 min/max/avg
  （`d_t==0` 的核跳过）→ 更新 prev。
  输出 4 个值 = CSV 的 `cpu_util_pct/min/max/avg` 四列。

**meminfo_sample（L988–1013）**：找 `MemTotal:` 与 `MemAvailable:`，
used = total − available，util = used/total。内存是**瞬时值**不是
增量——每行是当前快照，没有差分。

**net（L1015–1099）**：

- `net_add_iface(name)`（L1015）：读该接口 `statistics/rx_bytes` 与
  `tx_bytes` 的**当前值**存入前值表；
- `net_init`（L1037）两种模式：
  - 配置了 `interfaces =` → 只收录列出的接口（不存在的 WARN 跳过）；
  - 没配置 → 扫 `/sys/class/net`，跳过 `.` 开头的条目和 `lo`
    （回环无意义）；
- `net_sample`（L1072）：各接口 `cur-prev` 求和（回绕钳 0）。
  `rx_bytes` 是内核开机累计字节数，差分即窗口流量。

#### 5.13 fill_presence（L1105–1120）

把引擎全局的发现结果灌进 `g_pres`。值得注意的两行：

```c
g_pres.have_mem = 1;   /* /proc/meminfo always readable on Linux */
g_pres.have_l1  = g_pmu_ok;
```

mem 永远"存在"（Linux 必有 /proc/meminfo）；L1 的可用性取决于
PMU 是否打开成功。

#### 5.14 行写出器族（L1122–1266）—— 与表头严格镜像

共同签名风格：`(FILE *fp, int sampled, const ... *data)`。
`sampled=0` 时**只输出逗号**（空字段）；块禁用/硬件缺失时直接
return（一个逗号都不输出——表头也没有这些列）。

**`row_tile(fp, sampled, delta)`（L1122–1139）**：

```c
if (!b->enabled || !g_have_tile) return;
if (sampled) fprintf(fp, ",%d", g_tile_gid);   // 组号列：未采样时空
else         fprintf(fp, ",");
for 每列 c:
    if (sampled && (col->mask & (1 << g_tile_gid)))   // 当前组提供这列？
        fprintf(fp, ",%llu", delta[col->slot[g_tile_gid]]);  // 按槽位取值
    else fprintf(fp, ",");
```

`tile_cols` 视图（Part 4 §4.5）在这里兑现：mask 决定列是否有值，
slot 决定值在 delta 数组里的位置。

**`row_list`（L1141–1153）**：通用差分列表块（tilenet/trio/smmu/
gic 共用）——`b->n_events` 列逐一输出或留空。

**`row_triogen`（L1155–1167）**：固定 2 字段；只有 1 个 triogen 时
第一字段有值第二字段空。

**`row_l3(fp, sampled, vals)`（L1169–1189）**：

```c
for 每 half i, 每列 u:
    v = vals[i][col->ev0];
    if (col->ev1 >= 0) v += vals[i][col->ev1];   // 合并列 = 两 bank 求和
```

轮换模式下签名多一个 `l3_gid` 参数：先写 `,%d` 的 `l3_group` 标记列
（与 tile_group 镜像），再按 `l3_rot_cols` 的 mask/slot 视图取值——
`mask & (1<<gid)` 判该窗口是否有值，有值则把该组占用槽位
（合并列 2 个、单列 1 个）的读数求和。轮换列"该窗口留空（NaN）"
的语义与 tile 完全一致。

**`row_pcie(fp, sampled, delta)`（L1191–1222）**：每 pcie 块先调
`config_pcie_units` 拿列构成，kind 0/2 时把 rx/tx 三元组下标对应的
delta 相加，kind 1 直接取。**表头与行用同一个
`config_pcie_units`**——这是"镜像"不靠人肉保证、而靠共享代码保证
的范例。

**`row_l1`（L1224–1236）**：按 `l1_sel` 过滤输出。**`row_cpu`/
`row_mem`/`row_net`**：输出固定列（4/3/2 列），未采样全空。

#### 5.15 usage 与 main（L1272–1687）

**`usage(prog)`**：stderr 打印用法（信息命令/错误时调用）。

**main 六段结构**（与 architecture.md §5.1 对应）：

**① 参数解析（L1307–1335）**：手写循环逐个 `strcmp`。选项带值的
用 `argv[++i]` 消费下一个参数；`-i/-d` 立即做范围检查。
未知参数 → usage + 退出。

**② 信息模式（L1338–1345）**：`do_list`/`do_dump` 直接打印返回——
**不需要 root、不碰任何硬件**。

**③ 配置装载（L1348–1369）**：

```c
catalog_init();                    // 生成 tilenet 表（后面 resolve 要查）
config_set_defaults(&g_cfg);       // ★ 先填默认，解析只做覆盖
config_parse_file(...);            // 有 -c 才解析
-i/-d/-o 覆盖对应全局字段;
config_resolve(...);               // 全部校验+派生
if (do_check) { config_dump_resolved; return 0; }   // --check-config 到此为止
```

**④ 输出与信号（L1372–1386）**：打开输出文件（失败 ERROR）；
`setvbuf(out_fp, NULL, _IOLBF, 0)` 设**行缓冲**——每次 `fprintf("\n")`
后自动落盘，进程被杀已写完的行不丢；注册 SIGINT/SIGTERM。

**⑤ 初始化（L1392–1528）**：

- `need_bfperf`（L1394–1397）：**只有启用块里有机制 1/2 块**才找
  bfperf（软件-only 配置在普通 x86 Linux 上也能跑——这正是 x86
  冒烟测试的前提）。
- tile 初始化（L1408–1429）：发现失败是 **ERROR**（核心资源）；
  成功则 `program_tile_group(0)` + `read_tile_baseline()`——
  窗口 0 此刻开始。
- 其余块（L1432–1496）：各自"发现 → 编程 → 基线"，失败只 WARN
  （列省略）。
- L1（L1499–1511）：`pmu_init` 成功 → **立即读一次基线**
  （`g_pmu_prev`）——与 tile 基线同理，保证首行窗口准确。
- cpu/net 初始化（L1514–1522）。
- 表头（L1525–1526）：`fill_presence()` → `config_write_csv_header()`
  ——**先发现后表头**的落地。

**⑥ 主循环（L1533–1675）**：

L1533–1551 的注释是主循环时序的权威说明（窗口从 init 编程算起）。

```c
#define BLK_K(b) ((b).enabled ? (b).k : 1)
int k_tile = BLK_K(g_cfg.tile), ...;
```

**BLK_K 宏**（L1554）：禁用块的 k 可能被 resolve 算成 0
（interval=0 且被禁用等边角），宏强制为 1 防止 `%0` 除零崩溃。
宏定义范围限于这几个 k 变量，之后立即 `#undef`（L1585）——
宏卫生习惯。

循环体（与 §5.2 对照读）：

```c
if (g_cfg.duration > 0 && (time(NULL) - start_time) >= g_cfg.duration) break;
if (sleep_sec_interruptible(g_cfg.interval) < 0) break;   // ★ 先睡后采样

int s_tile = g_have_tile && tick % k_tile == k_tile - 1;  // ★ 采样条件
...（12 个 s_xxx，全部同构）
```

采样标志的完整条件 = **硬件存在 && tick 命中**。注意 `s_mem` 特殊：
`g_cfg.mem.enabled && ...`（mem 没有 ok 标志，`have_mem` 恒真）。

采样执行顺序：L3 门控（L1588–1594，disable→read→enable 一气呵成，
窗口闭合与重启之间只有几毫秒）→ tile delta → 常驻块 delta →
PMU → 软件 → `clock_gettime(CLOCK_REALTIME)` 时间戳 → 12 个
row_* 按规范顺序写行 → `\n` + `fflush` → 进度打印（每 10 行，
L1654–1662，展示 cpu_util/L1D/L1I/trio 数便于确认"在动"）→
**tile 轮换**（L1667–1672）：

```c
if (s_tile) {
    g_tile_gid = (g_tile_gid + 1) % ng;
    program_tile_group(g_tile_gid);   // 写 eventN = 清零 + 换绑
    read_tile_baseline();             // 新窗口基线
}
```

轮换放在**采样 tick 的末尾**：睡眠期间新组已经在计数，窗口不丢
时间。

**⑦ 清理（L1681–1686）**：`l3_disable_all()`（冻结，防止 L3 无意义
空转）、`pmu_shutdown()`、关输出文件。**无论正常结束还是被信号
打断都会走到这里**——中断只是提前 break，清理不跳过。

---

## Part 6

### `code/test_config.c` —— 主机单元测试（796 行）

**文件职责**：在 x86 主机（WSL）上验证 catalog + config 的全部纯逻辑。
**不碰硬件**——引擎（collect_all.c）不在被测范围，因为引擎的正确性
靠实机验收 + check_csv 兜底。

#### 6.1 基础设施

**`CHECK(cond)` / `CHECK_STR(a, b)`（L20–32）**：宏断言。注意
`CHECK_STR` 对 NULL 的防护（先判非 NULL 再 `strcmp`），失败时打印
"got/expected" 两行。两个全局计数 `n_fail/n_pass` 在 main 末尾汇总，
exit code = 有没有失败（可被 CI 直接消费）。

**`write_file(path, content)`（L34–43）**：把内联配置字符串写成
临时文件（测试 parser 需要真实文件路径）。

**`parse_resolve(content, cfg, errbuf, errsz)`（L46–57）**：测试专用
捷径——`config_set_defaults` → 写 `test_tmp.conf` → parse → resolve，
四步合一。**所有"解析类"测试都走这条路**。

**`render_header(cfg)`（L60–94）**：表头渲染捷径——

```c
FILE *fp = tmpfile();              // 匿名临时文件，测完自动回收
... 填一个假的 bf2_presence_t（4 tile/4 tilenet/2 trio/1 smmu/
     2 triogen/2 l3half/2 pcie/全部软件/1 gic——模拟实机配置）...
config_write_csv_header(fp, cfg, &p);
ftell 拿长度 → malloc → rewind → fread 读回 → 返回 malloc 的字符串
```

两个技巧：`tmpfile()` 避免管理临时文件生命周期；presence 手动填
"典型实机值"，让表头测试与真机同构（42 列 = 该 presence 下的
默认配置结果）。

#### 6.2 parity_line（L99–112）—— 项目的"宪法"

```c
static const char *parity_line =
    "timestamp,tile_group,"
    "tile_a72_access,tile_mem_reads,tile_mem_writes,tile_mss_nocredit,"
    "tile_dir_hit,tile_allocate,tile_victim_write,"
    "tilenet_cdn_req,tilenet_ddn_req,tilenet_ndn_req,"
    ...
    "net_rx_bytes,net_tx_bytes\n";
```

这是 8/13 BF2 实机验证的 42 列 CSV 表头**逐字节**。`test_defaults_resolve`
和 `test_template_roundtrip`、`test_case_insensitive`、`test_crlf_bom`
里都用它做最终断言。改任何默认值/列名/列序，这里立即红灯。

#### 6.3 15 个测试组逐一说明

| 测试 | 验证的规则 |
|---|---|
| `test_defaults_resolve` | 内置默认 resolve 成功；tile 2 组×4、7 列、mask/slot 值、各组编码（0x5d/0x4c/0x4d/0x67/0x61/0x6f/0x4e）；flat 块编码与 colname 覆盖；L3 配对（hits/misses 列 ev0/ev1）；PCIe 合并标志与下标；L1 全选；k 全 1；**parity gate** |
| `test_catalog_spots` | catalog 抽查：tile 编码、**生成式 tilenet DIAG 的 12 个编码**（type-major 公式的实证）、大小写不敏感、colname 覆盖、查不到返回 NULL |
| `test_template_roundtrip` | `--dump-config` 的文本 → 解析 → resolve → 表头 == parity（模板与内置默认等价） |
| `test_multirate` | 继承（interval=0）→ k=1；倍数 → k=5；非倍数 → 报错；负数 → 报错；**禁用块跳过倍数检查** |
| `test_errors` | 未知事件/未知 section/未知键/重复键/坏布尔/缺等号/超槽位/interval=0/duration 负数/重复 section/列表内重复事件——错误路径全覆盖 |
| `test_case_insensitive` | section/key/布尔/事件名大小写混合可解析，且**未覆盖的块保持默认**（tile 默认组还在），表头仍 parity |
| `test_crlf_bom` | BOM + CRLF + 行内 `;` 注释 + 小写事件名 → 正常解析，表头 parity |
| `test_replacement` | 只写 group0 → 单组轮换（默认组被清空）；events 键替换默认列表；重复 events 键报错 |
| `test_pcie_merge` | 缺 IN_C → rx 不合并（逐寄存器列）；PKT 寄存器永不合并；units 的 kind/idx 序列 |
| `test_l3_pair_rule` | 单 bank → 原名单列；两 bank 反序也合并成 hits；混合（单 bank + 成对）列序 |
| `test_l3_rotation` | L3 轮换：迷你 3 组配置（6 列、hits 掩码 = 组0+组2、合并列双槽位）；仅单 bank 场景；错误路径（bank 对拆到不同组 → "same group"、组不等长、组不连续、组内重复、未知事件） |
| `test_paper52` | 论文 52 计数器配置：从 `configs/paper52.conf` 加载（缺失则跳过），断言 tile 6 组/22 列、l3cache 8 组/20 列，重复事件掩码（a72_access/mem_reads/hits 各覆盖两组），其余块全部禁用，65 列完整表头逐字节比对 |
| `test_l1_subset` | l1_sel 过滤向量；未知 L1 事件报错 |
| `test_tile_errors` | 组不等长/组不连续/组内重复/组超 4 事件——tile 专属错误路径 |
| `test_net_ifaces` | 接口列表解析（保留大小写）；超长接口名报错 |

**main**：跑全部 15 组 → `remove("test_tmp.conf")` 清理
临时文件 → 打印 `N passed, N failed` → 失败返回 1。当前版本
**208 passed, 0 failed**。

学习用法：这些测试是**设计规则的可执行文档**——每条断言都在
回答"这个边界情况设计上应该怎样"。

---

## Part 7

### Makefile（根 17 行 + code/ 58 行）

**根 Makefile**：纯委托——`all/test/clean` 三个目标都 `$(MAKE) -C code`。
学习点：`$(MAKE)` 是递归 make 的正确写法（不要写 `make`），
`.PHONY` 声明伪目标。

**code/Makefile** 关键变量与目标：

```make
PROG  := collect_all
SRCS  := collect_all.c config.c catalog.c       # 三个模块一个二进制
TEST  := test_config
CROSS  ?=                                        # ?= 允许命令行覆盖
CC     := $(CROSS)gcc                            # CROSS=aarch64-linux-gnu- → 交叉
HOSTCC ?= cc                                     # 测试永远用主机编译器
CFLAGS  := -std=gnu11 -Wall -Wextra -O2 -g       # gnu11：strtok_r/clock_gettime 等
LDFLAGS := -lm -lrt                              # math + clock_gettime
```

| 目标 | 规则 | 说明 |
|---|---|---|
| `all` | 三源 → `collect_all` | 默认目标 |
| `$(TEST)` | `$(HOSTCC)` 编 test_config.c + config.c + catalog.c | **测试只依赖 config/catalog**，与引擎解耦——即使引擎在交叉环境下编不出来，测试也能跑 |
| `test` | 依赖 `$(TEST)` 然后执行 | 跑完打印 208 passed |
| `%.o` | 依赖 `config.h catalog.h Makefile` | 头文件变了自动重编 |
| `release` | 编译后 `strip` | 减小二进制 |

注意 `$(TEST)` 规则不用 `$(OBJS)` 而显式列出源文件，因为测试与
主程序**共享 config.c/catalog.c 源码**但用不同编译器。

---

## Part 8

### configs/default.conf 与 multirate.conf

`default.conf`（67 行）的内容 = `config_dump_template` 的输出 =
内置默认配置。逐段：

- L1–15 注释：直接说明三条核心规则（interval 倍数、NaN 语义、
  替换语义）——**配置文件的注释本身就是文档**；
- `[global]`：`interval=1`（每 1s 一行）、`duration=0`（永久）、
  `output=collector.csv`（写文件而非 stdout）；
- `[tile]`：两组轮换，A72_ACCESS 双组重复（100% 覆盖）；
- `[tilenet]` 3 事件、`[trio]` 4 事件、`[smmu]` 3 事件；
- `[triogen]`：只有 `enabled=true`——事件固定不可配；
- `[l3cache]`：HITS/MISSES × bank0/1 → 合并成 `hits`/`misses` 两列；
- `[pcie]`：6 个 BYTE 寄存器 → 合并成 `rx_bytes`/`tx_bytes` 两列；
- `[l1]` 4 事件、`[cpu]/[mem]/[net]` 只开不配；
- `[gic]`：`enabled=false` + 没有 events 行——**实机未验证默认关**。

`multirate.conf`（67 行）：与 default 的唯一差别——`[tile] interval=2`、
`[tilenet] interval=5`、`[l3cache] interval=2`（其余继承 1s）。
效果：每 5 行 tilenet 列才有值（行 4/9/14/...），每 2 行 tile/L3 有值
（行 1/3/5/...），其余块每行有值。头注给出 pandas resample 的示例
——**NaN 空字段在聚合时自动忽略**。

`paper52.conf`（78 行，2026-09-10 新增）：精确采集论文附录的 52 个
计数器。`[tile]` 6 组 × 4 = 22 个唯一事件（A72_ACCESS、MEMORY_READS
重复编入两组 → 2/6 窗口覆盖）；`[l3cache]` 8 组 × 4 = 30 个事件，
10 个 bank 对全部合并（每 half 20 列，HITS 对重复编入 G5+G7）；
其余块 `enabled=0`。完整输出 65 列：
`timestamp,tile_group,tile_×22,l3_group,l3half0_×20,l3half1_×20`，
轮换周期 tile 6 s / L3 8 s。单测 `test_paper52` 把这份配置的表头
逐字节锁定（防漂移门）。

---

## Part 9

### tools/check_csv.py —— CSV 不变量检查器（125 行）

**文件职责**：校验引擎输出 CSV 的结构正确性与**采样节奏正确性**。
stdlib only（argparse/csv/fnmatch/sys），设备上直接 `python3` 跑。

**检查清单（docstring L3–11）**：
1. 表头字段唯一；2. 每行字段数 = 表头；3. 时间戳单调不减；
4. `--period N --cols GLOB...` 时：匹配列必须**恰在**
   `行号 % N == N-1` 的行有值、其余行全空——这正是引擎
   `tick % k == k-1` 采样条件的镜像。

**逐段**：

- `fail(msg)`（L27）：打印 FAIL 并返回 False（所有检查返回 bool，
  主流程汇总）。
- **参数**（L33–41）：`file` 位置参数、`--period`（k 值）、
  `--cols`（通配符列表）；`--period` 无 `--cols` 或无值都是
  argparse 错误。
- **读入**（L48–53）：`csv.reader` 全量读成列表（采集输出量级
  完全可承受）；`newline=""` 是 csv 模块的标准要求。
- **结构检查**（L57–93）：
  - 至少表头 + 1 数据行；
  - **去重惯用法**（L69）：
    ```python
    dup = [h for h in header if h in seen or seen.add(h)]
    ```
    依赖 `set.add` 返回 None（假值）：第一次见到 h 时
    `h in seen` 为假 → 求值 `seen.add(h)` → None → 表达式假 →
    不进 dup；再次见到 h 时 `h in seen` 真 → 短路 → 进 dup。
    一行完成"找出所有重复项"。
  - 行宽一致、首列必须是 `timestamp`、时间戳 `int(row[0])`
    （try/except ValueError——非数字时间戳是数据损坏）且单调不减。
- **节奏检查**（L95–114）：`fnmatch.fnmatchcase` 匹配列名通配符
  （如 `tilenet_*`）；对每个匹配列逐行断言
  `filled == ((i % N) == (N-1))`，i 是 0 基数据行号（= tick）。
  一个列若全程没有匹配打印 WARN 提醒用户通配符写错。
- **exit code**：全过 0、任一失败 1（脚本可接 CI/验收流程）。

**用法场景**：设备验收时
`python3 tools/check_csv.py multirate.csv --period 5 --cols 'tilenet_*'`
一次性验证引擎的多速率调度在真机上符合设计。

---

## 结语：把整条链路串起来

最后用"一个 tick 的生命周期"收束全部模块：

```
配置文本(configs/default.conf)
   └─ config.c 解析 → resolve → bf2_config_t（catalog.c 提供编码）
引擎 main：
   发现硬件（collect_all.c discover_*）
   → 编程槽位（write_event：sysfs eventN）
   → 读基线（prev 系列）
   → 表头（config_write_csv_header）
   → [每 tick] 睡眠 → s_xxx 采样标志 → 各块读值/差分 → row_* 写行
   → [采样 tick 末] tile 轮换（program 下一组 + 基线）
产出 CSV → tools/check_csv.py 验证 → pandas 读入（NaN = 未采样）
```

配套文档：总体架构见 [architecture.md](architecture.md)，
事件语义总表见 [collected-events.md](collected-events.md)，
实机验收流程见 [acceptance-checklist.md](acceptance-checklist.md)。
