# 第五章验证阶段六案例操作单（2026-09-22）

**目的**：按 docs/pathfinder-ch5-app-research.md 的 6 case 提案执行实验——新应用
候选池上机、每 case 用参数翻转制造 BF2 数据路径瓶颈转移、引擎判读 + 独立语义对照。
**Claude 不连设备**：命令由用户执行、结果回传、Claude 本地判读。

**案例一览**（对应 PathFinder 第五章骨架）：

| Case | 主题 | 轮次 | 转移演示 |
|---|---|---|---|
| 1 | 新应用路径分类 | c1a–c1f（6 轮） | 六应用六种路径签名 |
| 2 | 方向翻转 | c2b、c2c（c2a 复用 c1d） | nad↔出向↔tx（p1） |
| 3 | 工作集转移 | c3a–c3e（5 轮） | 缓存驻留↔DRAM↔存储 |
| 4 | 访问模式转移 | c4a–c4e（5 轮） | seq↔rnd、rd↔wr、顺序灌↔随机灌 |
| 5 | 并发争用与份额 | c5a、c5b（2 轮） | 4 实例异参数同跑 + 份额分解 |
| 6 | 机制切换 | c6a–c6d（4 轮） | TCP↔UDP、页缓存↔O_DIRECT |

执行按四批次推进，每批回传判读后再进下一批。

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
  | 包 | 文件名 | 备注 |
  |---|---|---|
  | netperf（arm64，BF2+helong 各一份） | `netperf_2.7.0-0.1_arm64.deb` | 报错则换同目录 `netperf_2.6.0-2.1_arm64.deb`（bionic 版必兼容） |
  | sysbench（arm64） | `sysbench_1.0.18+ds-1_arm64.deb` | 弃 1.0.20+ds-9（noble）；补链三包见下 |
  | libluajit-5.1-2（arm64） | `libluajit-5.1-2_2.1.0~beta3+dfsg-6_arm64.deb` | universe 池；6ubuntu0.1=jammy 勿选 |
  | libluajit-5.1-common（all） | `libluajit-5.1-common_2.1.0~beta3+dfsg-6_all.deb` | 与 -2 精确同版（= 咬合） |
  | libpq5（arm64） | `libpq5_12.22-0ubuntu0.20.04.4_arm64.deb` | sysbench pg 驱动；12.16 不存在 |
  | gfortran-9（arm64） | `gfortran-9_9.4.0-1ubuntu1~20.04.3_arm64.deb` | 同目录再拿 libgfortran-9-dev 同版本 |
  | libgfortran-9-dev（arm64） | `libgfortran-9-dev_9.4.0-1ubuntu1~20.04.3_arm64.deb` | 缺 libgfortran5 时报错贴回（libquadmath0 对 arm64 不存在，勿找） |
  | sockperf（arm64） | `sockperf_3.6-2build1_arm64.deb` | 试装；报 glibc 错→跳过（c6 回退已备） |

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
- **0.2.7 GUPS（自写 ~60 行 C，设备上直接写入，不走 git）**
  ```bash
  mkdir -p /root/bf2k/bench/src
  cat > /root/bf2k/bench/src/gups.c <<'EOF'
  /* gups.c - HPCC RandomAccess 单进程内核（自写，表尺寸/时长可参数化）。
   * 对 2^lg 个 64 位字的表做随机地址流的读-改-写（T[idx] ^= ran），
   * 指标 GUPS = 每秒 10^9 次随机更新。地址流 = 64 位 LFSR（全周期）。 */
  #include <stdint.h>
  #include <stdio.h>
  #include <stdlib.h>
  #include <time.h>
  static inline uint64_t lfsr(uint64_t x){
      return (x << 1) | (((x >> 63) ^ (x >> 3) ^ (x >> 2) ^ (x >> 0)) & 1);
  }
  int main(int argc, char **argv){
      int lg = (argc > 1) ? atoi(argv[1]) : 20;      /* 表 = 2^lg 字 */
      double secs = (argc > 2) ? atof(argv[2]) : 30.0; /* 运行时长 */
      uint64_t n = 1ULL << lg, *T = calloc(n, 8);
      if (!T) { fprintf(stderr, "gups: alloc fail\n"); return 1; }
      uint64_t ran = 0x123456789abcdef0ULL, upd = 0;
      double end = (double)clock() / CLOCKS_PER_SEC + secs;
      while (((double)clock() / CLOCKS_PER_SEC) < end) {
          ran = lfsr(ran);
          T[ran & (n - 1)] ^= ran;
          upd++;
      }
      printf("GUPS lg=%d updates=%llu rate=%.3f GUP/s\n",
             lg, (unsigned long long)upd, upd / secs / 1e9);
      return 0;
  }
  EOF
  gcc -O2 -o /root/bf2k/bench/bin/gups /root/bf2k/bench/src/gups.c
  /root/bf2k/bench/bin/gups 20 3    # 冒烟：3 秒跑完，打印 GUP/s
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

### 0.4 NPB class 时长标定（BF2，决定每个内核用哪档 class）

目标：单轮 20–40s（采样行数充足）。逐条跑 `time`，按实际时长选档（预计量级
供参考，A72 上可能偏慢）：

```bash
time /root/bf2k/bench/bin/ep.A.x
time /root/bf2k/bench/bin/is.A.x
time /root/bf2k/bench/bin/ft.A.x
time /root/bf2k/bench/bin/mg.S.x
time /root/bf2k/bench/bin/mg.A.x
```

选档规则：20–40s 之间 → 该档；<10s → 升档（如 is.A→is.B）；>90s → 降档。
EP 是纯计算负例，档位只影响时长不影响签名。**把五条 time 输出贴回**，
确认选档后再进批次 1。

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
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c2c_tx_run1.csv \
  -a "netperf -H 10.99.99.3 -t TCP_STREAM -l 30" -b 0-3 -t 60
```

### 批次 1 判读标准（Claude 本地）

| 轮 | 期望判决 | 期望签名 | 不一致处置 |
|---|---|---|---|
| c1a EP | low | 七路径全 ≈0（openssl 同族负例） | 按 docs/validation-replay.md §8.6 三类诊断 |
| c1b IS | dominant cr | a72/hnf 抬升；wb 中度（排序阶段） | 同上 |
| c1c FT | dominant cr | a72 流式 + wb（转置写）；ib 可见 | 同上 |
| c1d=2a | dominant nad | en3f1_rx 抬升；其余路径残值（E2E-B/D2 同族） | 同上 |
| c1e | dominant ib/ih（io 域） | io_reads 抬升、a72 轻微（miss 读不重算）；与 c3e 对照用 | 同上 |
| c1f | dominant cr 或 wb | a72 写+io 读（eMMC DMA 读缓存区）+eMMC 随机写 | 同上 |
| c2b | dominant ih（E2 armsend 签名） | ih 1.0 量级+nad 近平局；tx≈0（不经 p1） | 同上 |
| c2c | tx 点亮 | p1_tx 抬升（tx 路径首证）；nad 随行（Arm 处理）；与 c2b 对照 | 同上 |

### 回传（批次 1）

```bash
tar czf /tmp/ch5_b1.tar.gz results/ch5_c1*.csv results/ch5_c1*.phase.log \
  results/ch5_c2*.csv results/ch5_c2*.phase.log
# scp 回本地（老流程）
```

---

## 批次 2：Case 3（工作集转移）+ Case 4（访问模式转移）

```bash
# c3a MG 小工作集（32³ 网格 ~2MB，接近 L2 驻留；单轮太短故循环 10 次）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3a_mgs_run1.csv \
  -a "for i in 1 2 3 4 5 6 7 8 9 10; do /root/bf2k/bench/bin/mg.S.x; done" -b 0-3 -t 120

# c3b MG 大工作集（256³ ~200MB，纯 DRAM；档位按 0.4 标定，A 或 B）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3b_mga_run1.csv \
  -a "/root/bf2k/bench/bin/mg.<档>.x" -b 0-3 -t 300

# c3c lmbench 指针链 1MB（L2 量级，延迟低；循环 15 次撑窗）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3c_lat1m_run1.csv \
  -a "for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do /root/bf2k/bench/bin/lat_mem_rd 1 64; done" \
  -b 0-3 -t 120

# c3d lmbench 指针链 256MB（纯 DRAM+TLB 失效，延迟 ~150ns+；循环 2 次）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c3d_lat256m_run1.csv \
  -a "for i in 1 2; do /root/bf2k/bench/bin/lat_mem_rd 256 64; done" -b 0-3 -t 120

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
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c4a_memseq_run1.csv \
  -a "sysbench memory --memory-block-size=1G --memory-scope=global --memory-total-size=64G \
  --memory-oper=read --memory-access-mode=seq --threads=4 --time=30 run" -b 0-3 -t 60

# c4b 随机读内存（同一 1GB 缓冲内随机 → cr miss 主导；与 c4a 仅换 access-mode）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c4b_memrnd_run1.csv \
  -a "sysbench memory --memory-block-size=1G --memory-scope=global --memory-total-size=64G \
  --memory-oper=read --memory-access-mode=rnd --threads=4 --time=30 run" -b 0-3 -t 60

# c4c 顺序写内存（→ wb 主导；与 c4a 仅换 oper）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c4c_memwr_run1.csv \
  -a "sysbench memory --memory-block-size=1G --memory-scope=global --memory-total-size=64G \
  --memory-oper=write --memory-access-mode=seq --threads=4 --time=30 run" -b 0-3 -t 60

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

| 轮 | 期望判决 | 期望签名 | 不一致处置 |
|---|---|---|---|
| c3a | cr 低（对比参照） | a72/victim 低于 c3b（工作集近 L2） | 三类诊断 |
| c3b | dominant cr（对比 c3a 明显抬升） | a72 高、victim/wb 中度 | 同上 |
| c3c | cr 低 | 指针链 1MB 延迟 ~10ns 量级（对照 c3d 的 ~150ns） | 同上 |
| c3d | dominant cr（对比 c3c 抬升） | 256MB 链 = 纯 DRAM+TLB；**判读口径**：cr 指归一化压力，指针追逐是"深度"非"带宽"，cr 抬升幅度预计温和 | 同上 |
| c3e | dominant cr（与 c1e 阈值翻转） | a72 高、io 低（页缓存清后全命中内存）；c1e↔c3e 一对 = 存储↔内存翻转 | 若仍判 ib 先查页缓存是否未清（重跑前必须 drop_caches） |
| c4a | dominant cr（流式） | a72 高、bypass 可见 | 三类诊断 |
| c4b | dominant cr（miss 主导） | a72 高 + victim/wb 高于 c4a（随机逐出） | 同上 |
| c4c | dominant cr 或 wb | a72 写侧 + wb 抬升（对照 c4a 读） | 同上 |
| c4d | wb 主导 | a72 写 + io 写（顺序 eMMC）；与 c4e 对照 | 同上 |
| c4e | wb 主导 + io 更高 | 随机 eMMC 写 io 高于 c4d（顺序） | 同上 |

### 回传（批次 2）

```bash
tar czf /tmp/ch5_b2.tar.gz results/ch5_c3*.csv results/ch5_c3*.phase.log \
  results/ch5_c4*.csv results/ch5_c4*.phase.log
```

---

## 批次 3：Case 5（并发争用与份额）+ Case 6（机制切换）

```bash
# c5a db_bench×4 异缓存同库并发读（16M/64M/256M/1G，读量按缓存反比配平；
#   M1 份额分解 + 四实例吞吐对照 = PathFinder Case 5 的 MBW×4 同构）。
#   诚实备注：四实例共享 OS 页缓存，per-instance 的"存储↔内存"对比会被抹平——
#   本轮判读重点是聚合份额与多实例共存下的 M1 分解；干净的 per-instance 份额
#   对照由 c5b（GUPS×4，纯内存表）承担：
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c5a_db4x_run1.csv \
  -a "sh -c '/root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=16777216 --reads=200000 \
  --db=/root/bf2k/data/dbtest > /tmp/db16.log 2>&1 & \
  /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=67108864 --reads=400000 \
  --db=/root/bf2k/data/dbtest > /tmp/db64.log 2>&1 & \
  /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=268435456 --reads=800000 \
  --db=/root/bf2k/data/dbtest > /tmp/db256.log 2>&1 & \
  /root/bf2k/bench/bin/db_bench --benchmarks=readrandom --use_existing_db=1 \
  --num=2000000 --value_size=1000 --cache_size=1073741824 --reads=1600000 \
  --db=/root/bf2k/data/dbtest > /tmp/db1g.log 2>&1 & \
  wait'" -b 0-3 -t 900

# c5b GUPS×4 异表尺寸（64M/256M/1G/2G 各 60s → wb+cr miss 四路并发；
#   RAM 预算 9GB，四表共 3.3GB，下调自原案 6.6GB）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c5b_gups4x_run1.csv \
  -a "sh -c '/root/bf2k/bench/bin/gups 23 60 > /tmp/gups1.log 2>&1 & \
  /root/bf2k/bench/bin/gups 25 60 > /tmp/gups2.log 2>&1 & \
  /root/bf2k/bench/bin/gups 27 60 > /tmp/gups3.log 2>&1 & \
  /root/bf2k/bench/bin/gups 28 60 > /tmp/gups4.log 2>&1 & wait'" -b 0-3 -t 120

# c6a sockperf TCP 出向压载（BF2 客户端→fujian sr；ih/nad 出向签名）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6a_socktcp_run1.csv \
  -a "sockperf ul -i 192.168.56.11 -t 30 --mps=max --msg-size=1472" -b 0-3 -t 60

# c6b sockperf UDP 出向压载（与 c6a 仅换传输；UDP pps 更高、无流控）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6b_sockudp_run1.csv \
  -a "sockperf ul -i 192.168.56.11 --udp -t 30 --mps=max --msg-size=1472" -b 0-3 -t 60

# c6c sysbench 随机读·页缓存模式（buffered：缺页+拷贝 → cr 参与）：
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6c_rndrd_buf_run1.csv \
  -a "cd /root/bf2k/data && sysbench fileio --file-num=8 --file-total-size=4G \
  --file-test-mode=rndrd --file-block-size=16K --file-io-mode=sync --threads=4 \
  --time=30 run" -b 0-3 -t 60

# c6d sysbench 随机读·O_DIRECT（绕过页缓存 → cr 分量消失、纯 io；与 c6c 对照）：
sudo ./run_phase.sh -c configs/e1_esw.conf -o results/ch5_c6d_rndrd_direct_run1.csv \
  -a "cd /root/bf2k/data && sysbench fileio --file-num=8 --file-total-size=4G \
  --file-test-mode=rndrd --file-block-size=16K --file-io-mode=sync \
  --file-extra-flags=direct --threads=4 --time=30 run" -b 0-3 -t 60
```

c6a/c6b 前置：fujian 上先起 `sockperf sr`（服务端，跑在后台）。sockperf 参数
如有出入以 `sockperf --help` 为准（ul=压载客户端、-t 秒数、--mps 每秒消息数
上限、--msg-size 字节、--udp 换 UDP）。**sockperf 无 focal 包时的回退**：
c6a 复用 c2b（netperf TCP_STREAM 出向）、c6b 改 `netperf -H 192.168.56.11
-t UDP_STREAM -l 30 -- -m 1472`（同方向同报文尺寸，仅换传输）。

### 批次 3 判读标准

| 轮 | 期望判决 | 期望签名 | 不一致处置 |
|---|---|---|---|
| c5a | dominant ib/ih 或 multi | 四实例聚合 io+cr 双高；**附加对照**：/tmp/db{16,64,256,1g}.log 四份 ops/s 与聚合带宽的份额关系（PathFinder 式吞吐↔带宽对照） | 三类诊断 |
| c5b | dominant cr/wb | a72+victim 高（随机读改写）；四实例 GUP/s 日志对照 | 同上 |
| c6a | dominant ih（E2 签名） | nad 近平局；pps 记录（sockperf 输出） | 同上 |
| c6b | dominant ih（E2 签名） | 与 c6a 对照：pps 更高、核域略升（UDP 无流控） | 同上 |
| c6c | cr 中度 + ib | buffered：缺页读+拷贝（a72 可见） | 同上 |
| c6d | dominant ib（cr 分量消失） | 与 c6c 对照：a72 明显下降、io 持平 | 同上 |

### 回传（批次 3）

```bash
tar czf /tmp/ch5_b3.tar.gz results/ch5_c5*.csv results/ch5_c5*.phase.log \
  results/ch5_c6*.csv results/ch5_c6*.phase.log \
  /tmp/db16.log /tmp/db64.log /tmp/db256.log /tmp/db1g.log /tmp/gups1.log \
  /tmp/gups2.log /tmp/gups3.log /tmp/gups4.log
```

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

| 轮 | CSV | 窗长 | 判决 | L_p 均值 (cr/ih/ib/wb/nad/nhd/tx) | 预期 | 结论 |
|---|---|---|---|---|---|---|
| c1a | | | | | low | |
| … | | | | | | |

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
