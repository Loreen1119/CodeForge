# CodeForge 编码 Agent 评测基准（BENCHMARK）

> 本文件是 CodeForge 的真实 issue 评测记录，所有数字**实测后填写**，不估、不借、不编。
> 红线规则见文末「红线检查清单」，任何一处触碰即判定该次评测作废。
>
> **所有实验基准：deepseek-chat（DeepSeek 官方 OpenAI 兼容端点）**（含 CodeForge 主跑 + Aider + 纯 GPT baseline，同模型版本同 prompt）

**状态**：🟢 两轮跑批 + 判定完成（2026-10-17）。19 条有效 issue 两轮各跑完一次，计入 **M=18**（`14905` 一条因生成器回退不计入）。第二轮只改检索词表（模型与 system prompt 未动），判定脚本另修了一处检索归因缺陷并整体重判。**结果为实测值，未做任何人工修正。**
>
> ⚠️ **[I] 引用前必读**：两轮的可应用补丁集合**零交集**（第一轮 3 条全部掉出，第二轮 6 条全是新上），该指标的波动与「改了什么」无法分离，**不得单独作为改进证据引用**。详见 §4.3。

---

## 0. 一句话口径

在 **18** 个真实开源 issue（Python 为主，已 merge PR，有 FAIL_TO_PASS 测试）上评测编码 Agent：检索命中率 **38.9%**（7/18，按 base 树三态判定）、Patch 可应用率 **33.3%**（6/18，统计不稳，见 §4.3）、端到端测试通过率 **0%**（0/18）、F2P 单口径 **1/18**；对比 baseline 提升 [K] 待补（baseline 未跑）；Worktree 隔离 + 丢弃即回滚，不碰原仓库；失败归因 **18** 条（检索失败 11 = 排序 8 + 结构性 3 / Patch 非法 4 / 方案错 2 / 测试不过 1）。

---

## 1. 筛选条件（冻结后不再改）

| 维度 | 标准 |
|------|------|
| issue 来源 | **已 merge PR**（删掉 open issue + 已知 fix 选项） |
| 语言 | Python 为主（备选 Java / TS） |
| 测试 | 有 pytest / unittest，能本地跑通 |
| FAIL_TO_PASS | **每个 issue ≥ 1 个**「修复前必失败、修复后必通过」的测试，无则淘汰 |
| 二次验证 | PR 描述说明根因（非 workaround）＋ ≥1 个 FAIL_TO_PASS ＋ base commit 可复现，三项必满 |
| 规模 | 平均改动 1-3 文件，单文件 ≤ 200 行 |
| 依赖 | 无外部服务（数据库、API），纯本地 |
| 复现 | `pip install` + `pytest` 能跑通 |
| 测试白名单 | 每个 issue 列出测试文件路径白名单，Patch 不得改 |

---

## 2. 实验锁定（所有实验同条件）

| 项 | 值 |
|----|----|
| 基准模型版本 | **deepseek-chat**（DeepSeek 官方端点，OpenAI 兼容；已定，全实验同用，含 baseline） |
| System prompt | **已冻结** · `backend/patch_generator.py::_SYSTEM_PROMPT`，sha256=`1145f394e1b9d628`（366 字符） |
| User prompt 模板 | **已冻结** · `backend/patch_generator.py::_build_user_prompt(ctx)`；结构固定（Issue 标题/正文 → Plan 摘要 → Proposed changes → Source context），正文按 issue 逐条注入 |
| per-issue token 硬上限 | 50K |
| 全局预算上限 | $30 |
| 判定脚本版本 | 见 `scripts/benchmark/` |
| **评测护栏档位（主跑档）** | `CODEFORGE_MAX_FILES_PER_PATCH=10`、`CODEFORGE_MAX_CHANGED_LINES=400`、`CODEFORGE_ENFORCE_PLAN_SCOPE=0` |
| 对照档（默认护栏） | 上述三项全用产品默认值（单文件 / 50 行 / 范围一致开启），**抽 5 条**跑对照 |

> ⚠️ **评测配置必须显式声明，不许静默放宽。** 产品默认护栏是「首版 Patch 单文件、≤50 行、改动文件须落在方案范围内」；而本 issue 池按上游 PR 选型，**平均改动 3.3 文件（min 2 / max 5）**，默认档结构性跑不通（实测 4 条 dry run 里 3 条被护栏拦下）。因此主跑档把这三项放宽，放宽事实记录在此，且**同一批实验全部使用该档**（红线 3 要求同条件）。
>
> 三项均为环境变量驱动，默认值与产品原行为**逐字一致**：`CODEFORGE_MAX_FILES_PER_PATCH`（默认 1）、`CODEFORGE_MAX_CHANGED_LINES`（默认 50）、`CODEFORGE_ENFORCE_PLAN_SCOPE`（默认 1=开启）。测试白名单、源码范围、危险 token、worktree 隔离等其余边界**一律未放宽**。
>
> 因护栏被拒的条目在 run 记录的 `error` 字段里带原文（如「首版 Patch 只能修改单个文件」），报告按关键词拆分统计，**不得与「模型产出坏补丁」混为一类**。
>
> **两轮实验的唯一变量**：检索词表（`backend/agent_graph.py` 的 `_STOP_WORDS` 与关键词排序）。模型、system/user prompt、护栏档位、issue 池、base commit 全部不变。因此两轮之间的差异只能归因于「词表」与「采样噪声」两者，而 §9.2 给出了噪声存在的直接证据 —— 这也是为什么 §4.3 不下「词表带来 +X 个百分点」的结论。

---

## 3. issue 清单（冻结）

| # | 仓库 | PR | base commit | 改动文件数 | 多文件 | FAIL_TO_PASS | 测试白名单 | 难度 |
|---|---|---|---|---|---|---|---|---|
| 1 | pytest-dev/pytest | #13073 | `868e1d225e` | 2 | 是 | 1 条 | `testing/test_mark.py` | 未分层 |
| 2 | pytest-dev/pytest | #13370 | `70dc4e94a5` | 3 | 是 | 8 条 | `testing/python/metafunc.py` | 未分层 |
| 3 | pytest-dev/pytest | #13745 | `a933cf5de4` | 3 | 是 | 5 条 | `testing/python/fixtures.py` | 未分层 |
| 4 | pytest-dev/pytest | #14125 | `dde81b1948` | 2 | 是 | 2 条 | `testing/python/metafunc.py` | 未分层 |
| 5 | pytest-dev/pytest | #14331 | `532b20103e` | 2 | 是 | 10 条 | `testing/test_tmpdir.py` | 未分层 |
| 6 | pytest-dev/pytest | #14463 | `8cb8097855` | 2 | 是 | 1 条 | `testing/test_assertion.py` | 未分层 |
| 7 | pytest-dev/pytest | #14645 | `35f21fdcfc` | 2 | 是 | 1 条 | `testing/test_conftest.py` | 未分层 |
| 8 | pytest-dev/pytest | #14670 | `e84e063e7d` | 3 | 是 | 2 条 | `testing/test_terminal.py` | 未分层 |
| 9 | pytest-dev/pytest | #14905 | `e16499d26e` | 3 | 是 | 4 条 | `testing/test_assertrewrite.py` | 未分层 |
| 10 | pytest-dev/pytest | #15032 | `fe9eef7ebc` | 3 | 是 | 16 条 | `testing/test_helpconfig.py`, `testing/test_parseopt.py` | 未分层 |
| 11 | pytest-dev/pytest | #15040 | `e4f7f174bc` | 3 | 是 | 2 条 | `testing/test_config.py` | 未分层 |
| 12 | pytest-dev/pytest | #15058 | `2950bc2392` | 3 | 是 | 1 条 | `testing/python/fixtures.py` | 未分层 |
| 13 | pytest-dev/pytest | #15133 | `aaa3e7ec82` | 3 | 是 | 2 条 | `testing/test_faulthandler.py` | 未分层 |
| 14 | pytest-dev/pytest | #15149 | `e90885e6b3` | 2 | 是 | 2 条 | `testing/code/test_excinfo.py`, `testing/test_subtests.py` | 未分层 |
| 15 | pytest-dev/pytest | #15150 | `3e34325a99` | 2 | 是 | 9 条 | `testing/test_junitxml.py` | 未分层 |
| 16 | fastapi/fastapi | #16418 | `3e33a03bda` | 1 | 否 | 11 条 | `tests/test_telemetry/test_runtime.py` | 未分层 |
| 17 | fastapi/fastapi | #16468 | `94918c1d40` | 2 | 是 | 4 条 | `tests/test_telemetry/test_tracer.py` | 未分层 |
| 18 | fastapi/fastapi | #16470 | `cb8dabcd4a` | 1 | 否 | 8 条 | `tests/test_telemetry/test_data.py` | 未分层 |
| 19 | fastapi/fastapi | #16484 | `f5c6e9b4f9` | 1 | 否 | 6 条 | `tests/test_router_include_context.py` | 未分层 |

> 冻结后不补新、不删「修不好」的 issue。

---

## 4. 结果总表（第二轮实测，2026-10-17）

> 「检索」列由 `judge.py` 按 **base 树**判定，三态定义见 §4.2。第一轮数据见 §4.3。

| # | 仓库 | 检索 | 可应用 [I] | 通过 [J] | 回归 | 失败归因 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | pytest-dev/pytest #13073 | 结构性 | 是 | 否 | 10 | 检索失败 | apply_mode=recount；补丁改到别的文件，带 10 条回归 |
| 2 | pytest-dev/pytest #13370 | 结构性 | 否 | 否 | 0 | 检索失败 | F2P 8 条 |
| 3 | pytest-dev/pytest #13745 | 排序 | 否 | 否 | 0 | 检索失败 | — |
| 4 | pytest-dev/pytest #14125 | 排序 | 否 | 否 | 0 | 检索失败 | — |
| 5 | pytest-dev/pytest #14331 | 排序 | 否 | 否 | 0 | 检索失败 | — |
| 6 | pytest-dev/pytest #14463 | 排序 | 是 | 否 | 0 | 检索失败 | apply_mode=recount |
| 7 | pytest-dev/pytest #14645 | 结构性 | 否 | 否 | 0 | 检索失败 | — |
| 8 | pytest-dev/pytest #14670 | 命中 | 否 | 否 | 0 | Patch 非法 | — |
| 9 | pytest-dev/pytest #15032 | 排序 | 否 | 否 | 0 | 检索失败 | — |
| 10 | pytest-dev/pytest #15040 | 命中 | 是 | 否 | 0 | 方案错 | apply_mode=strict |
| 11 | pytest-dev/pytest #15058 | 命中 | 否 | 否 | 0 | Patch 非法 | — |
| 12 | pytest-dev/pytest #15133 | 命中 | 是 | 否 | 1 | 测试不过 | **F2P 2/2 全过**，卡 1 条 P2P 回归 |
| 13 | pytest-dev/pytest #15149 | 命中 | 否 | 否 | 0 | Patch 非法 | — |
| 14 | pytest-dev/pytest #15150 | 命中 | 否 | 否 | 0 | Patch 非法 | — |
| 15 | fastapi/fastapi #16418 | 排序 | 否 | 否 | 0 | 检索失败 | — |
| 16 | fastapi/fastapi #16468 | 排序 | 否 | 否 | 0 | 检索失败 | — |
| 17 | fastapi/fastapi #16470 | 排序 | 是 | 否 | 0 | 检索失败 | apply_mode=recount |
| 18 | fastapi/fastapi #16484 | 命中 | 是 | 否 | 1 | 方案错 | apply_mode=recount+relaxed |

### 4.1 汇总指标

| 指标 | 定义 | 值 |
|------|------|----|
| [M] | 判定计入的 issue 数（剔除生成器回退） | **18**（19 条有效池 − 1 条回退） |
| [I] | Patch 可应用率 = `git apply` 通过 / M | **6/18 = 33.3%**（⚠️ 统计不稳，见 §4.3） |
| [J] | 端到端通过率 = F2P 全过 且 无回归 / M | **0/18 = 0.0%** |
| 条件通过率 | F2P 全过 / 可应用 patch 数 | **0/6 = 0.0%** |
| 回归率 | 被搞挂的 PASS_TO_PASS 条数 / 可应用 patch 数 | **12/6 = 200.0%**（涉及 3 个 issue） |
| F2P 单口径通过 | 只算 F2P 不看回归 | **1/18**（`15133` 2/2 全过） |
| 检索命中率 | 结果里出现预期源码文件 / M | **7/18 = 38.9%**（详见 §4.2） |
| [K] | 对比 baseline 提升百分点 | ⬜ 未做（baseline 尚未跑） |
| [L] | 失败归因条数 | **18**（检索失败 11 / Patch 非法 4 / 方案错 2 / 测试不过 1） |

### 4.2 检索命中率（诊断口径）

**为什么单列**：`[I]` 的变化同时受「检索给没给对文件」和「模型写得好不好」影响，两者不拆开就无法判断下一步该修哪边。

**判定方法**（`scripts/benchmark/judge.py::_retrieval_status`，工具链未随仓库公开、见 §7；判定规则如下，全部机械可复算）：

- 预期文件 = `candidates.json` 的 `src_files` 里检索覆盖得到的源码后缀（`.py/.ts/.tsx/.js/.jsx`），剔掉 changelog / docs / `.github/workflows`
- 检索结果 = 该 task 的 `CODE_SEARCHED` 事件 payload（run 记录只落状态，事件落库，用 `agent_run.task_id` 反查）
- 关键词 = 该 task 的 `REQUIREMENT_ANALYZED` 事件，**只取真正喂进检索的前 8 个**（超出预算的词从没被搜过，不能拿来证明「检索本可以命中」）
- ① 结果里出现预期文件 → **hit**；② 否则在 **base commit 树**上 `git grep` 预期文件，含任一被喂入关键词 → **ranking**；③ 一个都不含 → **structural**

| 检索态 | 条数 | issue | 含义 |
|--------|------|-------|------|
| hit | **7** | 14670 / 15040 / 15058 / 15133 / 15149 / 15150 / 16484 | 结果里出现了预期文件 |
| ranking | **8** | 13745 / 14125 / 14331 / 14463 / 15032 / 16418 / 16468 / 16470 | 文件里有词但没进结果 → **排序 / 路径深度问题**，改词表救不回来 |
| structural | **3** | 13073 / 13370 / 14645 | 文件在 base 树一个都没含被喂入的词 → **词汇层无信号**，词表与排序都救不回来 |
| unknown | 0 | — | 取不到信号（不计入分母） |

**命中率 7/18 = 38.9%。** 剩下 11 条里，8 条是排序问题（可修，但要改检索算法：同名文件 / 路径深度加权），3 条是结构性问题（无解，属 issue 文本本身不含可搜信息）。

### 4.3 两轮对照（改词表前后）

| 指标 | 第一轮（原词表） | 第二轮（改词表后） | 说明 |
|------|-----------------|-------------------|------|
| M | 16 | 18 | 回退假生成器 3 条 → 1 条（`14125` / `16484` 本轮产出了可判定补丁） |
| 检索命中 | 6/19 = 31.6% | 7/19 = 36.8% | **净值 +1**；毛值 **+3 / −2**（进 14670 / 15133 / 16484，出 13745 / 14645）；16 条同池口径 **6/16 持平** |
| [I] 可应用 | 3/16 = 18.8% | 6/18 = 33.3% | ⚠️ **两轮集合零交集**：旧 14670 / 15149 / 15150 全部变 Patch 非法，新 6 条全是本轮新上 |
| [J] 通过 | 0/16 = 0% | 0/18 = 0% | 无变化；但本轮 `15133` F2P 2/2 全过（第一轮 0 条） |
| 归因 | Patch 非法 13 / 方案错 3 / 检索失败 0 / 测试不过 0 | 检索失败 11 / Patch 非法 4 / 方案错 2 / 测试不过 1 | 第一轮的「检索失败 0」是判定缺陷造成的，见 §6 |

> **结论**：改词表**没有提高检索命中率**（同池持平，毛值一进一出）。`[I]` 从 3 条变 6 条不可归因于词表——两轮可应用集合零交集，且 `16470` 两轮检索上下文逐字相同、补丁却不同（`temperature: 0.0` 在 DeepSeek 端不保证可复现），说明单次采样的波动就有 ±3 条。

---

## 5. baseline 对比（同 issue、同模型版本、同 prompt）

| 工具 | 抽样方式 | 通过率 | 相对 CodeForge |
|------|---------|--------|---------------|
| 纯 GPT（单轮） | 全量 | ⬜ | ⬜ |
| Aider（多轮 + 工具） | 分层抽 easy/medium/hard = 3/4/3 | ⬜ | ⬜ |

> 两个基线回答两个不同问题，结论分开写：
> - 纯 GPT 单轮 → Agent 循环有没有价值？
> - Aider 多轮 → CodeForge 护栏 + 编排有没有增量？
> 不写成一句「比 X 强」。

---

## 6. 失败归因（4 类互斥 + 检索失败二分，基于事件日志客观判定）

> 🔴 **本节 2026-10-17 修正过，旧数字作废。** 第一轮写的是「Patch 非法 13 / 方案错 3 / 检索失败 0 / 测试不过 0」，其中「检索失败 0」是**判定缺陷**：`judge.py` 原先只看「检索结果是否为空」，返回 8 条无关文件也算「检索没失败」，且 run 记录里本来就不存事件（事件在 `backend/codeforge.db` 的 `task_events` 表），于是永远判「无检索信号」。修法：改从事件表取 `CODE_SEARCHED`，按 base 树三态判定（§4.2），并给 `judge.py` 加 `--force` 重判（旧判定留档到 `judge_previous`）。

| 类别 | 判定规则（基于事件日志 + base 树） | 条数 | 典型 case |
|------|-----------------------------------|------|-----------|
| 检索失败 · 排序 | 预期文件在 base 树含被喂入的关键词，但没进检索结果 | **8** | 16418 / 16468 / 16470：关键词 `telemetry` 命中 `fastapi/telemetry.py`，预期文件是 `fastapi/telemetry/_asgi.py` —— 同名文件的路径深度问题；14463：`mapping` / `compare` 在 `_compare_mapping.py` 里有，但 top-8 被字典序更靠前的 `_code/code.py`、`_io/pprint.py` 占满 |
| 检索失败 · 结构性 | 预期文件在 base 树**不含任何**被喂入的关键词 → 词汇层无信号 | **3** | 13073（`mark/structures.py` 不含正文里的任何词，`parametrize` 在预算外）、13370（`python.py`）、14645（`main.py`）。这三条改词表、改排序都无解 |
| Patch 非法 | 检索命中，但 diff 产不出来 / `git apply` 失败 / 改白名单测试 | **4** | 14670 / 15058 / 15149 / 15150：模型拿对了文件，但 diff 的 hunk 行数声明与实际内容对不上（`corrupt patch`）。**这 4 条才是真正的格式问题** |
| 方案错 | patch 可应用，但 FAIL_TO_PASS 未过 | **2** | 15040：改对了文件、打得上、F2P 0/2；16484：可应用但 F2P 0/6，另带 1 条 P2P 回归 |
| 测试不过 | F2P 全过，但 PASS_TO_PASS 被搞挂 | **1** | **15133**：F2P 2/2 全过，1 条 P2P 回归 —— 全池唯一一条把 F2P 打全绿的样本，差一步就是 `[J]` |

**归因优先级的口径说明**：沿用「根因在上游，下游补救不算数」——检索没给对文件，一律记检索失败。因此 `13073` / `14463` / `16470` 三条虽然补丁能打上（打的是别的文件），也记检索失败而不是方案错。若改用「补丁能打上就算方案错」的口径，则检索失败 8 / 方案错 5，其余不变；本表采用前者，与代码里的既有优先级一致。

---

## 7. 复现命令

> ⚠️ **评测工具链（`scripts/benchmark/`）未随本仓库公开**：其中 `issues.json` 含本机绝对路径、`candidates.json` 含 PR 预期改动文件（评测集答案）、`runs/` 含逐条失败样本与模型原始输出，均不适合公开。下面命令仅为记录当时怎么跑的，他人无法直接复现；如需完整复现，需另行提供工具链与评测数据。

```bash
# 1. 起后端（评测档环境变量见 §2；不设则退回产品默认值）
cd backend && .venv/Scripts/python.exe -m uvicorn main:api --host 127.0.0.1 --port 8000

# 2. 跑 Agent 修复（逐条落盘、可中断续跑；已 done 的会跳过，整批重跑前先备份 runs/）
#    有效池 = issues.json 剔除标记 drop 的 6963 后的 19 条
cd backend && CODEFORGE_BENCH_ISSUES=<issues 清单路径> PYTHONPATH= .venv/Scripts/python.exe <run_agent.py 路径>

# 3. 判定（只跑白名单测试）
#    ⚠️ 已判定的记录 --all 会静默跳过；改过判定逻辑必须加 --force 才会重判（旧判定留档 judge_previous）
PYTHONPATH= backend/.venv/Scripts/python.exe <judge.py 路径> --all --out <汇总输出路径>

# 4. 只重算汇总、不跑测试（改 rows / 汇总口径后用这个，几秒）
PYTHONPATH= backend/.venv/Scripts/python.exe <judge.py 路径> --out <汇总输出路径>
```

> 检索三态（§4.2）的判定需要两样东西：`backend/codeforge.db` 的 `task_events`（`CODE_SEARCHED` / `REQUIREMENT_ANALYZED`），以及 `repos/` 下各库的 `base_commit`。缺任一样，该条记 `unknown` 而不是 `hit`——「没有信号」与「检索没失败」是两件事，不许合并。

---

## 8. 红线检查清单（5 条，不可触碰）

| # | 规则 | 检查 |
|---|------|------|
| 1 | 没有 issue 因「总修不好」被删 | issue 池冻结，失败的写归因，不隐瞒 |
| 2 | 没有 Patch 被人工修正后算成功 | 手改过 = 废弃，单独记录 |
| 3 | baseline 同模型版本、同 prompt | 所有实验同一版本 + 同一套 prompt |
| 4 | 数字实测后填，不估不借不编 | dry run 3-5 个 → 外推 |
| 5 | Patch 只应用源码，测试走白名单，Agent 改的测试不计入判定 | 白名单 → 判定脚本只跑白名单 |

> 任何一条触碰 → 该次评测作废，重新跑。

---

## 9. 已发现局限

- n=20-30，50% vs 60% 差异统计上不显著，只报「失败模式 + 方向性信号」，不下精确胜率结论。
- 便宜档模型跑通闭环证明「编排有增量」，不代表模型本身能力上限；贵模型抽 5 个当上限探针。
- **生成器回退（FakePatchGenerator）**：第一轮 3 条（14125 / 14905 / 16484），第二轮只剩 1 条（14905）—— 同一 prompt 下另两条这一轮产出了可判定的 diff，本身也是采样不稳定的旁证。按红线剔除、不进 M。⚠️ 回退时原始响应未落盘（只存 sha256 摘要），无法进一步诊断是拒绝生成还是格式异常 —— 这是当前唯一的不可审计项。
- **判定脚本在本次跑批后修过两个缺陷**，修复前的判定结果已整体作废重跑（不是改数字，是判定逻辑本身错）：
  1. judge **从不贴 PR 的测试补丁** —— F2P 用例来自 PR 新增测试，不贴则 base/patched 两侧都「查无此例」，[J] 恒为 0；
  2. judge 读的是 `fail_to_pass`（选型期 suspected 列表，含 `test_a1` 这类噪声名），而硬确认回填的是 `fail_to_pass_tests`（实测 nodeid、带 `TestClass::` 前缀）。
  修复后 `[I]`/`[J]` 数字才成立；上表所有 `test_patch_applied` 均有落痕可复核。
- **难度分层（easy/medium/hard）未做** —— §5 的 Aider 分层抽样前提未就绪，故 [K] 仍为 ⬜。
- 本次仅测 1 个模型（deepseek-chat）。结果**不代表任务本身不可解**，只代表该模型在当前 prompt + 这套 issue 上的表现。

### 9.1 判定与复算的三处失真（均已定位，两处已修）

1. ✅ **检索归因的输入曾为空（已修）**。`run_agent.py` 只把状态落进 `runs/issue-*.json`，事件走 `backend/codeforge.db` 的 `task_events` 表，而旧 `_retrieval_missed()` 只在记录里找 `events` 字段 —— 结果永远是「无检索信号」，于是判出「检索失败 0」。这不是「检索都没问题」，是**判定没看到数据**。现改为从事件表按 `agent_run.task_id` 反查 `CODE_SEARCHED` / `REQUIREMENT_ANALYZED`，并按 §4.2 三态判定。
2. ✅ **重判会静默跳过（已修）**。`judge.py --all` 遇已判定的记录直接 `continue`，改完判定逻辑再跑 `--all` 会得到与旧结果完全相同的汇总。新增 `--force`：重判前把旧判定留档到 `judge_previous`，改了什么可逐条 diff。
3. ⚠️ **离线复算的树不对齐（方法已修正，历史数字作废）**。检索实验脚本原先搜的是**仓库当前 HEAD**，而真实跑批搜的是每条 issue 的 `base_commit`（`run_agent.align_repo_base()` 会 `git checkout -f`）。后果：拿 PR 之后的代码当「本可以命中的线索」，命中率虚高。据此得出的「只改词表能让命中率从 37.5% 升到 56%」**已作废**（那 3 条「救回」里 `14331` 是 HEAD 上的假命中）。现所有检索判定一律 `git grep <base_commit> --`，不读工作区。

### 9.2 统计效力与可复现性

- **可应用补丁两轮零交集**（§4.3）：`[I]` 在 n=18 上的波动约 ±3 条，单次采样不足以支撑「改了什么 → 涨了多少」的因果结论。**`[I]` 只能当量级参考，不能当胜率引用。**
- **`temperature: 0.0` 不等于可复现**：`16470` 两轮检索上下文（8 条 `path:line`）逐字相同、补丁却不同，判定从「Patch 非法」变成「可应用」。这是纯采样噪声的直接证据，不是推测。
- 因此本轮所有结论只报**方向**（检索是主要瓶颈、`15133` 证明链路可通），不报「提升 X 个百分点」。
