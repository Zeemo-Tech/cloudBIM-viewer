# 多智能体子智能体生命周期策略：复用会话还是一次性创建

研究日期：2026-09-07。范围：OpenAI/Codex 官方文档、当前 Codex 运行时契约与本机配置，以及非 OpenAI 的官方文档、维护者文章、维护者社区答复和公开论文。面向编码代理团队。

## 结论摘要

**有证据的结论：**“同一模型权重”不会因多次调用而被这支子智能体的对话改写；实际会变的是每次推理送入模型的会话上下文、外部状态，以及可能持续存活的执行进程。长上下文并非只是在到达窗口上限时才出问题：官方工程文章与公开实验均指出，冗余/过时内容会降低注意力聚焦和长距离检索的准确性，且成本、延迟会上升。[Anthropic 的上下文工程文章](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)；[Liu et al., *Lost in the Middle*](https://arxiv.org/abs/2307.03172)

因此，**长期复用会话可能表现变差，但原因是上下文污染、任务目标漂移或状态污染，不是“子智能体用久了把模型权重练坏”**。对同一目标、同一工作树、需要追踪中间决策的连续工作，应复用受预算约束的会话；对独立任务、权限/租户/仓库切换、需要无偏复核或出现上下文失配时，应新开一次性子智能体。把可复用知识提炼为结构化交接物，而不是把完整聊天记录带到新任务中。

**推断（工程政策）：**把“代理身份/工具进程”与“会话记忆”拆开管理。复用身份或温沙箱可以减少重启成本，但每个新的独立任务仍应使用新的线程/会话 ID，按需检索少量经过验证的项目记忆。该政策是对下列框架边界与长上下文证据的综合推断，不是任何一家发布的统一阈值。

## 对当前 Codex 策略的诊断

OpenAI 官方把子智能体的主要价值定义为并行处理独立、有边界的工作，并让每个子智能体保有自己的聚焦上下文；对于强顺序依赖、小任务或共享可变状态，则建议使用单一代理。[OpenAI Multi-agent](https://developers.openai.com/api/docs/guides/responses-multi-agent) Codex 文档还明确承认，聊天被探索日志和工具输出填满后可能出现 context pollution / context rot，并支持要求 Codex 关闭已完成的 agent thread。[OpenAI Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)

本机运行时契约比公开文档更具体：

- `send_input` 仅在新任务高度依赖该子智能体上一任务的上下文时建议复用；
- `close_agent` 明确说明已完成但未关闭的子智能体仍占用并发槽；
- `resume_agent` 可以恢复已关闭的子智能体，因此“及时关闭”不是不可逆删除。

当前 `/home/hong/.codex/config.toml` 把子智能体并发上限设为 3，默认模型/强度是 `gpt-5.6-terra` / `medium`。当前 `/home/hong/.codex/AGENTS.md` 已规定角色、模型、并发与写入隔离，却没有规定：什么叫“高度依赖”、结果消费后何时关闭、何时必须使用干净上下文、等待超时后如何止损。这些空白足以让调度器把“相关问题优先复用”泛化成“同一领域一直复用”。这是对本机规则和工具契约的诊断，不是对模型内部实现的断言。

本次调研也提供了一个小型运行观察：Sol/High 规划员在多次等待并收到一次收束指令后仍未返回，最终被关闭；随后 Terra/Medium 研究员在单一写入边界内完成了研究稿。单次观察不能证明某个模型普遍更优，但它说明“所有复杂任务都先等待规划员”会把本来可并行的流程变成阻塞链，且必须有超时后的退出规则。

## 概念区分

| 概念 | 是什么 | 会随复用改变吗 | 生命周期决策含义 |
| --- | --- | --- | --- |
| 模型权重 | 供应商训练完成的参数；常规 API/推理调用不对其在线训练 | 通常不会 | 不能把质量退化归因为“模型被聊天训练坏”；先检查输入与状态。 |
| 会话上下文 | 此轮随请求进入模型的消息、系统指令、工具结果、检索内容、摘要 | 会；可能增长、被裁剪或压缩 | 这是长会话质量退化的主要风险面，按 token/相关性管理。 |
| 进程 / agent identity | 有名字、配置、工具句柄的代理对象，或存活的解释器/沙箱 | 可能保存内部状态、文件、变量、连接 | 可为连续工具操作复用，但必须隔离任务、租户和权限，并可重置/关闭。 |
| 缓存 | KV cache、提示词缓存、检索缓存、构建缓存等优化层 | 可命中、过期或失效 | 缓存命中不等于继承对话；要区分性能优化与语义状态，并给缓存设失效条件。 |

框架层面的佐证：AutoGen 将 agent 定义为跨调用保持状态的对象，并要求调用方只传递新增消息；其 `AssistantAgent` 保存的是模型上下文。[AutoGen agent API](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.agents.html)；[AutoGen 状态管理](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/state.html) LangGraph 则把短期记忆明确建模为 thread-scoped state，把跨会话记忆放在独立 store/namespace。[LangGraph Memory overview](https://docs.langchain.com/oss/python/concepts/memory)

## 证据表

证据等级：A = 一手官方文档或维护者工程文章；B = 同行评审/公开预印本实验；C = 基于 A/B 的团队工程推断。链接均为原始发布者页面。

| 主张 | 证据 | 来源 | 等级 |
| --- | --- | --- | --- |
| 过长或陈旧的会话历史可使模型分心；即使窗口能容纳，长上下文仍可能降低质量并增加延迟/成本。 | 文档直接说明长对话会使模型被 stale/off-topic 内容分心，且变慢、变贵。 | [LangGraph: Memory overview](https://docs.langchain.com/oss/python/concepts/memory) | A |
| 长上下文性能是渐变而非硬阈值，检索与长距离推理的精度可能下降；应采用压缩、结构化笔记或子智能体隔离。 | 维护者文章给出机制解释及 compaction、note-taking、sub-agent 三种策略。 | [Anthropic: Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | A |
| 相关证据不在上下文首尾时，长上下文模型的 QA / key-value retrieval 表现可显著下降。 | 论文在多文档问答和键值检索中测试位置变化，报告“lost in the middle”。 | [Liu et al. (2023)](https://arxiv.org/abs/2307.03172) | B |
| “复用线程”与“跨线程复用知识”应是两条机制：前者保留本线程状态，后者放入显式存储。 | 短期记忆是 thread-scoped checkpoints；跨线程需要 store，checkpointer 本身不能共享。 | [LangGraph: Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | A |
| 代理实例可以是有状态的，必须提供 reset 语义；新调用应只给新增消息。 | AutoGen `BaseChatAgent` 的 API 说明 agent 跨调用保存状态，并要求实现 `on_reset()`。 | [AutoGen: Agent API](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.agents.html) | A |
| 状态可保存并加载到新的代理实例，因此“身份存活”不是延续任务记忆的必要条件。 | AutoGen 示例将 `AssistantAgent` state 保存后载入新实例继续回答；其 state 是 model context。 | [AutoGen: Managing State](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/state.html) | A |
| 子智能体可以采用“默认新实例，明确续作才恢复”的生命周期。 | Claude Code 每次调用默认创建干净实例；resume 会带回完整历史，并为 transcript 设置清理周期。 | [Claude Code: Subagents](https://code.claude.com/docs/en/sub-agents) | A |
| 会话具有明确的创建、关闭和删除边界。 | ADK `BaseSessionService` 提供 create/get/list/append、`closeSession` 与 `deleteSession`。 | [Google ADK: BaseSessionService](https://google.github.io/adk-docs/api-reference/java/com/google/adk/sessions/BaseSessionService.html) | A |
| 进程级状态适合单一多步任务，但任务/对话结束后应显式删除或依 TTL 清理。 | ADK 代码执行沙箱在 workflow session 内保存变量、模块和文件；文档要求结束后显式删除或依 TTL。 | [Google ADK: code-execution sandbox](https://google.github.io/adk-docs/tools/google-cloud/code-exec-agent-engine/) | A |

## 社区 / 框架经验

### Anthropic（维护者工程经验）

Anthropic 将上下文视为有限资源，并明确描述“context pollution”；其建议把临近上限的会话压缩后在新窗口重新开始。对复杂研究/分析，它建议用具有干净上下文窗口的专门子智能体，主代理只接收浓缩结果。[原文：compaction 与 sub-agent architectures](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 这直接支持“跨任务新开、以摘要交接”，但不意味着每个小步骤都应新开。

### LangGraph / LangChain（框架状态模型）

LangGraph 将 thread 作为持久会话容器：每次 run 读取并更新该 thread 的 state；assistant 的配置与 thread state 分离。[LangSmith threads 文档](https://docs.langchain.com/langsmith/use-threads) 它同时把 long-term memory 放入可自定义 namespace，而非把所有历史塞回会话。[Memory overview](https://docs.langchain.com/oss/python/concepts/memory) 对编码团队的含义是：任务续作使用同一 `thread_id`，项目事实/偏好使用带来源与失效时间的 store。

### Microsoft AutoGen（框架状态模型）

AutoGen 的 chat agent 是有状态对象，提供 reset、save 和 load 的接口。[Agent API](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.agents.html) 因而“新开任务”不必重新配置全部能力：可创建新实例或 reset 旧实例，再仅加载经过筛选的状态。不要把整个旧消息列表作为新任务输入；这也符合其“每次仅传新消息”的调用约定。

### Google ADK（会话和运行时资源）

ADK 的 SessionService 把 session 的创建、关闭、删除做成显式操作。[BaseSessionService](https://google.github.io/adk-docs/api-reference/java/com/google/adk/sessions/BaseSessionService.html) 其代码执行沙箱则在一个 workflow session 内保留变量/文件，但文档明确给出任务结束后的删除或 TTL 清理路径。[sandbox lifecycle](https://google.github.io/adk-docs/tools/google-cloud/code-exec-agent-engine/) 这说明会话语义和计算资源语义都需要生命周期所有者，不能只依赖“下次还在”。

### Claude Code（可直接借鉴的默认值）

Claude Code 的规则很接近适合编码团队的默认策略：每次子智能体调用创建一个干净实例；只有需要继续同一工作时才按 agent ID 恢复，而恢复会带回完整对话、工具调用和结果；transcript 与主会话分开保存，并有自动清理周期。[Claude Code Subagents](https://code.claude.com/docs/en/sub-agents) 这同时保留了独立任务的干净上下文和连续任务的低重启成本。

维护者社区里的问题也反复显示，用户容易混淆 agent identity、session state 和 conversation history。例如 AutoGen 维护者把 component config 与 state save/load 明确区分；Google ADK 维护者说明同一 session 的状态持久化与新 agent execution 是不同边界。[AutoGen discussion #6005](https://github.com/microsoft/autogen/discussions/6005)；[Google ADK discussion #4042](https://github.com/google/adk-python/discussions/4042) 这些讨论支持把配置复用、会话续作和执行实例分别建模。

## 可执行决策矩阵（编码代理团队）

| 情形 | 会话 / 子智能体选择 | 状态与交接 | 关闭动作 |
| --- | --- | --- | --- |
| 同一 issue、同一分支、连续调试或实现，下一步依赖未提交的中间发现 | **复用同一会话** | 每个里程碑写结构化摘要：目标、已证实事实、改动、测试、未决项；只保留仍相关的工具输出 | 任务完成后保存最终交接，reset/关闭会话；停止临时进程。 |
| 同一项目但独立模块/独立验收问题，或需要独立复核 | **新开一次性子智能体** | 传入 issue、明确文件范围、验收标准与最小必要摘要；不要传完整旧对话 | 回传证据摘要后关闭。 |
| 新仓库、分支、租户、权限级别、数据分类或工具凭据变化 | **必须新开并隔离** | 不继承敏感上下文、工作目录状态或凭据；仅迁移经审查的非敏感事实 | 立即关闭旧会话；撤销临时凭据并清理沙箱/工作树。 |
| 用户回来继续同一目标，但间隔后代码/需求可能改变 | **先恢复，后验证是否续用** | 读取摘要和当前 git/任务状态；若关键假设失效则新开，并把旧会话作为只读证据 | 旧会话归档；不要静默把旧结论当事实。 |
| 令牌预算高、摘要遗漏增多、开始引用无关历史、目标反复重述或工具行动循环 | **压缩后续用；若仍失配则新开** | 生成可审计摘要并在新上下文验证关键约束；按需检索原始证据 | 对失配会话 reset/close，保留摘要和可追溯链接。 |
| 多步代码运行/数据处理，需要变量、依赖或生成物连续存在 | **可复用受 TTL 限制的执行进程**，会话仍按任务隔离 | 显式记录环境、输入、输出及清理责任人 | 成功、失败、取消、空闲 TTL、权限变更任一触发即关闭/删除。 |

### 建议值 / 需评测的团队阈值

以下数字**不是跨模型统一证据**，应在本团队模型、仓库和任务集上校准，并记录任务成功率、重工率、token、时延和安全事件。

| 触发器 | 初始建议值 | 动作 |
| --- | --- | --- |
| 上下文预算 | 到模型有效窗口的约 50–70%，或每个逻辑里程碑 | 产出结构化摘要，删除可再检索的原始工具噪声；必要时新窗口继续。 |
| 相关性审计 | 连续 2 个回合无法指出当前输入中哪条历史事实仍支撑下一步 | 停止追加历史，重新检索需求/代码，压缩或新开。 |
| 任务边界 | 目标、责任模块、分支、权限或验收标准任一发生实质变化 | 新开会话；只传最小批准的交接包。 |
| 温进程 TTL | 任务结束立即清理；若需复用，空闲 15–30 分钟后清理 | 保留可复建的工件，不保留未审计的运行态。 |
| 质量门 | 压缩或恢复后先做 1 个关键事实回归（如文件状态、测试/需求断言） | 回归失败则舍弃会话上下文，改为新会话。 |

## 反模式

- **把任务串成一条无限聊天。** 这会把旧工具输出、失败假设和临时指令变成干扰；窗口未溢出也不代表准确。
- **把“新 agent”误认为“新模型”。** 新实例通常仍使用同一模型权重；隔离效果来自新上下文、状态和资源边界。
- **跨任务复制全部 transcript。** 应传有来源、可验证、带未决项的交接摘要，原始记录留作按需检索。
- **复用带权限或文件状态的温进程而没有 TTL/清理人。** 这会扩大状态泄漏与陈旧依赖风险；ADK 的沙箱文档也将删除/TTL 作为结束路径。[Google ADK](https://google.github.io/adk-docs/tools/google-cloud/code-exec-agent-engine/)
- **只按 token 数切换。** 位置偏差、过期假设和目标漂移可在低 token 时发生；同时监控相关性与任务边界。
- **把摘要当作事实数据库。** 摘要会遗漏或过期；关键结论须回链到 issue、代码、测试、文档或可复现命令。

## 建议写入当前 `AGENTS.md` 的生命周期规则

下面是可直接吸收的政策草案；其中“一次收束指令”是本团队的建议值，应通过实际任务评测调整。

```markdown
### Subagent lifecycle

- Treat one subagent thread as one bounded mission. Create a fresh subagent for
  an independent task, a changed role, a changed repository/branch/permission
  boundary, or independent verification.
- Reuse an existing subagent only when the follow-up directly depends on that
  agent's private context from the same unresolved workstream. Do not reuse a
  completed agent merely because the topic or repository is similar.
- Prefer reusable agent configurations and fresh task threads. Pass a compact
  handoff containing the objective, verified facts, changed files, validation,
  and unresolved items instead of the full transcript.
- After the root has consumed a final result, close the subagent immediately.
  Closing is the default retirement action; resume a closed agent only for a
  directly related follow-up whose saved context is still valid.
- Keep independent reviewers context-clean. Never ask the implementation agent
  to perform the final independent verification of its own work.
- Do not delegate the next critical-path blocker. While a subagent runs, the
  root must have useful non-overlapping work; otherwise handle the step locally.
- Wait only when the root is blocked on the result. After a timeout, send at
  most one explicit instruction to stop expanding and return current evidence;
  if it still does not finish within a short grace period, close it and continue
  in the root or report the missing evidence.
- In the final response, report every spawned subagent's role, requested
  model/effort, outcome, and whether it was closed.
```

现有“复杂任务先启动一个规划员”的规则建议改成条件式：只有当规划本身存在重大歧义、可能改变工作流拆分，而且根节点在规划期间有非重叠工作可做时才派规划员。否则由根节点先做轻量拆解，再把真正独立的侧任务并行分派。

## 证据限制

1. 公开资料没有给出跨厂商、跨模型均有效的“复用 N 轮后必然退化”的数字阈值；本文的 50–70%、15–30 分钟等均为**建议值/需评测**。
2. *Lost in the Middle* 测的是长输入中的检索/问答位置效应，并不直接测“子智能体连续执行编码任务”的端到端成功率；它支持风险机理，不能单独决定调度策略。
3. 官方框架文档说明生命周期 API 和推荐模型，但不是在同一基准上比较不同框架的质量。
4. 提示词缓存、KV cache 与服务端会话实现细节会随模型供应商和部署方式变化；未经运行时可观测性验证，不应从缓存命中推断历史语义被完整继承。

## 来源链接与类别

1. OpenAI 官方文档：[Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
2. OpenAI 官方文档：[Responses API Multi-agent](https://developers.openai.com/api/docs/guides/responses-multi-agent)
3. OpenAI 官方文档：[Compaction](https://developers.openai.com/api/docs/guides/compaction)
4. 官方维护者工程文章（Anthropic）：[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
5. 官方产品文档（Anthropic）：[Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)
6. 官方框架文档（LangGraph）：[Memory overview](https://docs.langchain.com/oss/python/concepts/memory)
7. 官方框架文档（LangGraph）：[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
8. 官方框架文档（LangSmith/LangGraph）：[Use threads](https://docs.langchain.com/langsmith/use-threads)
9. 官方框架文档（Microsoft AutoGen）：[Agent API](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.agents.html)
10. 官方框架文档（Microsoft AutoGen）：[Managing State](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/state.html)
11. 维护者社区答复（Microsoft AutoGen）：[Discussion #6005](https://github.com/microsoft/autogen/discussions/6005)
12. 官方框架 API 文档（Google ADK）：[BaseSessionService](https://google.github.io/adk-docs/api-reference/java/com/google/adk/sessions/BaseSessionService.html)
13. 官方框架文档（Google ADK）：[Agent Runtime Code Execution](https://google.github.io/adk-docs/tools/google-cloud/code-exec-agent-engine/)
14. 维护者社区讨论（Google ADK）：[Discussion #4042](https://github.com/google/adk-python/discussions/4042)
15. 公开论文：[Liu et al., *Lost in the Middle: How Language Models Use Long Contexts*](https://arxiv.org/abs/2307.03172)
