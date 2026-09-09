# 父/子智能体通信与编码角色设计研究（2026-09-07）

## 结论先行

**默认采用 hub-and-spoke（父代理为唯一协调者），不用自由群聊。** 父代理拆分、派发、汇总和决定下一步；子代理仅向父代理返回精炼结果，除非任务契约明确允许一个受限的点对点交接。该选择不是说群聊永远错误：需要多个代理与用户直接对话、实时协商或共同探索时可以使用 handoff/群聊；但编码与研究的常见目标是可控的上下文、权限和写入边界，中心调度更合适。LangGraph 将 subagents 定义为“所有路由经由主代理”的模式，并将 handoff 用于需要直接用户交互的情形；Anthropic 的生产研究系统也由 lead 协调专项子代理并只接收压缩结果。[LangChain Subagents 文档](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents) [Anthropic：多智能体研究系统](https://www.anthropic.com/engineering/multi-agent-research-system)

下列“应当”是团队针对编码代理的**启发式**，不是任何框架的强制协议；“事实”和“维护者经验”另行标注。

## 证据与适用边界

### 事实：为什么中心路由、过滤和工件引用能降低废话

- **事实（Anthropic 工程文章）**：长任务的上下文是有限资源；专项子代理可用干净窗口进行深度探索，父代理只接收压缩摘要（文中举例常为 1,000–2,000 tokens）。该架构把细节搜索上下文隔离在子代理，父代理聚焦综合。[有效上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- **事实（Anthropic 工程文章）**：让子代理把代码、报告、可视化等产物持久化到外部系统，再仅把轻量引用交给 lead，可减少逐层转述造成的 token 开销和失真（“传话游戏”）。[多智能体研究系统：artifact 模式](https://www.anthropic.com/engineering/multi-agent-research-system)
- **事实（LangChain/LangGraph）**：多智能体的核心是“每个代理看见什么”的上下文工程；subagents、router、handoff 是不同拓扑，不能把所有需求都套进群聊。[LangChain multi-agent 文档](https://docs.langchain.com/oss/python/langchain/multi-agent/index)
- **事实（AutoGen）**：GraphFlow 的执行图不等于消息图；默认会把消息发送给图中的所有代理。`MessageFilterAgent` 可按来源及首/尾 N 条消息过滤，以减少幻觉、记忆负荷并聚焦相关信息。[AutoGen GraphFlow 与消息过滤](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html) [MessageFilterAgent API](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.agents.html)
- **事实（Google A2A）**：协议把 `Task`、`Message`、`Artifact`、`TaskStatus`、`AgentCard` 分为独立对象，并定义任务状态、历史长度、结构化数据和工件更新；这支持将控制消息和大结果分离，而不是把所有内容拼进聊天记录。[A2A v1 规范](https://a2a-protocol.org/latest/specification/) [ADK 的 A2A 集成](https://adk.dev/a2a/)
- **事实（A2A）**：Agent Card 公开代理的能力、技能、接口和安全信息；这可作为跨进程/跨团队代理选择的机器可读能力合同，但不替代运行时最小权限控制。[A2A Agent Card](https://a2a-protocol.org/latest/specification/#agent-card)

### 维护者经验：群聊的上下文成本是真实风险

AutoGen 维护者 Eric Zhu 在官方讨论中说明，当时的 GroupChat 会在参与者间共享消息历史，并建议该提问者考虑 nested/sequential chats；这是历史实现与维护者建议，**不是通用理论或现行 API 保证**。它与当前 AutoGen 文档中“默认向图中所有代理发送消息、应使用消息过滤”的事实相互印证。[AutoGen Discussion #1877](https://github.com/microsoft/autogen/discussions/1877) [当前 GraphFlow 文档](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html)

## 推荐通信契约

### 父 → 子：最小任务信封

使用结构化 JSON（机器校验、路由、审计）承载固定字段；`brief` 可为短 Markdown，便于人读。字段只传递对子任务必要的信息。

```json
{
  "task_id": "T-042",
  "role": "test-investigator",
  "objective": "定位 API 回归的最小复现与根因",
  "scope": {"include": ["backend/api/**"], "exclude": ["frontend/**"]},
  "acceptance": ["给出可复现命令", "指出证据位置"],
  "constraints": ["只读；不得启动服务；不改文件"],
  "inputs": [{"kind": "path", "ref": "docs/issue-42.md"}],
  "budget": {"max_turns": 8, "max_result_tokens": 700},
  "return_schema": "result-envelope/v1"
}
```

必填最小字段为：`task_id`、`objective`、`scope`、`acceptance`、`constraints/authority`、`inputs`、`budget`。`role` 和 `return_schema` 在同类任务可由父代理默认填充，但跨代理或可审计任务中应显式发送。`scope` 必须同时说明允许与禁止的路径/系统；不要用“检查一下 X”“必要时改点东西”这种授权不清的委派。

### 子 → 父：最小结果信封

```json
{
  "task_id": "T-042",
  "status": "completed",
  "answer": "根因为序列化器遗漏 nullable 字段；复现已确认。",
  "evidence": [{"claim": "字段遗漏", "ref": "backend/api/serializer.go:88"}],
  "artifacts": [{"kind": "patch", "ref": "artifacts/T-042.patch", "summary": "仅 serializer 测试与修复"}],
  "changed": ["backend/api/serializer.go"],
  "validation": [{"command": "go test ./backend/api", "result": "pass"}],
  "risks_or_blockers": [],
  "next_action": "父代理决定是否合并"
}
```

必填最小字段为：`task_id`、`status`、`answer`、`evidence`、`artifacts`、`validation`、`risks_or_blockers`。若未改动，`changed` 为 `[]`；若失败或需要授权，`status` 应为 `blocked` 或 `needs_decision`，并在 `risks_or_blockers` 写明唯一可执行的父代理动作。不要回传过程日记、完整工具输出或思维链。

### 哪些内容只放工件/路径，不进消息

默认将下列内容持久化为工件，并在信封中只给路径、摘要、类型和（必要时）hash：完整 diff/补丁、源文件全文、测试日志、编译输出、截图、数据集、检索摘录、长表格、堆栈跟踪、生成的文档与设计稿。消息只保留能让父代理决定“接受、追问、重派、合并、终止”的结论与证据定位。

例外是：父代理无法或无权读取工件、工件本身不可信、或一小段原文是判断安全/正确性的必要证据时，可在消息中放最短片段并附路径。这个“引用优先”原则直接对应 Anthropic 的 artifact 模式与 A2A 将 Artifact 同 Message 分开的数据模型。[Anthropic artifact 模式](https://www.anthropic.com/engineering/multi-agent-research-system) [A2A Messages and Artifacts](https://a2a-protocol.org/latest/specification/#messages-and-artifacts)

### 格式与长度上限

- **团队启发式**：控制面采用 JSON Schema/Pydantic/Zod 等可验证结构；给人阅读的 `answer`、`summary`、阻塞说明可使用 Markdown，但不得藏入未声明字段或覆盖结构化状态。
- **团队启发式**：父→子初始信封默认不超过 **1,200 tokens**，子→父结果默认不超过 **800 tokens**；研究型子任务可上调至 **1,500 tokens**，但必须有摘要和工件引用。上限是预算而非质量指标：证据、风险和验证优先于礼貌语与步骤复述。
- **事实支撑**：AutoGen 支持按来源与首/尾 N 条过滤消息，A2A 将 history length 和结构化数据交换作为协议概念；因此可实施字段白名单、N 条窗口和 schema 校验，而不依赖提示词“请简短”。[AutoGen MessageFilterAgent](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.agents.html) [A2A History Length Semantics](https://a2a-protocol.org/latest/specification/#history-length-semantics)

## 编码代理角色设计

角色不能只写一个富有文采的“人设”。每个角色须由以下四个独立维度定义，父代理根据任务信封选择组合：

| 维度 | 必须说明 | 编码任务示例 |
| --- | --- | --- |
| 职责 | 可交付物、成功条件、明确不做什么 | `repo-map` 只产出符号/依赖地图，不给修复；`implementer` 只改获授权路径；`reviewer` 只找问题，不写产品功能。 |
| 工具 | 允许工具、读写范围、网络/服务策略 | 研究员可浏览和只读检索；实现者可编辑指定模块与运行测试；发布者才有部署工具。 |
| 权限 | 文件、Git、外部系统与审批边界 | 默认最小权限、无推送、无删除、无密钥读取；写操作须限定路径，外部副作用另行授权。A2A 也将能力发现与认证/授权分开建模。[A2A 安全与授权](https://a2a-protocol.org/latest/specification/#authentication-and-authorization) |
| 模型 | 能力档、推理预算、温度/结构输出要求 | 规划/安全审查用高推理档；机械检索、格式检查用低成本档；结构化结果必须启用 schema 校验。 |

### 适合常设配置的角色

稳定、反复出现且边界可检验的角色宜常设：任务协调者（只路由和综合）、仓库侦察/依赖映射者（只读）、实现者（按模块配置）、测试与复现者、代码审查者（默认只读）、安全/权限审查者、文档/发布说明作者。常设的是**角色合同和最小工具集**，不是把历史对话永久塞进其上下文。

### 应临时生成的角色

只在任务需要时创建领域专项角色：某个迁移版本专家、特定事故取证者、临时性能剖析者、第三方 API 调研者、跨仓库兼容性检查者。临时角色必须有过期条件、专属输入工件和明确返回 schema；完成后只留工件与摘要，不把其长上下文提升为全局记忆。LangChain 也将 skills 定义为由单一代理按需加载专项提示和知识的模式。[LangChain multi-agent 模式](https://docs.langchain.com/oss/python/langchain/multi-agent/index)

## 明确 anti-pattern

- **自由群聊默认化**：所有子代理互相可见、可互相追问、每轮广播全文。结果是上下文乘以参与人数，且责任、写入权和最终裁决不清。
- **把执行图误当消息图**：以为限制执行顺序就自动限制上下文。AutoGen 明确区分二者；必须配置消息白名单/过滤。[AutoGen GraphFlow](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html)
- **全量历史转发**：每次委派复制父代理的聊天记录、工具日志和先前子报告。改为任务信封 + 精选输入引用 + 有界摘要。
- **报告替代工件**：在消息里粘贴 patch、日志或大段源码，导致父代理无法追溯版本且浪费上下文。改为可读路径/ID、摘要、hash 与最小证据。
- **角色只有名称**：如“专家”“高级工程师”而没有职责、工具、权限、模型四维合同，最后变成同一个全权代理的不同提示词。
- **未声明的写权限与交叉写入**：多个子代理编辑同一文件或“必要时自行修复”。改为父代理划分文件所有权，写入者唯一，其他人输出只读建议或补丁工件。
- **把压缩当作删除理由**：摘要丢掉失败命令、版本、证据定位与未决风险。压缩的是叙述，不是可复现性；保留工件引用和验证元数据。

## 可直接写进 AGENTS.md 的规则

```md
### 父/子代理通信

- 默认采用 hub-and-spoke：仅父代理拆分、路由、汇总和决定下一步；子代理不得自由广播或互相委派，除非任务信封显式授权。
- 父→子使用有界任务信封：task_id、目标、允许/禁止范围、验收条件、权限约束、输入工件引用、回合/结果预算；不要转发完整聊天历史。
- 子→父使用有界结果信封：task_id、状态、结论、可定位证据、工件引用、改动清单、验证结果、风险/阻塞与下一动作；默认不超过 800 tokens。
- patch、日志、源码全文、数据、截图和长报告只写入工件并回传路径/ID、摘要和必要的 hash；消息只保留决策所需事实。
- 角色必须分别声明职责、工具、权限和模型预算。默认最小权限、单一写入者、审查者只读；临时领域角色完成即回收其上下文。
```

## 来源清单与分类

1. Anthropic 工程文章：[有效上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)；[多智能体研究系统](https://www.anthropic.com/engineering/multi-agent-research-system)。
2. LangChain/LangGraph 官方文档：[Multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent/index)；[Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)。
3. Microsoft AutoGen 官方文档：[GraphFlow / Message filtering](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html)；[MessageFilterAgent API](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.agents.html)。
4. Microsoft AutoGen 维护者讨论（经验性材料）：[Discussion #1877](https://github.com/microsoft/autogen/discussions/1877)。
5. Google ADK 官方文档：[ADK with A2A](https://adk.dev/a2a/)。
6. Google 发起的 A2A 官方规范：[A2A Protocol v1](https://a2a-protocol.org/latest/specification/)。

本附录没有把任何 OpenAI/Codex 材料作为证据；未使用二手博客。结论中的具体 token 数与角色划分是可调整的团队运行规则，不冒充为上述来源的硬性要求。
