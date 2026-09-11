# 真实应用路径实验操作手册（第一批：G1 xz + G3 Redis）

> 配套文档：apps-path-experiment-plan.md（方案）、e0-analysis.md（E0 定标）
> 本批目标：跑通 G1（xz，CR 主导）+ G3（Redis，NAD 点亮），出三张图。
> 说明：本批应用二进制改走**设备原生编译**（WSL 交叉编译环境 2026-09-11 出现
> 0x800705aa 资源不足故障；设备自带 gcc，native 编译不依赖 WSL，且无需静态链接）。

## 0. 本批交付清单（已入 GitHub，纯 git pull 即可部署）

| 文件 | 用途 |
|---|---|
| `configs/app_full.conf` | 采集配置：paper52 全块（tile 6 组/L3 8 组轮换）+ PCIe TLR + net |
| `run_phase.sh` | 设备侧相位式运行：5s 空载 + 应用窗口 + 5s 空载，自动起停 collect_all 并写相位日志 |
| `tools/split_path.py` | 本地归因+出图（相位差分 + 背景扣除 + 守恒校验；产出 2 张表 + 3 张图） |
| `apps/src/xz-5.6.4.tar.gz`、`redis-7.2.5.tar.gz` | 应用源码（已入仓库，随 git 流转） |
| `apps/build_apps_device.sh` | 设备侧原生编译脚本（xz + redis-server/benchmark/cli） |
| `apps/build_apps.sh` | WSL 交叉编译脚本（WSL 恢复后的备选，产物为静态二进制） |

## 1. 部署（GitHub → fujian → BF2）

```bash
# fujian 上（/tmp/bf2k 为部署克隆）：
cd /tmp/bf2k && git pull
tar czf /tmp/bf2deploy.tar.gz --exclude=.git .
scp /tmp/bf2deploy.tar.gz root@192.168.100.2:/root/bf2k/

# BF2 上：
cd /root/bf2k && tar xzf bf2deploy.tar.gz
```

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

- 每跑约 50s（5 空载 + 40 应用窗口 + 5 空载）；`xz -T 8` 在 8 核 A72 上压缩 1GB
  约 15-30s，实际应用时长以相位日志为准（split_path.py 按实际起止切分）。
- 跑完检查：`ls g1_run*.csv g1_run*.csv.phase.log`（3 份 CSV + 3 份相位日志）。

## 4. G3 Redis（NAD 点亮）三连跑

### 4.1 起服务（一次）

```bash
cd /root/bf2k
apps/bin/redis-server --bind 192.168.56.103 --port 6379 --save "" \
    --appendonly no --daemonize yes
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
scp root@192.168.100.2:/root/bf2k/g{1,3}_run*.csv* /tmp/bf2k/results/
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

| 文件 | 内容 |
|---|---|
| `split_counters_table.csv` | 全部计数器 × 应用的净速率（已扣背景，3 次中位） |
| `split_path_summary.csv` | 各路径代表计数器汇总 |
| `fig1_path_profiles.png` | 主图：应用 × 路径（左=网格侧事件 CR/IH/IB/WB 对数轴；右=NAD 字节） |
| `fig2_l3_behavior.png` | 每应用 L3 HITS/MISSES/ALLOCATIONS/EVICTIONS 堆叠 |
| `fig3_conservation.png` | 守恒校验：L3 读入口 vs 命中+未中（比值应 ≈1） |

## 7. 预期判读（E0 已预演，供对图）

| 指标 | G1 xz（预期） | G3 redis（预期） | 依据 |
|---|---|---|---|
| A72_ACCESS | 高（CR 主导） | 中等 | E0-2 iperf3 时 34-75M/s 作量级参考 |
| pcie0/pcie1 字节 | 近零（无 NAD） | ≈ 网络流量量级，且双向 | E0-2 备选 B 实测 |
| net rx/tx | 近零 | 逐行 rx≈tx | E0-2 环回签名 |
| L3 HITS/MISSES | MISSES 显著 | 中等 | CR 压缩工作集大 |
| VICTIM_WRITE / EVICTIONS | 有活动（WB） | 少量 | 脏行写回 |
| IO_ACCESS | 低 | 有（mlx5 MMIO） | E0-2 12-26M/s 参考 |
| 守恒比值 | ≈1 | ≈1 | 读入口 = 命中+未中 |

## 8. 故障排查

- `--check-config` 报错：贴输出给 Claude。
- redis 客户端连不上：fujian `ping 192.168.56.103` 先验证 56.x 管道；再
  `redis-cli -h 192.168.56.103 ping`。
- CSV 行数远少于窗口秒数：贴 `tail -5` 输出给 Claude。
- WSL 0x800705aa（本地交叉编译环境故障）：不影响本手册（设备原生编译）；
  恢复后可再跑 `apps/build_apps.sh` 产出静态二进制备用。
