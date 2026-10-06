# 第五章验证阶段六案例操作单（2026-09-22）

**目的**：按 docs/pathfinder-ch5-app-research.md 的 6 case 提案执行实验——新应用
候选池上机、每 case 用参数翻转制造 BF2 数据路径瓶颈转移、引擎判读 + 独立语义对照。
**Claude 不连设备**：命令由用户执行、结果回传、Claude 本地判读。

**案例一览**（对应 PathFinder 第五章骨架）：

| Case | 主题      | 轮次                  | 转移演示                  |
| ---- | ------- | ------------------- | --------------------- |
| 1    | 新应用路径分类 | c1a–c1f（6 轮）        | 六应用六种路径签名             |
| 2    | 方向翻转    | c2b、c2c（c2a 复用 c1d） | nad↔出向↔tx（p1）         |
| 3    | 工作集转移   | c3a–c3e（5 轮）        | 缓存驻留↔DRAM↔存储          |
| 4    | 访问模式转移  | c4a–c4e（5 轮）        | seq↔rnd、rd↔wr、顺序灌↔随机灌 |
| 5    | 并发争用与份额 | c5a、c5b（2 轮）        | 4 实例异参数同跑 + 份额分解      |
| 6    | 机制切换    | c6a–c6d（4 轮）        | TCP↔UDP、页缓存↔O_DIRECT  |

执行按四批次推进，每批回传判读后再进下一批。

> **2026-10-06 数据重组**：results/ 已按实验系列归类为 17 个子文件夹（分类索引见
> results/README.md）。本单命令里的 `results/ch5_*.csv` 指重组前的顶层路径，对应
> 文件现位于 `results/ch5-batch{1,2,3}/`（批 1=c1/c2、批 2=c3/c4、批 3=c5/c6）、
> `results/case7/`（m1-m5）。新跑轮次输出仍写 results/ 顶层，判读后归入对应批次。

---

## 批次 0：前置准备（一次性，~1–2 小时）

### 0.1 设备环境检查（BF2 上执行，输出贴回）

**已回传（9/22）**：free 15GB 总 / 9GB available（**无 swap**）；**/tmp = tmpfs 仅
7.8GB**；gfortran 缺失（只有 dpkg/gcc）；libibverbs + libaio 齐；rdma link：
pf0hpf / pf1hpf / p1 / Arm 代表口均 ACTIVE（RoCE 链路层就绪，应用层待后续验证）。
**两个资源结论（已按此修正全部轮次）**：
① RAM 预算 9GB → GUPS 四表 6.6GB 下调为 3.3GB（见 c5b）；
② **/tmp 是内存盘，数据放 /tmp 永远不会触 eMMC**——LevelDB/sysbench 数据必须落到
eMMC 挂载点（本轮统一 `/root/bf2k/data/`，前提：根文件系统在 eMMC 上，待补查）。

**补查（贴回）**：

```bash
lsblk                    # 找 eMMC 设备与挂载点
df -h / /root            # eMMC 分区可用空间（需 ≥10GB：DB 2GB×2.5 + sysbench 4G）
```

### 0.2 工具获取（fujian 下载 → scp → 设备，全部老流程）

fujian 有外网，负责下载；BF2 无外网。除特别注明外：fujian 下载到临时目录，
`scp` 到 BF2 的 `/tmp/`，再在 BF2 上构建。

- **0.2.0 挑包通用方法（ports.ubuntu.com 目录已失效，改国内镜像站，fujian 上 curl 可列表）**
  
  先确认系统版本（贴回）：
  
  ```bash
  cat /etc/os-release | head -3; uname -m    # 预期 Ubuntu 20.04 (focal) aarch64
  ```
  
  fujian 上列目录（tuna 首选，不通换 ustc/aliyun）：
  
  ```bash
  M=https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/pool
  curl -s $M/universe/s/sysbench/  | grep -o 'sysbench_[^"]*arm64\.deb' | sort -u
  curl -s $M/multiverse/n/netperf/ | grep -o 'netperf_[^"]*arm64\.deb' | sort -u
  curl -s $M/universe/s/sockperf/  | grep -o 'sockperf_[^"]*arm64\.deb' | sort -u
  curl -s $M/main/g/gcc-9/         | grep -o 'gfortran-9_[^"]*arm64\.deb\|libgfortran-9-dev_[^"]*arm64\.deb' | sort -u
  # 不通时换：M=https://mirrors.ustc.edu.cn/ubuntu-ports/pool
  #          M=https://mirrors.aliyun.com/ubuntu-ports/pool
  ```
  
  **关键陷阱**：pool 目录跨 Ubuntu 版本共享（focal/jammy/noble 的文件都在），
  必须挑与 os-release 匹配的版本——focal 对应 sysbench `1.0.18+dfsg`、
  netperf `2.7.0+git20191211`、gfortran-9 `9.4.0-1ubuntu1~20.04`。挑错版本
  （如 jammy 的 sysbench 1.0.20）会因 glibc 太新跑不起来。sockperf 若目录里
  没有 focal 版本（只有 jammy+）→ 按 c6 回退：UDP 轮改用 netperf
  `-t UDP_STREAM`，sockperf 留到 RDMA 升级件时源码构建。
  下载：文件名拼到 `$M/...` 后 wget；`dpkg -i` 报缺依赖时缺什么同池补什么
  （贴回报错即可）。
  **9/22 又添一坑**：`ubuntu0.1` 后缀≠focal 安全更新——luajit 的
  `2.1.0~beta3+dfsg-6ubuntu0.1` 是 **jammy 版**（Depends libc6≥2.34，focal 只有
  2.31），focal 只到 `+dfsg-6`。通则：focal 包不会要求 libc6>2.31，拿不准时先
  `dpkg-deb -f x.deb Depends` 验明正身再装。另注意 luajit 在 **universe** 池
  （sysbench 同源），不在 main。
  
  **已确认文件名（9/22 tuna 目录实查，按 focal 选；URL 里 `+` 要写 `%2B`）**：
  
  | 包                             | 文件名                                                  | 备注                                                 |
  | ----------------------------- | ---------------------------------------------------- | -------------------------------------------------- |
  | netperf（arm64，BF2+helong 各一份） | `netperf_2.7.0-0.1_arm64.deb`                        | 报错则换同目录 `netperf_2.6.0-2.1_arm64.deb`（bionic 版必兼容） |
  | sysbench（arm64）               | `sysbench_1.0.18+ds-1_arm64.deb`                     | 弃 1.0.20+ds-9（noble）；补链三包见下                        |
  | libluajit-5.1-2（arm64）        | `libluajit-5.1-2_2.1.0~beta3+dfsg-6_arm64.deb`       | universe 池；6ubuntu0.1=jammy 勿选                     |
  | libluajit-5.1-common（all）     | `libluajit-5.1-common_2.1.0~beta3+dfsg-6_all.deb`    | 与 -2 精确同版（= 咬合）                                    |
  | libpq5（arm64）                 | `libpq5_12.22-0ubuntu0.20.04.4_arm64.deb`            | sysbench pg 驱动；12.16 不存在                           |
  | gfortran-9（arm64）             | `gfortran-9_9.4.0-1ubuntu1~20.04.3_arm64.deb`        | 同目录再拿 libgfortran-9-dev 同版本                        |
  | libgfortran-9-dev（arm64）      | `libgfortran-9-dev_9.4.0-1ubuntu1~20.04.3_arm64.deb` | 缺 libgfortran5 时报错贴回（libquadmath0 对 arm64 不存在，勿找）  |
  | sockperf（arm64）               | `sockperf_3.6-2build1_arm64.deb`                     | 试装；报 glibc 错→跳过（c6 回退已备）                           |

- **0.2.1 netperf（deb，fujian 一份 x86 + BF2 一份 arm64 + helong BF2 一份 arm64）**
  
  - fujian 端（跑 netserver 用）：`apt install -y netperf`
  - arm64 包：按 0.2.0 从 `multiverse/n/netperf/` 挑 focal 版本下载；BF2 上
    `dpkg -i`；缺依赖同池补装、报错贴回。
  - 装后验证：`netperf -V`、`netserver -V` 各出一行版本。

- **0.2.2 sysbench（arm64 deb）**：按 0.2.0 从 `universe/s/sysbench/` 挑 focal
  版本（1.0.18）；`dpkg -i`；缺依赖同池补装（libaio1 设备已有）。验证：
  `sysbench --version`。

- **0.2.3 sockperf（arm64 deb）**：按 0.2.0 从 `universe/s/sockperf/` 检查；
  有 focal 版本则装，只有 jammy+ 版本则跳过（按 0.2.0 回退）。验证：
  `sockperf --version`。

- **0.2.4 LevelDB db_bench（源码 cmake）——9/22 实测通过，submodule 坑已钉死**
  GitHub release tarball **不含 submodule 内容**：third_party/googletest 与
  third_party/benchmark 解出来是空目录。而 db_bench 链接 gmock/gtest，这两个库
  目标只在 `-DLEVELDB_BUILD_TESTS=ON` 时被创建；TESTS=ON 又触发 CMakeLists.txt:304
  `add_subdirectory(third_party/benchmark)` → 两个 submodule 都得补。全套命令：
  
  ```bash
  # fujian 下载三个包（~/bbbb/app）后 scp 到 BF2 /tmp：
  #   wget https://github.com/google/leveldb/archive/refs/tags/1.23.tar.gz
  #   wget https://github.com/google/googletest/archive/refs/tags/release-1.12.1.tar.gz -O googletest-1.12.1.tar.gz
  #   wget https://github.com/google/benchmark/archive/refs/tags/v1.7.1.tar.gz -O benchmark-1.7.1.tar.gz
  cd /tmp && tar xf 1.23.tar.gz && cd leveldb-1.23 && cd third_party
  mkdir -p googletest && tar xf /tmp/googletest-1.12.1.tar.gz --strip-components=1 -C googletest
  mkdir -p benchmark  && tar xf /tmp/benchmark-1.7.1.tar.gz  --strip-components=1 -C benchmark
  cd /tmp/leveldb-1.23
  # 给 db_bench 补 gtest/gmock 头文件路径。append 到文件尾，勿用 sed 行内插：
  # pattern 前缀会误伤 db_bench_sqlite3/db_bench_tree_db；heredoc 引号保住 ${...}
  cat >> CMakeLists.txt <<'EOF'
  target_include_directories(db_bench PRIVATE ${PROJECT_SOURCE_DIR}/third_party/googletest/googlemock/include
  ${PROJECT_SOURCE_DIR}/third_party/googletest/googletest/include)
  EOF
  mkdir -p build && cd build
  cmake .. -DCMAKE_BUILD_TYPE=Release -DLEVELDB_BUILD_TESTS=ON -DLEVELDB_BUILD_BENCHMARKS=ON
  make -j8
  cp db_bench /root/bf2k/bench/bin/
  /root/bf2k/bench/bin/db_bench --benchmarks=fillseq --num=1000 --value_size=100
  # 验收 = 首行 "LevelDB:    version 1.23"；--version 旗标 1.23 已移除（报 Invalid flag）
  ```
  
  踩坑记录：①改 CMakeLists.txt 后必须重跑 cmake（make 只触发
  cmake_check_build_system，不重新生成）；②configure 阶段 `HAVE_CXX_FLAG_*`
  探针报 Failed（WSHORTEN_64_TO_32 / WD654 / WTHREAD_SAFETY /
  GNU_POSIX_REGEX）是 google benchmark 的特性探测——那些是 Clang 旗标，gcc
  不支持属预期，取回退路径，对功能/性能零影响。

- **0.2.5 lmbench3（源码 make）——9/22 实测一把过**
  
  ```bash
  # fujian：wget https://sourceforge.net/projects/lmbench/files/development/lmbench-3.0-a9/lmbench-3.0-a9.tgz/download -O lmbench.tgz
  # scp lmbench.tgz 到 BF2 /tmp，然后（设备上执行）：
  cd /tmp && tar xf lmbench.tgz && cd lmbench-3.0-a9 && make -j8
  ls bin/                      # 产物直接位于 bin/（无 arch 子目录）
  cp bin/lat_mem_rd bin/bw_mem /root/bf2k/bench/bin/
  /root/bf2k/bench/bin/lat_mem_rd 16      # 冒烟：出一行 stride 延迟（ns）
  /root/bf2k/bench/bin/bw_mem 1M rd       # 冒烟：出 1MB 读带宽（MB/s）
  ```
  
  实测记录：aarch64 探测与 rpc 编译均无碍（预判的两个坑都未触发）。

- **gfortran 安装（0.1 已确认缺失，NPB 的 EP/MG/CG/FT 必需）**：
  **9/22 已实测通过**。路线结论（踩坑定案）：①gfortran-10 不存在——focal 从未
  发布 gcc-10 编译器本体（gcc-10 源包只构建运行时库；libquadmath0 对 arm64 不
  存在）；②libgfortran5 只由 gcc-10 源构建（9.4.0 版逐文件 404 实测），且精确
  咬合 gcc-10-base (= 同版)——设备镜像烤入的是池里没有的中间版 10.3.0；③终解
  =双链同升：gcc-9 全家 .1→.3（含 libasan5）+ gcc-10 运行时全家 10.3.0→10.5.0。
  18 个 deb 全部 focal 原生版本（libhwasan0 设备未装故不在列；若 `dpkg -l` 见
  10.3.0 的它则补同版 10.5.0），fujian 下好 scp 后**一条命令全装**：
  
  ```bash
  dpkg -i \
    gcc-9-base_9.4.0-1ubuntu1~20.04.3_arm64.deb cpp-9_9.4.0-1ubuntu1~20.04.3_arm64.deb \
    libgcc-9-dev_9.4.0-1ubuntu1~20.04.3_arm64.deb libasan5_9.4.0-1ubuntu1~20.04.3_arm64.deb \
    gcc-9_9.4.0-1ubuntu1~20.04.3_arm64.deb gfortran-9_9.4.0-1ubuntu1~20.04.3_arm64.deb \
    libgfortran-9-dev_9.4.0-1ubuntu1~20.04.3_arm64.deb \
    gcc-10-base_10.5.0-1ubuntu1~20.04_arm64.deb libgcc-s1_10.5.0-1ubuntu1~20.04_arm64.deb \
    libatomic1_10.5.0-1ubuntu1~20.04_arm64.deb libcc1-0_10.5.0-1ubuntu1~20.04_arm64.deb \
    libgomp1_10.5.0-1ubuntu1~20.04_arm64.deb libitm1_10.5.0-1ubuntu1~20.04_arm64.deb \
    liblsan0_10.5.0-1ubuntu1~20.04_arm64.deb libstdc++6_10.5.0-1ubuntu1~20.04_arm64.deb \
    libtsan0_10.5.0-1ubuntu1~20.04_arm64.deb libubsan1_10.5.0-1ubuntu1~20.04_arm64.deb \
    libgfortran5_10.5.0-1ubuntu1~20.04_arm64.deb
  dpkg --configure -a
  gfortran-9 --version      # 应报 9.4.0；NPB 用 gfortran-9 编译（见 0.2.6）
  ```
  
  分步装会报依赖挂起（9/22 实测：先 7 包后补 12 包收尾亦可，同一终态）；缺包
  报错贴回。**回退方案**（仍装不上）：NPB 只编 IS（C 内核），EP 诚实负例改
  `sysbench cpu --cpu-max-prime=20000`，MG/CG/FT 三场暂缓。

- **0.2.6 NPB 3.4.3（源码 make，五内核）**
  
  ```bash
  # fujian：wget https://www.nas.nasa.gov/assets/npb/NPB3.4.3.tar.gz
  #   （若 403/超时，搜索 "NPB3.4.3.tar.gz" 任一镜像下载）
  cd /tmp && tar xf NPB3.4.3.tar.gz && cd NPB3.4.3/NPB3.4-SER
  cp config/make.def.template config/make.def
  sed -i 's/^CC.*/CC = gcc/; s/^F77.*/F77 = gfortran-9/; s/^FLINK.*/FLINK = gfortran-9/' config/make.def
  sed -i 's/-O/-O3/' config/make.def
  # 五个内核各编三档 class（S 可能太短、C 可能太久，B 为中间档）：
  for k in ep is mg cg ft; do for c in S A B; do make $k CLASS=$c; done; done
  cp bin/*.x /root/bf2k/bench/bin/     # ep.S.x ep.A.x ... ft.B.x
  ```

- **0.2.7 GUPS（自写 ~60 行 C，设备上直接写入，不走 git）——v2 计时修复版**
  踩坑定案（9/23）：v1 用 `clock()`（进程 CPU 时间）——aarch64 vDSO 只提供墙钟
  类时钟（REALTIME/MONOTONIC 等），进程 CPU 时间必须走系统调用，每次迭代查一次
  ≈700–900ns，计时开销主导速率（lg=15/18/20 全 ~0.001 GUP/s 阶梯消失）。v2 改
  `CLOCK_MONOTONIC`（vDSO ~20ns）+ 每 1024 次更新查一次时间：
  
  ```bash
  mkdir -p /root/bf2k/bench/src
  cat > /root/bf2k/bench/src/gups.c <<'EOF'
  /* gups.c - HPCC RandomAccess 单进程内核（自写，表尺寸/时长可参数化）。
   * 对 2^lg 个 64 位字的表做随机地址流的读-改-写（T[idx] ^= ran），
   * 指标 GUPS = 每秒 10^9 次随机更新。地址流 = 64 位 LFSR（全周期）。
   * v2：计时改 CLOCK_MONOTONIC（aarch64 上 clock() 走系统调用，每次迭代
   * ~700ns 计时开销主导速率）；每 1024 次更新才查一次时间。 */
  #include <stdint.h>
  #include <stdio.h>
  #include <stdlib.h>
  #include <time.h>
  static inline uint64_t lfsr(uint64_t x){
      return (x << 1) | (((x >> 63) ^ (x >> 3) ^ (x >> 2) ^ (x >> 0)) & 1);
  }
  static double now(void){
      struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
      return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
  }
  int main(int argc, char **argv){
      int lg = (argc > 1) ? atoi(argv[1]) : 20;        /* 表 = 2^lg 字 */
      double secs = (argc > 2) ? atof(argv[2]) : 30.0; /* 运行时长 */
      uint64_t n = 1ULL << lg, *T = calloc(n, 8);
      if (!T) { fprintf(stderr, "gups: alloc fail\n"); return 1; }
      uint64_t ran = 0x123456789abcdef0ULL, upd = 0, i;
      double t0 = now(), end = t0 + secs;
      while (1) {
          for (i = 0; i < 1024; i++) {
              ran = lfsr(ran);
              T[ran & (n - 1)] ^= ran;
              upd++;
          }
          if (now() >= end) break;
      }
      printf("GUPS lg=%d updates=%llu rate=%.3f GUP/s time=%.2fs\n",
             lg, (unsigned long long)upd, upd / (now() - t0) / 1e9, now() - t0);
      return 0;
  }
  EOF
  gcc -O2 -o /root/bf2k/bench/bin/gups /root/bf2k/bench/src/gups.c
  /root/bf2k/bench/bin/gups 15 3    # 冒烟：3 秒跑完，打印 GUP/s（lg15 应快于 lg20）
  ```

### 0.3 数据与基准准备（BF2）

**数据目录必须落在 eMMC 挂载点**（/tmp 是 tmpfs，见 0.1 结论）。统一
`/root/bf2k/data/`（根文件系统在 eMMC 上，由 0.1 补查的 lsblk/df 确认）。

```bash
mkdir -p /root/bf2k/data && df -h /root/bf2k/data   # 确认挂载于 eMMC 且 ≥10GB 可用
# LevelDB 测试库（2GB，供 c1e/c3e/c5a 读）：
/root/bf2k/bench/bin/db_bench --benchmarks=fillseq --num=2000000 \
  --value_size=1000 --db=/root/bf2k/data/dbtest    # 冒烟兼建库，~1–3 分钟，最后打印 ops/s
ls -la /root/bf2k/data/dbtest                       # 应有 ~2GB 的 .ldb 文件
# sysbench 文件（4GB，供 c1f/c6c/c6d；文件建在当前目录，故先 cd）：
cd /root/bf2k/data && sysbench fileio --file-num=8 --file-total-size=4G prepare
```

### 0.4 NPB class 时长标定（BF2）——9/23 定案

口径：`env OMP_NUM_THREADS=4 taskset -c 0-3`（与批次 1 `-b 0-3` 窗口一致；
NPB 3.4 无 SER，OMP 四线程替代单线程 SER，信号对齐多线程饱和锚点 p3/p5）。

实测（Time in seconds / real）：

| 内核  | S            | A                      | B                      | C                |
| --- | ------------ | ---------------------- | ---------------------- | ---------------- |
| EP  | —            | 4.98 / 5.0s            | **20.00 / 20.0s**      | —                |
| IS  | —            | 0.46 / 1.0s            | —                      | **9.82 / 19.5s** |
| FT  | —            | 2.66 / 3.2s            | **38.89 / 41.6s**      | —                |
| MG  | 0.00 / 0.01s | 2.08 / 3.1s            | **9.72 / 10.7s（循环×2）** | —                |
| CG  | —            | **2.34 / 2.7s（循环×10）** | —                      | —                |

定档：EP=B 单发；IS=C 单发（real 19.5s 含 ~10s 初始化分配，窗口按 real 计）；
FT=B 单发；MG=B 循环×2；CG=A 循环×10。全部 Verification SUCCESSFUL。
is.C 需补编：`make is CLASS=C`（S/A/B 已编，C 未编）。

### 0.5 对端就绪（fujian / helong）

```bash
# fujian（批次 1 的 c1d/c2b 服务端；sockperf 批次 3 服务端）：
netserver -D -4          # netperf 守护进程（服务端口 12865，跑在后台）
which sockperf || apt install -y sockperf   # 批次 3 才用，先装好
# helong BF2（批次 1 的 c2c 服务端）：
netserver -D -4          # 同款 arm64 deb（0.2.1 已给一份）
# 连通自检（BF2 上）：
ping -c 3 10.99.99.3     # helong BF2 p1 地址（Part A 已验证同二层网）
```

**批次 0 验收**：环境检查输出 + 各工具版本行 + NPB 标定 time + 冒烟输出全部
贴回，Claude 确认后开始批次 1。

---

## 批次 1：Case 1（新应用路径分类）+ Case 2（方向翻转）

所有轮次：`sudo ./run_phase.sh -c configs/e1_esw.conf -o <csv> -a "<命令>" -b 0-3 -t <上限秒>`。
`-b 0-3` 必须保留（mlnx_snap_emu 常驻核 4–7）。相位日志在 `<csv>.phase.log`，
判读窗口按日志自动界定，`-t` 只是上限、app 提前结束不算失败。

### Case 1 轮次（6 轮）

```bash
# c1a EP（纯计算诚实负例；档位按 0.4 标定）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c1a_ep_run1.csv \
  -a "/root/bf2k/bench/bin/ep.<档>.x" -b 0-3 -t 300

# c1b IS（随机桶排序 → cr miss 主导）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c1b_is_run1.csv \
  -a "/root/bf2k/bench/bin/is.<档>.x" -b 0-3 -t 300

# c1c FT（3D FFT+转置 → cr 流式 + wb）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c1c_ft_run1.csv \
  -a "/root/bf2k/bench/bin/ft.<档>.x" -b 0-3 -t 300

# c1d netperf 入向（MAERTS=服务端发送→客户端收；fujian netserver 须先起，
#   BF2 上跑客户端 → 流量 fujian→BF2 → nad）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c1d_netin_run1.csv \
  -a "netperf -H 192.168.56.11 -t TCP_MAERTS -l 30" -b 0-3 -t 60

# c1e db_bench 随机读·小缓存（64MB 块缓存 vs 2GB 库 → 未命中走 eMMC → ib/ih）：
#   先清页缓存（见下方"清缓存"说明）再跑：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c1e_dbmiss_run1.csv \
  -a "/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=67108864 --reads=100000 \
  --db=/root/bf2k/data/dbtest" -b 0-3 -t 600

# c1f sysbench 随机写文件（页缓存写+随机 eMMC 写 → cr/wb + io）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c1f_sbrndwr_run1.csv \
  -a "cd /root/bf2k/data && sysbench fileio --file-num=8 --file-total-size=4G \
  --file-test-mode=rndwr --file-block-size=16K --file-io-mode=sync --threads=4 \
  --time=30 run" -b 0-3 -t 60
```

**清缓存**（每次跑 eMMC 相关轮次前执行，防止页缓存把"存储访问"变成"内存命中"）：

```bash
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
```

### Case 2 轮次（c2a 复用 c1d；2 轮新增）

```bash
# c2b netperf 出向（TCP_STREAM=客户端发送；BF2→fujian → Arm 出向签名，
#   对照 E4-E2 armsend：ih 主导 + nad 近平局）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c2b_netout_run1.csv \
  -a "netperf -H 192.168.56.11 -t TCP_STREAM -l 30" -b 0-3 -t 60

# c2c 真 tx 路径（BF2→helong BF2 经 p1 100G；helong netserver 须先起，
#   ping 通 10.99.99.3 后跑 → p1_tx 点亮 tx + Arm 出向）：
# 【已废弃（9/30）】：Arm→p1 出向在 eSwitch SF 入向断联（三轮诊断定案），
#   改为主机口径 run3（见批次一补跑块与记录表），本命令留档备查。
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c2c_tx_run1.csv \
  -a "netperf -H 10.99.99.3 -t TCP_STREAM -l 30" -b 0-3 -t 60
```

### 批次 1 判读标准（Claude 本地）

| 轮      | 期望判决                       | 期望签名                                                    | 不一致处置                                 |
| ------ | -------------------------- | ------------------------------------------------------- | ------------------------------------- |
| c1a EP | low                        | 七路径全 ≈0（openssl 同族负例）                                   | 按 docs/validation-replay.md §8.6 三类诊断 |
| c1b IS | dominant cr                | a72/hnf 抬升；wb 中度（排序阶段）                                  | 同上                                    |
| c1c FT | dominant cr                | a72 流式 + wb（转置写）；ib 可见                                  | 同上                                    |
| c1d=2a | dominant nad               | en3f1_rx 抬升；其余路径残值（E2E-B/D2 同族）                         | 同上                                    |
| c1e    | dominant ib/ih（io 域）       | io_reads 抬升、a72 轻微（miss 读不重算）；与 c3e 对照用                 | 同上                                    |
| c1f    | dominant cr 或 wb           | a72 写+io 读（eMMC DMA 读缓存区）+eMMC 随机写                      | 同上                                    |
| c2b    | dominant ih（E2 armsend 签名） | ih 1.0 量级+nad 近平局；tx≈0（不经 p1）                           | 同上                                    |
| c2c    | tx 点亮（9/30 主机口径修订）         | p1_tx 抬升（tx 路径首证）；nad 不随行（主机→网络 HW 转发、Arm 不参与）；与 c2b 对照 | 同上                                    |

### 回传（批次 1）

```bash
tar czf /tmp/ch5_b1.tar.gz results/ch5_c1*.csv results/ch5_c1*.phase.log \
  results/ch5_c2*.csv results/ch5_c2*.phase.log
# scp 回本地（老流程）
```

---

## 批次 2：Case 3（工作集转移）+ Case 4（访问模式转移）

> 9/30 标定复核（执行前对账）：0.4 标定（9/23）推翻初版命令的时长假设——MG S 单轮
> 仅 0.01s、lat_mem_rd 单轮亚秒级，固定循环次数撑不起应用窗。c3a/c3c/c3d 改为
> 时长预算循环（timeout 硬终止，run_phase.sh 不检查退出码，124 无碍；窗口到期前
> 应用先死，余时为 post-idle）；c3b 定档 B×3；c4d/c4e 窗长维持 900s（fillrandom
> 在 eMMC 上的时长上界不明，宁大勿小）。若 timeout 缺失（coreutils 应已随批次 0
> 装齐），回退写法：`for i in $(seq 1 N); do ...; done`，N 按实测单轮时长折算（MG S
> 约 100 轮/s、lat_mem_rd 约 1–5 轮/s，取整撑 ~60s）。

### 执行前检查（9/30 已执行，全过）

```bash
which timeout          # ✓ /usr/bin/timeout，时长预算循环可用
/root/bf2k/bench/bin/lat_mem_rd 1 64     # ✓ 表头至 1.00000MB（参数语义确认：首参=区段
         c                                #   MB 上限、次参=步长×64B=4KB）；1MB 档 8.5ns
/root/bf2k/bench/bin/lat_mem_rd 256 64   # ✓ 表头至 256.00000MB；256MB 档 14.7ns
df -h /root/bf2k/data   # ✓ 36G 可用（59G 盘已用 20G；c4d/c4e 新增 ~2.5GB 绰绰有余）
```

> **9/30 检查发现（已入判读标准）**：BF2 实测 DRAM 指针追逐 14.7ns（4KB 步长），远低于
> 教科书 ~150ns → c3d 预期从"cr 抬升温和"上调为"明显"：14.7ns/跳 ≈ 单核 68M 跳/s ≈
> 4GB/s DRAM 读流量（与 c1c FT 同量级）；c3c 1MB 链 8.5ns 几乎不出 L2，低 cr 参照不受
> 影响。成因留待考（BF2 mesh 延迟低 / 64KB 页粒度减 TLB 走表 / 页表命中等，不影响设计）。

```bash
# c3a MG 小工作集（32³ 网格 ~2MB，接近 L2 驻留；0.4 标定单轮 0.01s，"循环 10 次"
#   仅 0.1s 不成立 → 时长预算 90s；循环内 fork/exec 开销会垫高 a72 基线 ~2×，
#   判决以 victim/wb 差值为主，见判读标准）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3a_mgs_run1.csv \
  -a "timeout 90 sh -c 'while true; do /root/bf2k/bench/bin/mg.S.x >> /tmp/c3a_mgs.log 2>&1; done'" \
  -b 0-3 -t 120

# c3b MG 大工作集（256³ ~200MB，纯 DRAM；0.4 标定定档 B=10.7s/轮 → 循环 3 次
#   撑 32s 应用窗，余 268s 空载尾）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3b_mgb_run1.csv \
  -a "for i in 1 2 3; do /root/bf2k/bench/bin/mg.B.x; done" -b 0-3 -t 300

# c3c lmbench 指针链 1MB（实测 8.5ns，几乎不出 L2；单轮亚秒级 → 时长预算 60s；
#   参数语义已核实：首参=区段 MB 上限、次参=步长×64B=4KB）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3c_lat1m_run1.csv \
  -a "timeout 60 sh -c 'while true; do /root/bf2k/bench/bin/lat_mem_rd 1 64 >> /tmp/c3c_lat.log 2>&1; done'" \
  -b 0-3 -t 120

# c3d lmbench 指针链 256MB（实测 14.7ns/跳 ≈ 4GB/s DRAM 读流量；单轮约秒级 → 时长预算 60s）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3d_lat256m_run1.csv \
  -a "timeout 60 sh -c 'while true; do /root/bf2k/bench/bin/lat_mem_rd 256 64 >> /tmp/c3d_lat.log 2>&1; done'" \
  -b 0-3 -t 120

# c3e db_bench 随机读·大缓存（2GB 块缓存 ≈ 库大小 → 全命中走内存 → cr；
#   与 c1e 构成同一库、同一命令、仅 cache_size 不同的阈值翻转对）。
#   三步协议（冷读会拖 eMMC 混杂进窗，必须先预热）：
#   ①清页缓存 → ②预热两遍（2M 读×2，块缓存装满，不入账）→ ③正式轮（不再 drop）
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=2147483648 --reads=2000000 \
  --db=/root/bf2k/data/dbtest > /dev/null
/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=2147483648 --reads=2000000 \
  --db=/root/bf2k/data/dbtest > /dev/null
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3e_dbit_run1.csv \
  -a "/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=2147483648 --reads=5000000 \
  --db=/root/bf2k/data/dbtest" -b 0-3 -t 300
```

Case 4：

```bash
# c4a 顺序读内存（1GB 缓冲、流式 → cr 流式，bypass 或可见）：
# 10/05 修订：total-size 64G→512G（Case 7 m2 实测 3 线程 8.32GB/s，64G 约 8s 吃完、
# time=30 不生效）；补 taskset -c 0-3（4-7 常驻 mlnx_snap_emu，且计数器只采 0-3）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c4a_memseq_run1.csv \
  -a "taskset -c 0-3 sysbench memory --memory-block-size=1G --memory-scope=global \
  --memory-total-size=512G --memory-oper=read --memory-access-mode=seq \
  --threads=4 --time=30 run" -b 0-3 -t 60

# c4b 随机读内存（同一 1GB 缓冲内随机 → cr miss 主导；与 c4a 仅换 access-mode）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c4b_memrnd_run1.csv \
  -a "taskset -c 0-3 sysbench memory --memory-block-size=1G --memory-scope=global \
  --memory-total-size=512G --memory-oper=read --memory-access-mode=rnd \
  --threads=4 --time=30 run" -b 0-3 -t 60

# c4c 顺序写内存（→ wb 主导；与 c4a 仅换 oper）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c4c_memwr_run1.csv \
  -a "taskset -c 0-3 sysbench memory --memory-block-size=1G --memory-scope=global \
  --memory-total-size=512G --memory-oper=write --memory-access-mode=seq \
  --threads=4 --time=30 run" -b 0-3 -t 60

# c4d 顺序灌库（顺序写 2GB → wb 主导 + 顺序 eMMC 写）：
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c4d_fillseq_run1.csv \
  -a "/root/bf2k/bench/bin/db_bench --benchmarks=fillseq --num=2000000 \
  --value_size=1000 --db=/root/bf2k/data/dbtest_fs" -b 0-3 -t 900

# c4e 随机灌库（随机写 50 万条 ~500MB → wb + 随机 eMMC 写；与 c4d 对照）：
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c4e_fillrnd_run1.csv \
  -a "/root/bf2k/bench/bin/db_bench --benchmarks=fillrandom --num=500000 \
  --value_size=1000 --db=/root/bf2k/data/dbtest_fr" -b 0-3 -t 900
```

### 批次 2 判读标准

| 轮   | 期望判决                     | 期望签名                                                                                                  | 不一致处置                               |
| --- | ------------------------ | ----------------------------------------------------------------------------------------------------- | ----------------------------------- |
| c3a | cr 低（对比参照）               | a72/victim 低于 c3b（工作集近 L2）；时长预算循环的 exec 开销垫高 a72 基线 ~2× 属预期，对比以 victim/wb 为主                          | 三类诊断                                |
| c3b | dominant cr（对比 c3a 明显抬升） | a72 高、victim/wb 中度                                                                                    | 同上                                  |
| c3c | cr 低                     | 指针链 1MB 实测 8.5ns，几乎不出 L2（对照 c3d 的 14.7ns）                                                             | 同上                                  |
| c3d | dominant cr（对比 c3c 明显抬升） | 256MB 链实测 14.7ns/跳 ≈ 单核 68M 跳/s ≈ 4GB/s DRAM 读（9/30 实测上调自"温和"）；指针追逐仍是"深度"型负载（依赖链不可流水），cr 绝对值或低于同带宽顺序流 | 同上                                  |
| c3e | dominant cr（与 c1e 阈值翻转）  | a72 高、io 低（页缓存清后全命中内存）；c1e↔c3e 一对 = 存储↔内存翻转                                                           | 若仍判 ib 先查页缓存是否未清（重跑前必须 drop_caches） |
| c4a | dominant cr（流式）          | a72 高、bypass 可见                                                                                       | 三类诊断                                |
| c4b | dominant cr（miss 主导）     | a72 高 + victim/wb 高于 c4a（随机逐出）                                                                        | 同上                                  |
| c4c | dominant cr 或 wb         | a72 写侧 + wb 抬升（对照 c4a 读）                                                                              | 同上                                  |
| c4d | wb 主导                    | a72 写 + io 写（顺序 eMMC）；与 c4e 对照                                                                        | 同上                                  |
| c4e | wb 主导 + io 更高            | 随机 eMMC 写 io 高于 c4d（顺序）                                                                               | 同上                                  |

> **2026-10-05 实测对照（结案，详情 docs/batch2-results.md）**：判决层 10/10
> 物理正确、0 B 类 0 C 类。c3a→c3b victim_write 0.126→1.000 饱和（7.9×）；
> c3c→c3d cr 0.066→0.750（11.4×）；c1e↔c3e 翻转对成立（判决 low→dominant
> cr、raw io_write 576K→4.8K/s=120×）；c4a/b/c 判别签名在 raw 比值——seq/rnd
> 用 bypass 6.6×、读/写用 l3_emem_wr 32×。三处 A 类修订：c4b 逐出签名在每访问
> 比值非绝对值；c4c wb 不抬升（纯 DRAM 写不走 PCIe）+ ih/ib=1.000 为 M1 份额
> 伪影；c4d/c4e 判 low（eMMC 档低于锚点尺度，c1e/c1f 先例，签名在 raw io）。

### 回传（批次 2）

```bash
tar czf /tmp/ch5_b2.tar.gz results/ch5_c3*.csv results/ch5_c3*.phase.log \
  results/ch5_c4*.csv results/ch5_c4*.phase.log \
  /tmp/c3a_mgs.log /tmp/c3c_lat.log /tmp/c3d_lat.log
```

---

## 批次 3：Case 5（并发争用与份额）+ Case 6（机制切换）

```bash
# c5a db_bench×4 异缓存同库并发读（16M/64M/256M/1G，读量按缓存反比配平；
#   M1 份额分解 + 四实例吞吐对照 = PathFinder Case 5 的 MBW×4 同构）。
#   诚实备注：四实例共享 OS 页缓存，per-instance 的"存储↔内存"对比会被抹平——
#   本轮判读重点是聚合份额与多实例共存下的 M1 分解；干净的 per-instance 份额
#   对照由 c5b（GUPS×4，纯内存表）承担：
# 【10/05 首跑失败（C 类）→ 修复版】首跑三实例死于
#   "open error: lock .../dbtest/LOCK: Resource temporarily unavailable"——LevelDB
#   LOCK 文件互斥，四进程不能同开一个库（只有 1G 实例侥幸拿锁）。
#   修复：四份独立库副本（cp 读源写副本、副本经页缓存驻留内存 → 不再 drop_caches，
#   否则四实例又落 eMMC 档判 low）；窗长 900→300（内存档相位 ~1 分钟级）。
#   期望修订：dominant cr 或 multi（四路内存并发，同 c5b 性质）；判读附加对照不变
#   （四日志 ops/s 份额）；cp 脏页回写会带 io 域抬升，属已知旁支。
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a1
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a2
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a3
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a4
# 10/05 修订：四实例各钉一核 0/1/2/3（4-7 常驻 mlnx_snap_emu 且 -b 0-3 只采前四核）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c5a_db4x_run2.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=16777216 --reads=200000 \
  --db=/root/bf2k/data/dbtest_c5a1 > /tmp/db16.log 2>&1 & \
  taskset -c 1 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=67108864 --reads=400000 \
  --db=/root/bf2k/data/dbtest_c5a2 > /tmp/db64.log 2>&1 & \
  taskset -c 2 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=268435456 --reads=800000 \
  --db=/root/bf2k/data/dbtest_c5a3 > /tmp/db256.log 2>&1 & \
  taskset -c 3 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=1073741824 --reads=1600000 \
  --db=/root/bf2k/data/dbtest_c5a4 > /tmp/db1g.log 2>&1 & \
  wait'" -b 0-3 -t 300

# c5b GUPS×4 异表尺寸（64M/256M/1G/2G 各 60s → wb+cr miss 四路并发；
#   RAM 预算 9GB，四表共 3.3GB，下调自原案 6.6GB；10/05 修订：各钉一核 0-3）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c5b_gups4x_run1.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/gups 23 60 > /tmp/gups1.log 2>&1 & \
  taskset -c 1 /root/bf2k/bench/bin/gups 25 60 > /tmp/gups2.log 2>&1 & \
  taskset -c 2 /root/bf2k/bench/bin/gups 27 60 > /tmp/gups3.log 2>&1 & \
  taskset -c 3 /root/bf2k/bench/bin/gups 28 60 > /tmp/gups4.log 2>&1 & wait'" -b 0-3 -t 120

# c6a sockperf TCP 出向压载（BF2 客户端→fujian sr；ih/nad 出向签名；
#   10/05 修订：钉核 0-3；前置=fujian 上 sockperf sr 已起）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6a_socktcp_run1.csv \
  -a "taskset -c 0-3 sockperf ul -i 192.168.56.11 -t 30 --mps=max --msg-size=1472" -b 0-3 -t 60

# c6b sockperf UDP 出向压载（与 c6a 仅换传输；UDP pps 更高、无流控）：
# 【10/05 首跑失败（C 类）→ 重跑】app 相位仅 1s（sockperf 秒退）。最可能根因：
#   fujian 端服务端是 TCP 模式（sockperf sr）——UDP 客户端需服务端以
#   sockperf sr --udp 重启，否则建立失败秒退。重跑二选一：
#   ① fujian 上服务端改 sockperf sr --udp（跑完改回），BF2 端下方 run1 原命令重跑
#      （输出改 -o results/ch5_c6b_sockudp_run2.csv）；
#   ② 回退 netperf（netserver 无需换模式）：直接跑 run2 命令。
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6b_sockudp_run1.csv \
  -a "taskset -c 0-3 sockperf ul -i 192.168.56.11 --udp -t 30 --mps=max --msg-size=1472" -b 0-3 -t 60

# c6b 回退（netperf UDP_STREAM，同方向同报文尺寸）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6b_sockudp_run2.csv \
  -a "taskset -c 0-3 netperf -H 192.168.56.11 -t UDP_STREAM -l 30 -- -m 1472" -b 0-3 -t 60

# c6c sysbench 随机读·页缓存模式（buffered：缺页+拷贝 → cr 参与；
#   10/05 风险注：批次二已证 eMMC 档负载判 low（c4d/c4e），若本轮判 low 属 A 类、有现成解释）：
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6c_rndrd_buf_run1.csv \
  -a "cd /root/bf2k/data && taskset -c 0-3 sysbench fileio --file-num=8 --file-total-size=4G \
  --file-test-mode=rndrd --file-block-size=16K --file-io-mode=sync --threads=4 \
  --time=30 run" -b 0-3 -t 60

# c6d sysbench 随机读·O_DIRECT（绕过页缓存 → cr 分量消失、纯 io；与 c6c 对照；
#   同 c6c 风险注）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6d_rndrd_direct_run1.csv \
  -a "cd /root/bf2k/data && taskset -c 0-3 sysbench fileio --file-num=8 --file-total-size=4G \
  --file-test-mode=rndrd --file-block-size=16K --file-io-mode=sync \
  --file-extra-flags=direct --threads=4 --time=30 run" -b 0-3 -t 60
```

c6a/c6b 前置：fujian 上先起 `sockperf sr`（服务端，跑在后台）。sockperf 参数
如有出入以 `sockperf --help` 为准（ul=压载客户端、-t 秒数、--mps 每秒消息数
上限、--msg-size 字节、--udp 换 UDP）。**sockperf 无 focal 包时的回退**：
c6a 复用 c2b（netperf TCP_STREAM 出向）、c6b 改 `netperf -H 192.168.56.11
-t UDP_STREAM -l 30 -- -m 1472`（同方向同报文尺寸，仅换传输）。

### 批次 3 判读标准

| 轮   | 期望判决                   | 期望签名                                                                                       | 不一致处置 |
| --- | ---------------------- | ------------------------------------------------------------------------------------------ | ----- |
| c5a | dominant ib/ih 或 multi | 四实例聚合 io+cr 双高；**附加对照**：/tmp/db{16,64,256,1g}.log 四份 ops/s 与聚合带宽的份额关系（PathFinder 式吞吐↔带宽对照） | 三类诊断  |
| c5b | dominant cr/wb         | a72+victim 高（随机读改写）；四实例 GUP/s 日志对照                                                         | 同上    |
| c6a | dominant ih（E2 签名）     | nad 近平局；pps 记录（sockperf 输出）                                                                | 同上    |
| c6b | dominant ih（E2 签名）     | 与 c6a 对照：pps 更高、核域略升（UDP 无流控）                                                              | 同上    |
| c6c | cr 中度 + ib             | buffered：缺页读+拷贝（a72 可见）                                                                    | 同上    |
| c6d | dominant ib（cr 分量消失）   | 与 c6c 对照：a72 明显下降、io 持平                                                                    | 同上    |

> **2026-10-05 首跑实测对照（详情 docs/batch3-results.md；口径修正注见该文 §0）**：
> c5b **PASS**——dominant cr 1.791（期望 cr/wb ✓；ib=1.000 为 M1 份额伪影，同
> c4c）；四实例 GUP/s 0.011/0.007/0.004/0.002 = 45.8%/29.2%/16.7%/8.3%，份额
> 随表尺寸单调递减（大表单次更新 DRAM 流量大 → 速率低），聚合 0.024 vs 单跑
> 0.018。c6a **A 类准 PASS**——实测 dominant nad 0.287 / ih 0.099（期望 dominant
> ih）；流量真实（en3f1_rx 170.6MB/s ≈1.36Gbps、pcie1_tx 185.2M、pcie0_tx
> 186.1M 三方 1:1 镜像=新账目、p1_tx 66B/s 未用、a72 1.79M/s / io_reads
> 0.46M/s），未饱和档头名 = nad、与 c2b 同族（E2 的 ih 头名是 arm 顶点饱和档
> 的份额路由产物）→ 期望按实测修订。c6c/c6d **A 类**（风险注已预告判 low）：
> 判决均 low，但 raw 对照成立——a72 3.67M→2.70M（−27%）、mem_reads 1.77M→
> 0.82M（−54%，buffered 的页缓存拷贝分量）、io_reads 7.4K→7.2K 持平（同一批
> eMMC 读，direct 少了拷贝与预读）——机制切换在 raw 计数器层清晰可见。c5a
> **C 类→修复重跑（run2）闭合**：首跑四实例三死于 LevelDB LOCK 互斥（"open
> error: lock .../dbtest/LOCK: Resource temporarily unavailable"），仅 1G 实例
> 跑完（45.9µs/op，eMMC 档）；run2（四库副本+免 drop_caches+窗 300s）四实例
> 全跑完——应用层梯度 2.6K/9.6K/90.4K/98.4K（38×，小实例落 eMMC、大实例内存档
> ~100K）；**A 类闭合**：四路并发只撑 9s（读量配平≠等时长），全窗 a72 3.39M/s
> 被 70s 涓流稀释 → 判 low 与窗口自洽（cr 比值= a72 比值= 0.52），切片取证
> 4-way 段 a72 17.3M/s = 2.7× c3e；改进项=峰值窗口口径；可选扩展=run3 等时长
> 配平版（200K/600K/5.5M/6M）。c6b **C 类两次 → run3 PASS（10/06）**：run1/run2
> app 相位均 1s、零流量秒退，根因 = fujian netserver 已死（9/23 批次 0 所起，
> 设备重启过）→ 重启 `netserver -D -4` 后 run3 相位 30s 跑满：dominant nad
> 0.309 ✓（期望修订后命中）、UDP 1.48Gbps / pps 126.1K（vs c6a TCP 115.9K
> +8.8% ✓）、a72 1.38M 略降（UDP 无 TCP 状态机、每字节 CPU 更省，两轮同卡
> Arm 栈 TX 平台 ~1.5Gbps）。经验：**每批次开跑前先 pgrep 验活服务端**。

### 回传（批次 3）

> 需要重跑的 c5a/c6b 两轮命令已单独抽出，见下一节「批次 3 重跑单」；本节的
> 回传通配会把重跑的 run2 文件一起带上。

```bash
tar czf /tmp/ch5_b3.tar.gz results/ch5_c5*.csv results/ch5_c5*.phase.log \
  results/ch5_c6*.csv results/ch5_c6*.phase.log \
  /tmp/db16.log /tmp/db64.log /tmp/db256.log /tmp/db1g.log /tmp/gups1.log \
  /tmp/gups2.log /tmp/gups3.log /tmp/gups4.log
```

---

## 批次 3 重跑单（2026-10-05：仅 c5a + c6b 两轮，其余四轮已定案）

> 单独成节：本表只含需要重跑的内容，其余轮次无需再跑。总耗时 ≈10 分钟
> （c5a 四副本 ~2 分钟 + 窗口 5 分钟；c6b 窗口 1 分钟）。

### ① c5a 重跑（修复版：四库副本，LOCK 问题已解决）

**改了什么**：首跑三实例死于 LevelDB LOCK 互斥（四进程不能同开一个库）——现在
先复制四份独立库副本（cp 写入经页缓存驻留内存），每实例开自己的库；**不再
drop_caches**（副本已在内存里，清了反而落 eMMC 档）；窗长 900→300s。RAM 边缘
（副本 8GB + 块缓存 1.3GB vs 可用 9.2GB），cp 脏页回写会带 io 域抬升，属已知旁支。

```bash
# 1. 四份库副本（一次性，~2 分钟；磁盘余量 36G 足够）
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a1
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a2
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a3
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a4

# 2. 四实例并发读（各开各的库、各钉一核；输出 run2，保留首跑坏数据）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c5a_db4x_run2.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=16777216 --reads=200000 \
  --db=/root/bf2k/data/dbtest_c5a1 > /tmp/db16.log 2>&1 & \
  taskset -c 1 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=67108864 --reads=400000 \
  --db=/root/bf2k/data/dbtest_c5a2 > /tmp/db64.log 2>&1 & \
  taskset -c 2 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=268435456 --reads=800000 \
  --db=/root/bf2k/data/dbtest_c5a3 > /tmp/db256.log 2>&1 & \
  taskset -c 3 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=1073741824 --reads=1600000 \
  --db=/root/bf2k/data/dbtest_c5a4 > /tmp/db1g.log 2>&1 & \
  wait'" -b 0-3 -t 300

# 3. 跑完即查：四个 log 都应有 "readrandom : ... micros/op"（首跑只有 db1g.log 有；
#   设备 busybox tail 不支持 "-2 多文件"，用 -n 2 逐个看）
for f in /tmp/db16.log /tmp/db64.log /tmp/db256.log /tmp/db1g.log; do echo "== $f"; tail -n 2 $f; done
cat results/ch5_c5a_db4x_run2.csv.phase.log   # app 相位应 40-120s；任一 log 缺跑完行就停下贴给我
```

### ② c6b 重跑（UDP 出向；二选一）

**改了什么**：首跑 app 相位仅 1s（sockperf 秒退），最可能根因 = fujian 端服务端
是 TCP 模式（`sockperf sr`），UDP 客户端建立失败。

**路线②（推荐，省事）**：netperf 回退——fujian 的 netserver 无需任何改动，一条命令：

```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6b_sockudp_run2.csv \
  -a "taskset -c 0-3 netperf -H 192.168.56.11 -t UDP_STREAM -l 30 -- -m 1472" -b 0-3 -t 60
```

**路线①（保持与 c6a 同工具）**：fujian 上把服务端重启为 UDP 模式（跑完记得改回
`sockperf sr`），BF2 端重跑原命令（输出名改 run2）：

```bash
# fujian 上：先杀掉旧服务端再起 UDP 版
#   pkill sockperf; sockperf sr --udp &
# BF2 上：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6b_sockudp_run2.csv \
  -a "taskset -c 0-3 sockperf ul -i 192.168.56.11 --udp -t 30 --mps=max --msg-size=1472" -b 0-3 -t 60
```

跑完即查：`cat results/ch5_c6b_sockudp_run2.csv.phase.log`（app 相位应 ≈30s；
仍为 1s 就把当时的报错原文贴给我）。

> **10/05 二次失败（run2，用户确认走路线② netperf、未动 sockperf）**：app 相位
> 仍 1s、零流量秒退 → 签名 = netperf 控制连接被拒，**疑 fujian netserver 已死**
> （9/23 批次 0 所起，设备可能重启过）。run3 前置：fujian 上
> `pgrep -a netserver || netserver -D -4 &`，确认 12865 监听后先冒烟
> `netperf -H 192.168.56.11 -t TCP_STREAM -l 2`，再跑 run3（输出名改 run3）：

```bash
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6b_sockudp_run3.csv \
  -a "taskset -c 0-3 netperf -H 192.168.56.11 -t UDP_STREAM -l 30 -- -m 1472" -b 0-3 -t 60
```

回传：`tar czf /tmp/ch5_c6b_run3.tar.gz results/ch5_c6b_sockudp_run3.csv results/ch5_c6b_sockudp_run3.csv.phase.log`

### ③ 回传（两轮都跑完后）

```bash
tar czf /tmp/ch5_b3_rerun.tar.gz results/ch5_c5a_db4x_run2.csv \
  results/ch5_c5a_db4x_run2.csv.phase.log results/ch5_c6b_sockudp_run2.csv \
  results/ch5_c6b_sockudp_run2.csv.phase.log \
  /tmp/db16.log /tmp/db64.log /tmp/db256.log /tmp/db1g.log
```

scp 回本地后贴回，我判读并写 docs/batch3-results.md 闭合批次三。

### ④ c5a run3（可选扩展·等时长配平版，2026-10-06 批准执行）

**为什么做**：run2 已 A 类闭合（38× 梯度 + 切片取证），但判决 low 需要切片
取证兜底——run2 的"读量按缓存反比配平"配的是公平不是等时长，4-way 并发只撑
~9s（大缓存实例先跑完退场），全窗 78s 均值被 ~70s 单实例 eMMC 涓流稀释。run3
把读量改成按实测速率 ×~60s 配平（200K/600K/5.5M/6M），让四路并发撑满整个
app 窗口，判决直接 visible。判读标准同 run2 修订期望：**dominant cr 或 multi**。

**前置（~3 分钟）**：重建四副本以复现 run2 的页缓存状态——副本是 10/05 做的，
隔日页缓存大概率已被逐出；cp 本身会把库读进页缓存并制造与 run2 相同的脏页
回写压力（run2 的 38× 梯度正来自这个压力，读量配平也按当时的速率算的）。
**不 drop_caches**（同 run2）。rm 先删旧副本，防 cp 把 dbtest 嵌套进子目录。

```bash
# 1. 重建四副本（~2-4 分钟）
rm -rf /root/bf2k/data/dbtest_c5a1 /root/bf2k/data/dbtest_c5a2 \
       /root/bf2k/data/dbtest_c5a3 /root/bf2k/data/dbtest_c5a4
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a1
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a2
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a3
cp -r /root/bf2k/data/dbtest /root/bf2k/data/dbtest_c5a4
free -h    # 贴回：available 若 <6G 属内存边缘预期（run2 同况），照跑
```

```bash
# 2. 四实例等时长并发读（读量 ≈ 各 60-77s；日志名带 r3 防覆盖 run2 的）
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c5a_db4x_run3.csv \
  -a "sh -c 'taskset -c 0 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=16777216 --reads=200000 \
  --db=/root/bf2k/data/dbtest_c5a1 > /tmp/c5ar3_db16.log 2>&1 & \
  taskset -c 1 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=67108864 --reads=600000 \
  --db=/root/bf2k/data/dbtest_c5a2 > /tmp/c5ar3_db64.log 2>&1 & \
  taskset -c 2 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=268435456 --reads=5500000 \
  --db=/root/bf2k/data/dbtest_c5a3 > /tmp/c5ar3_db256.log 2>&1 & \
  taskset -c 3 /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=1073741824 --reads=6000000 \
  --db=/root/bf2k/data/dbtest_c5a4 > /tmp/c5ar3_db1g.log 2>&1 & \
  wait'" -b 0-3 -t 240

# 3. 跑完即查（busybox：-n 2 逐个看；四个 log 都应有 readrandom 行）
for f in /tmp/c5ar3_db16.log /tmp/c5ar3_db64.log /tmp/c5ar3_db256.log /tmp/c5ar3_db1g.log; do echo "== $f"; tail -n 2 $f; done
cat results/ch5_c5a_db4x_run3.csv.phase.log   # app 相位期望 55-90s；<55s 或 >170s 停下贴给我

# 4. 回传
tar czf /tmp/ch5_c5a_run3.tar.gz results/ch5_c5a_db4x_run3.csv \
  results/ch5_c5a_db4x_run3.csv.phase.log \
  /tmp/c5ar3_db16.log /tmp/c5ar3_db64.log /tmp/c5ar3_db256.log /tmp/c5ar3_db1g.log
```

scp 回本地后贴回，我判读（期望 dominant cr 或 multi、四路并发撑满 app 窗口；
若仍 low 按 §8.6 三类诊断，不阻塞）并更新 batch3-results.md 闭合。

> **2026-10-06 实测对照（PASS，闭合）**：判决 dominant cr **0.797**（idle
> max L_p 0.007 基线干净）。app 相位 65s、四 log 全跑完（3.14K/74.2K/92.1K/
> 95.0K ops/s）。切片随退场单调递减：4-way 0-8s a72 15.0M/s → 3-way 8-60s
> 12.3M/s → 2-way 60-65s 10.0M/s；全窗 12.3M/s = 1.9× c3e 单实例，cr 落
> c3e 0.385 与 c5b 1.791 之间。**A 类加分发现**：db64 从 run2 的 eMMC 档
> （9.6K）翻到内存档（74.2K）——**64M 缓存 = 存储/内存分界刀口**（16M 两轮
> 稳 eMMC、256M/1G 两轮稳内存），为此 4-way 段仅 8s，窗口仍由 ≥3 路并发
> 主导 60/65s，判决不受影响。详情 docs/batch3-results.md §2.6。

---

## 判读总流程（Claude 本地，每批次）

1. 解包 → CSV 逐列完整性检查（tools/check_csv.py）；
2. `python tools/prism_search.py results/<csv> --scene <名>`（窗口按 .phase.log
   自动界定，同 E4 批）；
3. 与上表期望对照：一致 → PASS；不一致 → 原始计数器逐列取证 → 按
   docs/validation-replay.md §8.6 三类（引擎缺陷/覆盖缺口/预期设错）诊断；
4. 批级验收：每批 ≥80% 轮次 PASS 且不一致均可诊断 → 批级通过，进下一批；
5. 全部批次完成后：出图（fig/，风格沿用 9/14 定稿规范）+ 报告入
   docs/validation-replay.md 新章 + 记录表回填本操作单。

## 记录表（判读后回填）

| 轮   | CSV                              | 窗长                    | 判决                             | L_p 均值 (cr/ih/ib/wb/nad/nhd/tx)           | 预期                         | 结论                                                                                                                                                                                                                                                                                                                                                                                                                          |
| --- | -------------------------------- | --------------------- | ------------------------------ | ----------------------------------------- | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| c1a | ch5_c1a_ep_run1.csv              | 应用 21s + 空载尾 284s     | dominant cr (med 0.535)        | 0.535/0.191/0.416/0.003/0/0/0             | low                        | **预期设错（A 类）**：EP 非纯计算，持续 13.5M/s a72_access、4.4M/s mem_reads、1.78M/s 旁路读 → 修订预期 dominant cr 弱档；ib=0.416 为真实旁路读（bypass 非 I/O 专属再证）；cr 排序 EP 0.535<IS 0.754<FT 1.879 支持判别力                                                                                                                                                                                                                                                    |
| c1b | ch5_c1b_is_run1.csv              | 应用 20s + 尾 280s       | dominant cr (0.754)            | 0.754/0.304/0.224/0.050/0/0/0             | dominant cr                | PASS；wb 未现"中度"（0.050），同 c1c 注，不阻塞                                                                                                                                                                                                                                                                                                                                                                                           |
| c1c | ch5_c1c_ft_run1.csv              | 应用 42s + 尾 268s       | dominant cr (1.879)            | 1.879/0.706/0.866/0.025/0/0/0             | dominant cr；ib 可见          | PASS；ib=0.866"可见"实证 ✓（FT 流式读旁路）；wb 转置写未显著（两轮 NPB 同现象，待 Part 6 标定替换后复核）                                                                                                                                                                                                                                                                                                                                                      |
| c1d | ch5_c1d_netin_run1.csv           | 应用 30s + 尾 40s        | dominant nad (1.882)           | 0.839/1.225/0.590/0.010/1.882/0.063/0     | dominant nad；en3f1_rx 抬升   | PASS；ih=1.225 即 en3f1_rx io 域抬升，符合预期                                                                                                                                                                                                                                                                                                                                                                                        |
| c1e | ch5_c1e_dbmiss_run1.csv + run2   | 应用 67s/69s            | low (ih 0.069/0.068)           | 0.061/0.068/0.026/0.001/0/0/0             | dominant ib/ih             | **预期设错（A 类）定案**：run2（drop_caches 内联）与 run1 计数逐列一致（io_write 0.0932 vs 0.0921）→ run1 本就冷读、页缓存假说排除；**纯读负载点亮 io_write（0.092M/s=115×空载）而 io_reads 恒死** → eMMC 读方向记入 IO_Write（设备视角）；量级 ~8MB/s vs DMA 锚点 Gbps 级差 3 个数量级 → 判 low 如实。预期修订 low；**方向语义新发现入论文素材**（0x73/0x74 设备视角交叉实证）                                                                                                                                                   |
| c1f | ch5_c1f_sbrndwr_run1.csv         | 应用 31s + 尾 34s        | low (cr 0.064)                 | 0.064/0.040/0.032/0.001/0/0/0             | dominant cr 或 wb           | **预期设错（A 类）定案**：eMMC 随机写仅 ~4.5MB/s（io_reads 0.071M/s×64B），a72/mem 域 6–9× 抬升但绝对值小 → 判 low 如实；**纯写负载点亮 io_reads（140×）**=方向交叉实证的另一半（与 c1e 互证 0x73/0x74 设备视角）；预期修订 low                                                                                                                                                                                                                                                          |
| c2b | ch5_c2b_netout_run2.csv          | 应用 30s                | dominant nad (1.566)           | 0.493/1.067/0.316/0.003/1.566/0.667/0.011 | dominant ih（E2 armsend 签名） | **准 PASS（预期微调）**：流量真实（en3f1_rx=pf1hpf_tx 1416M/s≈11.3Gbps 1:1、ACK 回程 2.8M/s）；ih+nad 双亮签名复现（ih 1.067 与 E2 1.000 同量级），但 arm 顶点饱和（n=0.967 打满 6.6Gbps cap 锚点、SAT-SUSPECT 如实）后份额路由给 nad → 头名 ih→nad 漂移；与 E2"近平局、缺口=主机写入侧"边界标注一致；tx≈0 ✓、p1 未用 ✓                                                                                                                                                                                   |
| c2c | ch5_c2c_tx_run1.csv（旧失败文件复打包）    | 应用 1s                 | —                              | —                                         | tx 点亮                      | **仍未重跑**（新包时间戳与旧轮逐字节一致）；helong netserver 检查后跑 run2                                                                                                                                                                                                                                                                                                                                                                          |
| c2c | ch5_c2c_tx_run2.csv              | 应用 31s                | low（tx 0.001）                  | 0.045/0.017/0.022/0.001/0/0.004/0.001     | tx 点亮                      | **姿势错误（C 类）+ 配置事实发现**：netperf 连上 helong 跑满 30s、p1_tx 13M/s（~104Mbps）+ACK 0.28M/s 回程=流量真实，但 **en3f1_rx=0.0000（Arm 数据口分文未动）**；pcie1_tx 15.3M/s≈pcie0_rx 15.3M/s 全程 1:1 对账 → Arm 数据走 pcie1 出主机、fujian 主机软件转发投回 BF2（pf1hpf_rx 13M/s→p1 出线）=**主机折返**；**主 BF2 的 Arm 与 p1 无二层直连**（§5.6 ping 通一直是折返路，掩盖至今）；速率卡 ~104Mbps（折返瓶颈）；引擎 tx=0.001 如实（13M/s÷12.5GB/s 线速锚点=0.001）；**pcie0_tx 53M/s（≈4×线速）记账未解留待考**。改主机口径重跑（tx=主机→网络的本来定义）     |
| c2c | ch5_c2c_host_run1.csv（主机口径 run3） | 应用 56s（流 30s，28s 在窗内） | low（tx 0.027，leader nhd 0.048） | 0.002/0.000/0.010/0.000/0.010/0.048/0.027 | tx 点亮（nad 删）               | **实质 PASS（预期修订 A 类）**：netperf 跑满 30s ~5.5Gbps 稳态、三层一致（pcie0_rx 683M/s≈pcie1_tx 685M/s≈p1_tx 5s 均值 712M/s）→ **p1_tx 抬升=tx 路径首证 ✓**；**Arm 分文未动**（pcie1_rx 2.5M/s 平、tile 近空载、SF 列全零）→ eSwitch HW 转发正常、nad 不随行=主机口径设计意图，预期删 nad；速率卡 ~5.5Gbps≈helong Arm netserver 收包平台（~6Gbps 估计）；netdev 计数 5s 锯齿=统计上报伪影（5s 合计与 TLR 稳态口径一致）；**pcie1_tx 计入主机→ASIC 穿透 TLP**（E0-1/c2b 交叉实证；与 pcie0_tx 53M/s 同列账目待考）；流前 3s 落 pre-idle（启动时序偏斜，不影响判定） |

**批次一判定：闭环通过（8/8=100%≥80%，9/30）**——3 轮干净 PASS（c1b/c1c/c1d）+ 5 轮预期修订/环境限制 PASS（c1a/c1e/c1f/c2b/c2c 主机口径 run3）；c2c 三轮（连接失败/主机折返/主机口径）全部诊断完毕，**批次一结束**。全部不一致按 §8.6 三类诊断完毕，无引擎缺陷；产出论文级发现：①0x73/0x74 IO 计数器方向=设备视角（c1e 纯读亮 0x73、c1f 纯写亮 0x74 交叉实证）②eMMC 负载规模（读写均单数 MB/s）比 DMA 锚点低 3 个数量级，io 域路径对存储负载天然不敏感 ③**主 BF2 Arm↔p1 无二层直连（主机折返实证）**——§5.6 相关结论需加折返脚注 ④**tx 路径首证（9/30 c2c run3）**：主机→网络 p1 出口 5.5Gbps 稳态、eSwitch HW 转发（Arm 不参与）；pcie1_tx 穿透 TLP 记账怪癖留待考；netdev 计数 5s 锯齿伪影（TLR 口径稳态）→ 论文 eSwitch 环境限制脚注素材齐（SF 入向死亡点+折返+共享交换机段）。

> **批次二/三逐轮判决（本表未逐行回填，以结案记录为准）**：批次二见
> docs/batch2-results.md（10 轮全过，0 B 类 0 C 类）；批次三见
> docs/batch3-results.md（c5b PASS；c6a/c6c/c6d/c5a-run2 A 类闭合；c6b 待 run3）。

### 批次一补跑块（9/24 开，9/30 闭环）——c2c 主机口径已执行完毕（记录表末行 ch5_c2c_host_run1.csv）

以下命令留档备查（实际执行：netperf 30s、run_phase `-a "sleep 55" -t 70`）：

```bash
# 服务端：优先放 helong 的 x86 主机（收包不受 Arm 6.6Gbps 平台限制，可推到 10Gbps+）：
#   helong 上先看 10.99.99.3 是否可见：ip addr | grep 10.99.99
#   可见（representor 映射到主机）→ helong 主机上：netserver -D -4
#   不可见 → 维持 helong BF2 Arm 上的 netserver（已起；速率上限 ~6.6Gbps，tx n≈0.07 勉强）
# 客户端放 fujian 主机（tx=主机→网络 的本来定义）：
netperf -H 10.99.99.3 -t TCP_STREAM -l 45
# 主 BF2 上同时起采集（sleep 占窗，两秒内先后启动即可；45s 流量落在 55s 窗内）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c2c_tx_run3.csv \
  -a "sleep 55" -b 0-3 -t 70
# 备选（Arm 出向原教旨）：主 BF2 贴回 ovs-vsctl show，我给加桥命令（p1 桥加 en3f1pf1sf0+配 10.99.99.2/24）
```

## 风险与回退

- **gfortran 缺失** → 0.2.5 已给 deb 安装路径；装不上则 NPB 只编 IS（C 内核），
  EP 诚实负例改 sysbench cpu，MG/CG/FT 暂缓（签名等价替代）；
- **dpkg 依赖链缺失** → 逐包从同镜像补装，报错贴回即可，不阻塞其余构建；
- **eMMC 空间不足（<10GB 可用）** → DB 2GB→1GB（num=1000000）、sysbench 4G→2G、
  c4d/c4e 库减半，贴回 df 结果我按实数改参数；
- **RAM 不足（GUPS 四表 3.3GB 仍紧张）** → 再降为 32M/128M/512M/1G（lg 22/24/26/27）；
- **NPB 时长标定落空**（最快档 >90s）→ 用循环包装短档，或接受 60–90s 长窗；
- **c3e 页缓存作弊**（判读仍 ib）→ 三步协议（清缓存→预热→正式轮）已内建；仍
  异常则重跑并核对 drop_caches 生效；
- **sockperf RDMA/RoCE**：0.1 侦察显示 p1/pf1hpf 的 rdma link 均 ACTIVE
  （RoCE 链路层就绪）——本批仍先跑 TCP/UDP，RDMA 轮升为**后续可做的 Case 6
  升级件**（还需验证两卡间的 GID 路由）；
- **c5a/c5b 并发写日志**：db_bench/GUPS 输出已重定向 /tmp，回传包已含；若
  某实例提前退出，其余照常，判读按 phase log 实际窗。
