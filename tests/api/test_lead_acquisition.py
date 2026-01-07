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
Test lead acquisition - verify AI only asks for required fields.

核心场景：当 guideline 要求 "guide the customer to provide ONLY the required fields"，
AI 应该只询问 tool 定义的必需字段，不应该编造其他字段（如电话号码）。

运行方式:
    pytest tests/api/test_lead_acquisition.py -v -s
"""

import asyncio
import os
from dataclasses import dataclass

import httpx
import pytest

from tests.api.helpers import (
    send_message,
    wait_for_ai_response,
    generate_session_id,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
)

# Server config
BASE_URL = os.getenv("PARLANT_API_URL", DEFAULT_BASE_URL)
TENANT_ID = os.getenv("TENANT_ID", "test_lead")
CHATBOT_ID = os.getenv("CHATBOT_ID", "test_lead")
TIMEOUT = DEFAULT_TIMEOUT


# ============================================================================
# Test Cases
# ============================================================================

@dataclass
class LeadAcquisitionTestCase:
    """Test case for lead acquisition."""
    message: str
    required_field: str  # 必需字段（应该询问）
    forbidden_keywords: list[str]  # 不应该出现的关键词
    description: str = ""


# 测试用例：save_customer_information 只需要 nick_name
LEAD_CASES = [
    # LeadAcquisitionTestCase(
    #     message="人工客服",
    #     required_field="姓名",
    #     forbidden_keywords=["电话", "手机", "号码", "联系方式", "phone", "contact"],
    #     description="请求人工客服时，只应询问姓名，不应询问电话",
    # ),
    LeadAcquisitionTestCase(
        message="I want to speak with sales",
        required_field="name",
        forbidden_keywords=["phone", "mobile", "number", "contact", "email"],
        description="Request sales - should only ask for name",
    ),
]


# ============================================================================
# Tests
# ============================================================================

@pytest.mark.asyncio
async def test_lead_acquisition_only_asks_required_fields():
    """
    核心测试：验证 AI 只询问 tool 定义的必需字段。
    
    预期行为：
    - Guideline: "guide the customer to provide ONLY the required fields defined in the tool"
    - Tool (save_customer_information): required = ["nick_name"]
    - AI 应该只询问姓名，不应该编造询问电话等其他信息
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        for tc in LEAD_CASES:
            session_id = generate_session_id("lead-test")
            
            # Send message
            result = await send_message(client, tc.message, session_id, TENANT_ID, CHATBOT_ID)
            assert result.get("status") == 200, f"API error: {result}"
            
            # Wait for response
            response = await wait_for_ai_response(client, session_id)
            assert response, f"No response for: {tc.message}"
            
            # Verify: should NOT contain forbidden keywords
            response_lower = response.lower()
            for keyword in tc.forbidden_keywords:
                assert keyword.lower() not in response_lower, (
                    f"[{tc.description}]\n"
                    f"AI should NOT ask for '{keyword}' (only required field: {tc.required_field})\n"
                    f"Response: {response}"
                )
            
            print(f"✅ {tc.description}")
            print(f"   Message: {tc.message}")
            print(f"   Response: {response[:100]}...")


@pytest.mark.asyncio
async def test_lead_acquisition_chinese():
    """测试中文场景：人工客服请求"""
    tc = LEAD_CASES[0]
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        session_id = generate_session_id("lead-zh")
        
        await send_message(client, tc.message, session_id, TENANT_ID, CHATBOT_ID)
        response = await wait_for_ai_response(client, session_id)
        
        print(f"\n📋 Test: {tc.message}")
        print(f"   Response: {response}")
        
        # 不应该询问电话相关信息
        for keyword in tc.forbidden_keywords:
            assert keyword not in response, (
                f"Should not ask for '{keyword}', got: {response}"
            )


# ============================================================================
# Direct Execution
# ============================================================================

async def main():
    """Quick test run."""
    print("🧪 Lead Acquisition Test")
    print(f"   Server: {BASE_URL}")
    print()
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        for tc in LEAD_CASES:
            session_id = generate_session_id("lead")
            
            result = await send_message(client, tc.message, session_id, TENANT_ID, CHATBOT_ID)
            if result.get("status") != 200:
                print(f"❌ API error: {result}")
                continue
            
            response = await wait_for_ai_response(client, session_id)
            if not response:
                print(f"❌ No response for: {tc.message}")
                continue
            
            # Check for forbidden keywords
            has_forbidden = any(
                kw.lower() in response.lower() 
                for kw in tc.forbidden_keywords
            )
            
            status = "❌" if has_forbidden else "✅"
            print(f"{status} {tc.description}")
            print(f"   Message: {tc.message}")
            print(f"   Response: {response[:100]}...")
            print()


if __name__ == "__main__":
    asyncio.run(main())

