# 计数器失效探针实验操作单（counter-failure-probe）

> 背景：paper52 采集的 22 个 tile 计数器中有 4 个无有效信号——tile_a72_write
> （恒定 48/s）、tile_rnf_requests（恒 0）、tile_poc_reads（恒 0）、
> tile_mss_nocredit（恒 0）。本地已核查：四个编码在官方文档与驱动事件表中
> **全部正确**（排除编码错误/驱动拒绝）；驱动与采集器从不编程 HNF_PERF_CTL
> （0x4a RNF_REQUESTS 需要 RNF_SEL 先选中计数对象）。本实验用 sysfs 直控
> （**不经 collect_all**）逐个验证四个计数器"失效"的真实性质：
> 配置缺失 / 负载未触发 / 硅片观测点问题。每个结果都有对应的论文处理方式。
>
> 执行者：用户。角色：【BF2】= ssh 登录 192.168.100.2 的设备终端；仅
> E2-L5 需要【宿主机】= fujian。
> **【BF2】所有命令先 `cd /root/bf2k` 再执行**。sysfs 编程是易失的
> （重启即还原），实验结束无需清理。结果 log 回传后由 Claude 判读。
> 预计总时长约 1.5–2 小时。
>
> **状态（2026-09-17）：全实验完成，四个计数器的失效性质全部查清，定论见 §10。**

---

## 0. 准备阶段（一次性）

**0.1 找 bfperf 的 hwmon 路径**，下文所有 `$H` 都指它：

```bash
grep -l bfperf /sys/class/hwmon/hwmon*/name
```

输出形如 `/sys/class/hwmon/hwmon3/name` → 把它 export 给 `$H`（**每次新开
ssh 会话都要重新 export**）：

```bash
export H=/sys/class/hwmon/hwmon3    # 换成上面 grep 的输出
```

确认 tile 目录数量（**2026-09-17 实测修正：预期 8 = tile0–3 共 4 个
HNF tile + tilenet0–3 共 4 个 tilenet tile**，不是 8 个 HNF 分片。
probe 脚本的 `$H/tile*` 会两者都编程/读取；tilenet counter 恒 0x0，
对求和无害）：

```bash
ls -d $H/tile* | wc -l    # 预期 8（4 tile + 4 tilenet）
```

**0.2 确认 bench 二进制齐全**：

```bash
ls /root/bf2k/bench/bin/{memrand,stream,stress-ng,fio,iperf3}
```

**0.3 确认 stress-ng 有 futex stressor**（E2-L3 用；无输出 = 不可用）：

```bash
/root/bf2k/bench/bin/stress-ng --help 2>&1 | grep -i futex
```

**0.4 保存两个小工具脚本**（照抄即可，之后所有步骤靠它们）：

```bash
cat > /root/bf2k/probe_prog.sh <<'EOF'
#!/bin/sh
# probe_prog.sh - program the 4 slots of every tile.
# Usage: sudo ./probe_prog.sh <hwmon_dir> <code0> <code1> <code2> <code3>
# Writing eventN resets counterN and rebinds it to the new event.
[ $# -ne 5 ] && { echo "usage: probe_prog.sh <hwmon_dir> <c0> <c1> <c2> <c3>"; exit 1; }
H=$1
for t in $H/tile*; do
  echo $2 > $t/event0
  echo $3 > $t/event1
  echo $4 > $t/event2
  echo $5 > $t/event3
done
EOF
cat > /root/bf2k/probe_sample.sh <<'EOF'
#!/bin/sh
# probe_sample.sh - sample the 4 slots of every tile once per second.
# Usage: sudo ./probe_sample.sh <hwmon_dir> <seconds>
# Prints "sec slot0_sum slot1_sum slot2_sum slot3_sum" summed over all
# tiles.  Counters are CUMULATIVE (reading does NOT clear, programming
# does): discard the first row (it includes programming-time counts),
# per-second rates are the differences of the later rows.
[ $# -lt 2 ] && { echo "usage: probe_sample.sh <hwmon_dir> <seconds>"; exit 1; }
H=$1
N=${2:-30}
for s in $(seq 1 $N); do
  s0=0; s1=0; s2=0; s3=0
  for t in $H/tile*; do
    s0=$((s0 + $(cat $t/counter0)))
    s1=$((s1 + $(cat $t/counter1)))
    s2=$((s2 + $(cat $t/counter2)))
    s3=$((s3 + $(cat $t/counter3)))
  done
  echo "$s $s0 $s1 $s2 $s3"
  sleep 1
done
EOF
chmod +x /root/bf2k/probe_prog.sh /root/bf2k/probe_sample.sh
```

> ⚠️ 写 sysfs 需要 root：BF2 终端若是 root 直接跑，否则每条命令前加 sudo。
> ⚠️ 实验期间设备不要跑别的负载（collect_all、iperf3 服务端等先停掉），
> 否则背景噪声污染判读。

> 槽位约定（本实验全程）：
> **Pass A** = event0:0x71 A72_WRITE、event1:0x4a RNF_REQUESTS、
> event2:0x53 POC_READS、event3:0x5d A72_ACCESS（**活对照**，必须有值，
> 否则编程/读取链路本身有问题，全实验暂停排查）。
> **Pass B** = 0x71、0x4a、0x67 MSS_NO_CREDIT、0x5d。

**0.5 memrand 写模式修复版确认（2026-09-17）**：E2-L1 要跑 `-w` 写模式，
而仓库里的 `bench/bin/memrand` 仍是旧二进制——旧版把 stamp 写进块首
8 字节（覆盖了链表指针），写模式第二次访问同一块即段错误。先冒烟验证
（应打印两行统计，不应段错误）：

```bash
cd /root/bf2k
taskset -c 0 bench/bin/memrand -s 64 -b 64 -d 10 -w
```

若段错误，用修复版源码重建（stamp 改写到偏移 8、写模式要求
stride >= 16）：

```bash
cat > /root/bf2k/bench/memrand.c <<'EOF'
/*
 * memrand - random-access memory microbenchmark (pointer chase)
 *
 * Replaces sysbench --memory-access-mode=rnd for the BF2 bench campaign:
 * sysbench needs autotools + bundled LuaJIT to build, which the offline
 * cross toolchain cannot provide.  This single-file program gives the
 * same workload class with *more* experimental control (working set
 * size and stride are explicit), which matters for cache-locality
 * experiments:
 *
 *   working set >> LLC  ->  each access misses to DDR (random access)
 *   working set <  LLC  ->  mostly L2/L3 hits (locality retained)
 *
 * Model: classic pointer chase (lat_mem_rd / HPCC RandomAccess style).
 * The buffer is split into stride-sized blocks; each block's first
 * 8 bytes hold the address of the next block in a random permutation,
 * forming one closed loop.  The walk then jumps all over the buffer,
 * defeating both the HW prefetcher and the linearity of STREAM.
 *
 * Usage:  memrand [-s SIZE_MB] [-b BLOCK_BYTES] [-d SECONDS] [-w]
 *   -s  working set in MB           (default 512)
 *   -b  stride / block size         (default 64, cache line)
 *   -d  run duration in seconds     (default 30)
 *   -w  write mode: store into each block instead of reading it
 *       (default: read-only pointer chase)
 *
 * Output: one summary line to stdout, e.g.
 *   memrand: size=512MB stride=64B mode=read
 *   memrand: 1872345 accesses in 30.0s = 0.41 GB/s = 16.0 ns/access
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdint.h>

/* xorshift64* PRNG: deterministic, no libc rand() state issues. */
static uint64_t rng_state = 0x9e3779b97f4a7c15ULL;
static uint64_t rng_next(void)
{
    uint64_t x = rng_state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    rng_state = x;
    return x * 0x2545f4914f6cdd1dULL;
}

static double now_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
    long size_mb = 512;
    long stride = 64;
    long seconds = 30;
    int write_mode = 0;
    int i;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-s") == 0 && i + 1 < argc)
            size_mb = atol(argv[++i]);
        else if (strcmp(argv[i], "-b") == 0 && i + 1 < argc)
            stride = atol(argv[++i]);
        else if (strcmp(argv[i], "-d") == 0 && i + 1 < argc)
            seconds = atol(argv[++i]);
        else if (strcmp(argv[i], "-w") == 0)
            write_mode = 1;
        else {
            fprintf(stderr, "usage: %s [-s SIZE_MB] [-b BLOCK_BYTES] [-d SECONDS] [-w]\n",
                    argv[0]);
            return 1;
        }
    }
    if (size_mb < 1 || stride < 8 || seconds < 1) {
        fprintf(stderr, "bad arguments (size>=1MB, stride>=8, duration>=1s)\n");
        return 1;
    }
    if (write_mode && stride < 16) {
        fprintf(stderr, "write mode needs stride >= 16 (pointer at "
                        "offset 0, stamp at offset 8)\n");
        return 1;
    }

    size_t total = (size_t)size_mb * 1024 * 1024;
    size_t nblocks = total / (size_t)stride;
    if (nblocks < 2) {
        fprintf(stderr, "working set too small for stride (need >= 2 blocks)\n");
        return 1;
    }

    unsigned char *buf = malloc(total);
    if (buf == NULL) {
        fprintf(stderr, "malloc(%zu) failed\n", total);
        return 1;
    }

    /* Build a random permutation of block indices (Fisher-Yates). */
    uint32_t *perm = malloc(nblocks * sizeof(uint32_t));
    if (perm == NULL) {
        fprintf(stderr, "malloc perm failed\n");
        return 1;
    }
    for (i = 0; i < (int)nblocks; i++)
        perm[i] = (uint32_t)i;
    for (i = (int)nblocks - 1; i > 0; i--) {
        int j = (int)(rng_next() % (uint64_t)(i + 1));
        uint32_t t = perm[i];
        perm[i] = perm[j];
        perm[j] = t;
    }

    /* Link blocks: block k's first 8 bytes -> address of block k+1. */
    for (i = 0; i < (int)nblocks; i++) {
        uint32_t next = perm[(i + 1) % (int)nblocks];
        uintptr_t addr = (uintptr_t)(buf + (size_t)next * (size_t)stride);
        memcpy(buf + (size_t)perm[i] * (size_t)stride, &addr, sizeof(addr));
    }
    free(perm);

    /* Warm-up walk: bring the loop fully into caches / TLB if it fits. */
    unsigned char *p = buf + (size_t)(rng_next() % nblocks) * (size_t)stride;
    int warm = (int)nblocks / 8;
    if (warm < 16) warm = (int)nblocks;
    for (i = 0; i < warm; i++) {
        uintptr_t next;
        memcpy(&next, p, sizeof(next));
        p = (unsigned char *)next;
    }

    volatile uint64_t sink = 0;      /* defeats dead-code elimination */
    unsigned long long accesses = 0;
    double t0 = now_sec();

    if (write_mode) {
        for (;;) {
            uint64_t stamp = rng_next();
            unsigned char *q = p;
            uintptr_t next;
            memcpy(&next, q, sizeof(next));
            memcpy(q + 8, &stamp, sizeof(stamp)); /* store at offset 8:
                keeps the chase pointer (offset 0) intact - writing the
                pointer itself would corrupt the closed loop and crash
                on the next visit (segfault fixed 2026-09-17) */
            p = (unsigned char *)next;
            accesses++;
            if (now_sec() - t0 >= (double)seconds)
                break;
        }
    } else {
        for (;;) {
            uintptr_t next;
            memcpy(&next, p, sizeof(next));
            sink ^= next;
            p = (unsigned char *)next;
            accesses++;
            if (now_sec() - t0 >= (double)seconds)
                break;
        }
    }

    double dt = now_sec() - t0;
    double gbps = (double)accesses * (double)stride / dt / 1e9;
    double ns_per = dt / (double)accesses * 1e9;

    printf("memrand: size=%ldMB stride=%ldB mode=%s\n",
           size_mb, stride, write_mode ? "write" : "read");
    printf("memrand: %llu accesses in %.1fs = %.2f GB/s = %.1f ns/access\n",
           accesses, dt, gbps, ns_per);
    printf("memrand: sink=%llu\n", (unsigned long long)sink);

    free(buf);
    return 0;
}
EOF
gcc -O2 -static -o /root/bf2k/bench/bin/memrand /root/bf2k/bench/memrand.c
taskset -c 0 bench/bin/memrand -s 64 -b 64 -d 10 -w   # 复验：应打印统计
```

> 若此前段错误留下过 core 文件，删掉它（可能几百 MB，占 eMMC 系统盘）：
> `rm -f /root/bf2k/core /root/bf2k/core.*`

---

## 1. E1 空载基线（~10 分钟）

**1.1 Pass A 编程 + 采样 12 s**：

```bash
cd /root/bf2k
./probe_prog.sh $H 0x71 0x4a 0x53 0x5d
./probe_sample.sh $H 12 | tee probe_e1_passA.log
```

**1.2 Pass B 编程 + 采样 12 s**（event2 换 0x67）：

```bash
./probe_prog.sh $H 0x71 0x4a 0x67 0x5d
./probe_sample.sh $H 12 | tee probe_e1_passB.log
```

**1.3 48/s 的 per-tile 分布快照**（看 0x71 背景源是均匀分布还是集中在某个 tile；
此时 0x71 还在 event0 上，两组读值的差 = 各 tile 5 秒增量）：

```bash
for t in $H/tile*; do echo -n "$t "; cat $t/counter0; done
sleep 5
for t in $H/tile*; do echo -n "$t "; cat $t/counter0; done
```

**判读问题（Claude 答）**：① slot3（0x5d 对照）有值吗——没有 = 全链路有问题，暂停排查；② slot0（0x71）空载速率是否 ~48–64/s，**4 个真 tile（tile0–3）均匀还是集中**（tilenet0–3 恒 0 属正常）；③ slot1（0x4a）、slot2（0x53/0x67）是否恒 0。

**2026-09-17 实测结果已入档**：对照 2.1–3.0M/s ✓；0x71=64/s、4 tile 逐字节均匀（+138/5s 一致）→ 系统级周期背景；0x4a/0x53/0x67 空载恒 0（预期内）。

**笔记内容**：tile 目录个数、counter 初值、1.3 的两组 per-tile 输出、任何报错。

---

## 2. E2-L1 写洪流（主攻 0x71，~10 分钟）

保持 Pass A 编程（若与上一步隔了较久，重跑 1.1 的编程行）。三个变体，每个都照下面跑
（`memrand -w` 是纯写模式，512 MB 工作集远超 L2，写必达内存）。**变体 a/b 的
memrand 时长用 45 s**，前 10 s 留给 per-tile 快照（钉 tile↔cluster 映射 +
确认 tilenet 是否真的不计数）：

```bash
cd /root/bf2k
taskset -c 0 /root/bf2k/bench/bin/memrand -s 512 -b 64 -d 45 -w &
sleep 2
for t in $H/tile*; do echo -n "$t "; cat $t/counter0; done
sleep 5
for t in $H/tile*; do echo -n "$t "; cat $t/counter0; done
./probe_sample.sh $H 30 | tee probe_l1a.log
wait
```

- 变体 a：`taskset -c 0`（cluster 0 单核）+ 快照 → probe_l1a.log
- 变体 b：`taskset -c 4`（cluster 1 单核）+ 快照 → probe_l1b.log
- 变体 c：8 核齐写（每核一个 64 MB 实例，共 512 MB，无快照）：

```bash
for c in 0 1 2 3 4 5 6 7; do taskset -c $c /root/bf2k/bench/bin/memrand -s 64 -b 64 -d 30 -w & done
sleep 2
./probe_sample.sh $H 30 | tee probe_l1c.log
wait
```

> BF2 的 8 个 A72 分两个 cluster：core 0–3 / 4–7（若设备拓扑不同以 /proc/cpuinfo 为准）。

**判读问题**：① slot0（0x71）在哪个变体下涨、涨多少；② a/b 之间有差异吗（差异 = cluster 选择性计数的直接证据）；③ slot1（0x4a）是否依然恒 0；④ per-tile 快照：写核所在 cluster 的 tile 是否独占上涨、tilenet 是否仍 0。

**笔记内容**：memrand 是否正常退出、屏幕任何报错、两轮 per-tile 快照原始输出。

**2026-09-17 实测结果已入档（L1a/L1c）**：修复版 memrand 冒烟通过（31.8M/10s）。
L1a（core0，512MB，-d45 -w，2.66M 访问/s）0x71：per-tile 快照 0x11e→0x198
四块逐字节相同（+122/5s=24.4/s/tile）；probe_sample 窗口 1864→3784≈66/s
（末两行 72–88/s）——与空载 64/s 同量级。0x5d 对照 561.9M→714.2M=5.25M/s
≈2×访问率（每访问 1 读指针+1 写 stamp）✓。L1c（8 核两簇×64MB 齐写，
15.7M 访问/s）0x71：4864→7016≈74/s，同样无响应；0x5d 27.1M/s≈1.7×总访问 ✓。
**判定：0x71 不观测 A72 写流量**——五个数量级的写洪流只换来空载同量级的背景
计数，四块逐字节一致 = 均匀全局事件流，非按核/按地址归属的写请求；cluster
选择性假说被 L1c 直接证伪，**L1b 建议跳过**（已被 L1c 覆盖）。0x4a/0x53 全程
恒 0（符合预期：L1 不触发）。论文处理：维持"不可用"标注（观测点未接写流量，
或存在与 0x4a RNF_SEL 同类的隐藏过滤寄存器未编程——不影响 paper52 排除决定）。
附带观察（与判读无关）：core 0–3 实例 2.89M 次/s vs core 4–7 仅 ~1.03M 次/s
（950ns/访问），两簇随机写性能差 3 倍；L1a 启动头 2s 0x71 有 143/s/tile 小脉冲
（512MB 页错误风暴期），随后回落。

---

## 3. E2-L2 内存饱和（主攻 0x67，~10 分钟）

切 Pass B 编程；stream（循环，单次实测只跑 ~1.2 s，套 24 次盖满 30 s 窗口）与
memrand 各占半个 CPU 集，**必须并行**饱和内存。整段一次性粘贴执行（逐行执行
会让 stream 先跑完、双负载错开——2026-09-17 首轮就因此无效）。输出重定向到
文件，防止 stream 刷屏打断节奏：

```bash
cd /root/bf2k
./probe_prog.sh $H 0x71 0x4a 0x67 0x5d
(for i in $(seq 1 24); do taskset -c 0-3 /root/bf2k/bench/bin/stream; done) > stream_l2.txt 2>&1 &
taskset -c 4-7 /root/bf2k/bench/bin/memrand -s 1024 -b 64 -d 40 > memrand_l2.txt 2>&1 &
sleep 2
./probe_sample.sh $H 30 | tee probe_l2.log
wait
```

跑完后回传判读时附：`tail -3 memrand_l2.txt`（确认 40 s 跑完）与
`grep -c "STREAM version" stream_l2.txt`（应 = 24）。

> **2026-09-17 首轮无效已入档**：stream 12 轮先全部跑完（`[1]+ Done`）后才
> 启动 memrand，采样窗口内只有 memrand 读（54.8M 次/30s，547ns/访问，
> ~117MB/s）——延迟受限负载远不够饱和 DDR（~10GB/s 量级），0x67 背压事件无
> 触发条件，slot2=0 不能作为结论。窗口内 slot0=66/s（0x71 背景，与 L1 判定
> 一致）、slot3=6.86M/s（链路正常）。

> **2026-09-17 重跑结果已入档（并行成立）**：24 轮 stream ✓、memrand 40s ✓
> （72.8M 次，549.6ns/访问），两负载确实并行（stream Triad 9.7–10.1GB/s 全程
> 在窗口内，中段与 memrand 竞争小幅回落）。slot2（0x67）**仍恒 0**——但
> 10.3GB/s 约为 BF2 单通道 DDR4-2400 峰值（~19.2GB/s）的 54%，饱和未坐实。
> slot0=66/s（0x71 背景不变）；slot3=7.79M/s。**重大附带发现：slot3（0x5d
> A72_ACCESS）对 ~10GB/s 顺序带宽几乎无响应**（窗口全段 7.7–7.8M/s，与仅
> memrand 时同量级；stream 结束前后仅 ~2% 差异），而它始终 ≈2–3× memrand
> 随机访问率。**推断：A72_ACCESS 只计 demand 访问，HW 预取命中的流式带宽
> 不计入**（memrand 的 2–3× 来自每访问的 TLB walk 负载）。对 BF2-PF 模型是
> 重要语义修正：P3 类顺序带宽负载会被该计数器系统性低估，**Part 6 标定必须
> 专项验证**（若属实，论文计数器语义表需标注）。遗留待解：重跑编程后首 2s
> slot3 出现 ~5.0G 爆发（1GB malloc 页错误风暴期），不影响窗口内增量判读，
> 机理未明。

**L2b（最后一轮，8 线程真饱和，~1 分钟）**：若 0x67 依然为 0，且本轮
stream 8 线程带宽显著高于 10.3GB/s（→ 证实前两轮未饱和），0x67 判"实际
可达负载下不触发"。整段一次粘贴：

```bash
cd /root/bf2k
./probe_prog.sh $H 0x71 0x4a 0x67 0x5d
(for i in $(seq 1 40); do OMP_NUM_THREADS=8 taskset -c 0-7 /root/bf2k/bench/bin/stream; done) > stream_l2b.txt 2>&1 &
taskset -c 4-7 /root/bf2k/bench/bin/memrand -s 1024 -b 64 -d 40 > memrand_l2b.txt 2>&1 &
sleep 2
./probe_sample.sh $H 30 | tee probe_l2b.log
wait
```

回传判读附：`grep "Triad:" stream_l2b.txt | tail -5`（8 线程带宽是否冲上
~15GB/s+）与 `tail -3 memrand_l2b.txt`。

> **2026-09-17 L2b 实测已入档（L2 收口）**：8 线程 stream Triad 9.5–9.9GB/s
> ——与 4 线程 10.3GB/s 持平，**~10GB/s 即本机实际带宽硬顶**（理论峰值
> 19.2GB/s 的 54%，4→8 线程零增益 = 平台封顶，非线程数不足）。memrand 40s ✓
> （52.8M 次，757ns/访问，比上轮 549.6ns 慢 = 与 8 线程 stream 真实争抢 ✓）。
> **slot2（0x67）恒 0 → 判定：实际可达最大内存带宽负载下不触发**，
> tile_mss_nocredit 维持排除出 paper52，论文标注"任何实测负载下不触发"
> （与"硅片观测点问题"论文处理相同，无需区分）。slot0=68/s（0x71 背景不变，
> 首行 1208@2s 有启动脉冲、与 L1a 启动脉冲同型：对系统级页错误风暴有微弱
> 感知、对核写无感知，再次印证判死）。**slot3 重大新数据**：8 线程 stream
> 阶段（行 1–21，与 40 轮 stream 跑完时间点吻合）201.8M/s，stream 结束后
> 回落 7.56M/s（memrand 单独在场）；而 4 线程 stream 几乎为零。**修订
> A72_ACCESS 语义推断：计的是"逃过 HW 预取/L2 覆盖的 demand miss"（MPKI 类
> 随机访存压力指标）**——4 线程 12 个顺序流落在 L2 预取窗口内 → demand≈0；
> 8 线程 24 个流挤爆 L2、预取行被挤出 → demand miss 爆炸。对 BF2-PF 模型的
> 语义修正升级为：A72_ACCESS 是访问模式敏感的随机访存压力指标、**不反映
> 顺序带宽**，P3 类顺序负载须以 MEMORY_READS/WRITES 等带宽计数器为主、
> A72_ACCESS 仅作辅助；**Part 6 标定专项验证**。编程后首 2s 爆发 4.3G 与
> L2 重跑（5.0G）同型（1GB malloc 页错误风暴期），机理未明、不影响增量判读。

**判读问题**：① slot2（0x67）出现非零值了吗；② 顺带看 slot0（0x71）在双负载下与 L1 的异同。

---

## 4. E2-L3 排他读压力（主攻 0x53，~10 分钟）

切回 Pass A 编程。ping-pong 小程序（bench/pingpong.c，已随仓库交付；两线程
钉在 core 0 与 4——两个 cluster——在同一字上做 __atomic_fetch_add 乒乓
（ldaxr/stlxr），每次操作都经 POC 抢线，强制排他读请求。**替代 stress-ng
--futex**：2026-09-17 设备实测本机 stress-ng 无 futex stressor（0.3 判死）。
先造二进制（源码照抄见下，编译一次即可）：

```bash
cat > /root/bf2k/bench/pingpong.c <<'EOF'
/* pingpong.c - cross-cluster exclusive ping-pong for the BF2 counter
 * probe (E2-L3).  Two threads pinned to cores of different clusters
 * hammer one cache line with __atomic_fetch_add (ldaxr/stlxr loop):
 * every op steals the line through the POC, so POC_READS (0x53) sees
 * exclusive read requests if the counter is alive.
 *
 * Replaces stress-ng --futex (not built into the offline stress-ng).
 * Usage: pingpong [seconds] [cpu0] [cpu1]   (default 30 0 4)
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static uint64_t word __attribute__((aligned(64)));
static int seconds = 30;
static int cpus[2] = {0, 4};

static void pin(int cpu)
{
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    pthread_setaffinity_np(pthread_self(), sizeof(set), &set);
}

static void *spin(void *arg)
{
    int cpu = *(int *)arg;
    pin(cpu);
    time_t end = time(NULL) + seconds;
    while (time(NULL) < end)
        __atomic_fetch_add(&word, 1, __ATOMIC_SEQ_CST);
    return NULL;
}

int main(int argc, char **argv)
{
    pthread_t t[2];
    if (argc > 1)
        seconds = atoi(argv[1]);
    if (argc > 3) {
        cpus[0] = atoi(argv[2]);
        cpus[1] = atoi(argv[3]);
    }
    pthread_create(&t[0], NULL, spin, &cpus[0]);
    pthread_create(&t[1], NULL, spin, &cpus[1]);
    pthread_join(t[0], NULL);
    pthread_join(t[1], NULL);
    printf("pingpong done: %llu ops in %d s\n",
           (unsigned long long)word, seconds);
    return 0;
}
EOF
gcc -O2 -static -pthread -o /root/bf2k/bench/bin/pingpong /root/bf2k/bench/pingpong.c
```

然后跑：

```bash
cd /root/bf2k
./probe_prog.sh $H 0x71 0x4a 0x53 0x5d
/root/bf2k/bench/bin/pingpong 30 0 4 &
sleep 2
./probe_sample.sh $H 30 | tee probe_l3.log
wait
```

**判读问题**：slot2（0x53）涨了吗——涨 = **计数器有效，只是 7 应用没有排他读
流量**（论文可放本结果作测量链有效性证据）；不涨 = 语义比文档更窄或失效，
看 E2-L4 的对照再定。程序收尾打印的 ops 数（预期数亿量级）记入笔记作乒乓
确实发生的证据。

**2026-09-17 实测已入档（L3）**：pingpong（core0↔core4 跨簇 ldaxr/stlxr
乒乓）下 **slot2（0x53）全 30 行恒 0**；slot0（0x71）=66/s 背景不变；
slot1（0x4a）=0（预期，无网络）。对照 slot3（0x5d）：行 1–14 ≈8.2M/s
（乒乓在场，按 ~2 次 demand/op 估 ~4M ops/s），行 15 起回落 2.4M/s=空载
背景级——与 pingpong 30s 到期吻合（程序启动与采样块之间约有 14s 间隔），
判读窗口内至少 ~50M 次排他操作全部落空。**判定：0x53 对排他读洪流零响应**
→"语义比文档更窄或失效"成立，由 L4 对照合证（见 §5）。pingpong 收尾 ops
数未回传，如有请补记（判读不依赖：slot3 8.2M/s 窗口即乒乓在场证据）。

> 若设备拓扑不是 0–3/4–7 两 cluster（以 /proc/cpuinfo 为准），把
> `pingpong 30 0 4` 的 CPU 号换成两个 cluster 各一个核。

---

## 5. E2-L4 eMMC 真实读（0x53 对照，~10 分钟）

保持 Pass A 编程。**严禁对 /dev/mmcblk0 裸设备读写——eMMC 是系统盘。**
DMA 读是 ReadOnce/Shared 而非 Exclusive，预期 0x53 仍为 0——正好作"非排他读
不触发"的对照：

```bash
cd /root/bf2k
# 5.1 准备真实数据文件（必须真实写！fallocate 的文件读时不落盘）
/root/bf2k/bench/bin/fio --filename=/root/fio_testfile --rw=write --direct=1 --size=2G --bs=128k --name=warmup
# 5.2 直读 + 采样
/root/bf2k/bench/bin/fio --filename=/root/fio_testfile --rw=read --direct=1 --size=2G --bs=128k --time_based --runtime=25 --name=probe_emmc &
sleep 2
./probe_sample.sh $H 30 | tee probe_l4.log
wait
rm /root/fio_testfile
```

**判读前自查**：fio 末尾 `Disk stats` 行 `mmcblk0: ios=` 必须数千以上、
BW 在 100–400 MB/s 量级才算真读盘；`ios=0` 本轮作废重跑 5.1。

**判读问题**：① slot2（0x53）是否依然 0；② slot0（0x71）有无变化（预期无：
DMA 写不算 A72 写）。

**2026-09-17 实测已入档（L4）**：eMMC fio --direct=1 2GB 读期间 **slot2
（0x53）全 30 行恒 0**；slot0（0x71）=65/s 不变（DMA 读不算 A72 写 ✓）；
slot1（0x4a）=0。对照 slot3（0x5d）=2.83M/s（fio CPU 侧 demand，量级符合
以 DMA 为主、CPU 轻参与的直读特征），行 12–13 有 ~15M/s 单秒尖峰（读盘
突发，机理未明、不影响窗口增量判读）。**判定：非排他 DMA 读同样不触发
0x53 → 与 L3 合证，0x53 定案"语义比文档更窄或失效"**（排他读洪流、eMMC
DMA 读、网络 DMA 读三类流量全部零响应）。fio Disk stats 自查行未回传；
判定不依赖之（slot3 走势即 DMA 读在场证据），如有请补记。

---

## 6. E2-L5 网络 DMA（0x4a 的最终对照，~10 分钟）——需要【宿主机】

保持 Pass A 编程。用 E0-2 已跑通的 NAD 通路打短流量（接口若与当时不同，
按 docs/e0-opsheet.md §2 重新指认）：

> **2026-09-15 线路变更**：E0-2 通路（172.28.4.250）自 9/15 fujian 改线后
> 已断（fujian 自答 ARP 陷阱、ping 不通）。L5 改用 9/15 已验证的 56.x 通路
> 打流量（iperf3 -c 192.168.56.103），其余不变。

```bash
# 【BF2】
sudo ip addr add 172.28.4.250/24 dev enp3s0f0s0
/root/bf2k/bench/bin/iperf3 -s -p 5202 -B 172.28.4.250 -D
cd /root/bf2k
./probe_sample.sh $H 30 | tee probe_l5.log &
# 【宿主机】BF2 采样开始后立刻（30 s 窗口内）
iperf3 -c 172.28.4.250 -p 5202 -t 15
# 【BF2】等采样自然结束（30 s）后清理
pkill iperf3
sudo ip addr del 172.28.4.250/24 dev enp3s0f0s0
```

**判读问题**：slot1（0x4a）在网卡 DMA 流量下是否依然恒 0（连 DMA 都零而对照
0x5d 大涨 → RNF_SEL 未编程坐实）。

**笔记内容**：iperf3 吞吐、BF2 屏幕上采样是否照常滚动。

**2026-09-17 实测已入档（L5，经 56.x 通路）**：iperf3 窗口（行 4–16）对照
slot3（0x5d）41.3M/s（行 1–4 前奏 6.5M/s、行 17–30 收尾 4.8M/s），**slot1
（0x4a）全 30 行恒 0** → **RNF_SEL 未编程坐实**：连网络 DMA 流量都无法让
0x4a 计数，与"驱动从不编程 HNF_PERF_CTL"完全一致。slot2（0x53）=0（网络
DMA 读亦不触发，与 L4 一致）；slot0（0x71）=66/s 背景不变。iperf3 吞吐
未回传，如有请补记（判定不依赖——9/15 P1b 已证 56.x 通路计数三层一致）。

---

## 7. E3 邻域编码扫描（条件触发，~15 分钟）

**仅当 E2-L1 判读为"0x71 不响应写"时执行**：在持续写洪流下轮流把 event0
换成相邻编码，找写流量真正落在哪个计数口径上。

```bash
cd /root/bf2k
for c in 0 1 2 3 4 5 6 7; do taskset -c $c /root/bf2k/bench/bin/memrand -s 64 -b 64 -d 120 -w & done
sleep 2
for c in 0x5d 0x72 0x70 0x73 0x74 0x5f 0x71; do
  ./probe_prog.sh $H $c 0x4a 0x53 0x5d
  ./probe_sample.sh $H 15 | tee probe_e3_$c.log
done
wait
```

**判读问题（Claude 答）**：① 哪些编码在纯写下涨（0x5d 应涨——对照成立）；②
0x72（A72_READ）是否也涨——涨 = 文档语义与硅片不符（读写合并计数），论文改用
A72_ACCESS − A72_READ 反推写；③ 0x73/0x74/0x5f 是否不动——不动 = 槽隔离性正常。

**2026-09-17 实测已入档（E3 邻域扫描；catalog 核对：0x70=VICTIM、
0x73=IO_WRITE、0x74=IO_READS、0x5f=TSO_WRITE）**：

洪流期重建：轮 1–3（0x5d/0x72/0x70）与轮 4 行 1–8（0x73）对照 95–99M/s =
洪流在场；轮 4 行 9 起至轮 5–7（0x74/0x5f/0x71）对照 2.4M/s = 空载背景。
即 8 实例 -d 120 全程跑满，但洪流启动与扫描块分两次粘贴、间隔 ~60s，洪流
在轮 4 行 8–9 处到期（与 -d 120 精确吻合）。各轮 slot0（轮换编码）与 slot3
（0x5d 对照）同轮比值，消掉洪流绝对强度：

| 轮 | slot0 编码 | slot0 速率 | 对照速率 | 比值 | 判读 |
| --- | --- | --- | --- | --- | --- |
| 1 | 0x5d | 96.2M/s | 96.2M/s | 100.0% | **双槽同事件 1:1（0.5‰ 内）→ 编程/读取链路精度金标准证据** |
| 2 | 0x72 | 72.5M/s | 96.5M/s | 75.1% | 纯写洪流下大涨——但机制是"读侧计数"而非"读写合并"（合并应=100%）：每次写访问必带 chase 指针读+TLB walk 读，写仅落在 0x5d |
| 3 | 0x70 | 13.5M/s | 95.4M/s | 14.2% | VICTIM 对 L2 淘汰有响应（脏行 victim 回写）→ **活计数器**，BF2-PF 可作 L2 压力候选 |
| 4 | 0x73 | ~10K/s | 99.1M/s（行1–8） | ~0 | IO_WRITE 对 CPU 写洪流不动（语义正确：CPU store 非 IO 写）+ **槽隔离性正常** |
| 5 | 0x74 | 1.9K/s | 2.4M/s（仅背景） | ~0 | 仅背景期覆盖（洪流已过期）；IO_READS 本不预期触发，证据弱但无碍 |
| 6 | 0x5f | 692/s | 2.4M/s（仅背景） | ~0 | 同上；无 TSO 流量不触发属预期 |
| 7 | 0x71 | 66/s | 2.4M/s（仅背景） | 0.003% | 背景 66/s 再现——**0x71 判死第三证**（L1/L2/E3） |

**判读问题答复**：① 0x5d 涨 ✓（对照成立，且轮 1 双槽自证）；② 0x72 涨到
75% ✓，但机制修正为"读侧计数"：**0x5d − 0x72 = 24.0M/s，÷8 实例 = 3.0M/s
≈ 快核写模式实测 2.89M 访问/s → 差值即写率，反推写公式定量成立**（自洽
模型：0x5d = 数据读+写+walk，0x72 = 数据读+walk，walk≈50M/s≈2.2 次/访问）；
③ 0x73 洪流期不动 ✓；0x74/0x5f 仅背景期覆盖、证据弱，但二者是 IO 侧事件
本不预期触发，**重跑价值低，不建议重跑**（若重跑：-d 180 并整段一次粘贴）。
**论文采用：写流量 = A72_ACCESS − A72_READ 反推，替代失效的 0x71；VICTIM
列入 BF2-PF 候选指标；Part 6 标定验证 0x5d/0x72 语义（含 walk 计入假设）。**

---

## 8. 回传清单

| 文件 | 内容 |
| --- | --- |
| probe_e1_passA.log / probe_e1_passB.log | E1 空载基线（两 pass） |
| probe_l1a/b/c.log | E2-L1 写洪流三变体 |
| probe_l2.log | E2-L2 内存饱和 |
| probe_l3.log | E2-L3 排他读（若跳过则笔记记录原因） |
| probe_l4.log | E2-L4 eMMC 真实读（附 fio Disk stats 自查结果） |
| probe_l5.log | E2-L5 网络 DMA |
| probe_e3_0x*.log | E3 邻域扫描（若执行） |
| 笔记 | 各步要求记录的内容 + 1.3 的 per-tile 快照 |

【宿主机】拉回：

```bash
scp root@192.168.100.2:/root/bf2k/probe_*.log /tmp/
```

---

## 9. 判读标准（Claude 收到 log 后做的事）

| 实验现象 | 结论 | 论文处理 |
| --- | --- | --- |
| 0x4a 全负载恒 0、对照 0x5d 活 | RNF_SEL 未编程（驱动无入口、寄存器地址不公开） | 附录注明 0x4a 需 HNF_PERF_CTL.RNF_SEL；其信息由 HNF_REQUESTS(0x45)+A72_ACCESS 覆盖 |
| 0x71 写洪流下仍 ~48/s | 硅片观测点失效或固定背景源 | 用 A72_ACCESS − A72_READ 反推 A72 写 |
| 0x71 只在单个 cluster 的写下涨 | cluster 选择性计数（统计口径） | 论文标注口径；改绑双 cluster 重采 |
| 0x53 futex 下涨 | 计数器有效、7 应用无排他读 | 附录放 futex 结果作测量链有效性证据 |
| 0x67 饱和下仍 0 | 7 应用未触发 MSS 背压 | 定性"负载未触发"，非失效 |

无论走哪条分支，四个"失效"计数器都能在论文里获得有实验证据支撑的定性——
这正是本实验的目的。

---

## 10. 全实验定论（2026-09-17 收口）

| 计数器 | 编码 | 关键证据 | 失效性质 | 论文处理 |
| --- | --- | --- | --- | --- |
| tile_a72_write | 0x71 | L1a 单核 2.66M 写/s、L1c 八核 15.7M 写/s、L2/L2b、L5、E3 五轮全部 ~66/s 背景；4 tile 逐字节均匀（+122/5s 一致） | **不响应：观测点未接 A72 写流量**（均匀全局背景 ~16/s/tile，对写洪流零响应，仅对页错误风暴有微弱脉冲） | 排除出 paper52；写流量改用 A72_ACCESS − A72_READ 反推（E3 定量验证） |
| tile_rnf_requests | 0x4a | L5 网络 DMA 洪流下恒 0（对照 41.3M/s） | **配置缺失：RNF_SEL 未编程**（驱动无入口、寄存器不公开） | 排除；附录注明 RNF_SEL 前提，其信息由 HNF_REQUESTS(0x45)+A72_ACCESS 覆盖 |
| tile_poc_reads | 0x53 | L3 排他乒乓 ~50M 次 ldaxr/stlxr 恒 0；L4 eMMC DMA 读恒 0；L5 网络 DMA 恒 0 | **不触发：语义比文档更窄或失效** | 排除；标注"排他读洪流与 DMA 读实测均不触发" |
| tile_mss_nocredit | 0x67 | L2/L2b 内存饱和 10.3GB/s（本机硬顶，4→8 线程零增益坐实）恒 0 | **不触发：实际可达最大负载下不触发** | 排除；标注"任何实测负载下不触发" |

探针的正面产出（论文可用）：① 双槽同事件 1:1 = sysfs 直控链路精度证据
（测量链有效性）；② 写流量观测通道打通（0x5d−0x72 反推写，定量成立）；
③ VICTIM(0x70) 为活计数器（L2 淘汰压力候选指标）；④ A72_ACCESS(0x5d)
语义修正：MPKI 类随机访存压力指标、不反映顺序带宽（L2b 发现）——Part 6
标定专项验证。

遗留未明项（均不影响窗口增量判读）：L4 slot3 行 12–13 单秒尖峰（~15M/s）、
L2 系列编程后首 2s 爆发（~5G，1GB malloc 页错误风暴期）、L3 slot3 行 15
回落（已解释为 pingpong 到期）。待用户补回传（可选）：L3 pingpong ops 数、
L4 fio Disk stats、L5 iperf3 吞吐。
