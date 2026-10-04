# Motivation 支线计划书：多租户难定位假设（2026-10-01）

> 支线来源：师兄提供的 DPUSCOPE 论文 §3 截图（fig/example.jpg；OCR 文字档
> docs/dpuscope-excerpt-ocr.txt，匿名投稿稿，未入库 git）。任务：以该文本为参考，
> 为论文 Motivation 提出"多租户难以定位瓶颈"假设，并设计支撑实验。

---

## 1. DPUSCOPE §3 解读（参考文本讲了什么）

DPUSCOPE 是一篇匿名在审的 DPU 干扰诊断论文，§3 用**受害者负载（victim）+ 干扰者
（interferer）+ 撤离实验（withdrawal，让干扰者退出看受害者是否恢复）**三件套建立
三条论点。新名词解释：受害者 = 被观察性能的"正主"负载（他们用 SHA 吞吐）；干扰者
= 与受害者同机竞争的别的租户负载；撤离 = 人为终止干扰者，验证受害者指标是否回到
基线——恢复说明干扰者确实"参与"，但不说明"机理"。

- **§3.1 不同边界暴露不同症状**：同核忙循环让受害者吞吐掉 49.82%，而提交到完成
  P99 只变 0.53%；三个 256MiB 内存工作者（跨核）则让 P99 涨 11.71%、吞吐只掉
  1.92%。结论：软件线程决定工作何时到达异步硬件引擎；只盯一个观测面（吞吐或
  尾延迟）都会漏看问题，**必须两类证据都拿**。
- **§3.2 高活跃不足以归责**：主机侧 RDMA 写打满 ~95.8Gb/s，受害者吞吐只变 0.18%、
  P99 变 2.21%；原生计数器扫描（§6.3）同样显示共享内存活动剧变 ≠ 受害者成比例
  变慢。结论：**繁忙资源只是干扰"候选"，不是"租户标签"**——所有权与共享域范围
  必须先约束候选，再测试。
- **§3.3 撤离证责任、不证唯一机制**：同核干扰者退出后吞吐回到基线 99.77%；内存
  工作者撤离后吞吐与 P99 平均回到基线 0.12% 以内。恢复支持"共驻贡献了退化"，但
  区分不了缓存/mesh/内存控制器。结论：**租户责任与机制归因必须分离**，保留未决
  备选。
- **表 1（可操作诊断报告）**：每个观测只支持"中间一列"的主张——观测 → 支持的主张
  → 下一步需要的证据。这是"测量到归因"的诚实阶梯，我们下面直接仿用。

## 2. 我们的 Motivation 假设（双语，供论文）

**中文**：多租户共驻的 DPU 上，性能问题定位难，难在三点——①**边界错位**：症状
未必出现在你盯着的观测面上，单一边界（吞吐、单域计数器）系统性漏报；②**活跃
≠归责**：最繁忙的资源往往只是候选而非元凶，高活跃与真瓶颈之间没有等号；③**撤离
不定机制**：撤离嫌疑租户能证"参与"、不能证"机理"。因此定位需要一个**系统化的
多边界计数器引擎**——把观测证据与归因主张严格分级对应，本文的 PRISM 即为此设计。

**English**: On multi-tenant DPUs, bottleneck localization is hard for three reasons:
(i) boundary mismatch — symptoms may not appear on the observed plane, so a single
boundary (throughput, or one counter domain) systematically under-reports;
(ii) activity ≠ blame — a busy resource is a candidate interferer, not a culprit,
and high activity does not equal the true bottleneck; (iii) withdrawal proves
responsibility but not mechanism. Localization therefore requires a systematic
multi-boundary counter engine that maps each measurement to exactly the claim it
supports — which is what PRISM provides.

## 3. 证据复用映射（现有数据能支撑多少）

我们的存量数据**已经覆盖三条论点的大部分**，且不少是 DPUSCOPE 没有的反面教材
（连计数器都会骗人）。逐条映射（文件名按操作单记录表）：

| 论点 | 已有证据（轮次/文件） | 支撑方式 |
|---|---|---|
| ①边界错位 | E2 armsend 出向：ih 1.000 与 nad 0.980 近平局（results/ e2_armsend 系列）——同一负载两个边界双亮，只看一个必漏 | 单边界漏报 |
| | c2b 出向（results/ch5_c2b_netout_run2.csv）：arm 顶点饱和 n=0.967 打满锚点 → 份额路由给 nad、头名漂移——"最大计数器"不是真瓶颈 | 边界饱和扭曲判断 |
| | E0-1 NHD"34.5Gbps"幻象（F1 重证）：打流姿势错误（192.168.101.1 本机地址、Linux local 表本地投递）→ 假症状；重证后 pf1hpf_tx/p1_rx=1.0000 才是真 NHD | 错误边界=假症状 |
| | G6 计数口径漂移：同一 catch-all 规则对 NHD 从 23% 到 100% 计数（配置漂移）；netdev 5s 锯齿 vs TLR 稳态两种口径两种故事 | 同一对象不同口径结论不同 |
| | 执行前检查实测（2026-10-01）：DRAM 指针追逐 14.7ns vs 教科书 150ns——预期边界与设备现实错位 | 直觉不可靠 |
| ②活跃≠归责 | c1e 纯读点亮 io_write 115×（results/ch5_c1e_dbmiss_run1.csv）——计数器"高活跃"（io 域 115 倍）却判 low，eMMC 读写方向语义反直觉 | 高活跃≠瓶颈 |
| | c1a EP 纯计算（results/ch5_c1a_ep_run1.csv）：预期 low 实测 cr 0.535 中档——"看起来像内存压力"的假信号 | 假信号 |
| | G6 tilenet 三窗 59/59 全零：内存满载（a72 均值 180.4M/s）下网络 tile 分文不动 | 跨域活跃不传导 |
| | c2c run3（results/ch5_c2c_host_run1.csv）：p1 5.5Gbps 洪流穿行、Arm tile 近空载——传输繁忙但 Arm 无责 | 传输≠Arm 责任 |
| ③撤离不定机制 | c1e run1/run2：drop_caches 撤离页缓存 → 计数逐列一致（io_write 0.0932 vs 0.0921）——撤离未改变症状 → 机制另有其人（eMMC 设备视角语义） | 撤离证伪唯一机制 |
| | 0x71/0x4a/0x53/0x67 四计数器失效（五轮排除定案）——观测手段本身不可靠，定位更难 | 观测不可靠 |
| | 批次一 8 轮 5 个预期修订（A 类）——连拥有全套计数器+标定引擎的我们，第一猜测错 5/8 | 定位难可量化 |
| 论文仿用 | 判读标准表 + A/B/C 三类诊断协议（docs/validation-replay.md §8.6）≈ DPUSCOPE 表 1 的观测→主张→下一步证据阶梯 | 方法论同构 |

**复用结论**：①②③都有存量证据，Motivation 的定性叙事几乎零成本可写。**唯一缺口**
：我们从未做过"受害者负载吞吐的退化+恢复"闭环——所有轮次都只有计数器侧的观测，
没有受害者应用级指标（吞吐率）与撤离恢复的成对证据。这正是 DPUSCOPE §3 的骨架，
也是假设叙事最硬的那根钉。

## 4. Case 7 提案：受害者-撤离迷你实验（5+1 轮，补上缺口）

### 4.1 受害者选型（10/01 定案：db_bench 为主）

- **gups**：决定性（~2.3GB/s DRAM 随机压力与干扰者正面竞争，降幅预计 30-60%）、
  时长精确、零自噪；但"应用味"为零（自研微内核）。
- **db_bench readrandom（内存驻留口径）**：应用味足（DPU 上的 KV 存储服务租户，
  LevelDB 真实栈）；DRAM 流量仅 ~100MB/s 量级、以 CPU+延迟敏感为主，m2 内存干扰
  的降幅预计温和（5-20%）——**这恰是 DPUSCOPE 自己的模式**（其内存工作者案例仅
  1.92% 吞吐/11.71% P99，同核案例 49.82% 才是大头），叙事反而更贴范本。
- **定案**：db_bench 为主（师兄侧重应用）；gups 降为可选第 6 轮决定性对照（成本
  仅 +90s），在 db_bench 的 m2 降幅 <5% 时启用，作为"内存干扰确实存在、只是
  db_bench 不敏感"的决定性注脚。
- **致命前提**：受害者必须用内存驻留口径（cache_size=2GB + 每轮三步预热协议，
  即 c3e 口径）。若用 c1e 的 64MB 小缓存口径，受害者瓶颈在 eMMC（c1e 已证
  io_write 115×），内存干扰者打不动它，m2 直接废掉。

### 4.2 每轮通用三步预热协议（run_phase 之前执行）

```bash
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=2147483648 --reads=2000000 \
  --db=/root/bf2k/data/dbtest > /dev/null
/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=2147483648 --reads=2000000 \
  --db=/root/bf2k/data/dbtest > /dev/null
```

（原理：db_bench 的块缓存随进程退出消失，预热真正保温的是 Linux 页缓存——2M 读
×2 把整库读进页缓存，正式轮的读全从内存命中，不再碰 eMMC。）

| 轮 | 场景 | 命令要点 | 期望（先写死，判读对账） |
|---|---|---|---|
| m1 | 受害者基线 | db_bench 3M 读（2GB 缓存）钉核 0 | ops/s 基线；计数器低-中档 |
| m2 | +内存干扰者 | +sysbench memory seq ×3 线程(核1-3) 60s | ops/s 温和下降（5-20%）；流签名（cr/wb）抬升 |
| m3 | 撤离（重跑受害者） | 同 m1 | ops/s 恢复 ≈m1（±5%）→ 责任成立 |
| m4 | +繁忙传输 | +fujian→helong 经 p1 的 netperf 60s（c2c 姿势，helong netserver 前置） | ops/s ≈m1（±5%）；tx/nhd 高涨而 Arm 域不动 → 活跃≠归责 |
| m5 | +同核忙循环 | +`while true; do :; done`(核0) 60s | ops/s 明显下降（≥30%，同核抢占）→ 与 m2 签名可区分 → 边界判别 |
| m6（可选） | gups 决定性对照 | 受害者换 gups 20 60，干扰者同 m2 | rate 较 0.018 GUP/s 降 ≥30% |

```bash
# m1 受害者基线（3M 读 ≈50s；速率落 /tmp/m1_victim.log 末行 micros/op）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m1_victim_run1.csv \
  -a "taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m1_victim.log 2>&1" -b 0-3 -t 120

# m2 内存干扰（sysbench 3 线程流式读占核 1-3，与受害者并发）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m2_memintf_run1.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m2_victim.log 2>&1 & \
  taskset -c 1-3 sysbench memory --memory-block-size=1G --memory-scope=global \
  --memory-total-size=64G --memory-oper=read --memory-access-mode=seq --threads=3 \
  --time=60 run > /tmp/m2_intf.log 2>&1 & wait'" -b 0-3 -t 120

# m3 撤离（独立上下文重跑受害者，与 m1 同命令）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m3_withdraw_run1.csv \
  -a "taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m3_victim.log 2>&1" -b 0-3 -t 120

# m4 繁忙传输（BF2 上跑受害者；fujian 上另开终端、窗口内起 60s 洪流穿越 p1）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m4_busytrans_run1.csv \
  -a "taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m4_victim.log 2>&1" -b 0-3 -t 120
#   fujian（BF2 窗口启动后 5s 内）：netperf -H 10.99.99.3 -t TCP_STREAM -l 60
#   前置：helong BF2 上 netserver -D -4 仍在跑、fujian 能 ping 通 10.99.99.3

# m5 同核忙循环（纯用户态空转占同核，60s 后 timeout 自止）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m5_samecore_run1.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m5_victim.log 2>&1 & \
  taskset -c 0 timeout 60 sh -c \"while true; do :; done\" > /dev/null 2>&1 & wait'" \
  -b 0-3 -t 120

# m6（可选）gups 决定性对照（受害者换 gups lg20，干扰者同 m2）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m6_gupsctl_run1.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/gups 20 60 > /tmp/m6_victim.log 2>&1 & \
  taskset -c 1-3 sysbench memory --memory-block-size=1G --memory-scope=global \
  --memory-total-size=64G --memory-oper=read --memory-access-mode=seq --threads=3 \
  --time=60 run > /tmp/m6_intf.log 2>&1 & wait'" -b 0-3 -t 90
```

回传（与批次 2 同包或单独）：

```bash
tar czf /tmp/ch5_case7.tar.gz results/ch5_m*.csv results/ch5_m*.phase.log /tmp/m*_victim.log
```

判读口径：受害者速率 = db_bench 输出末行 `readrandom : ... micros/op; ... MB/s`，
ops/s = 1e6/micros（跨轮比相对变化；gups 则读 `rate=` 行）；计数器判读用
`python tools/prism_search.py results/ch5_mN_*.csv --scene mN`（与批次一相同流程）；
m2 与 m5 的路径判别（内存流 vs 同核计算）是 PRISM 判别力的直接演示。任何一轮与
期望不符 → docs/validation-replay.md §8.6 三类诊断，不阻塞叙事（负面结果同样入账）。

> **2026-10-04 执行结案**（详情 docs/case7-results.md）：五轮一次通过、应用级四条
> 期望全命中（m2 −11.6% / m3 −0.5% / m4 −2.1% / m5 −49.0%）；计数器级同步
> （cr：m1 0.365 → m2 0.820 → m3 0.355；m4 网络顶点首亮而内存域不动；m5 cr
> 腰斩 0.178 触发 A 类修订——纯计算干扰对计数器不可见，论点① 的最强演示）。
> m6 未启用（m2 降幅 ≥5%）。三论点闭环清单见结案记录 §4。

## 5. 与论文衔接

- **落点**：Motivation 段按 §2 三点假设铺开，每点配一个最硬的存量证据（建议
  ①用 E0-1 NHD 幻象或 c2b 头名漂移、②用 G6 tilenet 零传导或 c2c 洪流、③用 c1e
  drop_caches 撤离证伪）；Case 7 的 m1-m5 作为"可控闭环"收尾（m2→m3 恢复、m4 活跃
  无责、m5 双边界判别），仿 DPUSCOPE 表 1 做成"观测→主张→下一步证据"小表。
- **相关工作**：DPUSCOPE 若已发表则补引；其"租户责任与机制归因分离"与我们的
  三层索引引擎（§4.6）互补——它做受控实验分离责任，PRISM 做计数器侧机制定位。
- **成本**：5+1 轮 ≈ 15-20 分钟设备时间（每轮含三步预热协议 ~2 分钟；可并入
  批次 2 同一上机时段）+ 判读 ~1 天。

## 6. 待定/风险

- 受害者已定 db_bench（内存驻留口径，见 §4.1 选型说明）；若师兄最终想要更决定性
  的数字，启用 m6（gups 对照）。
- m2 若速率降幅 <5%（BF2 内存余量大 / 受害者 DRAM 需求低）：如实记录并启用 m6；
  或复跑时把干扰者换 rnd 模式。
- db_bench 轮间速率漂移：以 m1/m3 同协议（drop_caches+双预热）控制温度；若 m1
  与 m3 差 >5%，先查预热是否漏做（判读流程同 c1e 的页缓存排查）。
- m4 依赖 helong netserver 存活；若 10.99.99.3 不通，退回 c2b 姿势（BF2→fujian
  出向，Arm 参与度高，预期要改）。
