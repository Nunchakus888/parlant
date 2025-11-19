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

from ast import Return
from itertools import chain
from typing import Mapping, Optional, Sequence, cast

from cachetools import TTLCache

from parlant.core import async_utils
from parlant.core.agents import Agent, AgentId, AgentStore
from parlant.core.capabilities import Capability, CapabilityStore
from parlant.core.common import JSONSerializable
from parlant.core.context_variables import (
    ContextVariable,
    ContextVariableId,
    ContextVariableStore,
    ContextVariableValue,
)
from parlant.core.customers import Customer, CustomerId, CustomerStore
from parlant.core.engines.alpha.tool_calling.tool_caller import ToolCallEvaluation, ToolInsights
from parlant.core.journey_guideline_projection import (
    JourneyGuidelineProjection,
    extract_node_id_from_journey_node_guideline_id,
)
from parlant.core.guidelines import (
    Guideline,
    GuidelineId,
    GuidelineStore,
)
from parlant.core.journeys import Journey, JourneyId, JourneyNodeId, JourneyStore
from parlant.core.relationships import (
    RelationshipKind,
    RelationshipEntityKind,
    RelationshipStore,
)
from parlant.core.guideline_tool_associations import (
    GuidelineToolAssociation,
    GuidelineToolAssociationStore,
)
from parlant.core.glossary import GlossaryStore, Term
from parlant.core.sessions import (
    SessionId,
    Session,
    SessionStore,
    Event,
    MessageGenerationInspection,
    PreparationIteration,
    SessionUpdateParams,
)
from parlant.core.nlp.generation_info import GenerationInfo
from parlant.core.services.tools.service_registry import ServiceRegistry
from parlant.core.tags import Tag
from parlant.core.tools import ToolId, ToolService
from parlant.core.canned_responses import CannedResponse, CannedResponseStore
from parlant.core.loggers import Logger


class EntityQueries:
    def __init__(
        self,
        agent_store: AgentStore,
        session_store: SessionStore,
        guideline_store: GuidelineStore,
        customer_store: CustomerStore,
        context_variable_store: ContextVariableStore,
        relationship_store: RelationshipStore,
        guideline_tool_association_store: GuidelineToolAssociationStore,
        glossary_store: GlossaryStore,
        journey_store: JourneyStore,
        service_registry: ServiceRegistry,
        canned_response_store: CannedResponseStore,
        capability_store: CapabilityStore,
        journey_guideline_projection: JourneyGuidelineProjection,
        logger: Logger,
    ) -> None:
        self._agent_store = agent_store
        self._session_store = session_store
        self._guideline_store = guideline_store
        self._customer_store = customer_store
        self._context_variable_store = context_variable_store
        self._logger = logger
        self._relationship_store = relationship_store
        self._guideline_tool_association_store = guideline_tool_association_store
        self._glossary_store = glossary_store
        self._journey_store = journey_store
        self._capability_store = capability_store
        self._service_registry = service_registry
        self._canned_response_store = canned_response_store
        self._journey_guideline_projection = journey_guideline_projection

        self.find_journeys_on_which_this_guideline_depends = TTLCache[GuidelineId, list[Journey]](
            maxsize=1024, ttl=120
        )

    async def read_agent(
        self,
        agent_id: AgentId,
    ) -> Agent:
        return await self._agent_store.read_agent(agent_id)

    async def read_session(
        self,
        session_id: SessionId,
    ) -> Session:
        return await self._session_store.read_session(session_id)

    async def read_customer(
        self,
        customer_id: CustomerId,
    ) -> Customer:
        return await self._customer_store.read_customer(customer_id)

    async def find_guidelines_for_context(
        self,
        agent_id: AgentId,
        journeys: Sequence[Journey],
    ) -> Sequence[Guideline]:
        
        agent_guidelines = await self._guideline_store.list_guidelines(
            tags=[Tag.for_agent_id(agent_id)],
        )
        self._logger.debug(f"📋 Agent guidelines: {len(agent_guidelines)}")
        for g in agent_guidelines:
            condition_preview = (g.content.condition[:50] + "...") if g.content.condition and len(g.content.condition) > 50 else (g.content.condition or "")
            self._logger.debug(f"  → [{g.id}] condition: {condition_preview}")
        
        global_guidelines = await self._guideline_store.list_guidelines(tags=[])
        self._logger.debug(f"🌍 Global guidelines: {len(global_guidelines)}")

        agent = await self._agent_store.read_agent(agent_id)
        guidelines_for_agent_tags = await self._guideline_store.list_guidelines(
            tags=[tag for tag in agent.tags]
        )
        self._logger.debug(f"🏷️ Agent tags guidelines: {len(guidelines_for_agent_tags)}")

        guidelines_for_journeys = await self._guideline_store.list_guidelines(
            tags=[Tag.for_journey_id(journey.id) for journey in journeys]
        )
        self._logger.debug(f"🚀 Journey guidelines: {len(guidelines_for_journeys)}")
        for g in guidelines_for_journeys:
            condition_preview = (g.content.condition[:50] + "...") if g.content.condition and len(g.content.condition) > 50 else (g.content.condition or "")
            self._logger.debug(f"  → [{g.id}] condition: {condition_preview}")

        tasks = [
            self._journey_guideline_projection.project_journey_to_guidelines(journey.id)
            for journey in journeys
        ]
        projected_journey_guidelines = await async_utils.safe_gather(*tasks)
        
        self._logger.debug(f"📊 Journey list: {len(journeys)}")
        for journey in journeys:
            # journey.conditions 是 Sequence[GuidelineId]，即字符串列表
            title_preview = (journey.title[:40] + "...") if len(journey.title) > 40 else journey.title
            self._logger.debug(f"  → [{journey.id}] '{title_preview}': {len(journey.conditions)} condition(s)")

        all_guidelines = set(
            chain(
                agent_guidelines,
                global_guidelines,
                guidelines_for_agent_tags,
                guidelines_for_journeys,
                *projected_journey_guidelines,
            )
        )
        
        self._logger.debug(f"✅ total guidelines: {len(all_guidelines)}")
        
        # 注意：Journey condition guidelines 需要保留以便激活 journey
        # 它们会参与匹配来触发 journey 的启动，然后由 journey node guidelines 接管后续流程
        return list(all_guidelines)

    async def find_journey_related_guidelines(
        self,
        journey: Journey,
    ) -> Sequence[GuidelineId]:
        """Return guidelines that are dependent or derived on the specified journey."""
        iterated_relationships = set()

        guideline_ids = set()

        relationships = set(
            await self._relationship_store.list_relationships(
                kind=RelationshipKind.DEPENDENCY,
                indirect=False,
                target_id=Tag.for_journey_id(journey.id),
            )
        )

        while relationships:
            r = relationships.pop()

            if r in iterated_relationships:
                continue

            if r.source.kind == RelationshipEntityKind.GUIDELINE:
                guideline_ids.add(cast(GuidelineId, r.source.id))

            new_relationships = await self._relationship_store.list_relationships(
                kind=RelationshipKind.DEPENDENCY,
                indirect=False,
                target_id=r.source.id,
            )
            if new_relationships:
                relationships.update(
                    [rel for rel in new_relationships if rel not in iterated_relationships]
                )

            iterated_relationships.add(r)

        for id in guideline_ids:
            journeys = self.find_journeys_on_which_this_guideline_depends.get(id, [])
            journeys.append(journey)

            self.find_journeys_on_which_this_guideline_depends[id] = journeys

        guideline_ids.update(
            g.id
            for g in await self._journey_guideline_projection.project_journey_to_guidelines(
                journey.id
            )
        )

        return list(guideline_ids)

    async def find_context_variables_for_context(
        self,
        agent_id: AgentId,
    ) -> Sequence[ContextVariable]:
        agent_context_variables = await self._context_variable_store.list_variables(
            tags=[Tag.for_agent_id(agent_id)],
        )
        global_context_variables = await self._context_variable_store.list_variables(tags=[])
        agent = await self._agent_store.read_agent(agent_id)
        context_variables_for_agent_tags = await self._context_variable_store.list_variables(
            tags=[tag for tag in agent.tags]
        )

        all_context_variables = set(
            chain(
                agent_context_variables,
                global_context_variables,
                context_variables_for_agent_tags,
            )
        )
        return list(all_context_variables)

    async def read_context_variable_value(
        self,
        variable_id: ContextVariableId,
        key: str,
    ) -> Optional[ContextVariableValue]:
        return await self._context_variable_store.read_value(variable_id, key)

    async def find_events(
        self,
        session_id: SessionId,
    ) -> Sequence[Event]:
        return await self._session_store.list_events(session_id)

    async def find_guideline_tool_associations(
        self,
    ) -> Sequence[GuidelineToolAssociation]:
        return await self._guideline_tool_association_store.list_associations()

    async def find_journey_node_tool_associations(
        self,
        node_id: JourneyNodeId,
    ) -> Sequence[ToolId]:
        return (await self._journey_store.read_node(node_id=node_id)).tools

    async def find_capabilities_for_agent(
        self,
        agent_id: AgentId,
        query: str,
        max_count: int,
    ) -> Sequence[Capability]:
        agent_capabilities = await self._capability_store.list_capabilities(
            tags=[Tag.for_agent_id(agent_id)],
        )
        global_capabilities = await self._capability_store.list_capabilities(tags=[])
        agent = await self._agent_store.read_agent(agent_id)
        capabilities_for_agent_tags = await self._capability_store.list_capabilities(
            tags=[tag for tag in agent.tags]
        )

        all_capabilities = set(
            chain(
                agent_capabilities,
                global_capabilities,
                capabilities_for_agent_tags,
            )
        )

        result = await self._capability_store.find_relevant_capabilities(
            query,
            list(all_capabilities),
            max_count=max_count,
        )

        return result

    async def find_glossary_terms_for_context(
        self,
        agent_id: AgentId,
        query: str,
    ) -> Sequence[Term]:
        agent_terms = await self._glossary_store.list_terms(
            tags=[Tag.for_agent_id(agent_id)],
        )
        global_terms = await self._glossary_store.list_terms(tags=[])
        agent = await self._agent_store.read_agent(agent_id)
        glossary_for_agent_tags = await self._glossary_store.list_terms(
            tags=[tag for tag in agent.tags]
        )

        all_terms = set(chain(agent_terms, global_terms, glossary_for_agent_tags))

        return await self._glossary_store.find_relevant_terms(query, list(all_terms))

    async def read_tool_service(
        self,
        service_name: str,
    ) -> ToolService:
        return await self._service_registry.read_tool_service(service_name)

    async def finds_journeys_for_context(
        self,
        agent_id: AgentId,
    ) -> Sequence[Journey]:
        agent_journeys = await self._journey_store.list_journeys(
            tags=[Tag.for_agent_id(agent_id)],
        )
        self._logger.debug(f"📊 Found {len(agent_journeys)} agent-specific journeys for {agent_id}")
        
        # ❌ REMOVED: This was causing cross-agent data leakage due to bug in list_journeys(tags=[])
        # The list_journeys(tags=[]) was intended to return "global journeys" (journeys with no tags),
        # but due to a logic bug, it could return journeys from other agents.
        # Since all journeys should belong to a specific agent, we don't need global journeys.
        # global_journeys = await self._journey_store.list_journeys(tags=[])
        # self._logger.debug(f"🌍 Found {len(global_journeys)} global journeys")

        agent = await self._agent_store.read_agent(agent_id)
        journeys_for_agent_tags = (
            await self._journey_store.list_journeys(tags=[tag for tag in agent.tags])
            if agent.tags
            else []
        )
        self._logger.debug(f"🏷️  Found {len(journeys_for_agent_tags)} journeys for agent tags")
        # ✅ FIXED: Only use journeys that belong to this agent (no global_journeys)
        return list(set(chain(agent_journeys, journeys_for_agent_tags)))


    async def sort_journeys_by_contextual_relevance(
        self,
        available_journeys: Sequence[Journey],
        query: str,
    ) -> Sequence[Journey]:
        return await self._journey_store.find_relevant_journeys(
            query=query,
            available_journeys=available_journeys,
            max_journeys=len(available_journeys),
        )

    async def find_canned_responses_for_context(
        self,
        agent: Agent,
        journeys: Sequence[Journey],
        guidelines: Sequence[Guideline],
    ) -> Sequence[CannedResponse]:
        agent_canreps = await self._canned_response_store.list_canned_responses(
            tags=[Tag.for_agent_id(agent.id)],
        )
        global_canreps = await self._canned_response_store.list_canned_responses(tags=[])

        canreps_for_agent_tags = await self._canned_response_store.list_canned_responses(
            tags=[tag for tag in agent.tags]
        )

        journey_canreps = await self._canned_response_store.list_canned_responses(
            tags=[Tag.for_journey_id(journey.id) for journey in journeys]
        )

        guideline_canreps = await self.find_canned_responses_for_guidelines(guidelines)

        all_canreps = set(
            chain(
                agent_canreps,
                global_canreps,
                canreps_for_agent_tags,
                journey_canreps,
                guideline_canreps,
            )
        )

        return list(all_canreps)

    async def find_canned_responses_for_guidelines(
        self,
        guidelines: Sequence[Guideline],
    ) -> Sequence[CannedResponse]:
        tags = []

        for g in guidelines:
            if g.id.startswith("journey_node:"):
                tags.append(
                    Tag.for_journey_node_id(extract_node_id_from_journey_node_guideline_id(g.id))
                )

            else:
                tags.append(Tag.for_guideline_id(g.id))

        return await self._canned_response_store.list_canned_responses(tags=tags)

    async def find_guidelines_that_need_reevaluation(
        self,
        available_guidelines: dict[GuidelineId, Guideline],
        active_journeys: Sequence[Journey],
        tool_insights: ToolInsights,
    ) -> Sequence[Guideline]:
        """Find guidelines that need reevaluation based on the tool calls made."""

        if not tool_insights.evaluations:
            return []

        executed_tool_ids = [
            tid for tid, e in tool_insights.evaluations if e == ToolCallEvaluation.NEEDS_TO_RUN
        ]

        active_journeys_mapping = {journey.id: journey for journey in active_journeys}
        guidelines: list[Guideline] = []

        tasks = [
            self._relationship_store.list_relationships(
                kind=RelationshipKind.REEVALUATION,
                indirect=False,
                target_id=tool_id,
            )
            for tool_id in set(tid for tid, _ in tool_insights.evaluations)
        ]

        reevaluation_relationships = list(
            chain.from_iterable(await async_utils.safe_gather(*tasks))
        )

        for relationship in reevaluation_relationships:
            # See if the relationship's guideline is within
            # the set of guidelines we were given to check.
            guideline_to_reevaluate = next(
                (
                    g
                    for gid, g in available_guidelines.items()
                    if gid.startswith(relationship.source.id)
                ),
                None,
            )

            if not guideline_to_reevaluate:
                # Couldn't find this relationship's guideline
                # in the set of available guidelines to check.
                continue

            the_id_of_the_tool_related_to_the_guideline_to_reevaluate = relationship.target.id

            # At this point we know that one of the guidelines given to us
            # has a reevaluation relationship with one of the relevant tools.

            if guideline_to_reevaluate.metadata.get("journey_node"):
                # We found a journey node that has a reevaluation relationship with one of the tools.
                #
                # This journey node is by definition a tool node.
                #
                # Now, this actually means we need to reevaluate the entire journey,
                # so we'll need to add all of its projected guidelines to the list.

                # The only exception to this rule here is if the tool was deliberately skipped
                # because the context already existed in the session.

                # FIXME: Strictly speaking, we should only reevaluate the journey if the tool
                # was called ON BEHALF OF THE JOURNEY NODE — since it could have been called
                # for some other reason, e.g. due to an unrelated guideline.

                tool_should_be_considered_as_having_been_called = all(
                    e
                    in [ToolCallEvaluation.DATA_ALREADY_IN_CONTEXT, ToolCallEvaluation.NEEDS_TO_RUN]
                    for tool_id, e in tool_insights.evaluations
                    if tool_id == the_id_of_the_tool_related_to_the_guideline_to_reevaluate
                )

                if tool_should_be_considered_as_having_been_called:
                    journey_id = cast(
                        JourneyId,
                        cast(
                            Mapping[str, JSONSerializable],
                            guideline_to_reevaluate.metadata["journey_node"],
                        ).get("journey_id"),
                    )

                    if journey_id in active_journeys_mapping:
                        projected_journey_guidelines = (
                            await self._journey_guideline_projection.project_journey_to_guidelines(
                                journey_id
                            )
                        )

                        guidelines.extend(projected_journey_guidelines)
            else:
                # For normal guidelines, we only reevaluate them if their related
                # tool WAS JUST executed -- not if it was skipped.
                if the_id_of_the_tool_related_to_the_guideline_to_reevaluate in executed_tool_ids:
                    guidelines.append(guideline_to_reevaluate)

        return list(set(guidelines))


class EntityCommands:
    def __init__(
        self,
        session_store: SessionStore,
        context_variable_store: ContextVariableStore,
    ) -> None:
        self._session_store = session_store
        self._context_variable_store = context_variable_store

    async def create_inspection(
        self,
        session_id: SessionId,
        correlation_id: str,
        message_generations: Sequence[MessageGenerationInspection],
        preparation_iterations: Sequence[PreparationIteration],
        accumulate_only: bool = False,
    ) -> None:
        await self._session_store.create_inspection(
            session_id=session_id,
            correlation_id=correlation_id,
            preparation_iterations=preparation_iterations,
            message_generations=message_generations,
            accumulate_only=accumulate_only,
        )

    async def update_session(
        self,
        session_id: SessionId,
        params: SessionUpdateParams,
    ) -> None:
        await self._session_store.update_session(session_id, params)

    async def update_context_variable_value(
        self,
        variable_id: ContextVariableId,
        key: str,
        data: JSONSerializable,
    ) -> ContextVariableValue:
        return await self._context_variable_store.update_value(variable_id, key, data)
