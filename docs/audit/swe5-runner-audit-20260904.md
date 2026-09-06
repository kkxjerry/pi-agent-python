# SWE-5 Harness 审计：本轮实际结果

日期：2026-09-04（America/Los_Angeles）

## 结论与范围

本轮完成了实际代码审查、故障复现、局部修复、两套真实 CLI 的离线 HTTP/工具恢复验证，以及本地主线回归。没有完成五题的真实 Qwen 配对实验。

正式 SWE Agent Run：本轮未启动，0/10。这里的 0 是“未运行”，不是“解题失败”。没有新的正式 SWE Patch、Grade、F2P/P2P 成绩或模型费用数据，不能给出 TS 与 Python 谁更能解决这五题的结论。

已连接 A40，确认五个镜像仍在 Podman，读取了既有 Gold/Empty 预检结果，五题均为 accepted。没有重跑 Gold/Empty，没有重拉镜像，没有使用本地 Docker，也没有更改五题选择。

本轮修改位于本地 develop 工作区，尚未提交，尚未同步 A40。服务器仍是原代码快照。不要把下面的修复状态理解为服务器已经部署完成。

## 题目难度核对

从既有预检的实例元数据读取到：

| 实例 | 数据集原始难度标签 |
|---|---|
| pytest-dev__pytest-6202 | <15 min fix |
| pytest-dev__pytest-7205 | <15 min fix |
| pytest-dev__pytest-8399 | 15 min - 1 hour |
| sympy__sympy-19637 | <15 min fix |
| sympy__sympy-14248 | 1-4 hours |

这些是人工修复时间标签，不是千问的成功率预测。14248 明显比另外四题重，涉及多个打印表现；按交接说明保留，不重新选题。

## 已确认的启动与实验环境问题

A40 新 Shell 的 DASHSCOPE_API_KEY 未设置。进一步的凭据检查被安全限制阻止，本轮没有绕过限制、读取密钥或进行付费模型调用。

交接命令的 `/root/pi-swe5/python-venv/bin/python` 没有 duckdb，读取 parquet 时直接失败。已验证 `/root/pi-swe5/meta-venv/bin/python` 有 duckdb 1.5.5；它才是当前可用的元数据 Runner 解释器。Runner 的宿主解释器与容器内 Agent 解释器不是一回事。

在 pytest-6202 现有镜像中实际执行了无网络、无凭据的运行时检查：Node 为 v24.15.0，官方 CLI 为 0.84.4，基础 Python 3.11.5 能导入 pi_agent；但基础 Python 执行 `-m pytest` 报 No module named pytest。`/opt/miniconda3/envs/testbed/bin/python` 则能加载实例的 pytest。

本地 Runner 已将两端的工具 PATH 统一为 testbed 环境优先，Agent 自身仍使用显式指定的基础 Python 或 Node。其余四题没有完成本轮新的运行时启动检查，不能把一个镜像的实测推广为五个都已重新验证。

## 已复现并修复的问题

### 1. Python 的输出上限没有真正进入请求

使用两套实际 CLI 连接脚本化 localhost SSE 服务，检查 HTTP 请求体。TS 的七次请求都有 4096 上限；修复前 Python 的七次请求均没有 max_tokens/max_completion_tokens，虽然 CLI 参数写着 --max-tokens 4096。

修复：OpenAICompatibleProvider 在调用未显式指定 max_tokens 时使用 Model.max_tokens，显式调用覆盖值仍优先。补充两个直接请求测试。修复后的真实 CLI 配对测试验证了两端七次请求均发送 4096。

位置：`src/pi_agent/ai/openai_compatible.py`，`tests/ai/test_openai_compatible.py`。

### 2. 大 JSON 事件会导致 Trace 静默丢失

原 capture_agent 使用 asyncio StreamReader.readline 的默认单行限制。100KB 的合法 JSON 事件触发读取异常，而 gather(return_exceptions=True) 的返回异常被丢弃。复现中进程正常退出，但事件列表为空。

修复：按字节块读取并自行分行，记录采集异常，保留停止任务并等待清理，避免停止与重启竞争。为未结束的超大行设置显式安全限制。新增大事件、读取器故障、超时留痕测试。

这修复的是采集层，不代表 Python 的重复事件与消息快照膨胀已优化。

### 3. 评分异常会丢掉此前已经完成的 Agent Trace

原顺序是 Agent 完成、导出 Patch、评分完成，最后才写 Trace。评分抛异常时，最需要调查的 Trace 尚未落盘。

修复：采集返回后立即保存 Trace，再导出 Patch 和评分。故障注入测试确认评分抛异常后仍有 capture.json 和 jsonl。

边界：尚不是逐事件持续落盘，宿主进程在采集过程中崩溃仍可能丢失内存中的 Trace。

### 4. 评分依赖 Agent 改过的容器状态

原 run_agent 直接在 Agent 工作容器执行评分，可能把未包含在 Patch 中的环境、Git 索引或其他状态一起带进评分。

修复：用新容器应用导出的候选 Patch 后评分。测试确认走 grade_fresh_container，而非复用 Agent 容器。

此处有本地调用链回归证据，没有重新跑真实 SWE 评分容器，因此不宣称新版端到端评分已实测通过。

### 5. Agent 自行 commit 后可能导出空 Patch

原导出使用 git diff HEAD。Agent 修改源码后自行提交，HEAD 移动，diff 就可能为空。

修复：明确对数据集 base_commit 导出差异，Git 错误不再静默当作空结果。真实临时 Git 仓库测试验证：修复提交之后 git diff HEAD 为空，而新版仍能导出源码变化。

### 6. 输入预算漏算缓存 Token

pi 的 usage.input 不包含 cacheRead/cacheWrite。原 Runner 只累加 input，因此缓存命中的输入既漏记，也不占输入预算。

修复：输入预算和输入统计包含 input + cacheRead + cacheWrite，并额外保留缓存分项。测试中 1 个普通输入 Token 加 200 个缓存 Token，能够触发 100 Token 上限。

这不是严格请求准入预算的完整修复，见后面的阻塞项。

### 7. 不完整测试 ID 的别名匹配可能误判 P2P

原逻辑用“是否属于 PASSING”的布尔值判断多个候选是否一致。SKIPPED 和 FAILED 都映射为 False，可能被当成一致，再返回第一个 SKIPPED，进而误认为回归保持。

修复：只有候选实际状态完全一致才接受别名。SKIPPED/FAILED 混合结果返回未知，而非通过。

没有随意更改既有 SKIPPED、XFAIL 评分约定；修复的是歧义匹配。

### 8. 第一处分叉会漏掉真正不同的操作

原 action_sequence 把所有测试命令压成 bash:test，read/edit 只保留路径。读同一文件不同范围、运行不同测试、往同一文件写不同补丁，都可能被视为相同动作。

修复：比较工具名称及完整、稳定序列化的参数。三个回归用例分别覆盖测试命令、读取范围和编辑内容差异。

这提供参数级分叉，不等同于已经完成模型决策的因果归因；工具返回内容和后续推理仍需结合原始 Trace 分析。

### 9. Provider 错误可能被统计为进程健康

原 process_ok 在退出码为 0 且有 assistant message 时可能为 True，即便最后的 stopReason 是 error。

修复：采集错误、非法输出行、error/aborted 结束均不再视为健康完成。Patch 是否正确与进程是否健康继续分开记录。

### 10. thinking off 需要在请求体中验证

原配置没有确保请求显式带 enable_thinking=false。新增通用 CLI --sampling-params，转发已有的 Model.sampling_params 能力；TS 模型配置同样写 samplingParams。双方显式传 --thinking off。

离线真实 CLI 测试验证七轮请求均带 enable_thinking=false。没有把 Qwen 专属策略塞进 Agent Core。

## 两套真实 CLI 的离线恢复测试

使用实际安装的官方 0.84.4 CLI 和当前 Python CLI。模型端是本地脚本化 SSE 服务，不访问 DashScope；没有真实 API Key，Token usage 是夹具生成的数据。

固定执行路径：读取源码 → 运行失败基线 → 故意失败的精确编辑 → 重新读取 → 正确编辑 → 验证通过 → 结束。

| 项目 | TypeScript | Python |
|---|---:|---:|
| HTTP 请求数 | 7 | 7 |
| 工具调用数 | 6 | 6 |
| 被正确标记的工具错误 | 2 | 2 |
| 失败基线及退出码进入下一轮请求 | 是 | 是 |
| 失败编辑进入下一轮请求 | 是 | 是 |
| 最终源码通过验证 | 是 | 是 |
| 测试文件未被更改 | 是 | 是 |
| 每次请求发送输出上限 4096 | 是 | 是 |
| 每次请求显式关闭 thinking | 是 | 是 |
| 观测事件数 | 83 | 96 |
| 一次通过记录的 Trace 字节数 | 38,385 | 68,092 |

Python Trace 在这条固定路径中约为 TS 的 1.77 倍。字节数包含临时路径、时间等运行数据，不应当成稳定性能指标。没有由这个测试推断谁更会定位真实 SWE Bug；动作是脚本指定的，测的是执行、传输和恢复链路。

测试文件：`tests/benchmarks/test_swe_offline_cli.py`。原始合成 Trace 由测试保存在临时目录，未提交仓库。

## 测试记录

最初给正式 Runner 新增的 12 个针对性用例，在修复前为 11 failed、1 passed。这不是“11 个独立产品 Bug”，其中三个用例验证同一个参数比较问题。

扩充后，正式 Runner 专项为 19 passed。另有一个实际双 CLI 离线配对测试通过，两个 Provider 输出上限测试通过。

最终执行结果：

- phase 0–3 gate：通过。该门禁检查已有基线/场景元数据，不代表本轮重新执行了 20 个上游 golden 场景。
- 完整 pytest：265 passed，3 failed。三个失败均来自未修改的 `tests/benchmarks/test_swe5.py`，它加载被弃用的 `benchmarks/swe5/` 草稿，动态导入未注册 sys.modules 使 dataclass 初始化失败。
- 显式排除上述草稿测试：265 passed。
- 正式 agent_compare、src 和本轮新增/修改测试的 ruff：通过。
- 相同范围格式检查：141 files already formatted。
- mypy：124 个源码文件无问题。
- 全仓 ruff：45 个错误，集中于未修改的 `benchmarks/swe5/` 草稿；全仓格式检查有 1 个草稿文件不合规。没有删掉或顺手改写这些已有文件，也没有宣称全仓全绿。

复验命令：

```bash
python3.11 scripts/check_phase_0_3.py
uv run --python 3.11 pytest -q --tb=short
uv run --python 3.11 pytest -q --ignore=tests/benchmarks/test_swe5.py --tb=short
uv run --python 3.11 ruff check benchmarks/agent_compare src tests/benchmarks/test_swe_run.py tests/benchmarks/test_swe_offline_cli.py tests/ai/test_openai_compatible.py
uv run --python 3.11 mypy
```

## 正式实验前仍必须收口

首先是凭据与版本：在真正执行实验的进程中安全提供凭据，提交并同步已验证代码。当前服务器仍是旧快照，不应直接照原交接命令启动，也不能把已修改版本继续标成原始 b914f4a。新版 metadata 增加源码、Runner、Suite 的 SHA-256，辅助确认实际版本。

其次是严格预算：BudgetTracker 仍在消息或工具事件到达之后判断超限。它不是模型请求/工具执行之前的准入门禁，不能保证最多恰好 15 次模型调用或 60 次工具调用；输入/输出 Token 也通常在响应结束后才知道。修复缓存计数并不解决这个问题。正式宣称“相同硬预算”前需要对两端统一实现请求与工具准入，处理最后一次允许响应、工具批次、Provider 重试及压缩调用。当前 metadata 已明确标注 post-event observer，不伪装成硬限制。

最后是行为判读：baseline_test_before_edit、verification_test_after_edit、repeated_reads 仍含启发式。通过 bash 写文件、运行自定义复现脚本、先验证后再次编辑，以及同文件分段读取，都可能使粗统计失真。不能只凭布尔字段判定“最终补丁已验证”或“无效重复读取”；每题仍须对照完整工具参数、返回结果、最后一次修改和导出的 Patch。

完成这些条件后，保持原五题、两端交替、每题一次、同模型、同预算，再产生正式 F2P/P2P、Token、耗时和首处分叉报告。此次五题设计是配对 Harness 诊断，不是整体 SWE-bench 排名，也不是统计显著性结论。
