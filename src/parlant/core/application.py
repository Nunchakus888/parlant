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
        
        # Retriever cleanup callback (set by SDK Server)
        self._retriever_cleanup_callback = None
        
        # LRU 资源管理器
        self.resource_manager = ResourceManager(self, logger, background_task_service)
    
    def set_retriever_cleanup_callback(self, callback):
        """Set callback for cleaning up retrievers when agent is deleted.
        
        This is called by the SDK Server to register its cleanup method.
        
        Args:
            callback: Async function that takes agent_id and cleans up retrievers.
                     Should have signature: async def(agent_id: AgentId) -> None
        """
        self._retriever_cleanup_callback = callback

    async def delete_agent_cascade(self, agent_id: AgentId) -> None:
        """
        级联删除 Agent 及其所有关联对象。
        
        删除顺序（按依赖关系）：
        1. Journeys → 级联删除 Journey 的 nodes、edges、关联的 guidelines
        2. Guidelines → 级联删除 GuidelineToolAssociations（在 GuidelineModule.delete 中处理）
        3. Context Variables
        4. Capabilities
        5. Canned Responses
        6. Glossary Terms
        7. Relationships（涉及该 agent 的 guidelines 和 tag 的关系）
        8. Agent 工具
        9. Retrievers 和 Hooks (SDK模式)
        10. Agent 本身
        
        注意：
        - Evaluations 没有删除接口（设计上保留历史记录）
        - GuidelineToolAssociations 在 GuidelineModule.delete() 中级联删除
        - 此操作不可逆，请谨慎使用
        
        Args:
            agent_id: 要删除的 Agent ID
            
        Raises:
            ItemNotFoundError: 如果 Agent 不存在
            Exception: 如果删除过程中出现错误
        """
        # 验证 Agent 是否存在
        try:
            await self.agents.read(agent_id)
        except Exception as e:
            raise Exception(f"Agent {agent_id} not found or cannot be read: {e}")
        
        agent_tag = Tag.for_agent_id(agent_id)
        
        # 第一阶段：删除依赖于 Guidelines 的对象
        # Journey 依赖 Guidelines（作为 conditions），必须先删除
        await self._delete_journeys_for_agent(agent_tag)
        
        # 第二阶段：删除 Guidelines 和收集需要清理的 Relationship IDs
        # GuidelineModule.delete() 会级联删除：
        # - GuidelineToolAssociations
        # - 部分 Relationships（guideline-guideline 之间的）
        guidelines = await self.guidelines.find(tag_id=agent_tag)
        guideline_ids = [g.id for g in guidelines]
        
        # 收集涉及这些 guidelines 的 relationship IDs（在删除 guidelines 前）
        relationship_ids_to_delete = await self._collect_relationships_for_guidelines(guideline_ids)
        
        # 删除 guidelines
        await self._delete_guidelines_for_agent(agent_tag)
        
        # 第三阶段：并行删除无依赖关系的对象
        await safe_gather(
            self._delete_variables_for_agent(agent_tag),
            self._delete_capabilities_for_agent(agent_tag),
            self._delete_canned_responses_for_agent(agent_tag),
            self._delete_terms_for_agent(agent_tag),
            self._cleanup_agent_tools(agent_id),
        )
        
        # 第四阶段：删除 Relationships（涉及 agent tag 的关系）
        await self._delete_relationships_for_agent(agent_tag, relationship_ids_to_delete)
        
        # 第五阶段：清理 Retrievers 和 Hooks (SDK模式)
        if self._retriever_cleanup_callback:
            try:
                await self._retriever_cleanup_callback(agent_id)
            except Exception as e:
                self._logger.error(f"❌ Failed to cleanup retrievers via callback: {e}")
        
        # 最后：删除 Agent 本身
        await self.agents.delete(agent_id)
        self._logger.info(f"✅ Agent {agent_id} and all related data deleted successfully")

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
                self._logger.debug(f"⚠️  No journeys found for agent tag: {agent_tag}")
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

    async def _collect_relationships_for_guidelines(
        self, 
        guideline_ids: Sequence[str],
    ) -> set[str]:
        """收集涉及指定 guidelines 的 relationship IDs"""
        relationship_ids: set[str] = set()
        
        for guideline_id in guideline_ids:
            try:
                relationships = await self.relationships.find(
                    kind=None,
                    indirect=False,
                    guideline_id=guideline_id,
                    tag_id=None,
                    tool_id=None,
                )
                for r in relationships:
                    relationship_ids.add(r.id)
            except Exception:
                # Guideline 可能已被其他操作删除，忽略错误
                pass
        
        return relationship_ids

    async def _delete_relationships_for_agent(
        self, 
        agent_tag: TagId,
        additional_relationship_ids: set[str],
    ) -> None:
        """删除涉及 agent tag 的 relationships"""
        try:
            # 1. 查找涉及 agent tag 的 relationships
            tag_relationships = await self.relationships.find(
                kind=None,
                indirect=False,
                guideline_id=None,
                tag_id=agent_tag,
                tool_id=None,
            )
            
            # 2. 合并所有需要删除的 relationship IDs
            all_relationship_ids = {r.id for r in tag_relationships}
            all_relationship_ids.update(additional_relationship_ids)
            
            if not all_relationship_ids:
                return
            
            self._logger.info(f"🧹 Deleting {len(all_relationship_ids)} relationships for agent tag: {agent_tag}")
            
            # 3. 批量删除
            delete_tasks = [
                self._safe_delete_relationship(rid) 
                for rid in all_relationship_ids
            ]
            await safe_gather(*delete_tasks)
            
            self._logger.info(f"🗑️ Successfully deleted {len(all_relationship_ids)} relationships")
        except Exception as e:
            self._logger.error(f"❌ Failed to delete relationships for {agent_tag}: {e}")

    async def _safe_delete_relationship(self, relationship_id: str) -> None:
        """安全删除 relationship，忽略 NotFound 错误"""
        try:
            await self.relationships.delete(relationship_id)
        except Exception:
            # Relationship 可能已被级联删除，忽略错误
            pass
