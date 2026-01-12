# Copyright 2025 Emcie Co Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Test for evaluation bug fix: 'Evaluation' object has no attribute 'node_properties'

验证 journey 评估时 node_properties 访问的 bug 修复。
"""

import pytest
import httpx

from tests.api.helpers import (
    DEFAULT_BASE_URL,
    send_message,
    wait_for_ai_response,
    generate_session_id,
)


@pytest.mark.asyncio
async def test_journey_evaluation_no_attribute_error():
    """
    验证 journey 评估不会抛出 'node_properties' 属性错误。
    
    Bug 场景：
    1. guideline 被识别为 journey_candidate
    2. 创建 journey 并评估
    3. 处理评估结果时访问 result.node_properties 报错
    
    修复后：使用 result.properties.get("node_properties", {})
    """
    session_id = generate_session_id("eval-bug")
    tenant_id = "test-tenant"
    chatbot_id = "test-chatbot"
    
    async with httpx.AsyncClient(base_url=DEFAULT_BASE_URL, timeout=120) as client:
        # 发送消息
        resp = await send_message(
            client=client,
            message="Hello, I need help",
            session_id=session_id,
            tenant_id=tenant_id,
            chatbot_id=chatbot_id,
        )
        
        # 验证请求被接受（不是 500 错误）
        assert resp.get("status") != 500, f"Server error: {resp.get('message')}"
        assert "node_properties" not in str(resp.get("message", "")), \
            "Bug not fixed: 'node_properties' attribute error"
        
        # 等待 AI 响应（可选，确保完整流程无错误）
        ai_response = await wait_for_ai_response(
            client=client,
            session_id=session_id,
            max_wait=30,
        )
        
        # 只要没有 500 错误就算通过
        # AI 响应可能为空（取决于配置），不强制要求
        print(f"AI response: {ai_response or '(no response)'}")
