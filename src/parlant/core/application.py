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


import asyncio
from typing import Sequence

from parlant.app_modules.agents import AgentModule
from parlant.app_modules.capabilities import CapabilityModule
from parlant.app_modules.canned_responses import CannedResponseModule
from parlant.app_modules.context_variables import ContextVariableModule
from parlant.app_modules.evaluations import EvaluationModule
from parlant.app_modules.journeys import JourneyModule
from parlant.app_modules.relationships import RelationshipModule
from parlant.app_modules.services import ServiceModule
from parlant.app_modules.sessions import SessionModule
from parlant.app_modules.tags import TagModule
from parlant.app_modules.customers import CustomerModule
from parlant.app_modules.guidelines import GuidelineModule
from parlant.app_modules.glossary import GlossaryModule


from parlant.core.agents import AgentId
from parlant.core.sessions import SessionId

from parlant.core.tags import Tag, TagId
from parlant.core.async_utils import safe_gather
from parlant.core.loggers import Logger
from parlant.core.resource_manager import ResourceManager
from parlant.core.background_tasks import BackgroundTaskService


class Application:
    def __init__(
        self,
        agent_module: AgentModule,
        session_module: SessionModule,
        service_module: ServiceModule,
        tag_module: TagModule,
        customer_module: CustomerModule,
        guideline_module: GuidelineModule,
        context_variable_module: ContextVariableModule,
        relationship_module: RelationshipModule,
        journey_module: JourneyModule,
        glossary_module: GlossaryModule,
        evaluation_module: EvaluationModule,
        capability_module: CapabilityModule,
        canned_response_module: CannedResponseModule,
        background_task_service: BackgroundTaskService,
        logger: Logger,
    ) -> None:
        self.agents = agent_module
        self.sessions = session_module
        self.services = service_module
        self.tags = tag_module
        self.capabilities = capability_module
        self.variables = context_variable_module
        self.customers = customer_module
        self.guidelines = guideline_module
        self.relationships = relationship_module
        self.journeys = journey_module
        self.glossary = glossary_module
        self.evaluations = evaluation_module
        self.canned_responses = canned_response_module
        self._logger = logger
        
        # LRU 资源管理器
        self.resource_manager = ResourceManager(self, logger, background_task_service)

    async def delete_agent_cascade(self, agent_id: AgentId) -> None:
        """
        级联删除 Agent 及其所有关联对象。
        
        删除顺序：
        1. Sessions (直接引用 agent_id)
        2. Guidelines (通过 agent tag 关联)
        3. Journeys (通过 agent tag 关联)
        4. Context Variables (通过 agent tag 关联)
        5. Capabilities (通过 agent tag 关联)
        6. Canned Responses (通过 agent tag 关联)
        7. Glossary Terms (通过 agent tag 关联)
        8. Relationships (涉及该 agent 的关系)
        9. Evaluations (与该 agent 相关的评估)
        10. Cached Evaluations (清理缓存)
        11. Agent 本身
        
        注意：此操作不可逆，请谨慎使用。
        
        Args:
            agent_id: 要删除的 Agent ID
            
        Raises:
            ItemNotFoundError: 如果 Agent 不存在
            Exception: 如果删除过程中出现错误
        """
        # 首先验证 Agent 是否存在
        try:
            await self.agents.read(agent_id)
        except Exception as e:
            raise Exception(f"Agent {agent_id} not found or cannot be read: {e}")
        
        agent_tag = Tag.for_agent_id(agent_id)
        
        # 定义删除任务，按依赖关系排序
        # ⚠️  重要：删除顺序很关键！
        # Journey依赖Guidelines（作为conditions），所以必须先删除Journey
        deletion_tasks = [
            # 1. Sessions (直接引用 agent_id)
            # self._delete_sessions_for_agent(agent_id),
            
            # 2. 🔧 FIX: 先删除Journeys（会级联删除关联的guidelines和tools）
            # Journey.delete() 会处理：
            # - Journey的nodes和edges
            # - Journey关联的tools
            # - Journey的condition guidelines（如果不被其他journey使用）
            self._delete_journeys_for_agent(agent_tag),
            
            # 3. 再删除剩余的Guidelines（那些不属于任何journey的独立guidelines）
            self._delete_guidelines_for_agent(agent_tag),
            
            # 4. Context Variables (通过 agent tag 关联)
            self._delete_variables_for_agent(agent_tag),
            
            # 5. Capabilities (通过 agent tag 关联)
            self._delete_capabilities_for_agent(agent_tag),
            
            # 6. Canned Responses (通过 agent tag 关联)
            self._delete_canned_responses_for_agent(agent_tag),
            
            # 7. Glossary Terms (通过 agent tag 关联)
            self._delete_terms_for_agent(agent_tag),
            
            # 8. 清理Agent的工具
            self._cleanup_agent_tools(agent_id),
        ]
        
        # 批量异步执行所有删除任务
        await safe_gather(*deletion_tasks)
        
        # 8. 删除所有相关的 Relationships
        # 注意：这里需要根据实际的 RelationshipModule 接口调整
        # await self._delete_relationships_for_agent(agent_id)
        
        # 9. 清理相关的 Evaluations
        # 注意：这里需要根据实际的 EvaluationModule 接口调整
        # await self._delete_evaluations_for_agent(agent_id)
        
        # 注意：不再清理评估缓存，因为现在基于 chatbot_id 共享缓存
        # 同一个 chatbot 的其他 agent 可能还在使用这些缓存
        # 如果需要清理 chatbot 的缓存，应该在 chatbot 配置变更时使用新的 chatbot_id
        
        # 10. 最后删除 Agent 本身
        await self.agents.delete(agent_id)

    async def _delete_customer_from_memory_for_session(self, session_id: SessionId) -> None:
        """清理指定 Session 关联的 Session 和 Customer 内存"""
        self._logger.debug(f"👤 Deleting session and customer from memory for session {session_id}")
        try:
            session = await self.sessions.read(session_id)
            if session:
                # 清理 customer 内存
                await self.customers.delete(session.customer_id)
                self._logger.debug(f"👤 Deleted customer {session.customer_id} from memory")

                # 清理 session 内存
                await self.sessions.delete_from_memory_only(session_id)
                self._logger.debug(f"📋 Deleted session {session_id} from memory")
        except Exception as e:
            self._logger.error(f"Failed to delete session and customer from memory for session {session_id}: {e}")

    async def _delete_guidelines_for_agent(self, agent_tag: TagId) -> None:
        """删除指定Agent的所有Guidelines"""
        try:
            guidelines = await self.guidelines.find(tag_id=agent_tag)
            self._logger.info(f"🧹 Deleting {len(guidelines)} guidelines for agent tag: {agent_tag}")
            
            if not guidelines:
                self._logger.warning(f"⚠️  No guidelines found for agent tag: {agent_tag}")
                return
            
            delete_tasks = [self.guidelines.delete(guideline.id) for guideline in guidelines]
            await safe_gather(*delete_tasks)
            self._logger.info(f"🗑️ Successfully deleted {len(guidelines)} guidelines")
        except Exception as e:
            self._logger.error(f"❌ Failed to delete guidelines for {agent_tag}: {e}")
            raise

    async def _delete_journeys_for_agent(self, agent_tag: TagId) -> None:
        """删除指定Agent的所有Journeys"""
        try:
            journeys = await self.journeys.find(tag_id=agent_tag)
            self._logger.info(f"🧹 Deleting {len(journeys)} journeys for agent tag: {agent_tag}")
            
            if not journeys:
                self._logger.warning(f"⚠️  No journeys found for agent tag: {agent_tag}")
                return
            
            # 详细记录每个Journey
            for journey in journeys:
                self._logger.debug(f"🗑️  Journey: {journey.id} - {journey.title}")
            
            delete_tasks = [self.journeys.delete(journey.id) for journey in journeys]
            await safe_gather(*delete_tasks)
            self._logger.info(f"🗑️ Successfully deleted {len(journeys)} journeys")
        except Exception as e:
            self._logger.error(f"❌ Failed to delete journeys for {agent_tag}: {e}")
            raise

    async def _delete_variables_for_agent(self, agent_tag: TagId) -> None:
        variables = await self.variables.find(tag_id=agent_tag)
        delete_tasks = [self.variables.delete(variable.id) for variable in variables]
        await safe_gather(*delete_tasks)

    async def _delete_capabilities_for_agent(self, agent_tag: TagId) -> None:
        capabilities = await self.capabilities.find(tag_id=agent_tag)
        delete_tasks = [self.capabilities.delete(capability.id) for capability in capabilities]
        await safe_gather(*delete_tasks)

    async def _delete_canned_responses_for_agent(self, agent_tag: TagId) -> None:
        canned_responses = await self.canned_responses.find(tags=[agent_tag])
        delete_tasks = [self.canned_responses.delete(canned_response.id) for canned_response in canned_responses]
        await safe_gather(*delete_tasks)

    async def _delete_terms_for_agent(self, agent_tag: TagId) -> None:
        terms = await self.glossary.find(tag_id=agent_tag)
        delete_tasks = [self.glossary.delete(term.id) for term in terms]
        await safe_gather(*delete_tasks)

    async def _cleanup_agent_tools(self, agent_id: AgentId) -> None:
        """清理指定Agent的所有工具"""
        try:
            await self.services.cleanup_agent_tools(agent_id)
            self._logger.info(f"🗑️ Successfully cleaned up tools for agent {agent_id}")
        except Exception as e:
            self._logger.error(f"❌ Failed to cleanup tools for agent {agent_id}: {e}")
