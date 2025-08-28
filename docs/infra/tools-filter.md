## 工具过滤逻辑详细分析

### 1. 过滤机制概述

**答案：不是语义相关性，而是基于预定义的关联关系进行精确匹配。**

### 2. 核心过滤逻辑

#### 2.1 过滤步骤详解

```python
# 步骤1: 获取所有指南-工具关联关系
guideline_tool_associations = list(
    await self._entity_queries.find_guideline_tool_associations()
)

# 步骤2: 创建当前匹配指南的ID映射
guideline_matches_by_id = {p.guideline.id: p for p in guideline_matches}

# 步骤3: 过滤出与当前匹配指南相关的关联关系
relevant_associations = [
    a for a in guideline_tool_associations 
    if a.guideline_id in guideline_matches_by_id  # 精确ID匹配
]

# 步骤4: 构建指南到工具的映射
tools_for_guidelines: dict[GuidelineMatch, list[ToolId]] = defaultdict(list)
for association in relevant_associations:
    tools_for_guidelines[guideline_matches_by_id[association.guideline_id]].append(
        association.tool_id
    )
```

#### 2.2 过滤逻辑特点

**精确匹配**：
- 使用 `guideline_id in guideline_matches_by_id` 进行精确的ID匹配
- 不是基于语义相似性，而是基于预定义的关联关系
- 确保只有被明确授权的工具才能被使用

### 3. 关联关系的创建和管理

#### 3.1 手动创建关联关系
```python
# 通过API创建指南-工具关联
await guideline_tool_association_store.create_association(
    guideline_id=guideline_id,
    tool_id=ToolId(service_name="order_service", tool_name="query_order"),
)
```

#### 3.2 关联关系的数据结构
```python
@dataclass(frozen=True)
class GuidelineToolAssociation:
    id: GuidelineToolAssociationId
    creation_utc: datetime
    guideline_id: GuidelineId      # 指南ID
    tool_id: ToolId               # 工具ID
```

### 4. 实际应用示例

#### 4.1 客服系统示例
```python
# 预定义的关联关系
associations = [
    GuidelineToolAssociation(
        guideline_id="order_status_guideline",
        tool_id=ToolId("order_service", "query_order")
    ),
    GuidelineToolAssociation(
        guideline_id="refund_guideline", 
        tool_id=ToolId("payment_service", "process_refund")
    ),
    GuidelineToolAssociation(
        guideline_id="refund_guideline",
        tool_id=ToolId("order_service", "update_order_status")
    )
]

# 当前匹配的指南
current_matches = [
    GuidelineMatch(guideline_id="order_status_guideline"),
    GuidelineMatch(guideline_id="refund_guideline")
]

# 过滤结果
filtered_tools = {
    "order_status_guideline": [ToolId("order_service", "query_order")],
    "refund_guideline": [
        ToolId("payment_service", "process_refund"),
        ToolId("order_service", "update_order_status")
    ]
}
```

#### 4.2 技术支持系统示例
```python
# 预定义的关联关系
associations = [
    GuidelineToolAssociation(
        guideline_id="system_diagnosis_guideline",
        tool_id=ToolId("diagnostic_service", "collect_logs")
    ),
    GuidelineToolAssociation(
        guideline_id="system_diagnosis_guideline",
        tool_id=ToolId("diagnostic_service", "check_system_status")
    ),
    GuidelineToolAssociation(
        guideline_id="user_verification_guideline",
        tool_id=ToolId("user_service", "verify_user")
    )
]

# 当前匹配的指南
current_matches = [GuidelineMatch(guideline_id="system_diagnosis_guideline")]

# 过滤结果
filtered_tools = {
    "system_diagnosis_guideline": [
        ToolId("diagnostic_service", "collect_logs"),
        ToolId("diagnostic_service", "check_system_status")
    ]
}
```

### 5. 特殊处理：旅程节点工具

#### 5.1 旅程节点工具的特殊逻辑
```python
# 识别旅程节点指南
node_guidelines = [
    m.guideline for m in guideline_matches 
    if m.guideline.id.startswith("journey_node:")
]

# 获取旅程节点的工具关联
node_tools_associations = {
    guideline_matches_by_id[g.id]: list(tools)
    for g, tools in zip(
        node_guidelines,
        await async_utils.safe_gather(
            *[
                self._entity_queries.find_journey_node_tool_associations(
                    extract_node_id_from_journey_node_guideline_id(g.id),
                )
                for g in node_guidelines
            ]
        ),
    )
    if tools
}

# 合并到最终结果
tools_for_guidelines.update(node_tools_associations)
```

### 6. 设计优势

#### 6.1 安全性
- **精确控制**: 只有被明确授权的工具才能被使用
- **权限隔离**: 防止指南使用未授权的工具
- **审计追踪**: 可以追踪工具的使用情况

#### 6.2 性能优化
- **快速过滤**: 基于ID的精确匹配，性能高效
- **预计算**: 关联关系预先定义，运行时只需查找
- **缓存友好**: 关联关系可以缓存以提高性能

#### 6.3 可维护性
- **明确关系**: 指南与工具的关联关系明确且可追踪
- **灵活配置**: 可以动态添加或移除关联关系
- **版本控制**: 支持关联关系的版本管理

### 7. 与语义相关性的区别

#### 7.1 当前实现（基于关联关系）
```python
# 优点：
# - 安全性高：精确控制工具使用权限
# - 性能好：O(1)的查找复杂度
# - 可预测：行为完全可预测

# 缺点：
# - 需要手动配置：每个关联关系都需要手动创建
# - 灵活性较低：无法动态发现新的工具关联
```

#### 7.2 语义相关性方案（假设）
```python
# 优点：
# - 自动化：可以自动发现相关工具
# - 灵活性高：支持动态工具发现
# - 减少配置：减少手动配置工作

# 缺点：
# - 安全性低：可能使用未授权的工具
# - 性能差：需要计算语义相似性
# - 不可预测：行为可能不可预测
```

### 8. 总结

工具过滤机制采用**基于预定义关联关系的精确匹配**，而不是语义相关性：

1. **过滤方式**: 精确的ID匹配，确保只有被授权的工具被使用
2. **关联创建**: 通过API手动创建指南与工具的关联关系
3. **安全控制**: 提供精确的权限控制和审计能力
4. **性能优化**: 高效的查找和过滤机制

这种设计确保了系统的安全性、可预测性和性能，同时提供了灵活的工具管理能力。