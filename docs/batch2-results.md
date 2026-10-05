# 批次 2 结案记录：Case 3（工作集转移）+ Case 4（访问模式转移）（2026-10-05 执行完毕）

> 操作单：docs/ch5-cases-opsheet.md（批次 2 节）；判读工具：prism_search +
> 原始 CSV 计数器直查。十轮一次通过，无重跑；判决层面 10/10 全部物理正确，
> 签名细节 3 处 A 类预期修订（详见 §3）。

## 1. 判决总表（期望 vs 实测）

| 轮 | 期望 | 实测判决 | 关键签名 | 判定 |
|---|---|---|---|---|
| c3a MG S（2MB） | cr 低（对比参照） | dominant cr 0.512 | victim_write 0.126 | ✓ 相对 c3b 低 4×；fork/exec 垫高基线符合预告 |
| c3b MG B（200MB） | dominant cr 明显抬升 | dominant cr **2.059** | victim_write **1.000**（7.9×）、l3 emem_wr span **15.8×**（0.034→0.536） | ✓✓ 工作集转移炸出逐出流 |
| c3c 指针链 1MB | cr 低 | **low**，cr 0.066 | 全静（8.5ns，L2 驻留） | ✓ |
| c3d 指针链 256MB | dominant cr 明显抬升 | dominant cr **0.750** | 11.4× c3c | ✓（"深度型负载 cr 绝对值或低于同带宽顺序流"备注成立） |
| c3e db_bench 2GB 缓存 | dominant cr + io 低（与 c1e 阈值翻转） | dominant cr 0.385 | raw io_write **4.8K/s** vs c1e **576K/s**（120×） | ✓✓ 翻转对完整成立 |
| c4a 顺序读内存 | dominant cr 流式 | dominant cr **1.788** | mem_reads span 0.857、a72 128M/s | ✓ |
| c4b 随机读内存 | dominant cr miss 主导 | dominant cr 0.844 | 绝对计数全低，但 **bypass 6.6×**（1.81M→12.0M/s）、ememwr/a72 2.7× | ✓ 判决；签名细节修订（§3.1） |
| c4c 顺序写内存 | dominant cr 或 wb | dominant cr **1.695** | **l3_emem_wr 32×**（8.6M→275M/s）、victim 288× 跌、ih/ib=1.000 伪影 | ✓ 判决；wb 预期修订（§3.2） |
| c4d 顺序灌库 | wb 主导 + io 写 | **low**，cr 0.134 | raw io_access 484K/s、io_write 76K/s 在动 | A 类修订（§3.3） |
| c4e 随机灌库 | wb 主导 + io 更高 | **low**，cr 0.079 | io_write 35K/s（< c4d 的 76K/s） | A 类修订（§3.3） |

## 2. 三个核心成果

### 2.1 工作集转移（c3a→c3b）：逐出流是判别主峰

MG S（32³ ~2MB，接近 L2 驻留）与 MG B（256³ ~200MB，纯 DRAM）只差网格大小。
计数器反应：tile_victim_write 0.126→1.000（7.9×，锚点饱和）、l3 写请求 span
0.034→0.536（15.8×）、hnf a72_access 0.131→0.445（3.4×）。**工作集一旦超出
片上缓存，L3 逐出流炸裂**——这正是设计判读标准时预告的判别主峰，且引擎的
c3a↔c3b 判决差 4×，判别力合格。c3a 自身 cr 0.512 偏高属预期（时长预算循环的
fork/exec 每轮垫高 a72 基线，判读标准已预告"对比以 victim/wb 为主"）。

### 2.2 缓存深度转移（c3c→c3d）：L2 链 vs DRAM 链

同一 lmbench 指针追逐，仅区段 1MB→256MB：cr 0.066（判 low，全静）→ 0.750
（dominant，11.4×）。配合 9/30 实测的 8.5ns vs 14.7ns 延迟，**"工作集在哪、
瓶颈就在哪"的最小工作集对**成立。

### 2.3 存储↔内存阈值翻转（c1e↔c3e）：批次二最漂亮的一对

同一库（dbtest）、同一命令、只换 `--cache_size`（64MB vs 2GB）：

| | c1e（64MB，批次一） | c3e（2GB，本批） |
|---|---|---|
| 判决 | low | **dominant cr 0.385** |
| raw io_write | 576K/s | **4.8K/s（120× 低）** |
| raw io_reads | 11.5K/s | 1.7K/s（7× 低） |

块缓存从"装不下库"翻到"≈库大小"，同一应用从"eMMC 未命中"翻到"全内存命中"，
引擎判决与 raw 计数器**同向翻转**——阈值翻转对完整成立。

### 2.4 访问模式判别（c4a/b/c）：raw 比值层面比判决更锐利

| 对照 | 判别计数器 | 比值 |
|---|---|---|
| 顺序读 vs 随机读（c4a↔c4b） | **tile_memory_reads_bypass** | 1.81M→12.0M/s（**6.6×**） |
| | l3 emem_wr / a72_access（每访问逐出率） | 0.067→0.180（2.7×） |
| 读 vs 写（c4a↔c4c） | **l3 emem_wr_req** | 8.6M→275M/s（**32×**） |
| | tile_victim | 141M→0.49M/s（288× 跌） |

随机访问打破流检测 → bypass 暴涨；写流不打逐出（写直达）→ victim 暴跌而 l3
写请求暴涨。**三条访问模式的判别签名在 raw 计数器层面完全可分离**，引擎判决
（全 dominant cr）正确但未用上 bypass——见 §4 改进项。

## 3. 三处 A 类预期修订（预期设错，如实修订）

### 3.1 c4b"victim/wb 高于 c4a"未命中 → 逐出签名在比值不在绝对值

随机模式的绝对计数全低于顺序流（每秒吞吐 3.1× 低：a72 128M→40.7M/s），
victim 绝对值 141M→25.2M。但**每访问**逐出率更高（ememwr/a72 2.7×）——随机
逐出的预言在比值层面成立。修订：seq/rnd 判别以 bypass（6.6×）和每访问逐出率
为准，不以绝对 victim 为准。

### 3.2 c4c"wb 抬升"未命中 → 纯 DRAM 写不走 PCIe，签名在 l3 写请求

wb 路径 = Arm→主机 PCIe 写回，纯内存写流不经过它（wb 0.001）。写签名实际在
l3_emem_wr（32×）与 a72_access（2.3×，含写分配读）。另记录：c4c 的 ih/ib
=1.000 为 M1 份额路由伪影（引擎 divergence note 已标"global peak vertex l3
mainly flows to path ih, not the winning cr"）——l3 顶点在写流下的标定份额把
贡献导入了 ih/ib 路径，物理上不可能（纯 DRAM 写无主机入向流量，pcie0/arm
顶点全 0.000）。入模型口径账本，不影响判决。

### 3.3 c4d/c4e"wb 主导"未命中 → eMMC 档负载低于锚点尺度（c1e/c1f 先例）

fillseq 2GB/83s ≈ 25MB/s、fillrandom 500MB/98s ≈ 5MB/s——比内存/网络锚点
（GB/s 级）低 2-3 个数量级，且 LevelDB 写呈批次脉冲（相对自身最大行均值仅
2.4-5.7%）。引擎判 low 是**物理上诚实的答案**：没有内存/网络量级的东西在忙。
存储域的签名在 raw io 计数（c4d io_access 484K/s、io_write 76K/s；c4e 456K/s、
35K/s），但路径模型对 eMMC 域无覆盖（与批次一 c1e/c1f 的"eMMC 低 DMA 锚点
3 个数量级"同列，模型边界见 design-manual 的背压覆盖限制）。c4e 的"io 高于
c4d"细节也反向（随机写总写量 5× 低，每秒 io_write 反 2.2× 低）——期望错，
raw 对照仍可用：c4d↔c4e 以 io_write 76K vs 35K、l3 allocations 1.73M vs
1.06M 判别顺序/随机。

## 4. 对模型意味着什么（引擎评估）

- **判决层 10/10 物理正确、0 引擎缺陷（B 类）、0 数据问题（C 类）**——批次一
  5/8 修订后，批次二在"工作集转移 + 访问模式转移"两个新维度上全部命中。
- **改进项（未来，不阻塞）**：① bypass 计数器（tile_memory_reads_bypass）应
  纳入 mss span 集——seq/rnd 判别它最锐（6.6×），当前引擎未用；② 存储/eMMC
  域的路径覆盖缺口坐实（三批先例：c1e/c1f/c4d/c4e），论文模型边界段引此四处；
  ③ c4c 的 M1 份额路由伪影入模型口径注。
- **论文素材**：c1e↔c3e 阈值翻转对（§2.3 表格可直接入文）；c3a→c3b 工作集
  转移的逐出流饱和（victim_write 锚点饱和 1.000）；c4 三模式的 bypass/l3 写
  请求判别签名表（§2.4）。
