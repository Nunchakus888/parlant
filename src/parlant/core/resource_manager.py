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
支持并发安全，避免清理正在使用的资源。
"""

import os
import sys
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from parlant.core.agents import AgentId
from parlant.core.loggers import Logger
from parlant.core.sessions import SessionId

if TYPE_CHECKING:
    from parlant.core.application import Application
    from parlant.core.background_tasks import BackgroundTaskService


class ResourceManager:
    """LRU 资源管理器 - 支持并发安全"""
    
    def __init__(
        self,
        app: "Application",
        logger: Logger,
        background_task_service: "BackgroundTaskService",
    ) -> None:
        self._app = app
        self._logger = logger
        self._background_task_service = background_task_service
        self._max_sessions = int(os.getenv('MAX_SESSIONS_CACHED', '1000'))
        self._session_order: OrderedDict[SessionId, AgentId] = OrderedDict()
        
        # 并发控制 - 简单标志位
        self._eviction_in_progress = False
        
        # LRU 观测统计
        self._eviction_count = 0
        self._last_memory_stats = None
        self._observation_interval = int(os.getenv('LRU_OBSERVATION_INTERVAL', '300'))  # 5分钟
        self._last_observation_time = 0
    
    async def track_session(self, session_id: SessionId, agent_id: AgentId) -> None:
        """追踪 Session 使用"""
        # 移动到末尾（标记为最近使用）
        if session_id in self._session_order:
            self._session_order.move_to_end(session_id)
        else:
            self._session_order[session_id] = agent_id
        
        # 超过上限，安全地淘汰
        if len(self._session_order) > self._max_sessions:
            # 检查是否已经在进行eviction，如果是则跳过
            if self._eviction_in_progress:
                self._logger.debug("⏭️ LRU: Eviction already in progress, skipping to avoid concurrent eviction")
                return
                
            self._logger.debug(f"⏰ LRU: Evicting sessions to maintain max sessions: {len(self._session_order)} > {self._max_sessions}")
            start_time = time.time()
            await self._evict_safely()
            end_time = time.time()
            self._logger.debug(f"⏰ LRU: Evicted sessions in {(end_time - start_time):.3f} seconds")
            
            # 记录LRU效果观测
            # await self._log_lru_effectiveness()
        
        # 定期观测（即使没有淘汰也记录）
        # current_time = time.time()
        # if current_time - self._last_observation_time >= self._observation_interval:
        #     await self._log_lru_effectiveness()
        #     self._last_observation_time = current_time
    
    async def _evict_safely(self) -> None:
        """安全地淘汰所有不活跃的session - 高效版本"""
        if not self._session_order:
            return
        
        # 设置eviction进行中标志
        self._eviction_in_progress = True
        
        # 一次性获取所有活跃任务，避免重复查询
        active_tasks = self._background_task_service.get_active_tasks()
        self._logger.debug(f"Active tasks: {active_tasks}")
        active_session_ids = set()
        
        # 从任务标签中提取活跃的session_id - O(m)
        for task_tag in active_tasks:
            if "process-session(" in task_tag:
                # 提取session_id: process-session(session_id)-correlation_id
                start = task_tag.find("process-session(") + len("process-session(")
                end = task_tag.find(")", start)
                if end > start:
                    session_id = task_tag[start:end]
                    active_session_ids.add(session_id)
                    self._logger.debug(f"Found active session: {session_id} from task: {task_tag}")
        
        # 收集需要清理的session - O(n)
        sessions_to_evict = []
        for session_id, agent_id in self._session_order.items():
            if session_id not in active_session_ids:
                sessions_to_evict.append((session_id, agent_id))
                self._logger.debug(f"Session {session_id} marked for eviction (not in active tasks)")
            else:
                self._logger.debug(f"Session {session_id} is active, skipping eviction")
        
        # 批量清理 - O(k) where k = 不活跃session数量
        evicted_count = 0
        for session_id, agent_id in sessions_to_evict:
            try:
                await self._evict_session(session_id, agent_id)
                # 清理成功后才从 tracking 中删除
                self._session_order.pop(session_id, None)
                evicted_count += 1
            except Exception as e:
                # 清理失败，保留在 tracking 中，下次继续尝试
                self._logger.warning(f"⚠️ Eviction failed for session {session_id}, will retry: {e}")
        
        if evicted_count > 0:
            self._logger.debug(f"Evicted {evicted_count} inactive sessions")
            self._eviction_count += evicted_count
        elif sessions_to_evict:
            self._logger.warning(f"All {len(sessions_to_evict)} eviction attempts failed")
        else:
            self._logger.warning("All sessions are active, cannot evict any session")
        
        # 详细统计信息
        self._logger.debug(f"LRU eviction stats: total_tasks={len(active_tasks)}, active_sessions={len(active_session_ids)}, sessions_to_evict={len(sessions_to_evict)}, evicted={evicted_count}")
        
        # 清理eviction进行中标志
        self._eviction_in_progress = False
    
    
    async def _evict_session(self, session_id: SessionId, agent_id: AgentId) -> None:
        """
        执行 session 清理
        
        清理内容：
        - Agent 及其配置数据（TransientDocumentDatabase）
        - Customer（如果没有其他 Session 引用）
        """
        # 1. 获取 customer_id（在删除前）
        try:
            session = await self._app.sessions.read(session_id)
            customer_id = session.customer_id if session else None
        except Exception:
            customer_id = None
        
        # 2. 删除 Agent 配置数据
        await self._app.delete_agent_cascade(agent_id)
        
        # 3. 检查 Customer 是否还被其他 Session 引用，没有就删除
        if customer_id:
            try:
                other_sessions = await self._app.sessions.find(customer_id=customer_id, limit=1)
                if not other_sessions:
                    await self._app.customers.delete(customer_id)
            except Exception as e:
                self._logger.warning(f"⚠️ Customer cleanup skipped: {e}")
        
        self._logger.debug(f"✅ LRU evicted: session={session_id}, agent={agent_id}")
    
    async def _log_lru_effectiveness(self) -> None:
        """记录LRU管理效果观测"""
        try:
            # 获取当前内存使用情况
            current_stats = self._get_memory_stats()
            
            # 计算内存变化
            memory_change = ""
            if self._last_memory_stats:
                app_memory_diff = current_stats['app_memory_mb'] - self._last_memory_stats['app_memory_mb']
                system_memory_diff = current_stats['system_memory_mb'] - self._last_memory_stats['system_memory_mb']
                memory_change = f" (App: {app_memory_diff:+.1f}MB, System: {system_memory_diff:+.1f}MB)"
            
            # 获取transient存储统计
            transient_stats = await self._get_transient_stats()
            
            # 记录LRU效果观测日志
            self._logger.info(
                f"📊 LRU效果观测: "
                f"Sessions={len(self._session_order)}/{self._max_sessions} "
                f"Evictions={self._eviction_count} "
                f"App内存={current_stats['app_memory_mb']:.1f}MB "
                f"System内存={current_stats['system_memory_mb']:.1f}MB{memory_change} "
                f"Transient: {transient_stats}"
            )
            
            self._last_memory_stats = current_stats
            
        except Exception as e:
            self._logger.error(f"LRU effectiveness logging failed: {e}")
    
    def _get_memory_stats(self) -> dict:
        """获取内存使用统计"""
        try:
            import psutil
            process = psutil.Process()
            memory_info = process.memory_info()
            system_memory = psutil.virtual_memory()
            
            return {
                'app_memory_mb': memory_info.rss / 1024 / 1024,
                'system_memory_mb': system_memory.used / 1024 / 1024,
                'system_memory_percent': system_memory.percent
            }
        except ImportError:
            return {'app_memory_mb': 0, 'system_memory_mb': 0, 'system_memory_percent': 0}
        except Exception:
            return {'app_memory_mb': 0, 'system_memory_mb': 0, 'system_memory_percent': 0}
    
    async def _get_transient_stats(self) -> str:
        """获取transient存储统计"""
        try:
            # 获取所有transient collections的统计
            if hasattr(self._app, 'db') and hasattr(self._app.db, '_collections'):
                collections_stats = []
                total_docs = 0
                total_size_kb = 0
                
                for name, collection in self._app.db._collections.items():
                    if hasattr(collection, 'get_memory_stats'):
                        stats = collection.get_memory_stats()
                        if stats['count'] > 0:
                            collections_stats.append(f"{name}={stats['count']}({stats['actual_size_kb']:.1f}KB)")
                            total_docs += stats['count']
                            total_size_kb += stats['actual_size_kb']
                    elif hasattr(collection, '_documents'):
                        doc_count = len(collection._documents)
                        if doc_count > 0:
                            estimated_size = doc_count * 2
                            collections_stats.append(f"{name}={doc_count}({estimated_size}KB)")
                            total_docs += doc_count
                            total_size_kb += estimated_size
                
                if collections_stats:
                    return f"Collections: {', '.join(collections_stats)} | Total: {total_docs} docs, {total_size_kb:.1f}KB"
                else:
                    return "Collections: empty"
            else:
                return "Collections: unavailable"
        except Exception as e:
            return f"Collections: error({str(e)[:50]})"

