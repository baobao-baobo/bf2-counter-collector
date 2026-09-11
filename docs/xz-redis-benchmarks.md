# XZ 与 Redis Bench 深度解析（G1/G3 配套文档）

> 配套：docs/apps-path-experiment-plan.md（G1–G7 方案）、docs/e0-analysis.md（E0 定标）、
> docs/apps-ops-manual.md（操作手册）。本文档回答：这两个程序在做什么、执行时数据如何流动、
> 命令行参数各是什么含义、涉及哪些硬件、分别点亮哪些计数器路径。

## 1. 两个 bench 在实验中的角色

| | G1 xz | G3 Redis |
|---|---|---|
| 对应 PathFinder 应用族 | SPEC CPU（计算密集） | Redis + YCSB（网络服务） |
| 主要压测路径 | CR（核↔缓存↔内存） | NAD（PCIe 数据交付）+ CR + IH |
| 端数 | 单端（全部在 BF2 Arm） | 双端（server 在 BF2，client 在 fujian） |
| 预期主签名 | A72_ACCESS 高、L3 MISSES 显著 | pcie0/pcie1 字节≈网络流量量级且双向、net rx≈tx |

xz 是纯计算+访存负载：把 1GB 文件搬进 CPU、压缩、丢弃，不产生网络与磁盘输出，
用来把网格侧（CR）计数器打满。Redis 是网络服务负载：fujian 持续向 BF2 上的 Redis
服务发请求，把"网口→eSwitch→Arm"这条 NAD 数据通道点亮，同时服务端在 A72 上产生
常规的计算与访存。

## 2. XZ（G1）

### 2.1 xz 是什么

xz 是无损压缩工具（实验用 5.6.4），使用 **LZMA2** 算法，输出 **.xz** 容器格式
（带校验和，可选 CRC32/CRC64/SHA-256）。同类工具：gzip（LZ77+Huffman）、
bzip2（BWT）、zstd（LZ77+FSE）。xz 的特点是压缩率最高、CPU 消耗也最高——
这正是实验要的：让 A72 满负荷干活。

### 2.2 压缩原理（一分钟版）

LZMA2 属于"字典压缩"：在一个滑动窗口（字典）里找与当前数据相同的字节串，
找到就用"（距离，长度）"这对数字代替整串字节；找不到的字面量（literal）原样输出。
"匹配查找"是 CPU 最重的环节：每前进一个位置都要在字典里搜索。最后所有符号经
区间编码（range coder，算术编码的一种）压成比特流。

关键点：**随机数据不可压缩**。/tmp/g1.dat 由 `dd if=/dev/urandom` 生成，字典里
几乎找不到重复串，xz 花 100% 的时间在"查找→失败→输出 literal"上。结果是：
CPU 满转、字典窗口被高频扫读（访存密集）、输出体积≈输入体积。对实验而言这是
理想负载——压缩率不重要，重要的是它产生的内存访问模式。

### 2.3 G1 执行时的数据流

命令：`apps/bin/xz -c -9 -T 8 /tmp/g1.dat > /dev/null`

1. **读入**：xz 从 /tmp/g1.dat 顺序读 1GB（约 128KiB 大块缓冲读）；
   压缩结果写 stdout，被 `> /dev/null` 丢弃——内核立即完成写，不产生磁盘/网络流量。
2. **分块**：`-T 8` 开启多线程。xz 的 MT 模型是**按块并行**：输入切成块
   （块大小 = 3×字典，-9 时 3×64MiB = 192MiB），每个线程独立压缩自己的块，
   块与块之间互不依赖，因此能并行。1GB / 192MiB ≈ 5~6 块，8 个线程会有几个
   在末尾闲置（正常现象，不影响实验）。
3. **压缩**：每个线程持有自己的 64MiB 字典窗口，在窗口内做匹配查找，
   编码结果写入自己的输出缓冲，块流最后按序拼接成 .xz 流。
4. **内存占用**：-9 档压缩器每线程约 674MiB（字典 64MiB + 匹配器状态等），
   `-T 8` 理论峰值约 5.4GiB。跑之前 `free -h` 确认设备内存够（常见 16GB 无压力）。
5. **数据通路（访存侧）**：文件数据与字典窗口都在 DDR，每次匹配查找都产生
   核内/跨核缓存访问：A72 → L1D → 每 tile 共享 L2 → HNF（home node）→
   tilenet → SkyMesh → L3 → DDR。这就是 CR 路径——对应 tile 域的
   A72_ACCESS / RNF_REQUESTS / HNF_REQUESTS / MEMORY_READS / MEMORY_WRITES，
   以及 L3 域的 HITS/MISSES/ALLOCATIONS/EVICTIONS；字典窗口被反复改写，
   脏行写回（VICTIM_WRITE）也有活动。

**注意 /tmp 的介质**：若 BF2 的 /tmp 是 tmpfs（内存盘），g1.dat 整个在 DDR 里，
读取是纯内存路径；若 /tmp 挂在 eMMC 上，第一次读会经过 eMMC DMA（E0-3 已证明
该路径走 mesh RN-I，IO_ACCESS 会飙高），但第二、三次跑时 1GB 文件大概率已驻留
页缓存。跑之前 `mount | grep /tmp` 看一眼；若 run1 与 run2/3 的 IO 相关计数器
差异大，就是这个原因。

### 2.4 参数表

| 参数 | 含义 |
|---|---|
| `-c` | 输出到 stdout 而不是 .xz 文件（配合 `> /dev/null` 丢弃，消除输出侧 I/O） |
| `-9` | 预设级别最高档：64MiB 字典 + bt4 匹配器，每字节 CPU 消耗最大。预设 0~9（0 最快、字典 256KiB；9 最慢、压缩率最高）；另有 `-e`（--extreme）在 9 之上继续加大匹配器强度 |
| `-T 8` | 8 个压缩线程（按块并行，见 2.3） |
| `/tmp/g1.dat` | 输入文件（1GB 随机数据） |
| `> /dev/null` | 丢弃输出，消除输出侧 I/O |

验证：xz 退出码 0=成功、1=警告、2=错误，跑完 `echo $?` 应为 0。xz 在 8 核 A72 上
压 1GB 约需 15~30s，-t 40 的窗口足够；若提前结束，run_phase.sh 会记录实际 app_end，
split_path.py 按相位日志切分，不受影响。

### 2.5 涉及的硬件（单端，全在 BF2 Arm）

A72 八核（4 tile × 2 核）→ 每 tile 共享 L2 → HNF → tilenet（mesh 路由器）→
SkyMesh → L3 → MSS → DDR。CPU 侧全链；无网络、无 PCIe、无磁盘（输出被丢弃，
输入视 /tmp 介质而定）。在论文的数据通道分类里，这就是**核心访存通道（CR）**。

## 3. Redis（G3）

### 3.1 Redis 是什么

Redis（7.2.5）是内存键值存储（KV store）：所有数据放内存，网络侧用 RESP 协议
（文本协议：请求如 `SET key val`、`GET key`，回复如 `+OK`、`$128\r\n<值>`）。
服务端是**单线程事件循环**：一个线程用 epoll 同时监听所有客户端连接，来一个
请求处理一个。7.2 虽有 io-threads（读网络包的线程池），默认关闭，实验也不开——
单线程行为更可预测。

### 3.2 双端架构与网络路径

```
fujian（客户端/负载发生器）                 BF2 卡
┌──────────────────────────┐   ┌────────────────────────────────┐
│ redis-benchmark          │   │ 物理口 p1                       │
│  -c 64 个并发连接        │──▶│   ↓ 卡内 eSwitch + OVS(ovsbr1)  │
│  TCP:192.168.56.103:6379 │   │   ↓ Arm 侧 SF0(en3f1pf1sf0)    │
└──────────────────────────┘   │   ↓ mlx5 驱动 DMA → Arm 内存    │
                               │   ↓ 内核 TCP/IP 协议栈          │
                               │   ↓ redis-server(epoll 单线程)  │
                               │   ↓ dict 查找/插入(A72+DDR)     │
                               └────────────────────────────────┘
```

关键事实（E0-2 已实测）：56.x 是**卡内**网络——p1 的物理口、主机侧 PF（56.11）、
Arm 侧 SF0（56.103）被卡上 eSwitch + OVS 桥（ovsbr1）连在一起，fujian 发来的包
**不出卡**，在卡内转发给 Arm。这是论文 NAD 通道的教科书场景。

### 3.3 SET/GET 执行数据流（一次往返）

以 SET（写一个 128B 值）为例：

1. **客户端（fujian）**：redis-benchmark 的某个连接发一个 RESP 请求约 175B
   （`*3\r\n$3\r\nSET\r\n$16\r\nkey:...\r\n$128\r\n<128B>\r\n`），经 fujian
   网卡 → 物理链路 → BF2 的 p1 口。
2. **卡内转发**：eSwitch/OVS 把帧交给 Arm 侧 SF0 的接收队列；mlx5 驱动把它
   **DMA 进 Arm 的 DDR**——帧数据在 PCIe 上穿越（NAD 路径），这是 pcie0
   （SF0 收发队列 DMA）与 pcie1（eSwitch 交付链路）TLR 字节的来源；每次交付
   在 PCIe 上产生约 3 次穿越（E0-2 实测归一化约 2.8~3.7 字节/网络字节）。
3. **内核协议栈**：网卡中断/NAPI → skb → TCP 栈（校验、序号、拥塞控制）→
   socket 接收缓冲。驱动读 MMIO 寄存器体现在 IO_ACCESS（IH 路径）。
4. **redis-server**：epoll_wait 醒来 → read() 把请求拷进用户态 → 解析 RESP →
   计算键哈希（SipHash）→ 在 dict 中查找/插入 → 把 128B 值 malloc+memcpy 进堆 →
   这些是 A72 上的常规计算与访存（CR 路径）。
5. **回包**：`+OK\r\n`（5B）→ 内核发送路径 → mlx5 → DMA 读内存 → eSwitch →
   p1 → 物理链路 → fujian。

GET 类似：请求约 35B，回复约 135B（`$128\r\n<值>\r\n`），服务端做一次 dict 查找。

**流量账**：3M SET + 3M GET ≈ 3M×180B + 3M×170B ≈ 1GB 总线上流量——这就是 NAD
通道上要观测的字节量级，且 net 软件计数器的 rx 与 tx 应基本相等（环回签名）。

### 3.4 redis-server 参数表（BF2 上）

| 参数 | 含义 |
|---|---|
| `--bind 192.168.56.103` | 只监听 56.103（Arm 侧 SF0 地址）；不绑 0.0.0.0，避免监听无关接口 |
| `--port 6379` | 监听端口（Redis 默认端口） |
| `--save ""` | 关闭 RDB 快照（默认周期性 fork+bgsave 写磁盘，会污染计数器：fork 产生巨量 COW 内存流量） |
| `--appendonly no` | 关闭 AOF 追加日志（同样避免磁盘写入与额外内存拷贝） |
| `--protected-mode no` | 关闭保护模式（56.x 卡内隔离网络无公网暴露，已确认；否则非回环连接全被拒） |
| `--daemonize yes` | 后台守护进程运行 |

### 3.5 redis-benchmark 参数表（fujian 上）

| 参数 | 含义 |
|---|---|
| `-h 192.168.56.103 -p 6379` | 目标 server 地址/端口 |
| `-t set,get` | 只跑 SET 与 GET 两类测试（默认会跑十几类：PING/INCR/LPUSH/SADD/MSET…）；两个测试各自独立执行 |
| `-n 3000000` | 每类测试共 300 万请求（即 300 万 SET + 300 万 GET） |
| `-c 64` | 64 个并发客户端连接；请求在连接间分发，每条连接串行收发（未开 -P 管道） |
| `-d 128` | 测试载荷 128 字节（SET 写入的值、GET 读回的值） |
| `-q` | quiet 模式：每类只打一行吞吐（requests per second）；不加 -q 会额外打印延迟分位（p50/p95/p99）与直方图 |

输出怎么读：`SET: 123456.78 requests per second` = 每秒完成约 12.3 万次 SET。
3M 请求在 10 万 rps 量级下约 30s，落在 40s 应用窗口内。键名形如
`key:000000000000`，默认每条连接内递增（可用 `-r` 改成随机键空间）；本实验
不传 -r，键空间小、服务端内存占用低（约几十 MB）。

### 3.6 涉及的硬件（双端）

- **fujian（客户端）**：纯负载发生器。CPU 跑 benchmark 主循环、网卡收发；
  fujian 侧没有采集，它的状态与计数器无关。
- **BF2（服务端，观测对象）**：物理口 p1 → eSwitch/OVS → SF0 → PCIe TRIO DMA
  （NAD，pcie0/pcie1 TLR）→ Arm 内存 → 内核网络栈（mlx5 MMIO = IO_ACCESS，IH）→
  A72 单线程事件循环 + dict 操作（CR）→ 原路返回。
- 一张卡上同时出现网络入口、PCIe 交付、计算访存三类活动，这是 G3 的价值所在。

## 4. 与计数器/图的对应

| 路径 | 代表计数器 | G1 xz | G3 Redis |
|---|---|---|---|
| CR 核心访存 | A72_ACCESS, RNF_REQUESTS, HNF_REQUESTS, MEMORY_READS/WRITES | 高（主要活动） | 中 |
| IH I/O 入口 | IO_ACCESS, IO_READS, IO_WRITE | 低（视 /tmp 介质） | 有（mlx5 MMIO） |
| WB 写回 | VICTIM_WRITE, EVICTIONS | 有（字典窗口脏行） | 少量 |
| NAD PCIe 交付 | pcie0/pcie1 TLR 字节 | 近零 | ≈网络流量量级且双向 |
| net 环回签名 | net rx/tx | 近零 | rx≈tx |
| L3 | HITS/MISSES/ALLOCATIONS/EVICTIONS | MISSES 显著（大工作集） | 中等 |

产出图（tools/split_path.py）：fig1_path_profiles.png（应用×路径）、
fig2_l3_behavior.png（L3 堆叠）、fig3_conservation.png（守恒校验）。

## 5. 常见问题

- **Redis ping 报 DENIED**：protected-mode 未关（见 3.4），手册 4.1 已含修复命令。
- **xz 输出不比输入小**：正常，随机数据不可压缩，实验要的是访存负载而非压缩率。
- **benchmark 超过 40s 窗口**：`-n` 减半或 `-t` 加大；实际边界以相位日志为准。
- **run1 与其他跑的 IO 差异**：eMMC/页缓存冷热差异（见 2.3 的 /tmp 注意点）。
