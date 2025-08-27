```python
 async def _do_generate_preamble(
        self,
        context: LoadedContext,
    ) -> Sequence[MessageEventComposition]:
        agent = context.agent

        canrep_context = CannedResponseContext(
            event_emitter=context.session_event_emitter,
            agent=agent,
            customer=context.customer,
            context_variables=context.state.context_variables,
            interaction_history=context.interaction.history,
            terms=list(context.state.glossary_terms),
            ordinary_guideline_matches=context.state.ordinary_guideline_matches,
            tool_enabled_guideline_matches=context.state.tool_enabled_guideline_matches,
            journeys=context.state.journeys,
            capabilities=context.state.capabilities,
            tool_insights=context.state.tool_insights,
            staged_tool_events=context.state.tool_events,
            staged_message_events=context.state.message_events,
        )

        prompt_builder = PromptBuilder(
            on_build=lambda prompt: self._logger.trace(
                f"Canned response Preamble Prompt:\n{prompt}"
            )
        )

        prompt_builder.add_agent_identity(agent)

        preamble_responses: Sequence[CannedResponse] = []
        preamble_choices: list[str] = []

        if agent.composition_mode != CompositionMode.CANNED_STRICT:
            preamble_choices = [
                "Hey there!",
                "Just a moment.",
                "Hello.",
                "Sorry to hear that.",
                "Definitely.",
                "Let me check that for you.",
            ]

            preamble_choices_text = "".join([f"\n- {choice}" for choice in preamble_choices])

            instructions = f"""\
You must not assume anything about how to handle the interaction in any way, shape, or form, beyond just generating the right, nuanced preamble message.

Example preamble messages:
{preamble_choices_text}
etc.

Basically, the preamble is something very short that continues the interaction naturally, without committing to any later action or response.
We leave that later response to another agent. Make sure you understand this.

You must generate the preamble message. You must produce a JSON object with a single key, "preamble", holding the preamble message as a string.
"""
        else:
            preamble_responses = [
                canrep
                for canrep in await self._entity_queries.find_canned_responses_for_context(
                    agent=agent,
                    journeys=canrep_context.journeys,
                    guidelines=[m.guideline for m in canrep_context.guideline_matches],
                )
                if Tag.preamble() in canrep.tags
            ]

            with self._logger.operation(
                "Rendering canned preamble templates", create_scope=False, level=LogLevel.TRACE
            ):
                preamble_choices = [
                    str(r.rendered_text)
                    for r in await self._render_responses(canrep_context, preamble_responses)
                    if not r.failed
                ]

            if not preamble_choices:
                return []

            # LLMs are usually biased toward the last choices, so we shuffle the list.
            shuffle(preamble_choices)

            preamble_choices_text = "".join([f'\n- "{c}"' for c in preamble_choices])

            instructions = f"""\
These are the preamble messages you can choose from. You must ONLY choose one of these: ###
{preamble_choices_text}
###

Basically, the preamble is something very short that continues the interaction naturally, without committing to any later action or response.
We leave that later response to another agent. Make sure you understand this.

Instructions:
- Note that some of the choices are more generic, and some are more specific to a particular scenario.
- If you're unsure what to choose --> prefer to go with a more generic, bland choice. This should be 80% of cases.
  Examples of generic choices: "Hey there!", "Just a moment.", "Hello.", "Got it."
- If you see clear value in saying something more specific and nuanced --> then go with a more specific choice. This should be 20% or less of cases.
  Examples of specific choices: "Let me check that for you.", "Sorry to hear that.", "Thanks for your patience."

You must now choose the preamble message. You must produce a JSON object with a single key, "preamble", holding the preamble message as a string,
EXACTLY as it is given (pay attention to subtleties like punctuation and copy your choice EXACTLY as it is given above).



以下是您可以选择的序言信息。您只能从以下选项中选择一项：###
{preamble_choices_text}
###

简而言之，序言非常简短，可以自然地延续互动，而不会承诺任何后续操作或响应。
我们将后续响应留给其他客服人员处理。请务必理解这一点。

说明：
- 请注意，有些选项比较通用，有些选项则针对特定场景。
- 如果您不确定该选择什么 --> 建议您选择更通用、更平淡的选项。这应该占 80% 的情况。
通用选项示例：“您好！”、 “请稍等”、“您好”、“明白了”。
- 如果您认为更具体、更细致的表达更有价值 --> 请选择更具体的选项。这应该占 20% 或更少的情况。
具体选项示例：“我帮您检查一下。”，“很遗憾听到这个消息。”，“感谢您的耐心等待。”

现在您必须选择前导消息。您必须生成一个 JSON 对象，其中包含一个键“preamble”，该键将前导消息以字符串形式保存，
并且必须与给出的内容完全一致（请注意标点符号等细节，并严格按照上面给出的内容复制您的选择）。

"""

        prompt_builder.add_section(
            name="canned-response-fluid-preamble-instructions",
            template="""\
You are an AI agent that is expected to generate a preamble message for the customer.

The actual message will be sent later by a smarter agent. Your job is only to generate the right preamble in order to save time.

{composition_mode_specific_instructions}

You will now be given the current state of the interaction to which you must generate the next preamble message.


你是一位 AI 代理，需要为客户生成一条前导消息。
实际消息稍后将由更智能的代理发送。你的任务只是生成正确的前导消息以节省时间。
{composition_mode_specific_instructions}
现在，你将获得交互的当前状态，你必须根据该状态生成下一条前导消息。
""",
            props={
                "composition_mode_specific_instructions": instructions,
                "composition_mode": agent.composition_mode,
                "preamble_choices": preamble_choices,
            },
        )

        prompt_builder.add_interaction_history_in_message_generation(
            canrep_context.interaction_history,
            context.state.message_events,
        )

        await canrep_context.event_emitter.emit_status_event(
            correlation_id=f"{self._correlator.correlation_id}",
            data={
                "status": "typing",
                "data": {},
            },
        )

        canrep = await self._canrep_fluid_preamble_generator.generate(
            prompt=prompt_builder, hints={"temperature": 0.1}
        )

        self._logger.trace(
            f"Canned Response Preamble Completion:\n{canrep.content.model_dump_json(indent=2)}"
        )

        if agent.composition_mode == CompositionMode.CANNED_STRICT:
            if canrep.content.preamble not in preamble_choices:
                self._logger.error(
                    f"Selected preamble '{canrep.content.preamble}' is not in the list of available preamble canned_responses."
                )
                return []

        if await self._hooks.call_on_preamble_generated(context, payload=canrep.content.preamble):
            # If we're in, the hook did not bail out.

            emitted_event = await canrep_context.event_emitter.emit_message_event(
                correlation_id=f"{self._correlator.correlation_id}",
                data=MessageEventData(
                    message=canrep.content.preamble,
                    participant=Participant(id=agent.id, display_name=agent.name),
                    tags=[Tag.preamble()],
                ),
            )

            return [
                MessageEventComposition(
                    generation_info={"message": canrep.info},
                    events=[emitted_event],
                )
            ]

        return []


```




## Parlant多次回复实现机制深度分析

### 1. 核心发现：Preamble（前言）机制

Parlant通过**Preamble机制**实现"先回复确认，再执行任务"的多次回复流程。

#### 1.1 Preamble任务检查
```python
# 位置: src/parlant/core/engines/alpha/engine.py
async def _get_preamble_task(self, context: LoadedContext) -> asyncio.Task[bool]:
    async def preamble_task() -> bool:
        if (
            # 只在第一次迭代时考虑preamble
            len(context.state.iterations) == 0
            and await self._perceived_performance_policy.is_preamble_required(context)
        ):
            if not await self._hooks.call_on_generating_preamble(context):
                return False

            # 延迟发送preamble
            await asyncio.sleep(
                await self._perceived_performance_policy.get_preamble_delay(context),
            )

            # 生成并发送preamble消息
            if await self._generate_preamble(context):
                context.interaction = await self._load_interaction_state(context.info)

            await self._emit_ready_event(context)

            if not await self._hooks.call_on_preamble_emitted(context):
                return False

            # 发送处理状态事件
            await asyncio.sleep(
                await self._perceived_performance_policy.get_processing_indicator_delay(context),
            )

            await self._emit_processing_event(context, stage="Interpreting")

            return True
        else:
            return True  # 不需要preamble消息

    return asyncio.create_task(preamble_task())
```

### 2. Preamble生成逻辑

#### 2.1 Preamble内容生成
```python
# 位置: src/parlant/core/engines/alpha/canned_response_generator.py
async def _do_generate_preamble(self, context: LoadedContext) -> Sequence[MessageEventComposition]:
    # 获取preamble选择
    preamble_choices: list[str] = [
        "Hey there!",
        "Just a moment.",
        "Hello.",
        "Sorry to hear that.",
        "Definitely.",
        "Let me check that for you.",
    ]

    instructions = f"""\
You must not assume anything about how to handle the interaction in any way, shape, or form, beyond just generating the right, nuanced preamble message.

Example preamble messages:
{preamble_choices_text}
etc.

Basically, the preamble is something very short that continues the interaction naturally, without committing to any later action or response.
We leave that later response to another agent. Make sure you understand this.

You must generate the preamble message. You must produce a JSON object with a single key, "preamble", holding the preamble message as a string.
"""
```

#### 2.2 Preamble发送
```python
# 位置: src/parlant/core/engines/alpha/engine.py
async def _generate_preamble(self, context: LoadedContext) -> bool:
    generated_messages = False

    # 调用消息组合器生成preamble
    for event_generation_result in await self._get_message_composer(
        context.agent
    ).generate_preamble(context=context):
        generated_messages = True
        # 将preamble消息添加到状态中
        context.state.message_events += [e for e in event_generation_result.events if e]

    return generated_messages
```

### 3. 多次回复的核心实现

#### 3.1 消息分割和多次发送
```python
# 位置: src/parlant/core/engines/alpha/canned_response_generator.py
async def _do_generate_events(self, loaded_context, context, responses, composition_mode, temperature):
    if result is not None:
        # 将消息按双换行符分割成多个子消息
        sub_messages = result.message.strip().split("\n\n")
        events = []

        while sub_messages:
            m = sub_messages.pop(0)  # 取出第一个消息

            if await self._hooks.call_on_message_generated(loaded_context, payload=m):
                # 发送当前消息
                event = await event_emitter.emit_message_event(
                    correlation_id=self._correlator.correlation_id,
                    data=MessageEventData(
                        message=m,
                        participant=Participant(id=agent.id, display_name=agent.name),
                        draft=result.draft,
                        canned_responses=result.canned_responses,
                    )
                )
                events.append(event)

            # 发送ready状态
            await context.event_emitter.emit_status_event(
                correlation_id=self._correlator.correlation_id,
                data={
                    "status": "ready",
                    "data": {},
                },
            )

            # 如果还有下一个消息，添加延迟和typing状态
            if next_message := sub_messages[0] if sub_messages else None:
                await self._perceived_performance_policy.get_follow_up_delay()

                await context.event_emitter.emit_status_event(
                    correlation_id=self._correlator.correlation_id,
                    data={
                        "status": "typing",
                        "data": {},
                    },
                )

                # 计算打字延迟
                typing_speed_in_words_per_minute = 50
                initial_delay = 0.0

                word_count_for_the_message_that_was_just_sent = len(m.split())
                if word_count_for_the_message_that_was_just_sent <= 10:
                    initial_delay += 0.5
                else:
                    initial_delay += (
                        word_count_for_the_message_that_was_just_sent
                        / typing_speed_in_words_per_minute
                    ) * 2

                word_count_for_next_message = len(next_message.split())
                if word_count_for_next_message <= 10:
                    initial_delay += 1
                else:
                    initial_delay += 2

                # 等待延迟
                await asyncio.sleep(
                    initial_delay
                    + (word_count_for_next_message / typing_speed_in_words_per_minute)
                )
```

### 4. 完整的多次回复流程

#### 4.1 第一阶段：Preamble（确认消息）
```python
# 用户发送消息后
async def _do_process(self, context: LoadedContext) -> None:
    # 1. 发送acknowledged状态（静默）
    await self._emit_acknowledgement_event(context)
    
    # 2. 检查是否需要preamble
    while not context.state.prepared_to_respond:
        preamble_task = await self._get_preamble_task(context)
        
        # 3. 生成并发送preamble消息
        if await self._generate_preamble(context):
            # 发送类似"Just a moment."的确认消息
            # 然后发送"Interpreting"处理状态
```

#### 4.2 第二阶段：工具调用和任务执行
```python
    # 4. 执行工具调用（如天气查询）
    iteration_result = await self._run_preparation_iteration(context, preamble_task)
    
    # 5. 工具调用过程中可能发送多个状态事件
    await self._emit_processing_event(context, stage="Fetching data")
    # 工具执行...
    await self._emit_processing_event(context, stage="Processing results")
```

#### 4.3 第三阶段：最终回复生成
```python
    # 6. 生成最终回复
    with CancellationSuppressionLatch() as latch:
        message_generation_inspections = await self._generate_messages(context, latch)
        
        # 7. 如果回复包含多个段落，会分割成多个消息发送
        # 每个消息之间有延迟和typing状态
```

### 5. 性能感知策略

#### 5.1 Preamble触发条件
```python
# 位置: src/parlant/core/engines/alpha/perceived_performance_policy.py
async def is_preamble_required(self, context: LoadedContext | None = None) -> bool:
    if context is None:
        return False

    if self._last_agent_message_is_preamble(context):
        return False

    previous_wait_times = self._calculate_previous_customer_wait_times(context)

    if len(previous_wait_times) <= 2:
        # 前几次回复时，主动显示生命迹象以吸引客户
        return True

    last_2_wait_times = previous_wait_times[-2:]
    if all(wait_time >= 5 for wait_time in last_2_wait_times):
        # 如果最近两次等待时间超过5秒，需要preamble保持客户参与
        return True

    return False
```

#### 5.2 延迟策略
```python
async def get_preamble_delay(self, context: LoadedContext | None = None) -> float:
    return random.uniform(1.5, 2.0)  # 1.5-2秒随机延迟

async def get_follow_up_delay(self, context: LoadedContext | None = None) -> float:
    return random.uniform(0.5, 1.5)  # 0.5-1.5秒随机延迟
```

### 6. 实际示例：天气查询流程

#### 6.1 用户输入
```
用户: "今天天气怎么样？"
```

#### 6.2 系统响应序列
```python
# 1. 立即发送acknowledged状态（静默）
{
    "status": "acknowledged",
    "data": {}
}

# 2. 延迟1.5-2秒后发送preamble消息
{
    "kind": "message",
    "data": {
        "message": "Just a moment.",
        "participant": {"id": "agent_id", "display_name": "Agent"}
    }
}

# 3. 发送ready状态
{
    "status": "ready",
    "data": {}
}

# 4. 延迟1-2秒后发送处理状态
{
    "status": "processing",
    "data": {"stage": "Interpreting"}
}

# 5. 执行天气查询工具调用
{
    "status": "processing", 
    "data": {"stage": "Fetching weather data"}
}

# 6. 生成最终回复并可能分割发送
{
    "kind": "message",
    "data": {
        "message": "今天天气晴朗，温度25°C。",
        "participant": {"id": "agent_id", "display_name": "Agent"}
    }
}

# 7. 如果回复较长，可能继续发送
{
    "status": "typing",
    "data": {}
}
# 延迟后发送第二部分
{
    "kind": "message", 
    "data": {
        "message": "建议您外出时带防晒用品。",
        "participant": {"id": "agent_id", "display_name": "Agent"}
    }
}
```

### 7. 关键设计优势

#### 7.1 用户体验优化
- **即时反馈**: Preamble提供即时确认
- **自然对话**: 模拟人类打字和思考过程
- **进度透明**: 通过状态事件显示处理进度

#### 7.2 技术实现优势
- **事件驱动**: 每个消息都是独立事件
- **可取消性**: 新消息可以中断当前处理
- **性能感知**: 根据等待时间动态调整策略

### 8. 总结

Parlant的多次回复机制通过以下方式实现：

1. **Preamble机制**: 在复杂任务前发送简短确认消息
2. **消息分割**: 将长回复按段落分割成多个消息
3. **状态管理**: 通过状态事件控制显示效果
4. **延迟策略**: 模拟人类自然的对话节奏
5. **性能感知**: 根据历史等待时间动态调整策略

这种设计既保证了用户体验的流畅性，又支持了复杂的后台处理逻辑，是现代AI对话系统的优秀实践。