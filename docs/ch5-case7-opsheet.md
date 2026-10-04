# Case 7 操作单：受害者-撤离迷你实验（Motivation 支线，2026-10-04）

> 独立实验单。设计书：docs/motivation-multitenant-plan.md（§2 假设、§4 命令与本单
> 一致）。目的：为论文 Motivation 的"多租户难定位"假设补上**受害者应用级吞吐
> 退化+恢复闭环**这最后一根钉（存量证据已有计数器侧观测，缺应用级成对证据）。
>
> 新名词（详细解释见设计书 §1）：**受害者** = 被观察性能的正主负载（本实验 =
> db_bench 随机读，扮演 DPU 上的 KV 存储服务租户）；**干扰者** = 与受害者同机竞争
> 的别的租户负载（m2 内存流 / m4 网络洪流 / m5 同核空转）；**撤离** = 人为撤掉
> 干扰者、重跑受害者，验证指标是否回到基线——恢复证明干扰者"参与"了退化。
>
> **今日时间预算**：§0 检查 5 分钟 + 五轮 ≈25 分钟（每轮含预热 ~2 分钟）+ m6 可选
> 3 分钟 + 回传 2 分钟 ≈ **35 分钟**。

---

## 0. 前置检查（跑前一次，逐条过）

在设备仓库根目录执行（含 `run_phase.sh` 与 `configs/` 的目录，批次一老位置；
若忘记路径先执行：`find / -name run_phase.sh -not -path '*/proc/*' 2>/dev/null`）。

```bash
# 0.1 三工具与数据在位
ls /root/bf2k/bench/bin/ | grep -E '^(db_bench|gups)$'      # 两个自编译二进制 = OK
which sysbench && sysbench --version                       # sysbench 是 dpkg 系统包（在 /usr/bin），出路径+版本 = OK
ls /root/bf2k/data/dbtest | wc -l                          # 508 左右（LOG 随开库增长，509 正常）= 库完好
# 0.2 资源（m2 一轮峰值：2GB 块缓存 + 3GB 干扰者 + 系统）
df -h /root/bf2k/data                                              # 可用 ≥30G
free -h                                                           # available ≥6G
# 0.3 仓库位置确认
ls run_phase.sh configs/e1_esw.conf                               # 两个都在 = 当前目录正确
# 0.4 m4 前置（只影响 m4；不通就先跑 m1-m3/m5，m4 留最后）
#   helong BF2 上：pgrep netserver || netserver -D -4
#   fujian 上：ping -c 3 10.99.99.3     # 经本卡 p1 到 helong BF2（c2c run3 姿势）
```

---

## 1. 三步预热协议（m1–m5 每轮 run_phase 之前必做，顺序不能乱）

```bash
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'      # ① 清页缓存
/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=2147483648 --reads=2000000 \
  --db=/root/bf2k/data/dbtest > /dev/null                  # ② 预热第一遍（2M 读）
/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=2147483648 --reads=2000000 \
  --db=/root/bf2k/data/dbtest > /dev/null                  # ③ 预热第二遍（不入账）
```

**原理**（为什么必须做）：db_bench 的 2GB 块缓存是进程内数据结构，进程退出即消失；
预热两遍真正"保温"的是 **Linux 页缓存**——①清空后 ②③把整库 2GB 读进页缓存，
正式轮的读全从内存命中、不再碰 eMMC。不做预热，受害者的瓶颈就落在 eMMC 上
（c1e 已证），内存干扰者（m2）打不动它，整组对照作废。**五轮一轮都不能漏**。

---

## 2. m1 受害者基线

```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m1_victim_run1.csv \
  -a "taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m1_victim.log 2>&1" -b 0-3 -t 120
```

跑完即查：

```bash
tail -2 /tmp/m1_victim.log        # 末行形如 readrandom : 12.345 micros/op 81000 ops/sec; ...
cat results/ch5_m1_victim_run1.csv.phase.log   # app 相位应 ≈30-70s；<10s 停下贴给我
```

**期望（判读对账）**：ops/sec 即基线（预期几万量级）；计数器 cr/ib 低-中档。

---

## 3. m2 内存干扰者

```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m2_memintf_run1.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m2_victim.log 2>&1 & \
  taskset -c 1-3 sysbench memory --memory-block-size=1G --memory-scope=global \
  --memory-total-size=64G --memory-oper=read --memory-access-mode=seq --threads=3 \
  --time=60 run > /tmp/m2_intf.log 2>&1 & wait'" -b 0-3 -t 120
```

跑完即查：

```bash
tail -2 /tmp/m2_victim.log         # 记 ops/sec，与 m1 比较
tail -2 /tmp/m2_intf.log           # sysbench 结尾 MiB/s 供判读佐证
cat results/ch5_m2_memintf_run1.csv.phase.log   # app 相位 ≈60s
```

**期望**：ops/sec 较 m1 **温和下降 5-20%**；计数器 cr/wb 流式签名抬升（干扰者的
脚印）。若降幅 <5%，如实记录、继续跑，启用 m6 决定性对照（见 §7）。

---

## 4. m3 撤离（重跑受害者，与 m1 同命令）

```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m3_withdraw_run1.csv \
  -a "taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m3_victim.log 2>&1" -b 0-3 -t 120
```

跑完即查：`tail -2 /tmp/m3_victim.log`。

**期望**：ops/sec 恢复 ≈m1（±5%）→ **责任成立**（m2 的退化随干扰者撤离而消失）。

---

## 5. m4 繁忙传输（双终端编排）

BF2 端（先跑预热协议，再起窗口）：

```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m4_busytrans_run1.csv \
  -a "taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m4_victim.log 2>&1" -b 0-3 -t 120
```

fujian 端（看到 BF2 输出 `[run_phase] APP PHASE START` 后 **5 秒内**执行）：

```bash
netperf -H 10.99.99.3 -t TCP_STREAM -l 60
```

（前置 = §0.4：helong BF2 上 netserver 在跑、fujian 能 ping 通 10.99.99.3。
洪流路径：fujian → 本卡 pcie → eSwitch → p1 → helong，全程硬件转发、Arm 不参与。）

**期望**：ops/sec ≈m1（±5%）——**传输打满但受害者无感**；计数器 tx/nhd/p1 域
高涨而 Arm/tile 域不动 → "高活跃≠归责"的应用级证明。

---

## 6. m5 同核忙循环

```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m5_samecore_run1.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom \
  --use_existing_db=1 --num=2000000 --value_size=1000 --cache_size=2147483648 \
  --reads=3000000 --db=/root/bf2k/data/dbtest > /tmp/m5_victim.log 2>&1 & \
  taskset -c 0 timeout 60 sh -c \"while true; do :; done\" > /dev/null 2>&1 & wait'" \
  -b 0-3 -t 120
```

（`while true; do :; done` = 纯用户态空转，与受害者抢占同一个核；60s 后 timeout
自止。受害者约 50s 结束，空转尾部 ~10s 单独亮 compute 计数，属预期。）

**期望**：ops/sec 较 m1 **下降 ≥30%**（同核抢占）；a72/cr 签名与 m2（内存流）可
区分 → **不同干扰边界、不同计数器症状** = PRISM 判别力演示。

---

## 7. m6（可选）gups 决定性对照

**启用条件**：m2 的 ops/sec 降幅 <5% 时跑，作为"内存干扰确实存在、只是 db_bench
不敏感"的决定性注脚；若 m2 已达标则跳过。

```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_m6_gupsctl_run1.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/gups 20 60 > /tmp/m6_victim.log 2>&1 & \
  taskset -c 1-3 sysbench memory --memory-block-size=1G --memory-scope=global \
  --memory-total-size=64G --memory-oper=read --memory-access-mode=seq --threads=3 \
  --time=60 run > /tmp/m6_intf.log 2>&1 & wait'" -b 0-3 -t 90
```

**期望**：gups 速率（`grep rate /tmp/m6_victim.log`）较标定值 0.018 GUP/s
下降 ≥30%。（gups 不需预热协议——它不读盘。）

---

## 8. 期望与判读标准总表（先写死，判读对账）

| 轮 | 受害者指标期望 | 计数器签名期望 | 支撑论点 |
|---|---|---|---|
| m1 | ops/sec 基线 | cr/ib 低-中 | 参照 |
| m2 | 较 m1 温和降 5-20% | cr/wb 流签名抬升 | 共驻内存租户影响受害者完成 |
| m3 | ≈m1（±5%） | 回基线 | 撤离证责任 |
| m4 | ≈m1（±5%） | tx/nhd 高涨、Arm 域不动 | 高活跃≠归责 |
| m5 | 较 m1 降 ≥30% | a72/cr 高、与 m2 签名可区分 | 不同边界不同症状 |
| m6（可选） | 较 0.018 GUP/s 降 ≥30% | 同 m2 | 内存干扰的决定性注脚 |

任何一轮与期望不符 → docs/validation-replay.md §8.6 三类诊断（A 预期设错 /
B 引擎缺陷 / C 数据问题），不阻塞叙事（负面结果同样入账）。

---

## 9. 回传

```bash
tar czf /tmp/ch5_case7.tar.gz results/ch5_m*.csv results/ch5_m*.phase.log \
  /tmp/m*_victim.log /tmp/m*_intf.log
# scp 回本地（老流程），贴回后我判读
```

---

## 10. 风险与回退

- **m2 降幅 <5%**：如实记录 → 启用 m6（§7）。
- **m4 前置不通**（10.99.99.3 ping 不通或 helong netserver 不在）：先跑 m1-m3/m5，
  m4 留最后；仍不通则回退 c2b 姿势——fujian 起 `netserver -D -4`，干扰者改为 BF2
  出向 `netperf -H 192.168.56.11 -t TCP_STREAM -l 60`（Arm 参与度高，预期改为
  "受害者可能小幅下降、ih/nad 签名"，如实记录）。
- **预热漏做**：m1 与 m3 差 >5% 时先查这一步（判读流程同 c1e 的页缓存排查）。
- **timeout 缺失**：9/30 已确认 `/usr/bin/timeout` 在位，此风险已排除。
- **内核 4-7 常驻 mlnx_snap_emu**：全程不要碰（本单所有钉核均限 0-3）。
