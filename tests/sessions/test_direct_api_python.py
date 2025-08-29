#!/usr/bin/env python3
"""
Parlant API 直接调用示例 - Python requests
不依赖任何Parlant SDK，直接使用HTTP请求
"""

import requests
import json
import time
from typing import Optional, Dict, Any


class ParlantDirectClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session_id = None
        self.last_offset = 0
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json'
        })

    def create_agent(self, name: str, description: str) -> Dict[str, Any]:
        """创建AI代理"""
        url = f"{self.base_url}/agents"
        data = {
            "name": name,
            "description": description,
            "composition_mode": "fluid"
        }
        
        response = self.session.post(url, json=data)
        response.raise_for_status()
        
        agent = response.json()
        print(f"✅ 创建代理: {agent['name']} (ID: {agent['id']})")
        return agent

    def create_session(self, agent_id: str, title: str = "测试对话") -> Dict[str, Any]:
        """创建会话"""
        url = f"{self.base_url}/sessions"
        data = {
            "agent_id": agent_id,
            "title": title
        }
        
        response = self.session.post(url, json=data)
        response.raise_for_status()
        
        session = response.json()
        self.session_id = session['id']
        print(f"✅ 创建会话: {session['id']}")
        return session

    def send_message(self, message: str) -> Dict[str, Any]:
        """发送消息"""
        if not self.session_id:
            raise ValueError("请先创建会话")
        
        url = f"{self.base_url}/sessions/{self.session_id}/events"
        data = {
            "kind": "message",
            "source": "customer",
            "message": message
        }
        
        response = self.session.post(url, json=data)
        response.raise_for_status()
        
        event = response.json()
        print(f"👤 用户: {message}")
        return event

    def wait_for_reply(self, timeout: int = 30) -> Optional[str]:
        """等待AI回复"""
        if not self.session_id:
            raise ValueError("请先创建会话")
        
        url = f"{self.base_url}/sessions/{self.session_id}/events"
        params = {
            "min_offset": self.last_offset,
            "source": "ai_agent",
            "kinds": "message",
            "wait_for_data": timeout
        }
        
        try:
            response = self.session.get(url, params=params)
            
            if response.status_code == 504:
                print("⏰ 等待超时，未收到回复")
                return None
            
            response.raise_for_status()
            events = response.json()
            
            if events:
                last_event = events[-1]
                self.last_offset = last_event['offset'] + 1
                ai_message = last_event['data']['message']
                print(f"🤖 AI: {ai_message}")
                return ai_message
            
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"❌ 请求错误: {e}")
            return None

    def chat(self, message: str) -> Optional[str]:
        """完整的对话流程"""
        self.send_message(message)
        return self.wait_for_reply()


def test_direct_api():
    """测试直接API调用"""
    client = ParlantDirectClient()
    
    try:
        # 1. 创建代理
        agent = client.create_agent("测试助手", "一个简单的测试AI助手")
        
        # 2. 创建会话
        client.create_session(agent['id'], "Python API测试")
        
        # 3. 发送消息并等待回复
        client.chat("你好，请介绍一下你自己")
        
        # 4. 继续对话
        client.chat("你能帮我做什么？")
        
    except Exception as e:
        print(f"❌ 错误: {e}")


if __name__ == "__main__":
    test_direct_api()
