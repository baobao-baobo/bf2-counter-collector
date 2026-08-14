# 设备验收清单（BF2 实机，用户执行）

本地验证已完成：单元测试 170/170（含默认配置表头与 all_test.txt 第 2 行逐字节一致的
parity gate）、x86 与 aarch64 交叉编译零警告、多速率 NaN 节奏与 SIGINT 干净退出已在
x86 冒烟验证。本清单是**设备端**的最后确认步骤，全部在 BF2 上执行。

## 0 部署与构建

```bash
# 1. 把本仓库（或至少 code/ + configs/ + tools/）复制到 BF2
# 2. 本机编译（gcc 9.4, gnu11）
make            # 或 cd code && make
# 3. 预期：-Wall -Wextra 零警告，产出 code/collect_all
```

## 1 默认配置 60 s 采集（核心 parity 测试）

```bash
sudo ./code/collect_all -c configs/default.conf -d 60 -o dev_default.txt
```

检查（对照旧基线 `all_test.txt`）：

| # | 检查项 | 预期 |
|---|--------|------|
| 1a | 表头 | 与 all_test.txt 第 2 行 **逐字节一致**（42 列）：`head -2 dev_default.txt \| tail -1 \| diff - <(head -2 all_test.txt \| tail -1)` 无输出 |
| 1b | 行数 | 60 s → 60 行左右（±2 行容差） |
| 1c | 首行无假 0 | L3 4 列、PCIe 4 列首行即有真实值 |
| 1d | tile 轮换 | tile_group 列 0,1,0,1,... 交替；tile_a72_access 每行有值；其余 6 列隔行 NaN |
| 1e | 时间戳 | 单调递增、逐秒连续 |
| 1f | 与旧基线交叉一致 | tile_mem_reads、l3 hits/misses、l1d_access 量级与 all_test.txt 同时段相当 |

```bash
python3 tools/check_csv.py dev_default.txt            # 结构不变量
python3 tools/check_csv.py dev_default.txt --period 1 --cols tile_*   # 覆盖节奏
```

## 2 多速率配置 60 s 采集（NaN 节奏）

```bash
sudo ./code/collect_all -c configs/multirate.conf -d 60 -o dev_multirate.txt
```

预期：tile 组与 L3 每 2 行采样一次（tile_group 列在奇数行有值、偶数行为空），
tilenet 每 5 行采样一次。

```bash
python3 tools/check_csv.py dev_multirate.txt --period 2 --cols tile_* l3half* pcie* 
python3 tools/check_csv.py dev_multirate.txt --period 5 --cols tilenet_*
```

## 3 信息命令（无需 root、不碰硬件）

```bash
./code/collect_all --list-events   ; echo $?   # 期望 0，输出全量事件目录
./code/collect_all --dump-config   ; echo $?   # 期望 0，输出默认模板
./code/collect_all -c configs/default.conf --check-config ; echo $?   # 期望 0
./code/collect_all -c /bad/path.conf ; echo $?                 # 期望 1 + 报错信息
./code/collect_all -c configs/default.conf -i 2 ; echo $?      # 期望 1 + 倍数报错
```

注意：`--check-config` 不应触碰 hwmon（不需要 root 即可运行即是证据）。

## 4 SIGINT 干净退出

```bash
sudo ./code/collect_all -c configs/default.conf -o dev_sig.txt &
sleep 5; sudo kill -INT %1; wait %1; echo $?
tail -1 dev_sig.txt    # 末行字段数 = 表头字段数，无半行
```

## 5 gic 目录命名确认（启用 gic 前必做）

```bash
ls /sys/class/hwmon/hwmon*/ | grep -E '^(gic|smmu)' | sort -u
# 确认 gicN 目录实际命名（可能不存在或命名不同）
# 确认后再决定是否在配置里启用 [gic]；当前默认 enabled=false
```

## 6 回传结果

把以下内容发回（Claude 不连接设备，由用户自行部署与回传）：

- dev_default.txt（60 行）与 `head -1 dev_default.txt` 的 diff 结果
- 1a–1f 各项通过/失败情况
- check_csv.py 的输出（第 1、2 节全部）
- 第 3 节各命令的退出码与报错原文
- 第 5 节 `ls` 输出
- 编译警告（如有）
