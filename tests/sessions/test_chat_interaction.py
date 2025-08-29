#!/usr/bin/env python3
"""
Parlant 客户端问答互动测试例子
"""

import asyncio
import parlant.sdk as p
from typing import Optional


async def test_chat_interaction():
    """测试客户端与AI代理的问答互动"""
    
    # 1. 启动服务器
    async with p.Server(
        port=8000,
        tool_service_port=8001,
        log_level=p.LogLevel.INFO
    ) as server:
        
        # 2. 创建客户端
        client = p.AsyncParlantClient("http://localhost:8000")
        
        # 3. 创建AI代理
        agent = await client.agents.create(
            name="测试助手",
            description="一个简单的测试AI助手"
        )
        print(f"✅ 创建代理: {agent.name} (ID: {agent.id})")
        
        # 4. 创建会话
        session = await client.sessions.create(
            agent_id=agent.id,
            title="测试对话"
        )
        print(f"✅ 创建会话: {session.id}")
        
        # 5. 发送消息并等待回复
        await send_message_and_wait_reply(client, session.id, "你好，请介绍一下你自己")
        
        # 6. 继续对话
        await send_message_and_wait_reply(client, session.id, "你能帮我做什么？")


async def send_message_and_wait_reply(client: p.AsyncParlantClient, session_id: str, message: str):
    """发送消息并等待AI回复"""
    
    print(f"\n👤 用户: {message}")
    
    # 发送消息
    event = await client.sessions.create_event(
        session_id=session_id,
        kind="message",
        source="customer",
        message=message
    )
    
    # 等待AI回复（长轮询）
    agent_messages = await client.sessions.list_events(
        session_id=session_id,
        min_offset=event.offset,
        source="ai_agent",
        kinds="message",
        wait_for_data=30  # 等待30秒
    )
    
    if agent_messages:
        ai_reply = agent_messages[0].data.get("message", "")
        print(f"🤖 AI: {ai_reply}")
    else:
        print("❌ 未收到AI回复")


if __name__ == "__main__":
    asyncio.run(test_chat_interaction())
