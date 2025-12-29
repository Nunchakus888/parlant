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
Test handover instructions via /sessions/chat_async API.

Requires running server: python -m parlant.bin.server
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional
import httpx
import pytest

# Server config
BASE_URL = os.getenv("PARLANT_API_URL", "http://localhost:8800")
TENANT_ID = os.getenv("TENANT_ID", "test_handover")
CHATBOT_ID = os.getenv("CHATBOT_ID", "test_handover")
TIMEOUT = 120
POLL_INTERVAL = 1.0
MAX_WAIT = 60

# Handover test cases: (message, language, should_trigger)
HANDOVER_TEST_CASES = [
    # English - should trigger handover
    ("I want to speak with a human agent please", "en", True),
    ("This is frustrating! I need real help", "en", True),
    ("Can I talk to a real person?", "en", True),
    # Chinese - should trigger handover  
    ("我要找人工客服", "zh", True),
    ("这太烦人了，我需要真人帮助", "zh", True),
    # Normal queries - should NOT trigger handover
    ("What's the weather today?", "en", False),
    ("今天天气怎么样？", "zh", False),
]


@dataclass
class TestResult:
    message: str
    language: str
    expected: bool
    actual: bool
    response: str
    
    @property
    def passed(self) -> bool:
        return self.expected == self.actual
    
    @property
    def response_lang(self) -> str:
        return "zh" if any('\u4e00' <= c <= '\u9fff' for c in self.response) else "en"


@dataclass
class Stats:
    total: int = 0
    passed: int = 0
    results: list[TestResult] = field(default_factory=list)
    
    def add(self, r: TestResult) -> None:
        self.results.append(r)
        self.total += 1
        self.passed += 1 if r.passed else 0
    
    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0
    
    def summary(self) -> str:
        return f"\n{'='*50}\nHANDOVER: {self.passed}/{self.total} ({self.accuracy:.0%})\n{'='*50}"


def is_handover(response: str) -> bool:
    """Check if response starts with handover format."""
    return response.strip().startswith("ho000001:")


async def chat_async(
    client: httpx.AsyncClient,
    message: str,
    session_id: str,
) -> dict[str, Any]:
    """Send chat_async request, returns immediately with correlation_id."""
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
    """Get session events."""
    resp = await client.get(f"/sessions/{session_id}/events")
    if resp.status_code != 200:
        return []
    return resp.json()


async def wait_for_ai_response(
    client: httpx.AsyncClient,
    session_id: str,
    min_offset: int = 0,
    max_wait: float = MAX_WAIT,
) -> Optional[str]:
    """Poll session events waiting for AI response."""
    start = time.time()
    while time.time() - start < max_wait:
        events = await get_session_events(client, session_id)
        # Find AI message event after min_offset
        for evt in events:
            if (evt.get("source") == "ai_agent" and 
                evt.get("kind") == "message" and
                evt.get("offset", 0) > min_offset):
                return evt.get("data", {}).get("message", "")
        await asyncio.sleep(POLL_INTERVAL)
    return None


async def check_handover(
    client: httpx.AsyncClient,
    message: str,
    lang: str,
    expected: bool,
) -> TestResult:
    """Run single handover check via chat_async."""
    session_id = f"test-handover-{int(time.time() * 1000)}"
    
    # Send async chat request
    result = await chat_async(client, message, session_id)
    if result.get("status") != 200:
        return TestResult(message, lang, expected, False, f"API error: {result}")
    
    # Wait for AI response
    response = await wait_for_ai_response(client, session_id) or ""
    
    return TestResult(
        message=message,
        language=lang,
        expected=expected,
        actual=is_handover(response),
        response=response[:150],
    )


@pytest.mark.asyncio
async def test_handover_english():
    """Test English handover request."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        r = await check_handover(client, "I want to speak with a human agent please", "en", True)
        assert r.passed, f"Expected handover, got: {r.response}"
        assert is_handover(r.response)


@pytest.mark.asyncio
async def test_handover_chinese():
    """Test Chinese handover with language matching."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        r = await check_handover(client, "我要找人工客服", "zh", True)
        assert r.passed, f"Expected handover, got: {r.response}"
        assert r.response_lang == "zh", f"Expected Chinese, got: {r.response}"


@pytest.mark.asyncio
async def test_no_handover_normal():
    """Normal query should not trigger handover."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        r = await check_handover(client, "Tell me about your services", "en", False)
        assert r.passed, f"Unexpected handover: {r.response}"


@pytest.mark.asyncio
async def test_handover_batch():
    """Concurrent batch test with statistics."""
    stats = Stats()
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        tasks = [check_handover(client, msg, lang, exp) for msg, lang, exp in HANDOVER_TEST_CASES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for r in results:
            if isinstance(r, Exception):
                print(f"Error: {r}")
                continue
            stats.add(r)
    
    print(stats.summary())
    for r in stats.results:
        status = "✓" if r.passed else "✗"
        print(f"  {status} [{r.language}] '{r.message[:25]}...' exp={r.expected} got={r.actual}")
    
    assert stats.accuracy >= 0.7, f"Accuracy too low: {stats.accuracy:.0%}"


if __name__ == "__main__":
    """Run directly: python tests/api/test_code.py"""
    asyncio.run(test_handover_batch())
