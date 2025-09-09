# Parlant 系统 Prompt 分层设计深度分析报告

基于对 Parlant 系统代码的深入分析，我为您提供一份全面的 prompt 分层设计分析报告。

## 一、Prompt 分层设计的作用

### 1.1 核心设计理念

Parlant 的 prompt 分层设计遵循**关注点分离（SoC）**和**高内聚，低耦合**的设计原则：

```python
# 核心架构：PromptBuilder + PromptSection
@dataclass(frozen=True)
class PromptSection:
    template: str          # 模板内容
    props: dict[str, Any]  # 动态属性
    status: Optional[SectionStatus]  # 状态管理
```

### 1.2 分层设计的具体作用

**1. 模块化管理**
- 每个 section 负责特定的功能领域
- 便于独立测试、修改和扩展
- 支持条件性包含（通过 SectionStatus）

**2. 动态组合**
- 根据业务场景动态构建 prompt
- 支持运行时调整和优化
- 避免硬编码的单一 prompt

**3. 可维护性**
- 清晰的命名规范：`{组件}-{功能}-{类型}`
- 统一的模板格式和变量替换机制
- 便于团队协作和代码审查

**4. 性能优化**
- 支持缓存机制（`_cached_results`）
- 避免重复构建相同的 prompt
- 支持增量更新

## 二、LLM 分配处理逻辑

### 2.1 多模型分配策略

**是的，不同类型的 prompt 确实会由不同的 LLM 来执行**，系统采用了智能的模型分配策略：

```python
# 位置：src/parlant/adapters/nlp/openrouter_service.py
def get_model_config_for_schema(schema_type: type) -> ModelConfig:
    defaults = {
        SingleToolBatchSchema: ModelConfig("openai/gpt-4o", 128 * 1024),
        JourneyNodeSelectionSchema: ModelConfig("anthropic/claude-3.5-sonnet", 200 * 1024),
        CannedResponseDraftSchema: ModelConfig("anthropic/claude-3.5-sonnet", 200 * 1024),
        CannedResponseSelectionSchema: ModelConfig("anthropic/claude-3-haiku", 200 * 1024),
    }
```

### 2.2 具体分配逻辑

**1. 工具调用类（Tool Calling）**
- **模型**：GPT-4o
- **原因**：需要高精度的逻辑推理和参数验证
- **Token 限制**：128K

**2. 旅程节点选择（Journey Node Selection）**
- **模型**：Claude 3.5 Sonnet
- **原因**：需要复杂的上下文理解和流程推理
- **Token 限制**：200K

**3. 消息生成（Message Generation）**
- **模型**：Claude 3.5 Sonnet
- **原因**：需要自然语言生成和创意表达
- **Token 限制**：200K

**4. 准则匹配（Guideline Matching）**
- **模型**：根据复杂度动态选择
- **原因**：需要精确的条件判断和语义理解

### 2.3 分配处理流程

```python
# 位置：src/parlant/core/engines/alpha/engine.py
class AlphaEngine(Engine):
    async def _do_process(self, context: LoadedContext) -> None:
        # 1. 准则匹配阶段 - 使用专门的匹配模型
        guideline_and_journey_matching_result = (
            await self._load_matched_guidelines_and_journeys(context)
        )
        
        # 2. 工具调用阶段 - 使用工具专用模型
        if tool_calling_result := await self._call_tools(context, tool_preexecution_state):
            # 工具调用会使用工具调用器的系统提示词
            
        # 3. 消息生成阶段 - 使用生成专用模型
        message_generation_inspections = await self._generate_messages(context, latch)
```

## 三、所有 Prompt 模板详细清单

### 3.1 消息生成器（MessageGenerator）模板

**位置**：`src/parlant/core/engines/alpha/message_generator.py`

| 模板名称 | 作用 | 执行场景 |
|---------|------|----------|
| `message-generator-general-instructions` | 定义 AI 代理的基本角色和职责 | 每次消息生成时 |
| `message-generator-task-description` | 详细的任务描述和行为准则 | 每次消息生成时 |
| `message-generator-initial-message-instructions` | 首次交互的特定指令 | 对话开始时 |
| `message-generator-ongoing-interaction-instructions` | 持续对话的指令 | 对话进行中 |
| `message-generator-revision-mechanism` | 响应修订机制和流程 | 需要优化响应时 |
| `message-generator-examples` | 示例和最佳实践 | 提供参考案例时 |
| `message-generator-interaction-context` | 交互上下文信息 | 包含历史对话时 |
| `message-generator-missing-data-for-tools` | 工具缺失数据的处理 | 工具调用失败时 |
| `message-generator-invalid-data-for-tools` | 工具无效数据的处理 | 数据验证失败时 |
| `message-generator-output-format` | 输出格式规范 | 结构化输出时 |

### 3.2 准则匹配器（Guideline Matcher）模板

**位置**：`src/parlant/core/engines/alpha/guideline_matching/generic/`

| 模板名称 | 作用 | 执行场景 |
|---------|------|----------|
| `guideline-matcher-general-instructions` | 准则匹配的基本逻辑 | 每次准则评估时 |
| `guideline-matcher-observational-batch` | 观察性准则的匹配 | 判断条件是否满足时 |
| `guideline-matcher-actionable-batch` | 可执行准则的匹配 | 需要执行动作时 |
| `guideline-matcher-previously-applied-batch` | 已应用准则的重新评估 | 检查重复应用时 |
| `guideline-matcher-disambiguation-batch` | 歧义准则的消解 | 处理冲突准则时 |

### 3.3 旅程节点选择器（Journey Node Selection）模板

**位置**：`src/parlant/core/engines/alpha/guideline_matching/generic/journey_node_selection_batch.py`

| 模板名称 | 作用 | 执行场景 |
|---------|------|----------|
| `journey-step-selection-general-instructions` | 旅程选择的基本逻辑 | 旅程流程控制时 |
| `journey-step-selection-task_description` | 旅程选择的具体任务 | 决定下一步行动时 |
| `journey-step-selection-examples` | 旅程选择的示例 | 提供参考案例时 |
| `journey_description_background` | 旅程背景信息 | 理解旅程结构时 |
| `journey-step-selection-journey-steps` | 旅程步骤映射 | 导航旅程路径时 |
| `journey-step-selection-output-format` | 输出格式规范 | 结构化输出时 |
| `journey-general_reminder-section` | 通用提醒信息 | 确保任务完成时 |

### 3.4 工具调用器（Tool Caller）模板

**位置**：`src/parlant/core/engines/alpha/tool_calling/`

| 模板名称 | 作用 | 执行场景 |
|---------|------|----------|
| `tool-caller-general-instructions` | 工具调用的基本逻辑 | 评估工具适用性时 |
| `tool-caller-task-description` | 工具调用的具体任务 | 决定是否调用工具时 |
| `tool-caller-examples` | 工具调用的示例 | 提供参考案例时 |
| `tool-caller-tool-definitions` | 工具定义和规范 | 理解工具功能时 |
| `tool-caller-staged-tool-calls` | 预置工具调用信息 | 避免重复调用时 |

### 3.5 预置响应生成器（Canned Response Generator）模板

**位置**：`src/parlant/core/engines/alpha/canned_response_generator.py`

| 模板名称 | 作用 | 执行场景 |
|---------|------|----------|
| `canned-response-generator-draft-general-instructions` | 预置响应起草的基本逻辑 | 起草响应内容时 |
| `canned-response-generator-draft-task-description` | 预置响应起草的具体任务 | 生成响应草稿时 |
| `canned-response-generator-draft-revision-mechanism` | 预置响应修订机制 | 优化响应内容时 |
| `canned-response-generator-draft-examples` | 预置响应起草的示例 | 提供参考案例时 |
| `canned-response-generator-selection-task-description` | 预置响应选择的任务 | 选择最佳响应时 |
| `canned-response-generative-field-extraction-instructions` | 生成字段提取指令 | 提取动态字段时 |

### 3.6 内置 Section（BuiltInSection）模板

**位置**：`src/parlant/core/engines/alpha/prompt_builder.py`

| Section 名称 | 作用 | 执行场景 |
|-------------|------|----------|
| `AGENT_IDENTITY` | 代理身份信息 | 定义代理角色时 |
| `CUSTOMER_IDENTITY` | 客户身份信息 | 个性化交互时 |
| `INTERACTION_HISTORY` | 交互历史 | 上下文理解时 |
| `CONTEXT_VARIABLES` | 上下文变量 | 动态信息传递时 |
| `GLOSSARY` | 术语表 | 专业术语解释时 |
| `GUIDELINE_DESCRIPTIONS` | 准则描述 | 行为规范应用时 |
| `STAGED_EVENTS` | 预置事件 | 工具调用结果时 |
| `JOURNEYS` | 旅程信息 | 流程控制时 |
| `OBSERVATIONS` | 观察信息 | 状态监控时 |
| `CAPABILITIES` | 能力描述 | 功能展示时 |

## 四、执行场景详细分析

### 4.1 简单查询场景（2-3次 LLM 调用）

```python
# 用户输入："我想了解产品信息"
# 1. 准则匹配（1次）
guideline_matcher.generate(prompt=observational_batch_prompt)

# 2. 消息生成（1次）
message_generator.generate(prompt=message_generation_prompt)
```

### 4.2 复杂任务场景（6-9次 LLM 调用）

```python
# 用户输入："我想预约医生，但是我有特殊需求"
# 1. 旅程节点选择（1次）
journey_selector.generate(prompt=journey_selection_prompt)

# 2. 准则匹配（2-3次）
guideline_matcher.generate(prompt=observational_batch_prompt)
guideline_matcher.generate(prompt=actionable_batch_prompt)

# 3. 工具调用决策（1次）
tool_caller.generate(prompt=tool_calling_prompt)

# 4. 执行工具后的第二轮迭代
journey_selector.generate(prompt=journey_selection_prompt)
guideline_matcher.generate(prompt=updated_guideline_prompt)

# 5. 消息生成（1次）
message_generator.generate(prompt=final_message_prompt)
```

### 4.3 预置响应场景（4-6次 LLM 调用）

```python
# 启用 Canned Response 的复杂场景
# 1. 旅程节点选择（1次）
journey_selector.generate(prompt=journey_selection_prompt)

# 2. 准则匹配（2-3次）
guideline_matcher.generate(prompt=guideline_matching_prompt)

# 3. 预置响应起草（1次）
canned_response_generator.generate(prompt=draft_prompt)

# 4. 预置响应选择（1次）
canned_response_generator.generate(prompt=selection_prompt)

# 5. 预置响应修订（可选，1次）
canned_response_generator.generate(prompt=revision_prompt)
```

## 五、深度技术分析

### 5.1 架构优势

**1. 高内聚，低耦合**
- 每个 prompt section 职责单一明确
- 组件间通过标准接口交互
- 便于独立测试和维护

**2. 动态组合能力**
- 根据业务场景动态构建 prompt
- 支持条件性包含和排除
- 运行时优化和调整

**3. 多模型优化**
- 不同任务使用最适合的模型
- 成本效益优化
- 性能与质量的平衡

### 5.2 性能优化策略

**1. 批量处理**
```python
# 位置：src/parlant/core/engines/alpha/guideline_matching/generic/generic_guideline_matching_strategy.py
batch_size = self._get_optimal_batch_size(guidelines_dict)
```

**2. 缓存机制**
```python
# 位置：src/parlant/core/engines/alpha/prompt_builder.py
self._cached_results: set[str] = set()
```

**3. 概率剪枝**
```python
# 位置：src/parlant/core/engines/alpha/engine.py
top_k = 3
(relevant_guidelines, high_prob_journeys) = await self._prune_low_prob_guidelines_and_all_graph(
```

### 5.3 可扩展性设计

**1. 插件化架构**
- 支持多种 NLP 服务
- 可插拔的向量数据库
- 灵活的准则匹配策略

**2. 配置驱动**
- 通过 metadata 配置准则行为
- 支持动态旅程定义
- 可配置的匹配参数

## 六、总结

Parlant 的 prompt 分层设计是一个高度精密的系统，它通过以下方式实现了卓越的性能和可维护性：

1. **分层架构**：将复杂的 prompt 分解为可管理的模块
2. **智能分配**：根据任务特性选择最适合的 LLM 模型
3. **动态组合**：根据业务场景灵活构建 prompt
4. **性能优化**：通过缓存、批处理和剪枝提升效率
5. **可扩展性**：支持插件化扩展和配置驱动

这种设计使得 Parlant 能够处理从简单查询到复杂业务流程的各种场景，同时保持代码的可维护性和系统的可扩展性。