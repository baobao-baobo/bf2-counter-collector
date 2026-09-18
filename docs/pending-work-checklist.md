# 补做清单（2026-09-17 更新）

执行计划 7 部分中剩余工作 + 探针/P1 补验遗留。**Claude 不连设备**：设备线条目由用户执行后回传，本地线由 Claude 执行。每条标状态、依赖、验收。

---

## A. 本轮改造收尾（本地线 + 用户批准）

| # | 事项 | 状态 | 依赖/验收 |
|---|---|---|---|
| A1 | collect_pipe.sh 口径分层改造（p1→sysfs 物理口、Arm 面保留 OVS 规则、`-w` 参数） | ✅ 本地完成，未提交 | bash -n 语法自检通过 |
| A2 | pipe-collection-plan.md 修订（§5.5 采集器说明/验收判读、§5.6 修复注记） | ✅ 本地完成，未提交 | — |
| A3 | **git 提交推送（待用户批准）**：tools/collect_pipe.sh + docs/pipe-collection-plan.md + docs/pending-work-checklist.md | ⏳ 待批准 | 无 Co-Authored-By 尾注（老规矩）；**M2 部署的前置**（deploy.sh 只传 git 跟踪文件） |

## B. M2 部署与验收（Task #30，设备线，被 A3 阻塞）

| # | 事项 | 执行人 | 验收 |
|---|---|---|---|
| B1 | fujian：`git pull` + `bash deploy.sh`（替代 scp 单文件） | 用户 | /root/bf2k/collect_pipe.sh 有 `WIRE_PORTS` 字样 |
| B2 | BF2：`chmod +x` + `bash -n /root/bf2k/collect_pipe.sh` | 用户 | 无输出 |
| B3 | §5.5 M2 验收：起采集 50s，fujian `iperf3 -c 192.168.56.103 -p 5202 -t 10 -b 10G` | 用户 | pf1hpf 列（OVS 规则）增量 ≈8.7GB；en3f1pf1sf0 ≈ 背景；回传 pipe_m2.csv |
| B4 | §5.6 Part C 同窗验收：helong `iperf3 -c 10.99.99.1 -B 10.99.99.3 -t 5 -b 10G`（fujian 先挂 10.99.99.1/24） | 用户 | p1 列（sysfs 物理口）增量 ≈5.8GB；回传 CSV |
| B5 | Claude 判读 → Task #30、#39 关闭 | Claude | 两列均过 → M2 通过；p1 判死新形态与 Part B 13.1GB 对照记录在案 |

## C. memrand 修复版二进制回拉（用户 fujian 操作 + 本地线）

| # | 事项 | 状态 | 验收 |
|---|---|---|---|
| C1 | fujian：`scp root@192.168.100.2:/root/bf2k/bench/bin/memrand /tmp/memrand.fixed` | ⏳ | 文件存在 |
| C2 | Windows：`scp fujian:/tmp/memrand.fixed D:\bf2-collector\bench\bin\memrand`（覆盖） | ⏳ | — |
| C3 | Claude 验证（设备已冒烟 31.8M/10s；README 已带 stamp 偏移 8/写模式 stride≥16 说明） | ⏳ | 无段错误即过 |
| C4 | git 提交（待批准） | ⏳ | 无 Co-Authored-By |

## D. Task #40 饱和标定（设备线，docs/saturation-calibration-opsheet.md）

| # | 事项 | 验收/注意 |
|---|---|---|
| D1 | 四标定面（p1–p7）照操作单执行回传 | 操作单判读标准 |
| D2 | 7 应用真实负载重跑（含 e1_esw 每口列） | 与主图历史同量级 |
| D3 | **0x5d/0x72 语义专项验证**：walk 计入假设 + L2b 的"0x5d 不反映顺序带宽" | 探针 E3 遗留（见下 E 节） |
| D4 | SAT-SUSPECT g1/g2 锚点替换：memrand/cache 真应力（需 C 的修复版 memrand） | 重跑空载关+回放关+退化检查三关，SAT-SUSPECT 应消失 |

## E. 探针可选遗留（价值低，判读不依赖）

| # | 事项 | 说明 |
|---|---|---|
| E1 | E3 0x74/0x5f 重跑（`-d 180` 整段一次粘贴） | 洪流期未覆盖，重跑价值低，可选 |
| E2 | 补记：L3 pingpong ops 数、L4 fio Disk stats、L5 iperf3 吞吐 | 三条记录线，可选 |

## F. 论文相关（依赖 B/D 数据）

| # | 事项 | 状态/依赖 |
|---|---|---|
| F1 | E0-1 NHD 重证（BF2↔BF2 对打，p1 物理口口径） | 依赖 B 完成后安排；论文 CRITICAL #2 落笔前提 |
| F2 | Task #41 论文化：模型章节素材 + 图表 + CRITICAL 项 | 依赖 B/D；含 P1 判死新形态、0x5d−0x72 写反推公式、VICTIM 活计数器、offload 延迟 ≈1.1s 等探针产出 |
| F3 | 四失效计数器论文标注 | ✅ 已入操作单 §10，论文化时直接引用 |

## G. git 入库决策（用户定夺）

| # | 事项 | 说明 |
|---|---|---|
| G1 | 本轮三文件（A3） | 已列，等批准 |
| G2 | 历史遗留：fig/、results/、docs/ 学习笔记等 | 始终待用户定夺，勿擅自提交 |

---

**当前最前序动作**：A3（批准提交）→ B1–B5（M2 部署验收）。C1 与 B 无依赖，可并行做。
