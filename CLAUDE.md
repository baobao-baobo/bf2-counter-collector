# bf2-counter-collector 项目指引（给 Claude 的会话上下文）

## 项目是什么

BlueField-2 DPU 计数器采集工具：**配置文件驱动**——用户在 INI 里配置采集哪些
counter、采样频率，运行后输出 CSV（未采样的字段留空 = NaN 语义）。
仓库：https://github.com/baobao-baobo/bf2-counter-collector（public）。

## 学习资料（在本目录，按顺序）

1. `docs/architecture.md` —— 总体架构：三层设计（catalog→config→engine）、
   多速率调度（tick / k / `tick%k==k-1`）、三种采样机制、列名规则、设计决策表
2. `docs/code-walkthrough.md` —— 逐模块逐函数代码详解（行号与当前源码一致），
   用户正在按此文档逐行精读学习本项目
3. `README.md`、`docs/collected-events.md` —— 使用说明与事件总表
4. `docs/acceptance-checklist.md` —— 设备验收清单（用户自行执行）

用户学习用法：边读文档边跑信息命令对照
（`--list-events` 看事件目录、`-c xxx.conf --check-config` 看 resolve 结果）。

## 源码地图

```
code/catalog.c/.h    事件目录：硬件事件编码（唯一含编码的文件），
                     tilenet 51 事件部分由公式生成（build_tn_events）
code/config.c/.h     零依赖 INI 解析器 + resolve（事件名→编码、k 倍数校验、
                     tile 轮换 mask/slot、L3/PCIe 列合并）+ CSV 表头生成
code/collect_all.c   采集引擎：硬件发现/编程/差分采样、PMU、/proc 软件指标、
                     主循环（tick%k==k-1）、12 个 row_* 行写出器（与表头严格镜像）
code/test_config.c   主机单元测试 208 断言，parity gate：
                     默认配置表头 == 8/13 设备验证的 42 列逐字节一致
configs/default.conf 默认配置（= 内置默认 = 已验证基线）
configs/multirate.conf  多速率示例（tilenet k=5、tile/l3cache k=2）
tools/check_csv.py   校验 CSV 结构与采样节奏（stdlib only）
bench/               离线 bench 套件：bin/（静态 aarch64 二进制）+ configs/
                     （bench_p*.conf）+ run_bench.sh；src/ 留在本地不入库
Makefile（根+code/） 根委托 code/；test 用 HOSTCC，交叉编译用 CROSS=
```

## 关键约束（不可违反）

- **源文件（.c/.h）注释必须纯 ASCII**——BF2 的 gcc 9.4 不支持 UTF-8 注释；
  文档（.md）与配置注释可以用中文
- **Claude 不连接 BF2 设备/跳板机**：设备编译、部署、验收由用户自行执行，
  Claude 只做本地工作并等待用户回传结果
- 设备是 aarch64 Ubuntu 20.04（glibc 2.31）：交叉编译的二进制不能直接在
  设备跑，设备上要重新编译；本地 `make CROSS=aarch64-linux-gnu-` 只验证代码
- 改代码后必须：`make test` 全绿（parity gate 不能破）+
  `make CROSS=aarch64-linux-gnu-` 零警告
- 本仓库 git 提交**不带** `Co-Authored-By: Claude` 尾注（用户明确要求）

## 常用命令（在 WSL 里）

```bash
make                 # 编译（x86 原生，可跑 --list-events 等信息命令）
make test            # 208 断言单测
make CROSS=aarch64-linux-gnu-   # 交叉编译验证（零警告）
./collect_all --list-events                # 列出全部事件目录（无需 root）
./collect_all -c configs/default.conf --check-config   # 解析+resolve 结果
```

## 工作方式

- 与用户用中文交流；代码/提交信息用英文
- 回答学习问题时：引用 `文件:行号`，结合架构图与"一个 tick 的生命周期"
  主线解释，必要时用简单示例
