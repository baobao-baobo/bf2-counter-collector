# git 系统学习文档

> **定位**：简历可写、面试可答。Git 是版本控制的事实标准，软件岗面试几乎必问。本文与 `fio-guide.md`、`iperf3-guide.md` 同一套体系：核心概念 → 速查表 → 实战演练 → 场景模板 → 陷阱清单 → 简历与面试自测。
> **环境**：你日常用 Windows 的 Git Bash + Linux 服务器，两边命令完全一致。你的 bf2-collector 仓库就是现成的练习场（§10）。
> **先记住一句话**：git 的难点不在命令多，在于"三个区域 + 对象模型 + 分支就是指针"这套心智模型。模型建立起来，命令都是查得到的。

---

## 0. 学习路径建议（按顺序）

1. 装好 git，配好 §2 的全局配置
2. **精读 §3 核心概念**——尤其是 3.1（三个区域）和 3.3（分支指针），这两节通了 git 就通了一半
3. 在 bf2-collector 仓库上做 §5 的实战演练（**先在临时分支上玩撤销**，别在 main 上练 reset --hard）
4. 过一遍 §8 陷阱清单
5. 用 §9 面试题自测，答不上来回头补 §3

---

## 1. git 是什么

- **分布式版本控制系统（DVCS）**，Linus Torvalds 2005 年开发（最初为管理 Linux 内核源码）。现在是版本控制事实标准，GitHub / GitLab 整个生态都构建在它之上。
- **分布式 vs 集中式**（对比 SVN）：每个 clone 都是一份**完整仓库**——包含全部历史。断网也能提交、看历史、回滚；服务器只是"交换中心"而不是"依赖中心"。
- **快照模型**：git 每次 commit 存的是**整个文件树的快照**，内容没变的文件只存一次引用，而不是 SVN 那种逐文件存差异。
- 一句面试金句：*"git 的底层是一个内容寻址的对象数据库，上层是一套操作它的命令。"*

---

## 2. 安装与配置

```bash
# Ubuntu / Debian（你的 BF2、服务器）
sudo apt install -y git

# Windows：官方安装包（自带 Git Bash，你平时跑命令的就是它）
# https://git-scm.com/download/win

git --version   # 建议 2.30+，switch/restore 命令需要 2.23+
```

全局配置（一次配置，终身使用）：

```bash
git config --global user.name "baobao-baobo"       # 你的 GitHub 账号
git config --global user.email "1960796607@qq.com"
git config --global init.defaultBranch main        # 新仓库默认分支名
git config --global core.autocrlf true             # Windows 上换行符处理（§8 陷阱1）
git config --global pull.rebase true               # pull 时用 rebase 保持历史线性
git config --global alias.lg "log --oneline --graph --all --decorate"   # 常用别名
```

配置优先级：`system`（本机所有用户）< `global`（当前用户）< `local`（当前仓库，存在 `.git/config` 里）。查看生效来源：`git config --list --show-origin`。

---

## 3. 核心概念（★ 面试重点，逐条吃透）

### 3.1 三个区域（整个 git 的心智模型核心）

```
工作区（Working Directory）──add──▶ 暂存区（Staging/Index）──commit──▶ 仓库（Repository/.git）
       你正在编辑的文件              下一次提交会包含的内容              全部提交历史
```

- **工作区**：磁盘上你正在看的文件。
- **暂存区（index）**：一个清单，记录"下一次 commit 要包含哪些文件的哪个版本"。
- **仓库**：`.git` 目录，存所有提交、分支、标签。
- **为什么要有暂存区**：允许你"挑着提交"。用 `git add -p`（交互式部分暂存）可以把一次大改动拆成多个语义清晰的提交——这是专业开发者与初学者的明显区别。
- 查看三个区域之间的差异：
  - `git diff`：工作区 vs 暂存区
  - `git diff --staged`：暂存区 vs 最新提交（HEAD）
  - `git diff HEAD`：工作区 vs 最新提交（两者之和）

### 3.2 对象模型（git 底层怎么存数据）

git 把一切存成四类**对象**，每个对象按内容计算哈希命名（SHA-1，40 位十六进制；新版仓库可选 SHA-256）：

| 对象 | 存什么 |
|---|---|
| **blob** | 文件内容（不含文件名！） |
| **tree** | 一个目录：记录"文件名 → blob/tree"的映射 |
| **commit** | 一棵 tree（完整快照）+ 父提交指针 + 作者/提交者 + 时间 + 提交信息 |
| **tag** | 附注标签：指向 commit + 说明 |

- 相同内容只存一份（内容寻址自动去重）——所以 git 仓库比想象中省空间。
- commit 通过 parent 指针串成**有向无环图（DAG）**，所谓"历史"就是沿着 parent 指针往回走。
- 动手体验（在你自己的仓库里跑）：`git cat-file -p HEAD` 拆开看 commit → tree → blob 的层级。

> 面试题"git 存的是差异还是快照"的标准答案：*"快照。每个 commit 保存整个文件树的引用，未改动的文件复用已有 blob，所以既完整又省空间。"*

### 3.3 分支与 HEAD（git 最反直觉、也最优雅的设计）

- **分支只是一个指向 commit 的指针**——`.git/refs/heads/main` 文件里就一个 40 字节的哈希。创建分支 = 写一个指针文件，**零成本**（这就是 git 分支比 SVN 快的原因）。
- **HEAD**：指针的指针，标记"我现在站在哪"。通常指向某个分支（`HEAD -> main`）。
- **detached HEAD（游离状态）**：当 `git checkout <某个具体提交哈希>` 时，HEAD 直接指向提交而非分支。此时做的提交**不被任何分支引用**，一切走分支就"找不着了"（但 reflog 能找回，§6.2）。
- 常用操作：
  ```bash
  git switch -c feature-x    # 新建分支并切过去（2.23+ 写法）
  git switch feature-x       # 切换已有分支
  git switch -               # 切回上一个分支
  git branch                 # 列出本地分支
  git branch -d feature-x    # 删除（未合并的分支会拒绝，-D 强删）
  ```

### 3.4 合并（merge）

把另一个分支的改动并入当前分支。两种情形：

1. **Fast-forward（快进）**：目标分支在你之后没有任何新提交 → git 只是把指针**直接前移**，不产生 merge commit。历史是一条直线。
2. **3-way merge（三方合并）**：两条线各有新提交 → git 找共同祖先，把"你改的 + 他改的"合成一个新 merge commit（有两个 parent）。

- `git merge --no-ff feature`：强制生成 merge commit。好处是历史里保留"这里曾经合并过一个 feature 分支"的结构信息（团队常用）。
- **冲突**：同一处代码两边都改了 → git 在文件里标 `<<<<<<<` / `=======` / `>>>>>>>`，手动改成最终结果 → `git add` → `git commit` 完成合并。想放弃：`git merge --abort`。

### 3.5 变基（rebase）

- **原理**：找到当前分支与目标分支的共同祖先 → 把当前分支的提交**逐个摘下来"重放"**到目标分支最新提交之后。
- **结果**：历史变成一条干净的直线；但代价是**这些提交的哈希全部改变 = 历史被重写**。
- **merge vs rebase 的选择原则（★面试必考）**：
  - **本地未 push 的私有分支**：用 rebase 整理（历史干净）；
  - **已 push 的 / 共享的分支**：用 merge 或 revert（§3.6/§5.2），**绝不动共享历史**。
- 冲突时：解决 → `git add` → `git rebase --continue`；放弃：`git rebase --abort`。
- `git pull --rebase`：拉取时把本地新提交重放到远端最新提交之后（配了 `pull.rebase true` 后 `git pull` 默认如此）。

### 3.6 远程与协作模型

- **origin**：远端仓库的默认名字（只是个别名，可以叫任何名字）。`git remote -v` 查看。
- **远程跟踪分支**：`origin/main` 是"上次 fetch 时远端 main 的样子"，是一个本地只读快照。
- **fetch vs pull（★必考）**：
  - `git fetch`：只下载远端新对象并更新 `origin/main`，**不碰你的工作区和分支**；
  - `git pull` = fetch + merge（或 rebase），会直接改动你的当前分支。
- **push**：把本地提交上传到远端。`git push -u origin feature` 中的 `-u` 建立"本地 feature 跟踪 origin/feature"关系，之后裸 `git push` 即可。
- **克隆发生了什么**：`git clone` = 下载全部历史 + 建本地 `main` + 建远程跟踪分支 `origin/main`。

---

## 4. 常用命令速查表

### 仓库与配置

| 命令 | 说明 |
|---|---|
| `git init` / `git clone <url>` | 新建仓库 / 克隆（含全部历史） |
| `git config --global user.name "..."` | 提交者身份 |
| `git config --list --show-origin` | 查看配置及来源 |
| `git status` / `git status -s` | 工作区状态 / 短格式 |
| `git remote -v` | 查看远端 |

### 提交与历史

| 命令 | 说明 |
|---|---|
| `git add <file>` / `git add .` | 暂存指定文件 / 全部 |
| `git add -p` | 交互式部分暂存（拆提交神器） |
| `git commit -m "msg"` | 提交暂存区 |
| `git commit -am "msg"` | 暂存并提交**已跟踪**文件（新文件不包含！） |
| `git commit --amend` | 修改最近一次提交（信息/内容） |
| `git diff` / `--staged` / `HEAD` | 三层差异（§3.1） |
| `git log --oneline --graph --all --decorate` | 历史拓扑图（别名 `git lg`） |
| `git show <sha>` | 查看某次提交的完整内容 |
| `git blame -L 10,20 <file>` | 逐行查作者 |

### 分支与合并

| 命令 | 说明 |
|---|---|
| `git branch` / `-a` / `-vv` | 本地 / 含远端 / 含跟踪关系 |
| `git switch -c <name>` | 新建并切换分支 |
| `git merge <branch>` / `--no-ff` | 合并 / 强制产生 merge commit |
| `git rebase <branch>` / `-i` | 变基 / 交互式变基（§6.1） |
| `git cherry-pick <sha>` | 把单个提交复制到当前分支（§6.3） |
| `git tag v1.0` / `git tag -a v1.0 -m "..."` | 轻量标签 / 附注标签（发布用附注） |

### 撤销与恢复（★ 高频考点，配合 §5.2 对照表）

| 命令 | 说明 |
|---|---|
| `git restore <file>` | 丢弃工作区修改（回到暂存区版本） |
| `git restore --staged <file>` | 取消暂存（回到 HEAD 版本，改动保留在工作区） |
| `git reset --soft/--mixed/--hard <sha>` | 移动分支指针，三种力度（§5.2） |
| `git revert <sha>` | 生成"反向提交"安全撤销 |
| `git stash push -m "..."` / `pop` / `list` | 临时保存 / 恢复 / 查看手头改动 |
| `git reflog` | 本地 HEAD 操作历史（找回"丢失"的一切） |
| `git clean -n` / `-fd` | 预览 / 删除未跟踪文件（先 -n！） |

### 远程协作

| 命令 | 说明 |
|---|---|
| `git fetch [--prune]` | 拉取远端更新，不合并（安全） |
| `git pull [--rebase]` | fetch + merge（或 rebase） |
| `git push` / `-u origin <branch>` | 推送 / 首次推送并建立跟踪 |
| `git push --force-with-lease` | 安全强推（远端有新提交则拒绝） |
| `git remote add origin <url>` | 关联远端仓库 |

---

## 5. 实战演练与撤销对照表（★ 必练）

### 5.1 完整协作流程演练（GitHub Flow，业界主流）

在 bf2-collector 仓库里，从"接一个需求"到"合入 main"的完整流程：

```bash
# ① 同步主干，确保从最新代码出发
git switch main
git fetch
git pull                          # 或 git rebase origin/main

# ② 开功能分支（分支名用 kebab-case，如 feat-collect-x）
git switch -c feat-add-x-counter

# ③ 开发：改代码、编译、测试（你的 bf2 实验流程）
vim collect_x.c
make

# ④ 分多次小提交，每次一个语义（而不是攒一大坨）
git add -p                        # 挑着暂存
git commit -m "feat: add X counter collection"
git commit -m "test: add X counter bench"

# ⑤ 推送到远端并建跟踪（此时远端已有你的分支）
git push -u origin feat-add-x-counter

# ⑥ 在 GitHub 上开 Pull Request，review 通过后合并
# （命令行：gh pr create）

# ⑦ 合入后清理：切回 main，拉最新，删本地分支
git switch main
git pull
git branch -d feat-add-x-counter
```

### 5.2 撤销对照表（日常救急 + 面试高频）

| 我想…… | 命令 | 风险 |
|---|---|---|
| 丢弃某文件未提交的修改 | `git restore <file>` | 改动丢失，不可恢复 |
| 取消暂存（改错了 add） | `git restore --staged <file>` | 无（改动回到工作区） |
| 修改最近一次提交（还没 push） | `git commit --amend` | 无（本地历史重写） |
| 撤销最近一次提交，改动保留在暂存区 | `git reset --soft HEAD~1` | 历史被重写（仅本地） |
| 撤销最近一次提交，改动回到工作区 | `git reset --mixed HEAD~1`（即 `git reset HEAD~1`） | 同上 |
| 彻底丢弃最近一次提交和所有改动 | `git reset --hard HEAD~1` | **改动永久丢失** |
| 撤销**已经 push** 的提交（安全方式） | `git revert <sha>` | 无（生成反向提交，历史不动） |
| 找回"丢失"的提交/分支 | `git reflog` → `git cherry-pick <sha>` 或 `git branch rec <sha>` | 无 |
| 临时收起手头改动去干别的 | `git stash push -m "..."` → `git stash pop` | pop 冲突时别慌，list 里还在 |

**reset 三种模式的记忆法（★必考）**：`--soft` 只动指针（改动留在暂存区）；`--mixed`（默认）动指针 + 暂存区（改动回到工作区）；`--hard` 动指针 + 暂存区 + 工作区（全部丢弃）。三者都改"分支指向哪"，区别只在**改动去了哪里**。

---

## 6. 典型场景模板

### 6.1 整理提交历史（交互式 rebase，把 3 个烂提交合成 1 个）

```bash
git switch -c feat-x
# 开发过程中做了 3 个提交：feat 主体 / wip / fix typo
git rebase -i main
```

编辑器里会列出这 3 个提交，把后两个的 `pick` 改成 `squash`：

```
pick a1b2c3d feat: add feature X
squash 4e5f6g7 wip
squash 7h8i9j0 fix typo
```

保存后三个提交合并为一个 `feat: add feature X`，历史干净如一人所为。`rebase -i` 的全部操作：`pick`（保留）/ `reword`（改信息）/ `edit`（停下修改）/ `squash`（合并保留信息）/ `fixup`（合并丢弃信息）/ `drop`（丢弃）。

### 6.2 找回"丢失"的提交（reflog 是后悔药）

```bash
# 手滑 reset --hard 了？先看操作历史
git reflog
# abc1234 HEAD@{0}: reset: moving to HEAD~3
# def5678 HEAD@{1}: commit: 那个重要提交      ← 它还在
git branch recovered def5678      # 挂回分支，或：
git cherry-pick def5678           # 或直接挑回当前分支
```

原理：git 的对象数据库里，**只要没被 gc 清理，一切提交都还在**；reflog 记录了本地所有 HEAD 移动（默认保留 90 天）。

### 6.3 跨分支搬运提交（cherry-pick）

```bash
# hotfix 分支上修了一个 bug，release 分支也要
git switch release
git cherry-pick <hotfix 提交的哈希>
# release 上生成一个新提交（哈希不同、内容相同）
```

### 6.4 冲突解决的完整流程

```bash
git switch main && git merge feature
# Auto-merging main.c
# CONFLICT (content): Merge conflict in main.c

git status                         # 看哪些文件 both modified
# 编辑 main.c：删掉 <<<<<<< ======= >>>>>>> 标记，保留最终代码
git add main.c
git commit                         # 完成合并
# （rebase 冲突则用 git rebase --continue）
```

### 6.5 提交了不该提交的文件

```bash
# 情况1：文件应该忽略（build 产物）
echo "build/" >> .gitignore
git rm -r --cached build/          # 从跟踪移除，但保留本地文件
git commit -m "chore: stop tracking build/"

# 情况2：误提交了密钥——视为已泄露，立即轮换密钥，然后清除历史：
pip install git-filter-repo
git filter-repo --path secrets/ --invert-paths --force
git remote add origin <url> && git push --force-with-lease
```

---

## 7. 高级用法（知道即可，简历提分）

| 主题 | 一句话说明 |
|---|---|
| `git bisect` | 二分查找"坏提交"：`bisect start` → 标 `bad`/`good` → git 自动二分；配合 `bisect run <测试脚本>` 全自动 |
| hooks | `.git/hooks/` 下放脚本：`pre-commit`（提交前跑 lint）、`pre-push`（推送前跑测试）、`commit-msg`（校验信息格式） |
| submodule | 仓库里嵌仓库，固定到具体 commit；克隆后用 `git submodule update --init --recursive` |
| worktree | `git worktree add ../hotfix hotfix`：同一仓库开多个工作目录（修 bug 时不用 stash） |
| LFS | 大文件指针化：`git lfs track "*.bin"`，文件本体存在独立存储 |
| `.gitignore` 语法 | `*` 通配、`/build/` 锚定根目录、`*.log` 按后缀、`!keep.log` 例外、`**/logs` 任意层级 |
| GPG 签名 | `git config --global commit.gpgsign true`，提交显示 Verified（开源协作加分） |
| rerere | `git config --global rerere.enabled true`：记住冲突解法，反复 rebase 时自动复用 |
| 浅克隆 | `git clone --depth 1`：只取最新快照（CI 常用，省时省空间） |
| 维护 | `git gc` 打包压缩对象库；`git maintenance start` 定期自动维护 |

---

## 8. 陷阱清单（★ "真用过"和"背过命令"的分水岭）

1. **Windows 换行符（CRLF/LF）**：Windows 文件是 CRLF，Linux 是 LF，混用会让 diff 满屏噪音。对策：`core.autocrlf=true`（Windows）或仓库放 `.gitattributes` 写 `* text=auto`；已混入的用 `git add --renormalize .` 一次性修正。
2. **密钥进仓库 = 已泄露**：提交那一刻就必须当泄露处理——立即轮换密钥，再用 filter-repo/BFG 清历史。删掉提交不等于没发生过（fork、缓存、CI 日志里都可能有）。预防：`.gitignore` 里先写好 `*.pem`、`.env` 等。
3. **force push 覆盖他人提交**：`--force` 会直接覆盖远端历史，协作时可能抹掉别人的提交。要用就用 `--force-with-lease`（远端有新提交时拒绝强推），且公共分支（main/master）禁止强推。
4. **push 之后 amend/rebase**：规则一句话——"已 push 的提交不动历史"（个人 feature 分支除外，且要和协作者通气）。
5. **`git commit -am` 漏新文件**：`-a` 只暂存**已跟踪**文件的修改，新建的文件必须显式 `git add`。漏提交新文件是"我的代码明明 push 了怎么服务器上没有"的经典原因。
6. **大文件/二进制进仓库**：git 存快照，二进制每次改动都存完整副本 → 仓库永久膨胀。用 LFS 或外部存储。
7. **detached HEAD 上提交后切走**：提交悬空，感觉"丢了"——其实 reflog 里都在。养成习惯：游离状态下要提交就先 `git switch -c 新分支名`。
8. **生成物提交**（build/、node_modules/、日志、core dump）：建仓库时就把 `.gitignore` 写好，这是团队协作的基本素养。
9. **`reset --hard` / `git clean -fd` 不可逆**：动手前先 `git status` + 记下当前哈希（或看一眼 reflog）。不确定就用更温和的 stash / revert。
10. **push 被拒后的错误操作**：`git push` 被拒（远端有新提交）时，正确姿势是 `git fetch` → `git rebase origin/main` → 再 push；**不是** `git push -f` 一把梭。
11. **stash pop 冲突**：pop 遇到冲突会停下，但 stash 条目**还在**（`git stash list` 可见），解决冲突后手动 `git stash drop` 即可，不会丢。
12. **merge 噪音**：长期无脑 `--no-ff` 或来回合并，历史变成"意大利面"。习惯：本地分支 rebase 整理干净后再 merge 进 main。

---

## 9. 简历与面试（★ 自测）

### 简历写法参考

> **版本控制**：熟练使用 Git + GitHub 进行代码版本管理与团队协作，掌握 feature 分支 + Pull Request 工作流；使用交互式 rebase 维护线性清晰的提交历史，能通过 reflog、cherry-pick、revert 处理误操作恢复与跨分支代码同步。

### 高频面试题与参考答案

**Q1：merge 和 rebase 的区别？什么时候用哪个？**
答：merge 把两条历史线合成一个合并提交，保留真实历史但可能杂乱；rebase 把当前分支的提交逐个重放到目标分支最新提交之后，历史线性干净，但**提交哈希全部重写**。原则：本地未 push 的私有分支用 rebase 整理；已 push 或共享的分支用 merge/revert，绝不重写共享历史。

**Q2：git reset 的三种模式分别做什么？**
答：都移动"分支指针"到目标提交，区别在改动去哪：`--soft` 只动指针，改动留在暂存区；`--mixed`（默认）动指针并重置暂存区，改动回到工作区；`--hard` 连工作区一起重置，改动彻底丢弃。分别对应"重新提交""重新暂存""彻底放弃"三种意图。

**Q3：已经 push 的提交怎么撤销？**
答：首选 `git revert <sha>`——生成一个反向提交，不动历史，协作者 pull 即可，这是唯一安全的方式。特殊情况下（如泄漏密钥）需要彻底抹掉时，用 reset + `--force-with-lease` 强推，但必须和所有协作者协调，公共分支禁用。

**Q4：什么是 fast-forward 合并？**
答：目标分支在当前分支之后没有任何新提交时，合并只是把指针直接前移，不产生 merge commit，历史仍是直线。`--no-ff` 可以强制生成 merge commit，在历史中保留"这里合并过一个分支"的结构信息。

**Q5：fetch 和 pull 的区别？**
答：fetch 只下载远端新对象并更新远程跟踪分支（origin/main），不碰工作区和当前分支，是安全的"只读同步"；pull = fetch + merge（或 rebase），会直接改动当前分支。规范流程是先 fetch 查看差异再决定怎么合并。

**Q6：git 内部是怎么存储数据的？**
答：内容寻址的对象数据库，四类对象：blob（文件内容）、tree（目录映射）、commit（完整快照 + 父指针 + 元信息）、tag。对象按内容哈希命名、自动去重；历史是 commit 通过 parent 指针串成的有向无环图。存的是快照而不是差异。

**Q7：HEAD 是什么？detached HEAD 是什么？**
答：HEAD 是"当前所在位置"的指针，通常指向某个分支，分支再指向具体提交。当 checkout 一个具体提交哈希时，HEAD 直接指向提交本身，即为游离状态——此时提交不会被任何分支引用，切走后只能靠 reflog 找回。游离状态下要提交应先建分支。

**Q8：cherry-pick 是什么？典型场景？**
答：把任意一个提交的"变更内容"复制到当前分支顶端，生成一个哈希不同的新提交。典型场景：hotfix 分支上的修复单独同步到 release 分支；从 reflog 里找回丢失的提交。

**Q9：误删了分支/误 reset 了，怎么找回？**
答：`git reflog` 记录本地所有 HEAD 移动历史（默认 90 天），找到目标提交哈希后 `git branch <名字> <sha>` 重新挂回分支，或 cherry-pick 到当前分支。只要对象库没被 gc，数据都在。

**Q10：为什么不该在公共分支上 rebase？**
答：rebase 重写提交哈希，等于改写了大家共享的历史。协作者本地基于旧哈希的分支会与远端分叉，产生大量冲突，且同步只能靠 force push——极易覆盖他人提交。公共分支只允许"向前追加"，撤销用 revert。

---

## 10. 结合你的实际项目（bf2-collector）

你的仓库（GitHub: baobao-baobo/bf2-counter-collector）就是练习场，建议按顺序做这几件事：

1. `git log --oneline --graph --all -20`——看看自己仓库的历史拓扑长什么样，对照 §3.5 想想哪些是 merge 哪些是直推
2. `git cat-file -p HEAD`——亲手拆开 commit → tree → blob，把 §3.2 的对象模型落到自己的数据上
3. `git reflog`——看自己的全部本地操作记录
4. 建一个临时分支，试 `rebase -i` 的 squash 和 `reset --hard`，玩坏了直接删分支，零成本
5. 用 `gh pr create` 走一次完整 PR 流程——面试讲协作流程时有实操支撑

另外两个可以写进面试素材的点：

- **你的部署链路（GitHub → 服务器 → rshim）本身就是"git 作为交付渠道"的真实案例**：commit = 变更记录，push = 交付，服务器 `git pull` = 发布，任何一次部署都能回溯到具体提交。
- **提交信息规范**：团队建议用 Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`），可以对照检查自己仓库的提交信息风格。另外注意：Claude Code 帮你生成的提交带 `Co-Authored-By` 行，这是 GitHub 规范的一部分，面试被问到可以直接解释。

---

## 11. 参考资源

- **Pro Git（免费中文版，最权威教材）**：https://git-scm.com/book/zh/v2
- **Learn Git Branching（交互式练习游戏，强烈推荐，30 分钟打通分支/merge/rebase）**：https://learngitbranching.js.org/
- **Oh My Git!（可视化游戏，直观理解对象模型）**：https://ohmygit.org/
- 官方命令文档：https://git-scm.com/docs
- GitHub Docs（PR 与协作流程）：https://docs.github.com
