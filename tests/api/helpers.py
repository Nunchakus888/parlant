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
Common API helpers for tests.

公共测试辅助函数，可被多个测试文件复用。
"""

import asyncio
import os
import time
from typing import Any, Optional

import httpx

# Default config - can be overridden by environment variables
DEFAULT_BASE_URL = os.getenv("PARLANT_API_URL", "http://localhost:8800")
DEFAULT_TIMEOUT = 120
DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_MAX_WAIT = 60


async def send_message(
    client: httpx.AsyncClient,
    message: str,
    session_id: str,
    tenant_id: str,
    chatbot_id: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """
    Send chat_async request.
    
    Args:
        client: httpx async client
        message: User message to send
        session_id: Session identifier
        tenant_id: Tenant identifier
        chatbot_id: Chatbot identifier
        timeout: Request timeout
    
    Returns:
        API response as dict
    """
    resp = await client.post("/sessions/chat_async", json={
        "message": message,
        "tenant_id": tenant_id,
        "chatbot_id": chatbot_id,
        "session_id": session_id,
        "customer_id": f"customer-{session_id}",
        "timeout": timeout,
    })
    return resp.json()


async def wait_for_ai_response(
    client: httpx.AsyncClient,
    session_id: str,
    min_offset: int = 0,
    max_wait: float = DEFAULT_MAX_WAIT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> Optional[str]:
    """
    Wait for AI response message.
    
    Args:
        client: httpx async client
        session_id: Session identifier
        min_offset: Minimum event offset to consider
        max_wait: Maximum wait time in seconds
        poll_interval: Polling interval in seconds
    
    Returns:
        AI response message or None if timeout
    """
    start = time.time()
    while time.time() - start < max_wait:
        resp = await client.get(f"/sessions/{session_id}/events")
        if resp.status_code == 200:
            for evt in resp.json():
                if (evt.get("source") == "ai_agent" and 
                    evt.get("kind") == "message" and
                    evt.get("offset", 0) > min_offset):
                    return evt.get("data", {}).get("message", "")
        await asyncio.sleep(poll_interval)
    return None


async def get_session_events(
    client: httpx.AsyncClient,
    session_id: str,
) -> list[dict]:
    """
    Get all events for a session.
    
    Args:
        client: httpx async client
        session_id: Session identifier
    
    Returns:
        List of events
    """
    resp = await client.get(f"/sessions/{session_id}/events")
    if resp.status_code != 200:
        return []
    return resp.json()


def generate_session_id(prefix: str = "test") -> str:
    """Generate unique session ID with prefix."""
    return f"{prefix}-{int(time.time() * 1000)}"

