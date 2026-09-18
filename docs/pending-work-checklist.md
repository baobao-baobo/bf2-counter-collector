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
| B1 | fujian：`git pull` + `bash deploy.sh`（路径保持原样 → 设备上是 **tools/collect_pipe.sh**） | 用户 | /root/bf2k/tools/collect_pipe.sh 有 `WIRE_PORTS` 字样 |
| B2 | BF2：`chmod +x` + `bash -n /root/bf2k/tools/collect_pipe.sh` | 用户 | 无输出 |
| B3 | **pipe-collection-plan.md §5.5 第 5 步**（第一路）：fujian `iperf3 -c 192.168.56.103 -p 5202 -t 10 -b 10G` | 用户 | ✅ 机制通过（9/18 M5，diff 铁证 7.817GB=端口 rx 1:1）；脚本 bug 坐实并修复已推（c7ce8ec server-side match）。**M7 判读（9/18）**：窗口 07:42–07:47 UTC 三列全 0，但用户确认 iperf 打流晚于窗口 → **第三次窗外、数据无效**；修复版部署已确认（设备 md5=956f60ce…==本地 c7ce8ec，line 118 服务端 in_port 匹配）。**M8 判读（9/18）**：dump-flows 铁证 **7.03e9/4.643M 包=1514B/包**（打流在窗内、规则端全量成立=M5 复现，dump 缺 iperf 尾 ~0.5s 推断在打流最后一秒取的）；**但 CSV 全 84 行 pf1hpf/en3 恒 0 → 脚本每秒轮询在设备端读空**（本地用用户贴的规则行原文复现管道输出正确，逻辑本身无错）；en3 规则 duration=24.262s 比 pf1hpf 的 15.576s 老 8.7s，疑起过两次采集器（第一次中断，cleanup 的 del-flows 对 en3f1pf1sf0 静默失败留下旧规则）。**病根定案（9/18，设备排查回传）**：设备 `dump-flows` 首行是 `NXST_FLOW reply (xid=0x4):` 头行，脚本 `head -1` 抓到的是头行（无 n_packets）→ 轮询恒 0（M8 CSV 全 0 与 7.03e9 铁证并存的解释）；用户确认中途起过两次采集器（en3 旧规则=第一次中断 cleanup 对 en3f1pf1sf0 静默失败遗留，M9 的 add-flow 同 match 替换自愈）。**已修**：去掉 `head -1`、`grep -m1` 从整段回复取计数（6bba234，本地以设备原文模拟通过 pkts=4643223/bytes=7029786881，新 md5=5c78bc44…），**推送待用户批准** → 待 M9 部署复核 md5 + 按 M8 纪律重跑 |
| B4 | **pipe-collection-plan.md §5.5 第 6 步**（第二路，同窗）：helong 的 BF2 `iperf3 -c 10.99.99.1 -B 10.99.99.3 -t 5 -b 10G` | 用户 | ✅ 9/18 通过：p1 列 6.31GB/4.157M 包 = 1518B/包 标准线帧，1:1 对 iperf 5.60GiB×1.049 帧开销；ethtool p1 Speed=**100G** 记录在案；1782B 疑点定案=截尾+计数器异步刷新采样失真（判读看全窗汇总） |
| B5 | Claude 判读 → Task #30、#39 关闭 | Claude | 两列均过 → M2 通过；p1 判死新形态与 Part B 13.1GB 对照记录在案 |

## C. memrand 修复版二进制回拉（用户 fujian 操作 + 本地线）

| # | 事项 | 状态 | 验收 |
|---|---|---|---|
| C1 | **BF2 原地重建**：`gcc -O2 -static -o bin/memrand memrand.c` + 冒烟 | ✅ 9/18 | 冒烟通过：write 模式 31.8M 次/10s、无段错误 |
| C2 | 回传二进制 + 源码（results/memrand.fixed2 + memrand_fixed.c） | ✅ 9/18 | — |
| C3 | Claude 验证 + 落位 | ✅ 9/18 | 哈希 2604b059==设备、ELF AArch64 ET_EXEC、源码与操作单 §0.5 逐字节一致；已放 bench/bin/memrand + bench/src/memrand.c（src 在 .gitignore，提交需 -f） |
| C4 | git 提交（已批准）：bench/bin/memrand + bench/src/memrand.c（-f）+ bench/README.md | ✅ 9/18 bf8a891 | 已推送；deploy 从此铺修复版 |

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

**当前最前序动作**：B3 补跑（56.x 单路）→ M8 病根定案（`NXST_FLOW reply` 头行被 head -1 吃掉）→ 修复已提交 6bba234 **待推送批准** → **M9**（fujian `git pull`+deploy → BF2 md5 复核 `5c78bc44…` → M8 同纪律重跑：首行即打流+窗口内 dump 铁证）→ B5 判读 → 关 Task #30/#39。
