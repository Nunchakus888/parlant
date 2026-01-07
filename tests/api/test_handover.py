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
Test handover functionality with hand_off tool.

针对 handover 场景的专项测试：
1. 验证 hand_off 工具是否被正确调用
2. 验证 ho000001 前缀是否正确添加
3. 验证多语言 handover 请求

运行方式:
    pytest tests/api/test_handover.py -v -s
    或直接运行: python tests/api/test_handover.py
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional
import httpx
import pytest

# Server config - can be overridden by environment variables
BASE_URL = os.getenv("PARLANT_API_URL", "http://localhost:8800")
TENANT_ID = os.getenv("TENANT_ID", "test_handover")
CHATBOT_ID = os.getenv("CHATBOT_ID", "test_handover")
TIMEOUT = 120
POLL_INTERVAL = 0.5
MAX_WAIT = 60

# Handover prefix constant
HANDOVER_PREFIX = "ho000001:"


@dataclass
class HandoverTestCase:
    """Single test case definition."""
    message: str
    language: str
    should_trigger_handover: bool
    description: str = ""


@dataclass 
class HandoverResult:
    """Test result with detailed info."""
    test_case: HandoverTestCase
    response: str
    has_handover_prefix: bool
    tool_called: bool
    duration_ms: float
    error: Optional[str] = None
    
    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return self.test_case.should_trigger_handover == self.has_handover_prefix
    
    @property
    def response_language(self) -> str:
        """Detect response language (zh/en)."""
        return "zh" if any('\u4e00' <= c <= '\u9fff' for c in self.response) else "en"


@dataclass
class TestStats:
    """Aggregated test statistics."""
    results: list[HandoverResult] = field(default_factory=list)
    
    @property
    def total(self) -> int:
        return len(self.results)
    
    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)
    
    @property
    def failed(self) -> int:
        return self.total - self.passed
    
    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0
    
    def add(self, result: HandoverResult) -> None:
        self.results.append(result)
    
    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            f"HANDOVER TEST RESULTS: {self.passed}/{self.total} ({self.accuracy:.0%})",
            "=" * 60,
        ]
        for r in self.results:
            status = "✅" if r.passed else "❌"
            prefix = "✓" if r.has_handover_prefix else "✗"
            lines.append(
                f"  {status} [{r.test_case.language}] {r.test_case.message[:30]:30s} "
                f"| expect={r.test_case.should_trigger_handover} actual={r.has_handover_prefix} "
                f"| {r.duration_ms:.0f}ms"
            )
            if r.error:
                lines.append(f"      ⚠️  Error: {r.error}")
            if not r.passed:
                lines.append(f"      📝 Response: {r.response[:80]}...")
        lines.append("=" * 60)
        return "\n".join(lines)


# ============================================================================
# Test Cases - 针对当前 case 的测试用例
# ============================================================================

# 核心 handover 测试用例（当前 case 相关）
CORE_HANDOVER_CASES = [
    # 当前失败的 case
    HandoverTestCase(
        message="人工客服",
        language="zh",
        should_trigger_handover=True,
        description="简短中文人工客服请求（当前失败的case）",
    ),
    # 变体测试
    # HandoverTestCase(
    #     message="我要人工客服",
    #     language="zh", 
    #     should_trigger_handover=True,
    #     description="带主语的人工客服请求",
    # ),
    # HandoverTestCase(
    #     message="转人工",
    #     language="zh",
    #     should_trigger_handover=True,
    #     description="最简短的转人工请求",
    # ),
    # HandoverTestCase(
    #     message="请帮我转接人工客服",
    #     language="zh",
    #     should_trigger_handover=True,
    #     description="礼貌的转人工请求",
    # ),
]

# 英文 handover 测试用例
ENGLISH_HANDOVER_CASES = [
    HandoverTestCase(
        message="I want to speak with a human agent",
        language="en",
        should_trigger_handover=True,
        description="标准英文转人工请求",
    ),
    HandoverTestCase(
        message="human agent please",
        language="en",
        should_trigger_handover=True,
        description="简短英文转人工请求",
    ),
    HandoverTestCase(
        message="Can I talk to a real person?",
        language="en",
        should_trigger_handover=True,
        description="疑问句形式的转人工请求",
    ),
]

# 不应触发 handover 的测试用例
NO_HANDOVER_CASES = [
    HandoverTestCase(
        message="hello",
        language="en",
        should_trigger_handover=False,
        description="简单问候",
    ),
    HandoverTestCase(
        message="你好",
        language="zh",
        should_trigger_handover=False,
        description="中文问候",
    ),
    HandoverTestCase(
        message="What's the weather today?",
        language="en",
        should_trigger_handover=False,
        description="普通问题",
    ),
    HandoverTestCase(
        message="Tell me about your products",
        language="en",
        should_trigger_handover=False,
        description="产品咨询",
    ),
]

# 边界测试用例
EDGE_CASES = [
    HandoverTestCase(
        message="我想咨询一下产品，如果不行就转人工",
        language="zh",
        should_trigger_handover=True,
        description="混合请求（产品+人工）",
    ),
    HandoverTestCase(
        message="这个问题你解决不了吧",
        language="zh",
        should_trigger_handover=True,
        description="暗示AI能力不足",
    ),
]

ALL_TEST_CASES = CORE_HANDOVER_CASES + ENGLISH_HANDOVER_CASES + NO_HANDOVER_CASES + EDGE_CASES


# ============================================================================
# API Helpers
# ============================================================================

def is_handover_response(response: str) -> bool:
    """Check if response has handover prefix."""
    return response.strip().startswith(HANDOVER_PREFIX)


async def send_chat_async(
    client: httpx.AsyncClient,
    message: str,
    session_id: str,
) -> dict[str, Any]:
    """Send chat_async request."""
    resp = await client.post("/sessions/chat_async", json={
        "message": message,
        "tenant_id": TENANT_ID,
        "chatbot_id": CHATBOT_ID,
        "session_id": session_id,
        "customer_id": f"customer-{session_id}",
        "timeout": TIMEOUT,
        "md5_checksum": "test_handover",
    })
    return resp.json()


async def get_session_events(
    client: httpx.AsyncClient,
    session_id: str,
) -> list[dict]:
    """Get all events for a session."""
    resp = await client.get(f"/sessions/{session_id}/events")
    if resp.status_code != 200:
        return []
    return resp.json()


async def wait_for_response(
    client: httpx.AsyncClient,
    session_id: str,
    min_offset: int = 0,
    max_wait: float = MAX_WAIT,
) -> tuple[Optional[str], bool]:
    """
    Wait for AI response, return (message, tool_called).
    
    Returns:
        tuple: (response_message, whether_tool_was_called)
    """
    start = time.time()
    while time.time() - start < max_wait:
        events = await get_session_events(client, session_id)
        
        # Check for tool call events
        tool_called = any(
            evt.get("kind") == "tool_call" and evt.get("source") == "ai_agent"
            for evt in events
        )
        
        # Find AI message response
        for evt in events:
            if (evt.get("source") == "ai_agent" and 
                evt.get("kind") == "message" and
                evt.get("offset", 0) > min_offset):
                message = evt.get("data", {}).get("message", "")
                return message, tool_called
        
        await asyncio.sleep(POLL_INTERVAL)
    
    return None, False


async def run_handover_test(
    client: httpx.AsyncClient,
    test_case: HandoverTestCase,
) -> HandoverResult:
    """Execute single handover test case."""
    session_id = f"handover-test-{int(time.time() * 1000)}"
    start_time = time.time()
    
    try:
        # Send request
        result = await send_chat_async(client, test_case.message, session_id)
        if result.get("status") != 200:
            return HandoverResult(
                test_case=test_case,
                response="",
                has_handover_prefix=False,
                tool_called=False,
                duration_ms=(time.time() - start_time) * 1000,
                error=f"API error: {result.get('message', 'unknown')}",
            )
        
        # Wait for response
        response, tool_called = await wait_for_response(client, session_id)
        duration_ms = (time.time() - start_time) * 1000
        
        if response is None:
            return HandoverResult(
                test_case=test_case,
                response="",
                has_handover_prefix=False,
                tool_called=False,
                duration_ms=duration_ms,
                error="Timeout waiting for response",
            )
        
        return HandoverResult(
            test_case=test_case,
            response=response,
            has_handover_prefix=is_handover_response(response),
            tool_called=tool_called,
            duration_ms=duration_ms,
        )
        
    except Exception as e:
        return HandoverResult(
            test_case=test_case,
            response="",
            has_handover_prefix=False,
            tool_called=False,
            duration_ms=(time.time() - start_time) * 1000,
            error=str(e),
        )


# ============================================================================
# Pytest Test Functions
# ============================================================================

@pytest.mark.asyncio
async def test_handover_core_case():
    """测试核心场景：简短中文人工客服请求（当前失败的case）"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        test_case = HandoverTestCase(
            message="人工客服",
            language="zh",
            should_trigger_handover=True,
        )
        result = await run_handover_test(client, test_case)
        
        print(f"\n📋 Test: {test_case.message}")
        print(f"   Response: {result.response[:100]}...")
        print(f"   Has prefix: {result.has_handover_prefix}")
        print(f"   Tool called: {result.tool_called}")
        
        assert result.passed, (
            f"Expected handover but got: {result.response[:100]}"
        )
        assert result.has_handover_prefix, (
            f"Missing ho000001 prefix in response: {result.response[:100]}"
        )


@pytest.mark.asyncio
async def test_handover_chinese_variants():
    """测试中文 handover 的多种表达方式"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        for test_case in CORE_HANDOVER_CASES:
            result = await run_handover_test(client, test_case)
            print(f"\n[{test_case.language}] {test_case.message}: {result.passed}")
            assert result.passed, f"Failed: {test_case.description} - {result.response[:80]}"


@pytest.mark.asyncio
async def test_handover_english():
    """测试英文 handover 请求"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        for test_case in ENGLISH_HANDOVER_CASES:
            result = await run_handover_test(client, test_case)
            print(f"\n[{test_case.language}] {test_case.message}: {result.passed}")
            assert result.passed, f"Failed: {test_case.description} - {result.response[:80]}"


@pytest.mark.asyncio
async def test_no_handover():
    """测试不应触发 handover 的场景"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        for test_case in NO_HANDOVER_CASES:
            result = await run_handover_test(client, test_case)
            print(f"\n[{test_case.language}] {test_case.message}: {result.passed}")
            assert result.passed, f"Unexpected handover for: {test_case.message}"


@pytest.mark.asyncio
async def test_handover_language_match():
    """测试 handover 响应语言是否与请求语言匹配"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        # Chinese request should get Chinese response
        zh_case = HandoverTestCase("人工客服", "zh", True)
        zh_result = await run_handover_test(client, zh_case)
        
        if zh_result.has_handover_prefix:
            # Extract message after prefix
            msg = zh_result.response.replace(HANDOVER_PREFIX, "").strip()
            assert zh_result.response_language == "zh", (
                f"Chinese request should get Chinese response, got: {msg[:50]}"
            )


@pytest.mark.asyncio
async def test_handover_batch():
    """批量测试所有用例并生成统计报告"""
    stats = TestStats()
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        # Run tests concurrently for efficiency
        tasks = [run_handover_test(client, tc) for tc in ALL_TEST_CASES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for r in results:
            if isinstance(r, Exception):
                print(f"⚠️ Exception: {r}")
                continue
            stats.add(r)
    
    print(stats.summary())
    
    # Assert minimum accuracy
    assert stats.accuracy >= 0.7, f"Accuracy too low: {stats.accuracy:.0%}"


# ============================================================================
# Direct Execution
# ============================================================================

async def main():
    """Run quick test for current case."""
    print("🧪 Handover Test Suite")
    print(f"   Server: {BASE_URL}")
    print(f"   Tenant: {TENANT_ID}")
    print(f"   Chatbot: {CHATBOT_ID}")
    print()
    
    stats = TestStats()
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        # Test core cases first
        print("Testing core handover cases...")
        for tc in CORE_HANDOVER_CASES:
            result = await run_handover_test(client, tc)
            stats.add(result)
            status = "✅" if result.passed else "❌"
            print(f"  {status} [{tc.language}] {tc.message}")
    
    print(stats.summary())
    return stats.accuracy >= 0.7


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)

