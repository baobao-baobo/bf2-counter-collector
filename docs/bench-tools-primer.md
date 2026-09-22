# 基准工具速览（Batch 0 装了什么、为什么、怎么跑）

> 2026-09-22 晚 · Batch 0 环境搭建收尾整理。目标读者：明天早上的自己。
> 详细操作步骤与踩坑记录在 docs/ch5-cases-opsheet.md（§0.2 各小节），本文只讲"是什么、为什么、怎么跑、看什么"，不讲技术细节。

---

## 0. 背景：为什么要装这些

第五章验证要给 BF2 的 Arm 侧喂**各种口味的负载**（负载 = 让机器忙起来的工作），
然后看计数器怎么反应。负载分四种口味：

- **计算型**：纯 CPU 算数，不碰内存——EP 内核
- **内存型**：大量读写内存——lmbench、gups、NPB 的 MG/CG/FT
- **网络型**：收发网络流量——netperf、sockperf（BF2 的本职）
- **存储型**：读写磁盘（BF2 上是 eMMC 闪存）——sysbench fileio、LevelDB db_bench

另外还装了编译器 gcc/gfortran——它们是"造工具的机器"，上面的工具从源码变成
能跑的二进制都靠它们（BF2 没有外网，很多东西只能下载源码自己编译）。

所有编译好的工具统一放在设备上的 `/root/bf2k/bench/bin/`，
源码在 `/root/bf2k/bench/src/`，测试数据在 `/root/bf2k/data/`。

---

## 1. netperf —— 测网络"吞吐量"

**是什么**：最经典的网络测速工具，一对程序配合：`netserver` 在服务端等着，
`netperf` 在客户端发起测量，报告**吞吐量**（每秒能传多少数据，单位 Mb/s）。

**为什么**：BF2 是一张网卡，网络流量是它最本职的负载。三个案例轮次用它：
c1d 入向（fujian 发 → BF2 收）、c2b 出向（BF2 发 → fujian 收）、
c2c（BF2 → helong 的 BF2，走 100G 网口）。

**怎么跑**：

- 服务端（fujian 或 helong，占住一个终端别关）：
  `netserver -D -4`
- 客户端（BF2 上）：
  `netperf -H 对端IP -t TCP_STREAM -l 30` —— 30 秒测流，本端发送
  `netperf -H 对端IP -t TCP_MAERTS -l 30` —— 反过来，对端发送本端收

**看什么**：输出最后一行 `Throughput`，数字越大越快。
今晚 fujian 本机回环实测 28.3 Gb/s，属正常水平。

---

## 2. sysbench —— 瑞士军刀

**是什么**：多合一性能测试工具，一个程序能测 CPU、内存、文件读写、数据库等
多种项目，通过 `fileio`、`memory` 这样的"模式名"切换。

**为什么**：我们用两个模式——`fileio` 模拟磁盘读写压力（c1f 随机写案例）；
`memory` 测内存顺序带宽（批次 2 的 c4a）。另外它的 `prepare` 子命令负责
"备料"：提前生成 8 个 512MB 的测试文件（共 4GB，在 `/root/bf2k/data/`）。

**怎么跑**：

- 备料（已做完）：`sysbench fileio --file-num=8 --file-total-size=4G prepare`
- 随机写 30 秒：`cd /root/bf2k/data && sysbench fileio --file-num=8 \
  --file-total-size=4G --file-test-mode=rndwr --file-block-size=16K \
  --file-io-mode=sync --threads=4 --time=30 run`

**看什么**：结束时的统计——`reads/s`、`writes/s`（每秒操作数）、`MiB/s`（带宽）。

---

## 3. sockperf —— 测网络"包率"

**是什么**：跟 netperf 类似也是网络工具，但关注点不同：netperf 测**吞吐量**
（把水管灌满），sockperf 测**包率**（每秒钟能处理多少个独立的小包）。
包率对网卡来说往往比吞吐量更考验——每个包都要走一遍处理流程。

**为什么**：批次 3 的小包洪流案例用它（大量 1472 字节的小包猛打）。

**怎么跑**：`sockperf ul -i 对端IP -t 30 --mps=max --msg-size=1472`
（ul = under-load，即打流量模式；mps=max 表示不设上限、能打多快打多快）。

**看什么**：`msg/sec`（每秒消息数）。

---

## 4. LevelDB / db_bench —— 键值数据库

**是什么**：Google 的轻量级键值数据库（键值 = 像字典一样"钥匙→数据"的存取），
`db_bench` 是它自带的性能测试程序，可以指定"顺序写、随机读"等模式。

**为什么**：模拟"应用要频繁访问存储里的数据"的场景。今晚先用它建了 2GB 的
测试库（200 万个键、每个 1KB）。案例里两个对照轮：
c1e 用 64MB 小缓存 → 大量访问落在 eMMC 上（存储压力）；
c3e 用 2GB 大缓存 → 全部命中内存（与 c1e 形成"同一库、只差缓存大小"的对照）。

**怎么跑**：`db_bench --benchmarks=fillseq --num=2000000 --value_size=1000 \
--db=/root/bf2k/data/dbtest`
（fillseq = 顺序写入建库；readrandom = 随机读，案例里用）

**看什么**：输出中 `fillseq : ... micros/op; ... MB/s` 一行（每次操作耗时和带宽）。

---

## 5. lmbench —— 内存"体检"

**是什么**：90 年代的老牌微型测试套件，专测系统底层细节。我们只取两个小工具：

- `lat_mem_rd`：内存访问**延迟**（内存层级有多快——L1 最快、L2 次之、内存最慢）
- `bw_mem`：内存**带宽**（每秒能搬多少数据）

**为什么**：直接给 BF2 的 Arm 内存做体检，得到"内存层级"的基准数据。
案例批次 2 用它打延迟负载（c3c 小表、c3d 大表对照）。

**怎么跑**：

- `lat_mem_rd 16` —— 参数是步长（单位 64B/行），16 → 步长 1024B
- `bw_mem 1M rd` —— 对 1MB 区段测读带宽

**看什么**：lat_mem_rd 打一张两列表——左列区段越来越大，右列延迟（ns）逐级
抬升（今晚实测 2ns → 4.5ns → 8ns → 13ns 阶梯）；bw_mem 打一行"区段大小 +
带宽（MB/s）"。

---

## 6. NPB —— NASA 的科学计算五件套

**是什么**：NASA 出的经典并行计算基准集，五个内核（kernel = 一小段代表性的
科学计算程序），各自代表一种计算模式：

| 内核 | 全名 | 干什么 | 负载口味 |
|---|---|---|---|
| EP | Embarrassingly Parallel | 生成 5 亿个随机数、统计分布 | 纯计算 |
| IS | Integer Sort | 大数组整数排序 | 内存随机 |
| FT | Fourier Transform | 三维傅里叶变换（FFT） | 内存流式 |
| MG | MultiGrid | 多重网格解方程 | 内存密集 |
| CG | Conjugate Gradient | 共轭梯度解稀疏方程组 | 稀疏内存 |

S/A/B/C 是**题目大小档**（S 最小、C 最大），同一道题不同量级。

**为什么**：给 BF2 的 CPU 提供"像模像样的计算负载"，且五种模式锻炼不同部件，
正好用来验证计数器能不能区分不同负载类型（第五章的核心问题）。

**怎么跑**（今晚定下的统一姿势）：`env OMP_NUM_THREADS=4 taskset -c 0-3 \
/root/bf2k/bench/bin/ep.B.x`
——4 个线程、钉在 0–3 号核（4–7 号核被常驻进程占着，约定不碰）。

**看什么**：`Time in seconds`（用时）和 `Verification = SUCCESSFUL`
（计算校验正确——今晚 15 个二进制全部验证通过，说明工具链编译质量过关）。

---

## 7. gups —— 自写的随机访存测试

**是什么**：我们自己的 ~40 行小程序，模仿 HPCC RandomAccess 测试：对一张大表
做完全随机的"读-改-写"（随机抽一个位置、改一下、写回去），统计**每秒十亿次
更新（GUP/s）**。

**为什么**：随机访存是最"刁钻"的内存负载——顺序读有捷径（预取），随机访问
没有捷径可走，最能暴露内存系统的真实延迟。lg 参数选表的大小：
lg=15 → 256KB（在缓存内）、lg=20 → 8MB（超出缓存，真正打到内存）。

**怎么跑**：`gups 20 3`（表 8MB、跑 3 秒）。

**看什么**：`rate=0.018 GUP/s` 这类数字——越大越快。

**今晚的小故事**（一句话版）：第一版程序跑出来全是 0.001，三个表大小没有
区别——查出来是计时函数本身太慢（每次计时都要进内核，开销反而成了瓶颈）。
换成轻量计时后，256KB 表 0.221、8MB 表 0.018，相差 12 倍的"内存层级阶梯"
出现了。**教训：跑分之前先确认分数真实。**

---

## 8. 编译器（gcc / gfortran）

**是什么**：把人类可读的源代码翻译成机器可执行的二进制。gcc 管 C 语言
（gups、lmbench、LevelDB 用它），gfortran 管 Fortran 语言（NPB 用它，
Fortran = 科学计算界的传统语言）。

**怎么跑**：`gcc -O2 -o 输出名 源码.c`（-O2 = 开中档优化）。

**为什么费这么大劲装它**：BF2 没有外网，装不了现成的软件包，很多东西
只能拿源码自己编译；而设备镜像里又缺 Fortran 编译器，为补它花了今晚
最长的功夫（最终方案：gcc-9 全家 + gcc-10 运行时双链升级，18 个包一条命令）。

---

## 9. 今晚定好的"档位"速查（明早验收用）

**NPB 定档**（口径：4 线程、钉 0–3 核，目标单轮 20–40 秒）：

| 内核 | 档位 | 实测用时 | 跑法 |
|---|---|---|---|
| EP | B | 20.0s | 单发 |
| IS | C | 19.5s | 单发 |
| FT | B | 41.6s | 单发 |
| MG | B | 10.7s | 连跑 2 遍 |
| CG | A | 2.7s | 连跑 10 遍 |

**gups 三档**（表大小 → 速率）：lg15/256KB → 0.221（缓存内）、
lg18/1MB → 0.029、lg20/8MB → 0.018（真内存）。案例用 lg=20。

**数据落位**：`/root/bf2k/data/dbtest`（LevelDB 2GB，508 个文件）、
`/root/bf2k/data/test_file.0–7`（sysbench 8×512MB=4GB）。

---

## 10. 一句话速查表

| 工具 | 一句话 | 典型命令 | 关键输出 |
|---|---|---|---|
| netperf | 测网络吞吐量 | `netperf -H IP -t TCP_STREAM -l 30` | Throughput Mb/s |
| sysbench | 多合一（文件/内存） | `sysbench fileio ... run` | reads/s、MiB/s |
| sockperf | 测网络包率 | `sockperf ul -i IP -t 30` | msg/sec |
| db_bench | 键值库性能测试 | `db_bench --benchmarks=fillseq ...` | micros/op、MB/s |
| lat_mem_rd | 内存延迟阶梯 | `lat_mem_rd 16` | 尺寸→ns 表 |
| bw_mem | 内存带宽 | `bw_mem 1M rd` | MB/s |
| NPB 五内核 | 五种科学计算负载 | `env OMP_NUM_THREADS=4 taskset -c 0-3 ep.B.x` | Time in seconds |
| gups | 随机访存速率 | `gups 20 3` | GUP/s |
| gcc/gfortran | 编译工具 | `gcc -O2 -o a a.c` | （产出二进制） |
