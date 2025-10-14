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
LRU Resource Manager - 精简高效的内存管理

基于 LRU 策略自动淘汰最少使用的 Session 和 Agent，防止内存泄漏。
"""

import os
from collections import OrderedDict
from typing import TYPE_CHECKING

from parlant.core.agents import AgentId
from parlant.core.loggers import Logger
from parlant.core.sessions import SessionId

if TYPE_CHECKING:
    from parlant.core.application import Application


class ResourceManager:
    """LRU 资源管理器"""
    
    def __init__(
        self,
        app: "Application",
        logger: Logger,
    ) -> None:
        self._app = app
        self._logger = logger
        self._max_sessions = int(os.getenv('MAX_SESSIONS_CACHED', '1000'))
        self._session_order: OrderedDict[SessionId, AgentId] = OrderedDict()
    
    async def track_session(self, session_id: SessionId, agent_id: AgentId) -> None:
        """追踪 Session 使用"""
        # 移动到末尾（标记为最近使用）
        if session_id in self._session_order:
            self._session_order.move_to_end(session_id)
        else:
            self._session_order[session_id] = agent_id
        
        # 超过上限，淘汰最老的
        if len(self._session_order) > self._max_sessions:
            await self._evict_oldest()
    
    async def _evict_oldest(self) -> None:
        """淘汰最老的 Session 和关联的 Agent"""
        if not self._session_order:
            return
        
        # 获取最老的 Session
        old_session_id, old_agent_id = self._session_order.popitem(last=False)
        
        try:
            # 删除 Session
            # await self._app.sessions.delete(old_session_id)
            
            # 检查 Agent 是否还有其他 Session
            remaining = [s for s, a in self._session_order.items() if a == old_agent_id]
            if not remaining:
                # 级联删除 Agent
                await self._app.delete_agent_cascade(old_agent_id)
            
            self._logger.debug(f"LRU evicted: session={old_session_id}, agent={old_agent_id}")
        except Exception as e:
            self._logger.error(f"LRU eviction failed: {e}")

