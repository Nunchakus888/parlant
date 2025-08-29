#!/usr/bin/env python3
"""
Parlant 简化聊天接口测试
只需要一次请求即可完成对话
"""

import requests
import json
import asyncio
import parlant.sdk as p


def test_simple_chat_api():
    """测试简化的聊天API - 使用requests直接调用"""
    
    print("🚀 测试简化聊天接口...")
    
    # 1. 第一次聊天 - 系统会自动创建会话
    response = requests.post(
        "http://localhost:8800/sessions/chat",
        json={
            "message": "你好，我需要帮助",
            # 可选参数都可以不传，系统会自动处理
        }
    )
    
    if response.status_code == 200:
        event = response.json()
        ai_message = event['data']['message']
        print(f"👤 用户: 你好，我需要帮助")
        print(f"🤖 AI: {ai_message}")
        print(f"✅ 会话ID: {event['id']}")
    else:
        print(f"❌ 错误: {response.status_code} - {response.text}")
        return
    
    # 2. 继续对话 - 系统会自动找到之前的会话
    response = requests.post(
        "http://localhost:8000/sessions/chat",
        json={
            "message": "你能帮我做什么？",
            "timeout": 60  # 可以自定义超时时间
        }
    )
    
    if response.status_code == 200:
        event = response.json()
        ai_message = event['data']['message']
        print(f"\n👤 用户: 你能帮我做什么？")
        print(f"🤖 AI: {ai_message}")
    else:
        print(f"❌ 错误: {response.status_code} - {response.text}")


async def test_simple_chat_with_server():
    """完整测试：启动服务器并测试简化接口"""
    
    # 启动服务器
    async with p.Server(
        port=8000,
        tool_service_port=8001,
        log_level=p.LogLevel.INFO
    ) as server:
        
        # 创建一个默认代理
        client = p.AsyncParlantClient("http://localhost:8000")
        await client.agents.create(
            name="Default",
            description="默认AI助手"
        )
        print("✅ 创建默认代理")
        
        # 使用简化API进行对话
        print("\n📱 使用简化API进行对话...")
        test_simple_chat_api()


class SimpleChatClient:
    """简化的聊天客户端 - 展示如何使用新接口"""
    
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.agent_id = None
        self.customer_id = None
    
    def chat(self, message, **kwargs):
        """发送消息并获取AI回复 - 一次调用完成"""
        
        data = {
            "message": message,
            "agent_id": self.agent_id,
            "customer_id": self.customer_id,
            **kwargs
        }
        
        response = requests.post(
            f"{self.base_url}/sessions/chat",
            json=data
        )
        
        if response.status_code == 200:
            event = response.json()
            return event['data']['message']
        elif response.status_code == 504:
            return "⏰ AI响应超时"
        else:
            return f"❌ 错误: {response.text}"
    
    def set_agent(self, agent_id):
        """设置要对话的代理"""
        self.agent_id = agent_id
    
    def set_customer(self, customer_id):
        """设置客户ID"""
        self.customer_id = customer_id


def demo_simple_client():
    """演示简化客户端的使用"""
    
    print("🎯 简化客户端演示\n")
    
    # 创建客户端
    chat = SimpleChatClient()
    
    # 对话1
    reply = chat.chat("你好")
    print(f"👤: 你好")
    print(f"🤖: {reply}\n")
    
    # 对话2
    reply = chat.chat("今天天气怎么样？")
    print(f"👤: 今天天气怎么样？")
    print(f"🤖: {reply}\n")
    
    # 对话3 - 带自定义超时
    reply = chat.chat("帮我写一个Python函数", timeout=60)
    print(f"👤: 帮我写一个Python函数")
    print(f"🤖: {reply}")


if __name__ == "__main__":
    # 选择测试方式
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        # 启动服务器并测试
        asyncio.run(test_simple_chat_with_server())
    elif len(sys.argv) > 1 and sys.argv[1] == "demo":
        # 演示简化客户端
        demo_simple_client()
    else:
        # 直接测试API（需要服务器已经运行）
        test_simple_chat_api()
        print("\n" + "="*50)
        print("提示：")
        print("1. 运行 'python test_simple_chat.py' - 测试API（需要服务器运行）")
        print("2. 运行 'python test_simple_chat.py server' - 启动服务器并测试")
        print("3. 运行 'python test_simple_chat.py demo' - 演示简化客户端")
