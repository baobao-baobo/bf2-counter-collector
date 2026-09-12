# 真实应用路径实验操作手册（G1–G5）

> 配套文档：apps-path-experiment-plan.md（方案）、e0-analysis.md（E0 定标）、
> xz-redis-benchmarks.md（G1/G3 深度解析）
> 第一批：G1（xz，CR 主导）+ G3（Redis，NAD 点亮）；第二批：G2（GAPBS，CR 随机
> 访存）+ G4（SQLite eMMC，IB/IH）+ G5（blackscholes，多核 CR）。
> 说明：本批应用二进制改走**设备原生编译**（WSL 交叉编译环境 2026-09-11 出现
> 0x800705aa 资源不足故障；设备自带 gcc，native 编译不依赖 WSL，且无需静态链接）。

## 0. 本批交付清单（已入 GitHub，纯 git pull 即可部署）

| 文件                                              | 用途                                                    |
| ----------------------------------------------- | ----------------------------------------------------- |
| `configs/app_full.conf`                         | 采集配置：paper52 全块（tile 6 组/L3 8 组轮换）+ PCIe TLR + net    |
| `run_phase.sh`                                  | 设备侧相位式运行：5s 空载 + 应用窗口 + 5s 空载，自动起停 collect_all 并写相位日志 |
| `tools/split_path.py`                           | 本地归因+出图（相位差分 + 背景扣除 + 守恒校验；产出 2 张表 + 3 张图）            |
| `apps/src/xz-5.6.4.tar.gz`、`redis-7.2.5.tar.gz` | 应用源码（已入仓库，随 git 流转）                                   |
| `apps/build_apps_device.sh`                     | 设备侧原生编译脚本（xz + redis-server/benchmark/cli）            |
| `apps/build_apps.sh`                            | WSL 交叉编译脚本（WSL 恢复后的备选，产物为静态二进制）                       |

## 1. 部署（GitHub → fujian → BF2）

```bash
# fujian 上（/tmp/bf2k 为部署克隆；首次先 cd /tmp/bf2k && git pull 拿到 deploy.sh）：
bash deploy.sh        # 只同步（脚本/配置变更）
bash deploy.sh -b     # 同步 + 设备上 make 重编译（改了 code/*.c 时）
```

- deploy.sh 只打包 **git 跟踪的文件**（`git ls-files`），本地编译产物（如
  Windows 侧的 x86_64 collect_all）和回传的 results/CSV 永远不会混入部署包、
  不会覆盖设备上的原生二进制。

## 2. 设备侧一次性准备（约 15-20 分钟，主要在编译）

```bash
cd /root/bf2k
./code/collect_all --check-config -c configs/app_full.conf     # 应通过
bash apps/build_apps_device.sh                                   # 编译 xz + redis（A72 上 ~10-20 分钟）

# 准备 G1 数据文件（一次性，~1-2 分钟；1024MB 随机数据）：
dd if=/dev/urandom of=/tmp/g1.dat bs=1M count=1024 status=progress
ls -lh /tmp/g1.dat   # 应约 1.0G
```

## 3. G1 xz（CR 主导）三连跑

```bash
cd /root/bf2k
sudo ./run_phase.sh -c configs/app_full.conf -o g1_run1.csv -t 40 \
    -a "apps/bin/xz -c -9 -T 8 /tmp/g1.dat > /dev/null"
# 重复 run2、run3（把 -o 换名即可）
```

- 每跑约 50s（5 空载 + 40 应用窗口 + 5 空载）。xz 压 1GB 随机数据（-9 -T 8）
  实测全程约 55-65s，40s 窗口会中途截断：应看到
  `[run_phase] app window expired, killed app process group`，app=40s，
  属正常（采集到的就是 40s 完整压缩时段）。
- **跑前先确认没有残留 xz**：`pgrep -a xz`（有输出就 `pkill -x xz`）。
  手动校准/试跑过 xz 的话要等它彻底结束（约 1 分钟）再开始相位跑，
  否则残留压缩会污染下一跑的 pre 窗口。
- 每跑结束核对结尾的 `[run_phase] done: ... app Ns ...`：app 应 =40s；
  若是 1s 说明 xz 秒退，看 §8 的 xz 条目。
- 跑完检查：`ls g1_run*.csv g1_run*.csv.phase.log`（3 份 CSV + 3 份相位日志）。

## 4. G3 Redis（NAD 点亮）三连跑

### 4.1 起服务（一次）

```bash
cd /root/bf2k
# 56.x 为卡内 OVS 隔离网络（仅 Arm 与主机之间），无公网暴露风险，故
# 关闭 protected-mode（否则非回环连接全部被拒，ping 也会 DENIED）。
pkill -x redis-server || true                       # 清掉可能残留的旧实例
apps/bin/redis-server --bind 192.168.56.103 --port 6379 --save "" \
    --appendonly no --protected-mode no --daemonize yes
apps/bin/redis-cli -h 192.168.56.103 ping        # 期望 PONG
```

### 4.2 fujian 装客户端（一次性；NEEDRESTART_MODE 防 rshim 被重启）

```bash
sudo NEEDRESTART_MODE=l apt install -y redis-tools
ping -c 2 192.168.56.103        # 先确认 56.x 管道通
```

### 4.3 三连跑（注意时序）

```bash
# BF2 上（先起采集；app 相位用 sleep 占位）：
cd /root/bf2k
sudo ./run_phase.sh -c configs/app_full.conf -o g3_run1.csv -t 40 -a "sleep 40"
```

- 屏幕出现 **`APP PHASE START`** 后（约启动 5s），立即在 fujian 上执行：

```bash
redis-benchmark -h 192.168.56.103 -p 6379 -t set,get -n 3000000 -c 64 -d 128 -q
```

- benchmark 本身约 30-40s，落在 40s 应用窗口内；重复 run2/run3 同样节奏。

### 4.4 收尾

```bash
apps/bin/redis-cli -h 192.168.56.103 shutdown nosave    # 关服务
```

## 5. CSV 回传

```bash
# fujian 上：
mkdir -p /tmp/bf2k/results
scp root@192.168.100.2:/root/bf2k/g{1,2,3,4,5}_run*.csv* /tmp/bf2k/results/
# 再从 fujian 拉回 Windows，放 D:\bf2-collector\results\
```

## 6. 本地出图（Windows，需 pandas/matplotlib，与 plot_v1_axis.py 同环境）

```cmd
cd /d D:\bf2-collector
python tools\split_path.py ^
  --run results\g1_run1.csv:xz --run results\g1_run2.csv:xz --run results\g1_run3.csv:xz ^
  --run results\g3_run1.csv:redis --run results\g3_run2.csv:redis --run results\g3_run3.csv:redis ^
  --out results
```

产出（results/ 下）：

| 文件                         | 内容                                           |
| -------------------------- | -------------------------------------------- |
| `split_counters_table.csv` | 全部计数器 × 应用的净速率（已扣背景，3 次中位）                   |
| `split_path_summary.csv`   | 各路径代表计数器汇总                                   |
| `fig1_path_profiles.png`   | 主图：应用 × 路径（左=网格侧事件 CR/IH/IB/WB 对数轴；右=NAD 字节） |
| `fig2_l3_behavior.png`     | 每应用 L3 HITS/MISSES/ALLOCATIONS/EVICTIONS 堆叠  |
| `fig3_conservation.png`    | 守恒校验：L3 读入口 vs 命中+未中（比值应 ≈1）                 |

## 7. 预期判读（E0 已预演，供对图）

| 指标                       | G1 xz（预期） | G3 redis（预期） | 依据                           |
| ------------------------ | --------- | ------------ | ---------------------------- |
| A72_ACCESS               | 高（CR 主导）  | 中等           | E0-2 iperf3 时 34-75M/s 作量级参考 |
| pcie0/pcie1 字节           | 近零（无 NAD） | ≈ 网络流量量级，且双向 | E0-2 备选 B 实测                 |
| net rx/tx                | 近零        | 逐行 rx≈tx     | E0-2 环回签名                    |
| L3 HITS/MISSES           | MISSES 显著 | 中等           | CR 压缩工作集大                    |
| VICTIM_WRITE / EVICTIONS | 有活动（WB）   | 少量           | 脏行写回                         |
| IO_ACCESS                | 低         | 有（mlx5 MMIO） | E0-2 12-26M/s 参考             |
| 守恒比值                     | ≈1        | ≈1           | 读入口 = 命中+未中                  |

## 8. 故障排查

- `cannot execute binary file: Exec format error`（collect_all）：部署包曾覆盖
  设备原生二进制（旧 tar 方式混入 Windows 侧 x86_64 产物）。设备上
  `cd /root/bf2k && make` 重建即可；新版 deploy.sh 已根治此问题。
- `--check-config` 报错：贴输出给 Claude。
- `apps/bin/xz: error: '.../apps/bin/.libs/xz' does not exist`（跑 G1 时
  app 窗口只有 1s）：xz 是 libtool 工程，旧版编译脚本复制的是 wrapper 脚本
  而非真身 ELF。已修复（改走 `make install` + 静态链接），设备上重跑
  `bash apps/build_apps_device.sh xz` 即可，修复版会打印
  `statically linked` 和版本行作自检。
- post/pre 空载窗口出现高活动（如 A72_ACCESS 60M+/s 且相位日志显示 app
  已被截断）：旧版 run_phase 到期只杀 sh 包装进程，应用本体成孤儿继续跑。
  新版已改为整进程组击杀（setsid + kill -- -PID），同步部署后即生效；
  跑前也确认 `pgrep -a xz` 为空。
- redis 客户端连不上：fujian `ping 192.168.56.103` 先验证 56.x 管道；再
  `redis-cli -h 192.168.56.103 ping`。
- CSV 行数远少于窗口秒数：贴 `tail -5` 输出给 Claude。
- WSL 0x800705aa（本地交叉编译环境故障）：不影响本手册（设备原生编译）；
  恢复后可再跑 `apps/build_apps.sh` 产出静态二进制备用。

## 9. G2 GAPBS（CR 随机访存/指针追逐）三连跑

- 部署（见 §1）后补编新应用（跳过已编好的 xz/redis）：
  `bash apps/build_apps_device.sh gapbs sqlite blackscholes`（约 10 分钟）。
  编译依赖 g++（实测 9.4.0 已具备；blackscholes 的 pthread 版已预展开，
  不需要 m4）。
- 校准（相位外试跑，10s 档）：
  ```bash
  time apps/bin/bfs -g 20 -n 3
  ```
  按耗时调整 `-n` 使单次运行 ≈30-35s（落在 -t 40 窗口内；`-g 20` = 2^20 顶点
  kron 图，`-n` = 试跑次数）。
- 三连跑（`-n` 用校准值）：
  ```bash
  sudo ./run_phase.sh -c configs/app_full.conf -o g2_run1.csv -t 40 -a "apps/bin/bfs -g 20 -n 8"
  ```
- 预期：A72_ACCESS / HNF_REQUESTS 高、L3 MISSES 显著（图遍历随机访存）、
  MEMORY_READS 高；pcie0/pcie1、net 近零（无 NAD）。
- 可选加跑 pr/cc（`-a "apps/bin/pr -g 20 -n 3"`、`cc` 同理），作为额外 label。

## 10. G4 SQLite eMMC 真实读写（IB/IH）三连跑

- eMMC 挂载点实测 = **根分区 /**（/dev/mmcblk0p2，59G）。DB 路径直接用
  `/root/bf2k/g4.db`（下文以 `<EM>` 代替）。注意：只做文件级读写，**严禁**
  对 /dev/mmcblk0 裸设备操作；`/tmp` 是 tmpfs（内存盘），DB 绝不能放 /tmp，
  否则测的就不是 eMMC。
- 校准（`.timer on` 会打每条语句耗时）：
  ```bash
  rm -f <EM>/g4.db* && time apps/bin/sqlite3 <EM>/g4.db < apps/sqlite_workload.sql
  ```
  调 apps/sqlite_workload.sql 里的 1000000 行数使总时长 ≈30-35s。
- 三连跑（每跑前删旧库，保证冷库）：
  ```bash
  rm -f <EM>/g4.db*
  sudo ./run_phase.sh -c configs/app_full.conf -o g4_run1.csv -t 40 \
      -a "apps/bin/sqlite3 <EM>/g4.db < apps/sqlite_workload.sql"
  ```
- 预期：IO_ACCESS 飙高（E0-3 定标 735K/s 量级）、IO_READS/IO_WRITE 活动、
  MEMORY_READS_BYPASS（IB 路径）活动、A72_ACCESS 中；pcie/net 近零。

## 11. G5 blackscholes（多核对称 CR）三连跑

- 生成输入（一次；数量按校准调）：
  ```bash
  apps/bin/inputgen 5000000 /tmp/bs_in.txt
  ```
- 校准：`time apps/bin/blackscholes 8 /tmp/bs_in.txt /dev/null`（输出 /dev/null
  消除写盘）→ 单跑 t 秒，则窗口内循环约 35/t 次。
- 三连跑（循环次数按校准）：
  ```bash
  sudo ./run_phase.sh -c configs/app_full.conf -o g5_run1.csv -t 40 \
      -a "for i in 1 2 3 4 5 6 7 8; do apps/bin/blackscholes 8 /tmp/bs_in.txt /dev/null; done"
  ```
- 预期：8 核对称 A72_ACCESS、HNF_REQUESTS、L3 活动（多核共享 L3）；
  pcie/net 近零。
