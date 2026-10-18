# CodeForge 编码 Agent 评测基准（BENCHMARK）

> 本文件是 CodeForge 的真实 issue 评测记录，所有数字**实测后填写**，不估、不借、不编。
> 红线规则见文末「红线检查清单」，任何一处触碰即判定该次评测作废。
>
> **所有实验基准：deepseek-chat（DeepSeek 官方 OpenAI 兼容端点）**（含 CodeForge 主跑 + Aider + 纯 GPT baseline，同模型版本同 prompt）

**状态**：🟢 三轮跑批 + 判定完成（2026-10-18）。19 条有效 issue 三轮各跑完一次，第三轮计入 **M=16**（`14670` / `14905` / `15032` 三条因生成器回退不计入）。第三轮只改检索算法（排序键 + 文件名命中加权 + 同文件去重），模型、system prompt、词表、护栏档位全部未动。**结果为实测值，未做任何人工修正。**
>
> ⚠️ **[I] 引用前必读**：三轮的可应用补丁条数分别是 3 / 6 / 6，第三轮与第二轮**只重叠 3 条**（`15133` / `16470` / `16484`），且 M 从 18 变 16（回退 1 → 3 条）。`[I]` 的百分比变化无法与采样噪声分离，**不得单独作为改进证据引用**。详见 §4.3。

---

## 0. 一句话口径

在 **16** 个真实开源 issue（Python 为主，已 merge PR，有 FAIL_TO_PASS 测试）上评测编码 Agent：检索命中率 **75.0%**（12/16，按 base 树三态判定）、Patch 可应用率 **37.5%**（6/16，统计不稳，见 §4.3）、端到端测试通过率 **0%**（0/16）、F2P 单口径 **1/16**（`15133`）；对比 baseline 提升 [K] 待补（baseline 未跑）；Worktree 隔离 + 丢弃即回滚，不碰原仓库；失败归因 **16** 条（检索失败 4 = 排序 2 + 结构性 2 / Patch 非法 8 / 方案错 3 / 测试不过 1）。

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
> **三轮实验各自只有一处变量**：第一→第二轮是**检索词表**（`backend/agent_graph.py` 的 `_STOP_WORDS` 与标识符排序）；第二→第三轮是**检索算法**（`backend/code_search.py` 排序键改为 `(-score, -depth, path, matched_line)` + 关键词命中文件名/目录名时加权 200→280；`backend/agent_graph.py` 的结果去重键由 `(path, matched_line)` 改为 `path`）。模型、system/user prompt、护栏档位、issue 池、base commit 三轮全同。因此轮间差异只能归因于「当轮那处变量」与「采样噪声」两者，而 §9.2 给出了噪声存在的直接证据 —— 这也是为什么 §4.3 不下「某处改动带来 +X 个百分点」的结论。

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

## 4. 结果总表（第三轮实测，2026-10-18）

> 「检索」列由 `judge.py` 按 **base 树**判定，三态定义见 §4.2。前两轮数据见 §4.3。第三轮 M=16，`14670` / `14905` / `15032` 三条生成器回退、不进表。

| # | 仓库 | 检索 | 可应用 [I] | 通过 [J] | 回归 | 失败归因 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | pytest-dev/pytest #13073 | 命中 | 否 | 否 | 0 | Patch 非法 | 检索引到了 `mark/structures.py`；diff `corrupt patch at <stdin>:9` |
| 2 | pytest-dev/pytest #13370 | 结构性 | 否 | 否 | 0 | 检索失败 | 检索 7 条全无关；diff `corrupt patch at <stdin>:21` |
| 3 | pytest-dev/pytest #13745 | 命中 | 否 | 否 | 0 | Patch 非法 | 生成期被产品校验拒（`---`/`+++` 路径不一致），diff 未落盘 |
| 4 | pytest-dev/pytest #14125 | 命中 | 否 | 否 | 0 | Patch 非法 | `corrupt patch at <stdin>:1514` |
| 5 | pytest-dev/pytest #14331 | 排序 | 否 | 否 | 0 | 检索失败 | 只有 6 条结果，预期 `pathlib.py` 未进；生成期路径不一致 |
| 6 | pytest-dev/pytest #14463 | 命中 | 否 | 否 | 0 | Patch 非法 | 生成期被产品校验拒（路径不一致），diff 未落盘 |
| 7 | pytest-dev/pytest #14645 | 结构性 | 是 | 否 | 0 | 检索失败 | 检索只给 2 条无关文件，补丁却 `relaxed` 打上（改的不是预期文件） |
| 8 | pytest-dev/pytest #15040 | 命中 | 否 | 否 | 0 | Patch 非法 | `corrupt patch at <stdin>:37` |
| 9 | pytest-dev/pytest #15058 | 命中 | 否 | 否 | 0 | Patch 非法 | `patch fragment without header at <stdin>:12` |
| 10 | pytest-dev/pytest #15133 | 命中 | 是 | 否 | 1 | 测试不过 | `recount+relaxed`；**F2P 2/2 全过**，卡 1 条 P2P 回归（连续两轮同一位置） |
| 11 | pytest-dev/pytest #15149 | 命中 | 是 | 否 | 0 | 方案错 | `recount`；F2P 0/2 |
| 12 | pytest-dev/pytest #15150 | 命中 | 否 | 否 | 0 | Patch 非法 | `corrupt patch at <stdin>:32` |
| 13 | fastapi/fastapi #16418 | 命中 | 否 | 否 | 0 | Patch 非法 | 检索引到 `_runtime.py`；diff `corrupt patch at <stdin>:18` |
| 14 | fastapi/fastapi #16468 | 排序 | 是 | 否 | 0 | 检索失败 | 预期 `_asgi.py` 未进结果，补丁 `recount` 打上，F2P 0/4 |
| 15 | fastapi/fastapi #16470 | 命中 | 是 | 否 | 0 | 方案错 | `recount`；F2P 0/8 |
| 16 | fastapi/fastapi #16484 | 命中 | 是 | 否 | 3 | 方案错 | `recount+relaxed`；F2P 0/6，另带 3 条 P2P 回归 |

### 4.1 汇总指标

| 指标 | 定义 | 值 |
|------|------|----|
| [M] | 判定计入的 issue 数（剔除生成器回退） | **16**（19 条有效池 − 3 条回退） |
| [I] | Patch 可应用率 = `git apply` 通过 / M | **6/16 = 37.5%**（⚠️ 条数与第二轮持平，百分比变化来自分母，见 §4.3） |
| [J] | 端到端通过率 = F2P 全过 且 无回归 / M | **0/16 = 0.0%** |
| 条件通过率 | F2P 全过 / 可应用 patch 数 | **0/6 = 0.0%** |
| 回归率 | 被搞挂的 PASS_TO_PASS 条数 / 可应用 patch 数 | **4/6 = 66.7%**（涉及 2 个 issue：`15133` 1 条 / `16484` 3 条） |
| F2P 单口径通过 | 只算 F2P 不看回归 | **1/16**（`15133` 2/2 全过） |
| 检索命中率 | 结果里出现预期源码文件 / M | **12/16 = 75.0%**（详见 §4.2） |
| [K] | 对比 baseline 提升百分点 | ⬜ 未做（baseline 尚未跑） |
| [L] | 失败归因条数 | **16**（检索失败 4 / Patch 非法 8 / 方案错 3 / 测试不过 1） |

### 4.2 检索命中率（诊断口径）

**为什么单列**：`[I]` 的变化同时受「检索给没给对文件」和「模型写得好不好」影响，两者不拆开就无法判断下一步该修哪边。

**判定方法**（`scripts/benchmark/judge.py::_retrieval_status`，工具链未随仓库公开、见 §7；判定规则如下，全部机械可复算）：

- 预期文件 = `candidates.json` 的 `src_files` 里检索覆盖得到的源码后缀（`.py/.ts/.tsx/.js/.jsx`），剔掉 changelog / docs / `.github/workflows`
- 检索结果 = 该 task 的 `CODE_SEARCHED` 事件 payload（run 记录只落状态，事件落库，用 `agent_run.task_id` 反查）
- 关键词 = 该 task 的 `REQUIREMENT_ANALYZED` 事件，**只取真正喂进检索的前 8 个**（超出预算的词从没被搜过，不能拿来证明「检索本可以命中」）
- ① 结果里出现预期文件 → **hit**；② 否则在 **base commit 树**上 `git grep` 预期文件，含任一被喂入关键词 → **ranking**；③ 一个都不含 → **structural**

| 检索态 | 条数 | issue | 含义 |
|--------|------|-------|------|
| hit | **12** | 13073 / 13745 / 14125 / 14463 / 15040 / 15058 / 15133 / 15149 / 15150 / 16418 / 16470 / 16484 | 结果里出现了预期文件 |
| ranking | **2** | 14331 / 16468 | 预期文件在 base 树含被喂入的关键词，但没进结果 |
| structural | **2** | 13370 / 14645 | 文件在 base 树一个都没含被喂入的词 → **词汇层无信号** |
| unknown | 0 | — | 取不到信号（不计入分母） |

**命中率 12/16 = 75.0%**（19 条池口径含回退条：13/19 = 68.4%，与离线复算逐条一致）。第二轮是 7/18 = 38.9%，排序问题从 8 条压到 2 条，结构性 3 条降到 2 条（`13073` 由 structural 转为 hit）。

**命中的 12 条里只有 4 条产出了可应用补丁**（`15133` / `15149` / `16470` / `16484`），另有 2 条补丁可应用但检索没命中（`14645` structural / `16468` ranking）—— 即「检索给了对文件」并未自动转化为「补丁能用」，见 §4.3 与 §6。

### 4.3 三轮对照（改词表 → 改检索算法）

| 指标 | 第一轮（原词表） | 第二轮（改词表） | 第三轮（排序键 + 去重） | 说明 |
|------|-----------------|-----------------|------------------------|------|
| M | 16 | 18 | 16 | 回退条数 3 → 1 → 3（`14670` / `14905` / `15032`） |
| 检索命中（19 条池） | 6/19 = 31.6% | 7/19 = 36.8% | **13/19 = 68.4%** | 第三轮净增 6 条，逐条见下 |
| 检索命中（M 口径） | 6/16 = 37.5% | 7/18 = 38.9% | **12/16 = 75.0%** | 排序 8 → 2，结构性 3 → 2 |
| [I] 可应用 | 3/16 = 18.8% | 6/18 = 33.3% | **6/16 = 37.5%** | 条数持平；与第二轮重叠仅 3 条（`15133` / `16470` / `16484`） |
| [J] 通过 | 0/16 = 0% | 0/18 = 0% | **0/16 = 0%** | 三轮全 0；`15133` F2P 连续两轮 2/2 全过，都卡 P2P 回归 |
| 回归条数 | 113（1 个 issue） | 12（3 个 issue） | 4（2 个 issue） | — |
| 归因 | Patch 非法 13 / 方案错 3 / 检索失败 0 / 测试不过 0 | 检索失败 11 / Patch 非法 4 / 方案错 2 / 测试不过 1 | 检索失败 4 / Patch 非法 8 / 方案错 3 / 测试不过 1 | 第一轮的「检索失败 0」是判定缺陷造成的，见 §6 |

第三轮新增命中的 6 条：`16418`（第 4 位）/ `16470`（第 5 位）/ `14125`（第 8 位）/ `13745`（第 5 位）/ `14463`（第 6 位）/ `13073`（第 4 位），原有命中无一条丢失。

> **结论一（检索侧成立）**：检索命中率从 38.9% 提到 75.0%，且与离线复算（停 `base_commit`，13/19）逐条一致 —— 说明「同文件多行重复占位」确是被压制的根因，排序键与去重两处改动都有效。这条是本轮唯一可以当因果引用的结论。
>
> **结论二（未传导到 [I]）**：可应用补丁仍是 6 条，与第二轮持平；百分比 33.3% → 37.5% 来自分母缩小（M 18 → 16，回退条数 1 → 3），**不是改进**。丢掉的是 `13073`（转 hit 但 diff `corrupt patch`）、`14463`（转 hit 但生成期路径不一致）、`15040`（hit 但 `corrupt patch`）；新上的是 `14645` / `15149` / `16468`。
>
> **结论三（瓶颈转移到生成层）**：19 条里有 13 条卡在「产不出可解析或可应用的 diff」——7 条 `git apply` 报 `corrupt patch` / `patch fragment without header`、3 条生成期被产品校验拒（`---`/`+++` 路径不一致）、3 条直接回退假生成器。检索阻断的只有 2 条（`13370` / `14331`）。下一瓶颈是模型产出 diff 的格式稳定性与方案质量，不再是检索。

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

> 🔴 **本节随每轮实测重写，旧数字作废。** 第一轮写的是「Patch 非法 13 / 方案错 3 / 检索失败 0 / 测试不过 0」，其中「检索失败 0」是**判定缺陷**：`judge.py` 原先只看「检索结果是否为空」，返回 8 条无关文件也算「检索没失败」，且 run 记录里本来就不存事件（事件在 `backend/codeforge.db` 的 `task_events` 表），于是永远判「无检索信号」。修法：改从事件表取 `CODE_SEARCHED`，按 base 树三态判定（§4.2），并给 `judge.py` 加 `--force` 重判（旧判定留档到 `judge_previous`）。下表为**第三轮**（2026-10-18，修排序 + 去重后）的归因。

| 类别 | 判定规则（基于事件日志 + base 树） | 条数 | 典型 case |
|------|-----------------------------------|------|-----------|
| 检索失败 · 排序 | 预期文件在 base 树含被喂入的关键词，但没进检索结果 | **2** | `14331`：`pathlib.py` 在 base 树含 `directories` / `currently` 等词，6 条结果仍被 `legacypath.py` 等占满；`16468`：预期 `telemetry/_asgi.py` 未进结果 |
| 检索失败 · 结构性 | 预期文件在 base 树**不含任何**被喂入的关键词 → 词汇层无信号 | **2** | `13370`（`python.py`）、`14645`（`main.py`）。后者补丁虽以 `relaxed` 打上，但改的不是预期文件 |
| Patch 非法 | 检索命中，但 diff 产不出来 / `git apply` 失败 / 改白名单测试 | **8** | 6 条 `git apply` 报 `corrupt patch` 或 `patch fragment without header`（`13073` / `14125` / `15040` / `15058` / `15150` / `16418`）；2 条在生成期就被产品校验拒（`Diff 的 --- 与 +++ 文件路径不一致`，`13745` / `14463`，diff 未落盘）。**这 8 条全部是格式层问题，与检索无关** |
| 方案错 | patch 可应用，但 FAIL_TO_PASS 未过 | **3** | `15149`（recount，F2P 0/2）、`16470`（recount，F2P 0/8）、`16484`（recount+relaxed，F2P 0/6，另带 3 条 P2P 回归） |
| 测试不过 | F2P 全过，但 PASS_TO_PASS 被搞挂 | **1** | **`15133`**：F2P 2/2 全过，1 条 P2P 回归 —— 连续两轮唯一一条把 F2P 打全绿的样本，差一步就是 `[J]` |

**归因优先级的口径说明**：沿用「根因在上游，下游补救不算数」——检索没给对文件，一律记检索失败。因此 `14645` / `16468` 两条虽然补丁能打上（打的是别的文件），也记检索失败而不是方案错。若改用「补丁能打上就算方案错」的口径，则检索失败 2 / 方案错 5，其余不变；本表采用前者，与代码里的既有优先级一致。

**另一个必须并存的口径**：上表是「归因」，不是「阻断分析」。按是否真正阻断修复看，检索只切断 2 条（`13370` / `14331`）——`14645` / `16468` 的补丁照样打上了，只是方案落空。第三轮的失败主体是生成层的 13 条（7 条 apply 失败 + 3 条生成期校验拒 + 3 条回退假生成器），不是检索。

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
- **生成器回退（FakePatchGenerator）**：回退条数三轮是 3 → 1 → 3（第三轮 `14670` / `14905` / `15032`）。同一 prompt、同一档位下这个数字在 1 与 3 之间摆动，本身是采样不稳定的直接证据。按红线剔除、不进 M。⚠️ 回退时原始响应未落盘（只存 sha256 摘要），无法进一步诊断是拒绝生成还是格式异常 —— 这是当前唯一的不可审计项。
- **第三轮 M 从 18 掉到 16**（回退 1 → 3 条），所以 `[I]` 的**百分比不可与第二轮直接比**：条数是 6 对 6 持平，33.3% → 37.5% 全部来自分母缩小。引用时必须同时给分母。
- **检索命中率与离线复算逐条一致**（19 条池 13/19 = 68.4%）：离线脚本停在各 issue 的 `base_commit` 上跑真实的 `run_code_search_node`，与生产链路 `CODE_SEARCHED` 事件逐条对得上（新增 6 条、原 7 条无丢失）。这是「树对齐」方法成立的正例，与 §9.1 第 3 条的作废教训互为对照。
- **失败已从检索前移到生成层**：第三轮 19 条里 13 条卡在「产不出可解析或可应用的 diff」（7 条 apply 失败 + 3 条生成期校验拒 + 3 条假生成器回退），检索真正阻断的只有 2 条。这意味着继续修检索的边际收益已经很低，下一步该动生成侧（模型档位或 prompt）。
- **难度分层（easy/medium/hard）未做** —— §5 的 Aider 分层抽样前提未就绪，故 [K] 仍为 ⬜。
- **判定脚本在本次跑批后修过两个缺陷**，修复前的判定结果已整体作废重跑（不是改数字，是判定逻辑本身错）：
  1. judge **从不贴 PR 的测试补丁** —— F2P 用例来自 PR 新增测试，不贴则 base/patched 两侧都「查无此例」，[J] 恒为 0；
  2. judge 读的是 `fail_to_pass`（选型期 suspected 列表，含 `test_a1` 这类噪声名），而硬确认回填的是 `fail_to_pass_tests`（实测 nodeid、带 `TestClass::` 前缀）。
  修复后 `[I]`/`[J]` 数字才成立；上表所有 `test_patch_applied` 均有落痕可复核。
- 本次仅测 1 个模型（deepseek-chat）。结果**不代表任务本身不可解**，只代表该模型在当前 prompt + 这套 issue 上的表现。

### 9.1 判定与复算的三处失真（均已定位，两处已修）

1. ✅ **检索归因的输入曾为空（已修）**。`run_agent.py` 只把状态落进 `runs/issue-*.json`，事件走 `backend/codeforge.db` 的 `task_events` 表，而旧 `_retrieval_missed()` 只在记录里找 `events` 字段 —— 结果永远是「无检索信号」，于是判出「检索失败 0」。这不是「检索都没问题」，是**判定没看到数据**。现改为从事件表按 `agent_run.task_id` 反查 `CODE_SEARCHED` / `REQUIREMENT_ANALYZED`，并按 §4.2 三态判定。
2. ✅ **重判会静默跳过（已修）**。`judge.py --all` 遇已判定的记录直接 `continue`，改完判定逻辑再跑 `--all` 会得到与旧结果完全相同的汇总。新增 `--force`：重判前把旧判定留档到 `judge_previous`，改了什么可逐条 diff。
3. ⚠️ **离线复算的树不对齐（方法已修正，历史数字作废）**。检索实验脚本原先搜的是**仓库当前 HEAD**，而真实跑批搜的是每条 issue 的 `base_commit`（`run_agent.align_repo_base()` 会 `git checkout -f`）。后果：拿 PR 之后的代码当「本可以命中的线索」，命中率虚高。据此得出的「只改词表能让命中率从 37.5% 升到 56%」**已作废**（那 3 条「救回」里 `14331` 是 HEAD 上的假命中）。现所有检索判定一律 `git grep <base_commit> --`，不读工作区。

### 9.2 统计效力与可复现性

- **可应用补丁条数三轮 3 / 6 / 6，第二与第三轮只重叠 3 条**（`15133` / `16470` / `16484`，§4.3）：`[I]` 的波动约 ±3 条，单次采样不足以支撑「改了什么 → 涨了多少」的因果结论。**`[I]` 只能当量级参考，不能当胜率引用。**
- **`temperature: 0.0` 不等于可复现**：`16470` 两轮检索上下文（8 条 `path:line`）逐字相同、补丁却不同，判定从「Patch 非法」变成「可应用」。这是纯采样噪声的直接证据，不是推测。
- **哪些结论可以报具体数字**：检索命中率可以（12/16，且 19 条池的 13/19 与离线复算逐条一致，方法可复现）；`[I]` 只报条数（6 条）与分母（16），不报「提升 4.2 个百分点」；`[J]` 三轮都是 0，只能报「链路尚未打通」，不报趋势。
