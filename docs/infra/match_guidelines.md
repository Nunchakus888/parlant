
## `match_guidelines` 方法详细实现原理

### 1. 方法概述

`match_guidelines` 是 Parlant 引擎中指南匹配系统的核心方法，它采用**策略模式**和**批处理架构**，将不同类型的 Guidelines 分组并并行处理，最终返回匹配结果。

### 2. 核心架构设计

#### 2.1 策略模式架构
```python
class GuidelineMatcher:
    def __init__(self, logger: Logger, strategy_resolver: GuidelineMatchingStrategyResolver):
        self.strategy_resolver = strategy_resolver
```

- **策略解析器**: 根据 Guideline 的特征选择合适的匹配策略
- **策略实现**: 不同类型的 Guideline 使用不同的匹配策略
- **批处理**: 每种策略将 Guidelines 分组为批次进行并行处理

#### 2.2 数据流架构
```
Guidelines → 策略分组 → 批次创建 → 并行处理 → 结果合并 → 后处理
```

### 3. 详细实现步骤

#### 3.1 步骤1: 边界条件检查
```python
if not guidelines:
    return GuidelineMatchingResult(
        total_duration=0.0,
        batch_count=0,
        batch_generations=[],
        batches=[],
        matches=[],
    )
```
- **目的**: 处理空输入情况
- **优化**: 避免不必要的计算

#### 3.2 步骤2: 策略分组
```python
guideline_strategies: dict[str, tuple[GuidelineMatchingStrategy, list[Guideline]]] = {}

for guideline in guidelines:
    strategy = await self.strategy_resolver.resolve(guideline)
    if strategy.__class__.__name__ not in guideline_strategies:
        guideline_strategies[strategy.__class__.__name__] = (strategy, [])
    guideline_strategies[strategy.__class__.__name__][1].append(guideline)
```

**策略分组逻辑**:
- 为每个 Guideline 解析合适的策略
- 按策略类型分组 Guidelines
- 支持多种策略并行处理

#### 3.3 步骤3: 批次创建
```python
batches = await async_utils.safe_gather(
    *[
        strategy.create_matching_batches(
            guidelines,
            context=GuidelineMatchingContext(
                agent=context.agent,
                session=context.session,
                customer=context.customer,
                context_variables=context.state.context_variables,
                interaction_history=context.interaction.history,
                terms=list(context.state.glossary_terms),
                capabilities=context.state.capabilities,
                staged_events=context.state.tool_events,
                active_journeys=active_journeys,
                journey_paths=context.state.journey_paths,
            ),
        )
        for _, (strategy, guidelines) in guideline_strategies.items()
    ]
)
```

**批次创建特点**:
- **并行创建**: 使用 `safe_gather` 并行创建所有策略的批次
- **上下文传递**: 将完整的上下文信息传递给每个策略
- **策略特定**: 每种策略可以创建不同类型的批次

#### 3.4 步骤4: 批次处理
```python
with self._logger.operation("Processing batches", create_scope=False):
    batch_tasks = [
        self._process_guideline_matching_batch_with_retry(batch)
        for strategy_batches in batches
        for batch in strategy_batches
    ]
    batch_results = await async_utils.safe_gather(*batch_tasks)
```

**批次处理特点**:
- **并行处理**: 所有批次同时处理
- **重试机制**: 每个批次都有重试策略
- **错误隔离**: 单个批次失败不影响其他批次

#### 3.5 步骤5: 结果合并
```python
result_batches = [result.matches for result in batch_results]
matches: Sequence[GuidelineMatch] = list(chain.from_iterable(result_batches))

for strategy, _ in guideline_strategies.values():
    matches = await strategy.transform_matches(matches)
```

**结果处理**:
- **扁平化**: 将所有批次的匹配结果合并
- **后处理**: 每个策略可以对最终结果进行转换
- **链式处理**: 支持多个策略的顺序后处理

### 4. 策略类型详解

#### 4.1 策略解析机制

**策略解析器** (`GenericGuidelineMatchingStrategyResolver`):
```python
async def resolve(self, guideline: Guideline) -> GuidelineMatchingStrategy:
    # 1. 优先级1: Guideline特定覆盖
    if override_strategy := self.guideline_overrides.get(guideline.id):
        return override_strategy
    
    # 2. 优先级2: Tag覆盖
    tag_strategies = [s for tag_id, s in self.tag_overrides.items() if tag_id in guideline.tags]
    if first_tag_strategy := next(iter(tag_strategies), None):
        return first_tag_strategy
    
    # 3. 优先级3: 默认通用策略
    return self._generic_strategy
```

**策略分组逻辑**:
```python
for guideline in guidelines:
    strategy = await self.strategy_resolver.resolve(guideline)
    if strategy.__class__.__name__ not in guideline_strategies:
        guideline_strategies[strategy.__class__.__name__] = (strategy, [])
    guideline_strategies[strategy.__class__.__name__][1].append(guideline)
```

#### 4.2 通用策略分类逻辑

**GenericGuidelineMatchingStrategy** 将Guidelines分为6个主要类别:

##### 4.2.1 旅程步骤策略 (Journey Step Selection)
```python
if g.metadata.get("journey_node") is not None:
    if journey_id := g.metadata.get("journey_node", {}).get("journey_id"):
        if journey_id in active_journeys_mapping:
            journey_step_selection_journeys[active_journeys_mapping[journey_id]].append(g)
```
- **用途**: 处理Journey中的步骤Guidelines
- **特点**: 与特定Journey流程相关
- **示例**: "执行订单查询流程的下一步"
- **批处理**: `GenericJourneyStepSelectionBatch`

##### 4.2.2 观察型策略 (Observational)
```python
elif not g.content.action:
    if targets := await self._try_get_disambiguation_group_targets(g, guidelines):
        disambiguation_groups.append((g, targets))
    else:
        observational_guidelines.append(g)
```
- **用途**: 匹配纯观察型Guidelines
- **特点**: 只检查条件，不执行动作
- **示例**: "用户表达了不满情绪"
- **批处理**: `GenericObservationalGuidelineMatchingBatch`

##### 4.2.3 可执行策略 (Actionable)
```python
else:
    if g.metadata.get("continuous", False):
        actionable_guidelines.append(g)
    else:
        if g.id in context.session.agent_states[-1].applied_guideline_ids:
            # 已应用的可执行Guidelines
        else:
            actionable_guidelines.append(g)
```
- **用途**: 匹配需要执行动作的Guidelines
- **特点**: 有具体的action内容
- **示例**: "如果用户询问价格，提供价格信息"
- **批处理**: `GenericActionableGuidelineMatchingBatch`

##### 4.2.4 已应用可执行策略 (Previously Applied Actionable)
```python
if g.id in context.session.agent_states[-1].applied_guideline_ids:
    data = g.metadata.get("customer_dependent_action_data", False)
    if isinstance(data, Mapping) and data.get("is_customer_dependent", False):
        previously_applied_actionable_customer_dependent_guidelines.append(g)
    else:
        previously_applied_actionable_guidelines.append(g)
```
- **用途**: 处理之前已经应用过的可执行Guidelines
- **特点**: 考虑历史应用状态，决定是否重新应用
- **示例**: "继续执行之前开始的流程"
- **批处理**: `GenericPreviouslyAppliedActionableGuidelineMatchingBatch`

##### 4.2.5 已应用客户依赖策略 (Previously Applied Customer Dependent)
```python
if isinstance(data, Mapping) and data.get("is_customer_dependent", False):
    previously_applied_actionable_customer_dependent_guidelines.append(g)
```
- **用途**: 处理需要客户配合的已应用Guidelines
- **特点**: 需要检查客户是否完成了其部分
- **示例**: "等待客户提供订单号后继续处理"
- **批处理**: `GenericPreviouslyAppliedActionableCustomerDependentGuidelineMatchingBatch`

##### 4.2.6 消歧策略 (Disambiguation)
```python
if targets := await self._try_get_disambiguation_group_targets(g, guidelines):
    disambiguation_groups.append((g, targets))
```
- **用途**: 处理需要消歧的Guidelines
- **特点**: 当多个Guidelines可能冲突时进行选择
- **示例**: "在多个相似流程中选择最合适的"
- **批处理**: `GenericDisambiguationGuidelineMatchingBatch`

#### 4.3 策略优先级和覆盖机制

##### 4.3.1 策略覆盖优先级
1. **Guideline特定覆盖**: 最高优先级，针对特定Guideline ID
2. **Tag覆盖**: 中等优先级，基于Guideline的标签
3. **默认策略**: 最低优先级，使用通用策略

##### 4.3.2 覆盖配置
```python
# Guideline特定覆盖
strategy_resolver.guideline_overrides[guideline_id] = custom_strategy

# Tag覆盖
strategy_resolver.tag_overrides[tag_id] = custom_strategy
```

#### 4.4 批处理策略

##### 4.4.1 动态批处理大小
```python
def _get_optimal_batch_size(self, guidelines: dict[GuidelineId, Guideline]) -> int:
    guideline_n = len(guidelines)
    if guideline_n <= 10: return 1
    elif guideline_n <= 20: return 2
    elif guideline_n <= 30: return 3
    else: return 5
```

##### 4.4.2 批处理特点
- **自适应大小**: 根据Guideline数量动态调整批次大小
- **并行处理**: 多个批次同时处理
- **错误隔离**: 单个批次失败不影响其他批次
- **重试机制**: 每个批次都有重试策略

### 5. 策略执行流程

#### 5.1 策略分组执行顺序
```python
# 1. 旅程步骤策略 (最高优先级)
if g.metadata.get("journey_node") is not None:
    # 处理Journey相关Guidelines

# 2. 观察型策略
elif not g.content.action:
    # 处理纯观察型Guidelines

# 3. 可执行策略 (根据状态细分)
else:
    if g.metadata.get("continuous", False):
        # 连续性可执行Guidelines
    elif g.id in context.session.agent_states[-1].applied_guideline_ids:
        # 已应用的可执行Guidelines
        if customer_dependent:
            # 客户依赖的已应用Guidelines
        else:
            # 普通已应用Guidelines
    else:
        # 新的可执行Guidelines
```

#### 5.2 批处理创建逻辑
```python
guideline_batches: list[GuidelineMatchingBatch] = []

# 按优先级顺序创建批次
if observational_guidelines:
    guideline_batches.extend(self._create_batches_observational_guideline(...))

if previously_applied_actionable_guidelines:
    guideline_batches.extend(self._create_batches_previously_applied_actionable_guideline(...))

if previously_applied_actionable_customer_dependent_guidelines:
    guideline_batches.extend(self._create_batches_previously_applied_actionable_customer_dependent_guideline(...))

if actionable_guidelines:
    guideline_batches.extend(self._create_batches_actionable_guideline(...))

if disambiguation_groups:
    guideline_batches.extend([self._create_batch_disambiguation_guideline(...) for ...])

if journey_step_selection_journeys:
    guideline_batches.extend(self._create_batch_journey_step_selection(...))
```

### 6. 批处理架构优势

#### 6.1 性能优化
- **并行处理**: 多个批次同时执行
- **批量优化**: 减少AI模型调用次数
- **缓存利用**: 相似Guidelines可以共享上下文
- **动态批处理**: 根据Guideline数量自动调整批次大小

#### 6.2 可扩展性
- **策略扩展**: 新增策略类型不影响现有代码
- **批次扩展**: 每种策略可以创建多个批次
- **处理扩展**: 支持复杂的后处理逻辑
- **覆盖机制**: 支持Guideline和Tag级别的策略覆盖

#### 6.3 错误处理
- **重试机制**: 每个批次都有重试策略
- **错误隔离**: 单个批次失败不影响整体
- **降级处理**: 支持部分失败的情况
- **策略隔离**: 不同策略类型的错误互不影响

### 7. 上下文信息传递

#### 7.1 完整上下文
```python
GuidelineMatchingContext(
    agent=context.agent,                    # 代理信息
    session=context.session,                # 会话信息
    customer=context.customer,              # 客户信息
    context_variables=context.state.context_variables,  # 上下文变量
    interaction_history=context.interaction.history,    # 交互历史
    terms=list(context.state.glossary_terms),           # 词汇表
    capabilities=context.state.capabilities,            # 能力列表
    staged_events=context.state.tool_events,            # 工具事件
    active_journeys=active_journeys,                    # 活跃旅程
    journey_paths=context.state.journey_paths,          # 旅程路径
)
```

#### 7.2 上下文利用
- **语义匹配**: 使用交互历史进行语义匹配
- **状态感知**: 考虑当前会话状态
- **个性化**: 基于客户信息进行个性化匹配

### 8. 实际应用场景

#### 8.1 客服对话
```
用户: "我想退货"
系统: 
1. 观察型策略: 识别用户意图
2. 可执行策略: 启动退货流程
3. 旅程步骤策略: 执行退货Journey的步骤
```

#### 8.2 技术支持
```
用户: "我的电脑无法开机"
系统:
1. 观察型策略: 识别技术问题类型
2. 可执行策略: 提供诊断建议
3. 已应用策略: 继续之前的诊断流程
```

### 9. 策略分组总结

#### 9.1 策略分类体系
| 策略类型 | 判断条件 | 批处理类 | 用途 |
|---------|---------|---------|------|
| **旅程步骤策略** | `journey_node` 存在 | `GenericJourneyStepSelectionBatch` | 处理Journey流程步骤 |
| **观察型策略** | 无 `action` 且无消歧目标 | `GenericObservationalGuidelineMatchingBatch` | 纯观察型Guidelines |
| **消歧策略** | 无 `action` 但有消歧目标 | `GenericDisambiguationGuidelineMatchingBatch` | 处理冲突Guidelines |
| **连续性可执行策略** | 有 `action` 且 `continuous=True` | `GenericActionableGuidelineMatchingBatch` | 连续性可执行Guidelines |
| **已应用可执行策略** | 有 `action` 且已应用 | `GenericPreviouslyAppliedActionableGuidelineMatchingBatch` | 已应用的可执行Guidelines |
| **已应用客户依赖策略** | 已应用且客户依赖 | `GenericPreviouslyAppliedActionableCustomerDependentGuidelineMatchingBatch` | 需要客户配合的已应用Guidelines |
| **新可执行策略** | 有 `action` 且未应用 | `GenericActionableGuidelineMatchingBatch` | 新的可执行Guidelines |

#### 9.2 策略优先级
1. **旅程步骤策略** (最高优先级)
2. **观察型策略** / **消歧策略**
3. **已应用客户依赖策略**
4. **已应用可执行策略**
5. **连续性可执行策略** / **新可执行策略**

#### 9.3 策略覆盖机制
- **Guideline特定覆盖**: 针对特定Guideline ID的覆盖
- **Tag覆盖**: 基于Guideline标签的覆盖
- **默认策略**: 通用策略作为后备

### 10. 策略Prompt差异分析

#### 10.1 观察型策略Prompt (`GenericObservationalGuidelineMatchingBatch`)

**核心特点**:
- **任务描述**: 评估条件是否适用于当前交互状态
- **评估标准**: 基于自然语言含义和上下文进行二元判断
- **输出格式**: 只包含条件评估，不涉及动作

**关键Prompt内容**:
```python
template="""
GENERAL INSTRUCTIONS
-----------------
In our system, the behavior of a conversational AI agent is guided by how the current state of its interaction with a customer (also referred to as "the user") compares to a number of pre-defined conditions:

- "condition": This is a natural-language condition that specifies when a guideline should apply.
          We evaluate each conversation at its current state against these conditions
          to determine which guidelines should inform the agent's next reply.

Task Description
----------------
Your task is to evaluate whether each provided condition applies to the current interaction between an AI agent and a user. For each condition, you must determine a binary True/False decision.

Evaluation Criteria:
- Current Activity Or State: Conditions about what's happening "now" in the conversation
- Historical Events: Conditions about things that happened during the interaction
- Persistent Facts: Conditions about user characteristics or established facts
"""
```

#### 10.2 可执行策略Prompt (`GenericActionableGuidelineMatchingBatch`)

**核心特点**:
- **任务描述**: 评估未应用的可执行Guidelines的适用性
- **评估重点**: 基于最新对话状态，假设动作尚未执行
- **输出格式**: 包含条件和动作的完整评估

**关键Prompt内容**:
```python
template="""
GENERAL INSTRUCTIONS
-----------------
In our system, the behavior of a conversational AI agent is guided by "guidelines". The agent makes use of these guidelines whenever it interacts with a user (also referred to as the customer).
Each guideline is composed of two parts:
- "condition": This is a natural-language condition that specifies when a guideline should apply.
- "action": This is a natural-language instruction that should be followed by the agent

Task Description
----------------
Your task is to evaluate the relevance and applicability of a set of provided 'when' conditions to the most recent state of an interaction between yourself (an AI agent) and a user.
You examine the applicability of each guideline under the assumption that the action was not taken yet during the interaction.

A guideline should be marked as applicable if it is relevant to the latest part of the conversation and in particular the most recent customer message.
"""
```

#### 10.3 已应用可执行策略Prompt (`GenericPreviouslyAppliedActionableGuidelineMatchingBatch`)

**核心特点**:
- **任务描述**: 评估已应用的可执行Guidelines是否需要重新应用
- **评估重点**: 基于新上下文判断是否需要重复动作
- **输出格式**: 包含重新应用决策的评估

**关键Prompt内容**:
```python
template="""
GENERAL INSTRUCTIONS
-----------------
In our system, the behavior of a conversational AI agent is guided by "guidelines". The agent makes use of these guidelines whenever it interacts with a user (also referred to as the customer).

Task Description
----------------
You will be given a set of guidelines, each associated with an action that has already been applied one or more times during the conversation.

In general, a guideline should be reapplied if:
1. The condition is met again for a new reason in the most recent user message, and
2. The associated action has not yet been taken in response to this new occurrence, but still needs to be.

Your task is to determine whether reapplying the action is appropriate, based on whether the guideline's condition is met again in a way that justifies repeating the action.
"""
```

#### 10.4 已应用客户依赖策略Prompt (`GenericPreviouslyAppliedActionableCustomerDependentGuidelineMatchingBatch`)

**核心特点**:
- **任务描述**: 评估需要客户配合的已应用Guidelines
- **评估重点**: 检查客户是否完成其部分，或是否需要新上下文
- **输出格式**: 包含客户依赖状态的评估

**关键Prompt内容**:
```python
template="""
GENERAL INSTRUCTIONS
-----------------
In our system, the behavior of a conversational AI agent is guided by "guidelines". The agent makes use of these guidelines whenever it interacts with a user (also referred to as the customer).

While an action can only instruct the agent to do something, some guidelines may require something from the customer in order to be completed. These are referred to as "customer dependent" guidelines.

Task Description
----------------
Your task is to evaluate whether a set of "customer dependent" guidelines should be applied to the current state of a conversation between an AI agent and a user.

A guideline should be applied if either of the following conditions is true:
1. Incomplete Action: The original condition still holds, the reason that triggered the agent's initial action remains relevant, AND the customer has not yet fulfilled their part of the action.
2. New Context for Same Condition: The condition arises again in a new context, requiring the action to be repeated by both agent and customer.
"""
```

#### 10.5 消歧策略Prompt (`GenericDisambiguationGuidelineMatchingBatch`)

**核心特点**:
- **任务描述**: 识别客户意图的歧义性并确定可能的解释
- **评估重点**: 分析歧义条件并提供选择选项
- **输出格式**: 包含歧义识别和选项提供

**关键Prompt内容**:
```python
template="""
GENERAL INSTRUCTIONS
-----------------
In our system, the behavior of a conversational AI agent is guided by "guidelines". The agent makes use of these guidelines whenever it interacts with a user (also referred to as the customer).

Task Description
----------------
Sometimes a customer expresses that they've experienced something or want to proceed with something, but there are multiple possible ways to go, and it's as-yet unclear what exactly they intend.
In such cases, we need to identify the potential options and ask the customer which one they mean.

Your task is to determine whether the customer's intention is currently ambiguous and, if so, what the possible interpretations or directions are.
You'll be given a disambiguation condition — one that, if true, signals a potential ambiguity — and a list of related guidelines, each representing a possible path the customer might want to follow.

If you identify an ambiguity, return the relevant guidelines that represent the available options.
Then, formulate a response in the format:
"Ask the customer whether they want to do X, Y, or Z..."
"""
```

#### 10.6 旅程步骤选择策略Prompt (`GenericJourneyNodeSelectionBatch`)

**核心特点**:
- **任务描述**: 分析当前对话状态并确定下一个适当的旅程步骤
- **评估重点**: 基于最后执行的步骤和对话当前状态
- **输出格式**: 包含步骤推进和回溯决策

**关键Prompt内容**:
```python
template="""
GENERAL INSTRUCTIONS
-------------------
You are an AI agent named {agent_name} whose role is to engage in multi-turn conversations with customers on behalf of a business.
Your interactions are structured around predefined "journeys" - systematic processes that guide customer conversations toward specific outcomes.

## Journey Structure
Each journey consists of:
- **Steps**: Individual actions you must take (e.g., ask a question, provide information, perform a task)
- **Transitions**: Rules that determine which step comes next based on customer responses or completion status
- **Flags**: Special properties that modify how steps behave

## Your Core Task
Analyze the current conversation state and determine the next appropriate journey step, based on the last step that was performed and the current state of the conversation.
"""
```

#### 10.7 Prompt差异总结

| 策略类型 | 任务重点 | 评估标准 | 输出特点 |
|---------|---------|---------|---------|
| **观察型** | 条件适用性 | 二元判断 | 只评估条件 |
| **可执行** | 新动作适用性 | 最新上下文 | 条件和动作 |
| **已应用可执行** | 重新应用决策 | 新上下文判断 | 重新应用评估 |
| **已应用客户依赖** | 客户配合状态 | 完成状态检查 | 客户依赖状态 |
| **消歧** | 意图歧义识别 | 歧义分析 | 选项提供 |
| **旅程步骤** | 步骤推进决策 | 状态分析 | 步骤和回溯 |

### 11. create_matching_batches实现差异分析

#### 11.1 GuidelineMatchingContext参数详解

**GuidelineMatchingContext** 是传递给所有策略的核心上下文对象，包含以下参数：

```python
@dataclass(frozen=True)
class GuidelineMatchingContext:
    agent: Agent                                    # 代理信息
    session: Session                                # 会话信息  
    customer: Customer                              # 客户信息
    context_variables: Sequence[tuple[ContextVariable, ContextVariableValue]]  # 上下文变量
    interaction_history: Sequence[Event]           # 交互历史
    terms: Sequence[Term]                          # 词汇表
    capabilities: Sequence[Capability]              # 能力列表
    staged_events: Sequence[EmittedEvent]          # 工具事件
    active_journeys: Sequence[Journey]             # 活跃旅程
    journey_paths: dict[JourneyId, list[Optional[GuidelineId]]]  # 旅程路径
```

**参数作用分析**:
- **agent**: 提供代理身份、配置和能力信息
- **session**: 包含会话状态、历史和应用记录
- **customer**: 客户信息和个性化数据
- **context_variables**: 动态上下文变量，用于个性化匹配
- **interaction_history**: 完整的对话历史，用于语义分析
- **terms**: 词汇表，用于术语理解和匹配
- **capabilities**: 代理能力，用于能力相关的Guideline匹配
- **staged_events**: 待处理的工具事件，影响匹配决策
- **active_journeys**: 当前活跃的旅程，用于旅程相关匹配
- **journey_paths**: 旅程执行路径，用于步骤选择

#### 11.2 通用策略实现 (`GenericGuidelineMatchingStrategy`)

**核心特点**:
- **分类逻辑**: 根据Guideline特征进行6种分类
- **批次创建**: 为每种类型创建专门的批次
- **上下文传递**: 完整传递所有上下文信息

**实现逻辑**:
```python
async def create_matching_batches(
    self,
    guidelines: Sequence[Guideline],
    context: GuidelineMatchingContext,
) -> Sequence[GuidelineMatchingBatch]:
    # 1. 初始化分类列表
    observational_guidelines: list[Guideline] = []
    previously_applied_actionable_guidelines: list[Guideline] = []
    previously_applied_actionable_customer_dependent_guidelines: list[Guideline] = []
    actionable_guidelines: list[Guideline] = []
    disambiguation_groups: list[tuple[Guideline, list[Guideline]]] = []
    journey_step_selection_journeys: dict[Journey, list[Guideline]] = defaultdict(list)

    # 2. 分类逻辑
    for g in guidelines:
        if g.metadata.get("journey_node") is not None:
            # 旅程步骤策略
        elif not g.content.action:
            # 观察型或消歧策略
        else:
            # 可执行策略分类

    # 3. 创建批次
    guideline_batches: list[GuidelineMatchingBatch] = []
    if observational_guidelines:
        guideline_batches.extend(self._create_batches_observational_guideline(...))
    # ... 其他类型批次创建
```

#### 11.3 观察型策略实现 (`ObservationalGuidelineMatching`)

**核心特点**:
- **简化逻辑**: 只处理观察型Guidelines
- **动态批处理**: 根据Guideline数量调整批次大小
- **上下文优化**: 使用相关的Journeys而非所有活跃Journeys

**实现逻辑**:
```python
async def create_matching_batches(
    self,
    guidelines: Sequence[Guideline],
    context: GuidelineMatchingContext,
) -> Sequence[GuidelineMatchingBatch]:
    # 1. 获取相关Journeys
    journeys = (
        self._entity_queries.find_journeys_on_which_this_guideline_depends.get(
            guidelines[0].id, []
        )
        if guidelines else []
    )

    # 2. 动态批处理
    guidelines_dict = {g.id: g for g in guidelines}
    batch_size = self._get_optimal_batch_size(guidelines_dict)
    batch_count = math.ceil(len(guidelines_dict) / batch_size)

    # 3. 创建批次
    for batch_number in range(batch_count):
        start_offset = batch_number * batch_size
        end_offset = start_offset + batch_size
        batch = dict(guidelines_list[start_offset:end_offset])
        batches.append(self._create_batch(
            guidelines=list(batch.values()),
            journeys=journeys,
            context=GuidelineMatchingContext(...)  # 完整上下文传递
        ))
```

#### 11.4 可执行策略实现 (`GenericActionableGuidelineMatching`)

**核心特点**:
- **与观察型策略相同**: 使用相同的批处理逻辑
- **上下文优化**: 使用相关Journeys进行优化
- **批处理策略**: 动态调整批次大小

**实现差异**:
- 使用不同的批处理类 (`GenericActionableGuidelineMatchingBatch`)
- 相同的动态批处理逻辑
- 相同的上下文传递方式

#### 11.5 已应用策略实现 (`GenericPreviouslyAppliedActionableGuidelineMatching`)

**核心特点**:
- **状态感知**: 考虑Guideline的应用状态
- **上下文优化**: 基于应用状态进行优化
- **批处理策略**: 与可执行策略相同的批处理逻辑

**实现差异**:
- 使用不同的批处理类 (`GenericPreviouslyAppliedActionableGuidelineMatchingBatch`)
- 相同的动态批处理逻辑
- 相同的上下文传递方式

#### 11.6 消歧策略实现 (`GenericDisambiguationGuidelineMatching`)

**核心特点**:
- **特殊处理**: 每个消歧组创建单独的批次
- **一对一映射**: 每个消歧Guideline对应一个批次
- **上下文传递**: 完整传递上下文信息

**实现逻辑**:
```python
async def create_matching_batches(
    self,
    guidelines: Sequence[Guideline],
    context: GuidelineMatchingContext,
) -> Sequence[GuidelineMatchingBatch]:
    # 1. 获取消歧组
    disambiguation_groups = await self._get_disambiguation_groups(guidelines)
    
    # 2. 为每个消歧组创建批次
    batches = []
    for disambiguation_guideline, targets in disambiguation_groups:
        batches.append(self._create_batch_disambiguation_guideline(
            disambiguation_guideline=disambiguation_guideline,
            disambiguation_targets=targets,
            context=context
        ))
    
    return batches
```

#### 11.7 旅程步骤选择策略实现 (`GenericJourneyNodeSelectionBatch`)

**核心特点**:
- **旅程特定**: 每个Journey创建单独的批次
- **路径感知**: 考虑Journey的执行路径
- **步骤优化**: 基于Journey结构进行优化

**实现逻辑**:
```python
async def create_matching_batches(
    self,
    guidelines: Sequence[Guideline],
    context: GuidelineMatchingContext,
) -> Sequence[GuidelineMatchingBatch]:
    # 1. 按Journey分组
    journey_groups = self._group_guidelines_by_journey(guidelines)
    
    # 2. 为每个Journey创建批次
    batches = []
    for journey, step_guidelines in journey_groups.items():
        batches.append(self._create_batch_journey_step_selection(
            examined_journey=journey,
            step_guidelines=step_guidelines,
            context=context
        ))
    
    return batches
```

#### 11.8 批处理策略对比

| 策略类型 | 批处理方式 | 批次大小 | 上下文优化 | 特殊处理 |
|---------|-----------|---------|-----------|---------|
| **通用策略** | 分类批处理 | 动态调整 | 完整上下文 | 6种分类 |
| **观察型** | 动态批处理 | 1-5个Guidelines | 相关Journeys | 简化逻辑 |
| **可执行** | 动态批处理 | 1-5个Guidelines | 相关Journeys | 标准处理 |
| **已应用** | 动态批处理 | 1-5个Guidelines | 相关Journeys | 状态感知 |
| **消歧** | 一对一处理 | 1个消歧组 | 完整上下文 | 特殊映射 |
| **旅程步骤** | 旅程分组 | 1个Journey | 旅程特定 | 路径感知 |

#### 11.9 上下文参数使用分析

**所有策略共同使用**:
- `agent`: 代理身份和能力
- `session`: 会话状态和历史
- `customer`: 客户信息
- `context_variables`: 动态变量
- `interaction_history`: 对话历史
- `terms`: 词汇表
- `capabilities`: 代理能力
- `staged_events`: 工具事件

**策略特定使用**:
- **观察型/可执行/已应用**: 使用`active_journeys`中的相关Journeys
- **消歧**: 使用完整的`active_journeys`和`journey_paths`
- **旅程步骤**: 使用特定的Journey和`journey_paths`

### 12. 总结

`match_guidelines` 方法体现了以下设计原则：

1. **策略模式**: 不同类型的Guidelines使用不同的匹配策略
2. **批处理架构**: 通过批次处理提高性能和可扩展性
3. **并行处理**: 充分利用异步特性提高效率
4. **错误隔离**: 确保系统的健壮性
5. **上下文感知**: 充分利用所有可用信息进行匹配
6. **灵活覆盖**: 支持多种级别的策略覆盖机制
7. **专业化Prompt**: 每种策略都有针对性的Prompt设计
8. **智能批处理**: 根据策略类型和Guideline数量动态调整批处理策略
9. **上下文优化**: 不同策略使用不同的上下文优化策略

这种设计确保了AI引擎能够：
- 高效处理大量Guidelines
- 支持复杂的匹配逻辑
- 保持系统的可扩展性
- 提供准确的匹配结果
- 支持灵活的定制化需求
- 通过专业化Prompt提高匹配精度
- 通过智能批处理优化性能
- 通过上下文优化提高匹配准确性