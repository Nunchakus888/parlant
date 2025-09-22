
### 1. 异步机制分析

**任务调度层面：**
- ✅ `engine.process()` 确实在独立的 `asyncio.Task` 中运行
- ✅ 使用 `BackgroundTaskService` 管理后台任务
- ✅ 每个会话的处理都在独立的任务中

**但是问题在于：**
- ❌ 虽然任务本身是异步的，但任务内部的操作仍然是**顺序执行**的
- ❌ `_do_process()` 方法使用 `await` 同步等待所有操作完成
- ❌ 整个 `_do_process()` 方法必须完成才能返回

### 2. 阻塞的根本原因

**当前流程：**
```python
async def _do_process():
    await _generate_messages()        # 发送消息
    await _emit_ready_event()         # 发送 ready 状态
    await create_inspection()         # 数据库写入 (阻塞)
    await _add_agent_state()          # LLM 调用 (阻塞)
    await call_on_messages_emitted()  # 钩子调用 (阻塞)
```

**问题：**
- 客户端需要等待整个 `_do_process()` 方法完成才能收到 "ready" 状态
- 后置处理（数据库写入、LLM调用）阻塞了 ready 状态的发送

### 3. 优化空间

**方案：重构 `_do_process()` 方法，分离同步和异步操作**

```python
async def _do_process(self, context: LoadedContext) -> None:
    # 前置处理
    message_generation_inspections = await self._generate_messages(context, latch)
    await self._emit_ready_event(context)  # 立即发送 ready 状态
    
    # 启动后置处理，不等待完成
    asyncio.create_task(self._post_process(context, message_generation_inspections))

async def _post_process(self, context: LoadedContext, message_generation_inspections):
    try:
        await self._entity_commands.create_inspection(...)
        await self._add_agent_state(...)
        await self._hooks.call_on_messages_emitted(context)
    except Exception as e:
        self._logger.warning(f"Post-processing failed: {e}")
```

### 4. 优化优势

- ✅ **用户立即收到 ready 状态**，可以发送新消息
- ✅ **后置处理在后台进行**，不影响用户体验
- ✅ **保持数据完整性**和统计准确性
- ✅ **最小化代码变更**，保持架构一致性
- ✅ **错误处理健壮**，后置处理失败不影响主流程

### 5. 关键洞察

**异步 vs 阻塞的区别：**
- ✅ **异步**：多个任务可以并发执行
- ❌ **阻塞**：单个任务内部的操作是顺序执行的
- ❌ 即使任务本身是异步的，任务内部的操作仍然是顺序的

这个优化方案可以显著改善用户体验，让用户能够立即响应AI的消息，而后置处理在后台进行，既保证了响应速度，又保持了数据的完整性。