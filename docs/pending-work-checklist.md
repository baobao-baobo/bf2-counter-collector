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
| B3 | **pipe-collection-plan.md §5.5 第 5 步**（第一路）：fujian `iperf3 -c 192.168.56.103 -p 5202 -t 10 -b 10G` | 用户 | ✅ **9/18 M9 通过（B3 关闭）**：CSV pf1hpf 列 10s 段（09:01:49–58）逐秒洪水 1514B/包，合计 5.278M 包/7.99e9 B，与 dump 铁证逐位一致；p1 列 90s 背景 15.7KB（56.x 不经 p1 符合设计）、en3 背景。历程：M5 机制铁证 7.817GB → 脚本两 bug（c7ce8ec 服务端匹配 → 8c35e38 头行修复 `NXST_FLOW reply` 被 head -1 吃掉）→ M7 三次窗外 → M8 铁证在窗但轮询 0 → M9 修复版全通 |
| B4 | **pipe-collection-plan.md §5.5 第 6 步**（第二路，同窗）：helong 的 BF2 `iperf3 -c 10.99.99.1 -B 10.99.99.3 -t 5 -b 10G` | 用户 | ✅ 9/18 通过：p1 列 6.31GB/4.157M 包 = 1518B/包 标准线帧，1:1 对 iperf 5.60GiB×1.049 帧开销；ethtool p1 Speed=**100G** 记录在案；1782B 疑点定案=截尾+计数器异步刷新采样失真（判读看全窗汇总） |
| B5 | Claude 判读 → Task #30、#39 关闭 | Claude | ✅ **9/18 完成：M2 通过（B3 M9+B4 双过）→ Task #30/#39 关闭**；p1 判死新形态与 Part B 13.1GB 对照记录在案 |

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
| D1 | 四标定面（p1–p7）照操作单执行回传 | 操作单判读标准；**执行照 docs/saturation-calibration-execution.md（9/18 独立执行单，一次上机 ~40min）**；进度：✅ 规格钉死（9/18，双侧 lspci）——Arm 侧五链 Gen4 x16 → pcie1 [cap]=31.5GB/s；**fujian 侧 LnkSta 8GT/s (downgraded) → 主机面实际 Gen3 x16 → pcie0 [cap]=15.75GB/s**；p1=100G → wire 系 [cap]=12.5GB/s；已回填 anchor_sat.conf + extract_anchors.py；✅ **§4 已回传验证（9/18 晚，sat_results_0918_1319.tar.gz，工具 tools/check_sat_results.py）**：五面响应合理（p5 最强×234–390、p3×43–227、p6×88–149、p4×9–34、p1×2–5 语义自洽）；四失效复证（0x67/0x53/0x4a 饱和面 FLAT，poc_writes/fail 活）；tilenet×3+trio×4 真零（[unverified] 证据+1）；✅ **p7 通过（9/20）**：根因=fujian 服务端绑错地址（`-B 10.99.99.1`，§5.6 Part A/B 遗留），补 56.11 服务端后三轮满载 1.86/1.95/1.97GB/s net_tx（iperf 14.3/14.8/15.1Gbps 吻合）、**io_reads×64B≈net_tx 精确互证**（29.04M×64=1.858GB/s vs 1.86GB/s，NIC DMA 内存读=TX 速率）；net_rx≈net_tx=采集伪影（collect_all.c:1077 [net] 无过滤求和含 ovsbr1，桥 rx 镜像 SF0 tx）→ net_rx 锚点不取 p7、靠 §5 e1_esw 每口列；✅ **p3 重跑通过（22:43–22:46）**：sed 60 循环生效，负载窗 100% 占空比（a72_access 三轮均值 179.35/179.14/178.99M/s 离散 0.2%）→ 锚点直取窗口均值；✅ **锚点回填完成（9/20）**：18 CSV 入 bench/results/ → `extract_anchors.py sat` 再生成；首轮暴露**弱面降级回归**（如 rd_req_out 25.4M→316.8K 致三关恶化）→ 修正为 obs-max 地板规则（sat=max(bench, om)）+ io_access=reads+write 推导（p7 实证 29.24M=29.04M+201K）；真应力净增 6 组（io_reads/evictions/emem_rd/wr_req/wr_dbid_ack/wr_req_in/victim_write/allocate）；重验见 D4、报告 docs/validation-replay.md §7；**§5/§6/§7 回传验证（9/21 批 39 文件 → 9/22 重传同名包 40 文件，两次全验）**：①36 旧文件两批均逐字节一致 ✓；②e1_sat_rx 复验**通过**（9/22 重传版 81 列）：pf1hpf_rx 803MB/s=6.43Gbps 干净入流量（10G 流×平台瓶颈）、en3f1pf1sf0_tx+enp3s0f1s0_rx 镜像对双计数（2× 伪影每口级分解）、p1=0、pcie1_rx 843MB/s=6.75Gbps（旧批同为 846——此前"787M/6.3Gbps"系本地二次差分误算，两批负载实际一致）→ 每口列复验达成（根因=bench 二进制早于 8ff40c4，make+cp 修复）；③tilenet 接线确认（三轮 idle 70/70 全零），但 9/22 两窗均带 **~50% 残留负载**（stress-ng 疑未退出：cpu 50.8% 平坦+l1d 3.0G/s+a72_access 1M/s 纯计算签名）且 stream 循环又落窗外（txt 01:17 vs 窗 01:15:41 结束）→ 防呆重跑块已入执行单 §6（pkill + 30 循环 + 整段粘贴），判读目标 a72_access≈150M/s 且 tilenet 仍 0；✅ **第三次回传验证（9/22 02:44 批 42 文件）**：tilenet 三列三轮窗均 70/70 全零=接线证据固化；但 stream2 窗（02:06:42–02:07:51）第三次无 STREAM 签名（a72 基态 0.5–1.6M/s、仅 02:07:40–41 两秒脉冲 5.2/9.0M/s；stream1/idle 同位置无脉冲=一次性事件），txt 30 run 完整 mtime 02:12=整段落窗外——三次同模式定案为**执行时序问题非数据问题**；**~50% 背景在 pkill 后仍在**=非 stress-ng/stream、系常驻进程（9/20 23:57 idle 即存在）→ §6 已改**顺序防呆块**（loop 先起后台+collect 前台 60s 落在循环内，不可能再错过）+ ps 定位命令；**50% 进程身份已定（9/22 ps 回传）：mlnx_snap_emu（SNAP 仿真守护进程，`-m 0xf0` 钉核 4–7、399% CPU=4 核满转，开机常驻）**——非 stress-ng/OVS（ovs-vswitchd 仅 0.5%）；§6 判读不受影响（179M/s≫1M/s 基线、snap 不抢核 0–3），可选 systemctl stop 后重跑更干净；✅ **§6 闭合（9/22 12:25 重跑，tilenet_stream3 批）**：窗 12:25:09–12:26:08 前 41 行 a72_access 177.0–195.6M/s（均值 180.4M/s，与 p3 三轮均值 179.3M/s 差 0.6%）、cpu 94.4–99.0%=STREAM 满窗占空；**tilenet cdn/ddn/ndn 59/59 全零（含 41 行满负载行）→ 内存负载不触网络 tile 闭合证据达成**；snap 未停、满载行仍精确复现 p3 → p3 锚点无需修正；**Task #40 §5/§6/§7 全部闭环**；p4/p5 扩展重跑 + D2 7 应用已编入执行单 §9（9/22，命令自包含，前置=扩展配置提交推送待批准） |
| D2 | 7 应用真实负载重跑（含 e1_esw 每口列） | ⏳ 命令已备：saturation-calibration-execution.md **§9c**（d2_gN 命名防覆盖、g3/g7 双端时序、run1 必做 run2/3 可选）；前置 = §9a 提交推送（待批准） |
| D3 | **0x5d/0x72 语义专项验证**：walk 计入假设 + L2b 的"0x5d 不反映顺序带宽" | 探针 E3 遗留（见下 E 节） |
| D4 | SAT-SUSPECT g1/g2 锚点替换：memrand/cache 真应力（需 C 的修复版 memrand） | ✅ **本地线完成（9/20）**：三关重验——空载与 M4 逐位一致（0.043/0.048/0.100/0.118）、回放 17/17（e1_g7 加低载平局条款，cr 36:nad 35）、SAT-SUSPECT 收敛 g1×5/g2×1/e1_n0_2b×1（M4 时 g1×5/g2×2）；victim_write 锚点已换 p5 真应力却仍标记=xz 真实贴近饱和（非缺陷）；残余 om 取自负载自身峰值（g1_run1/g2_run3），**可选** p4/p5 扩展配置重跑（~8 分钟，配置+报告 docs/validation-replay.md §7.4 已就绪）→ 不阻塞 BFS 本地线 |

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

**当前最前序动作**：**BFS 搜索层已实现并通过自检（9/22，实例集 A 17/17 + B 6/6，tools/bfs_search.py）**；**Task #40 §5/§6/§7 全闭环**。下一优先：① **批准提交推送**（扩展 bench_p4/p5 配置 + bfs_search 等，deploy 前置）→ 用户跑执行单 §9（p4/p5 扩展 + D2 七应用，~20 分钟）；② 回传后本地判读（锚点再生成 + 三关重验 + D2 入 BFS 实例表）；③ **F1 E0-1 NHD 重证**（BF2↔BF2 对打，p1 物理口口径，M2 依赖已解除）。
