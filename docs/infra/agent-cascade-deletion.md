# Agent 级联删除功能设计

## 概述

本文档描述了 Parlant 系统中 Agent 级联删除功能的设计和实现。该功能确保当删除一个 Agent 时，所有与其关联的对象都会被正确清理，维护数据一致性。

## 架构设计决策

### 为什么选择在 Application 层实现？

经过深入分析，我们选择在 `Application` 类中实现级联删除功能，原因如下：

1. **架构职责清晰**：`Application` 是应用层协调器，负责协调各个模块之间的交互
2. **依赖关系最完整**：`Application` 已经拥有所有模块的引用，无需额外注入依赖
3. **符合现有架构模式**：从代码中可以看到 Application 已经在协调跨模块操作
4. **最小侵入性**：不需要修改任何现有模块的构造函数
5. **符合单一职责原则**：各个 Module 专注于单一职责，Application 负责协调

## 实现细节

### 删除顺序

级联删除按照以下顺序执行，确保依赖关系正确：

1. **Sessions** (直接引用 agent_id)
2. **Guidelines** (通过 agent tag 关联)
3. **Journeys** (通过 agent tag 关联)
4. **Context Variables** (通过 agent tag 关联)
5. **Capabilities** (通过 agent tag 关联)
6. **Canned Responses** (通过 agent tag 关联)
7. **Glossary Terms** (通过 agent tag 关联)
8. **Relationships** (涉及该 agent 的关系) - 待实现
9. **Evaluations** (与该 agent 相关的评估) - 待实现
10. **Agent 本身**

### 批量异步处理

使用 `safe_gather` 进行批量异步处理，提高性能：

```python
# 定义删除任务，按依赖关系排序
deletion_tasks = [
    self._delete_sessions_for_agent(agent_id),
    self._delete_guidelines_for_agent(agent_tag),
    # ... 其他任务
]

# 批量异步执行所有删除任务
await safe_gather(*deletion_tasks)
```

### 优化细节

1. **直接使用序列**：所有 `find` 方法都返回 `Sequence[Type]`，直接遍历而不需要额外的条件检查
2. **移除冗余检查**：`safe_gather` 可以处理空列表，无需额外的 `if` 条件
3. **统一模式**：所有辅助方法都遵循相同的模式，提高代码一致性

```python
async def _delete_xxx_for_agent(self, agent_tag: TagId) -> None:
    """删除指定 Agent 的所有 XXX"""
    items = await self.xxx.find(tag_id=agent_tag)
    delete_tasks = [self.xxx.delete(item.id) for item in items]
    await safe_gather(*delete_tasks)
```

### 错误处理

- **Agent 验证**：首先验证 Agent 是否存在，如果不存在则抛出 `ItemNotFoundError`
- **异常捕获**：每个删除步骤都有独立的异常处理，确保错误信息清晰
- **优雅降级**：如果某个类型的实体没有找到，会记录信息日志而不是抛出异常
- **批量处理**：使用 `safe_gather` 确保即使某个删除任务失败，其他任务仍能继续
- **详细日志**：每个步骤都有详细的日志记录，包括成功和失败的情况

### 性能监控

- **耗时统计**：记录整个级联删除操作的总耗时
- **分步日志**：每个删除步骤都有独立的日志记录
- **进度跟踪**：实时记录删除进度和结果
- **性能分析**：便于分析删除操作的性能瓶颈

```python
# 性能监控示例
start_time = time.time()
self._logger.info(f"🚀 Starting cascade deletion for agent {agent_id}")
# ... 执行删除操作 ...
total_time = time.time() - start_time
self._logger.info(f"🎉 Cascade deletion completed in {total_time:.2f} seconds")
```

## 5. 缓存处理架构重构

### CachedEvaluationModule 设计

为了优雅地处理缓存清理，我们进行了以下架构重构：

1. **创建独立的 CachedEvaluationService**：
   - 将 `_CachedEvaluator` 重构为独立的 `CachedEvaluationService`
   - 提供标准的缓存管理接口
   - 支持按 Agent 清理缓存

2. **Application 层集成**：
   - 创建 `CachedEvaluationModule` 作为 Application 层的标准模块
   - 在 `Application` 类中集成 `CachedEvaluationModule`
   - 提供统一的缓存管理接口

3. **自动缓存清理**：
   - 在 `delete_agent_cascade` 中自动清理相关缓存
   - 缓存清理失败不会阻止整个删除过程
   - 确保数据一致性

### 缓存清理流程

```python
async def _clear_cached_evaluations_for_agent(self, agent_id: AgentId) -> None:
    """清理指定 Agent 的所有缓存评估"""
    try:
        await self.cached_evaluations.clear_cache_for_agent(agent_id)
    except Exception as e:
        # 缓存清理失败不应该阻止整个删除过程
        # 记录错误但继续执行
        pass  # 在实际实现中应该记录日志
```

### 架构优势

- **统一管理**：所有缓存操作通过 Application 层统一管理
- **解耦合**：缓存服务与 Server 类解耦，提高可维护性
- **可扩展**：易于添加新的缓存类型和管理策略
- **容错性**：缓存清理失败不影响主要业务逻辑

## 使用方法

```python
# 在 API 层或服务层调用
await app.delete_agent_cascade(agent_id)
```

## 缓存清理

### 当前状态
- 各个 Store 的 delete 方法应该自动处理缓存清理
- `_CachedEvaluator` 的缓存清理需要在更高层处理

### 未来改进
- 考虑在 Application 层添加缓存清理逻辑
- 或者在各个 Store 的 delete 方法中自动处理缓存清理

## 扩展性

### 待实现的功能
1. **Relationships 删除**：需要根据实际的 RelationshipModule 接口调整
2. **Evaluations 删除**：需要根据实际的 EvaluationModule 接口调整

### 添加新的关联对象
当需要添加新的与 Agent 关联的对象时，只需要：
1. 在 `delete_agent_cascade` 方法中添加新的删除任务
2. 实现对应的 `_delete_xxx_for_agent` 辅助方法

## 测试建议

1. **单元测试**：测试各个辅助方法的正确性
2. **集成测试**：测试完整的级联删除流程
3. **性能测试**：测试大量数据时的删除性能
4. **错误处理测试**：测试各种异常情况的处理

## 注意事项

1. **不可逆操作**：级联删除是不可逆的，请谨慎使用
2. **数据备份**：在生产环境中使用前，建议先备份数据
3. **权限控制**：确保只有有权限的用户才能执行级联删除
4. **监控和日志**：建议添加详细的监控和日志记录

## 相关文件

- `src/parlant/core/application.py` - 主要实现
- `src/parlant/core/tags.py` - Tag 相关逻辑
- `src/parlant/core/async_utils.py` - 异步工具函数
