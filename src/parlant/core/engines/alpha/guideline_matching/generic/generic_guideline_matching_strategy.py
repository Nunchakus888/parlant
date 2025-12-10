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

from collections import defaultdict
from datetime import datetime
from itertools import chain
import math
from typing import Mapping, Optional, Sequence, cast
from typing_extensions import override

from parlant.core import async_utils
from parlant.core.common import JSONSerializable, generate_id
from parlant.core.engines.alpha.guideline_matching.generic.common import internal_representation
from parlant.core.engines.alpha.guideline_matching.generic.disambiguation_batch import (
    DisambiguationGuidelineMatchesSchema,
    GenericDisambiguationGuidelineMatchingBatch,
)
from parlant.core.engines.alpha.guideline_matching.generic.guideline_actionable_batch import (
    GenericActionableGuidelineMatchesSchema,
    GenericActionableGuidelineMatchingBatch,
)
from parlant.core.engines.alpha.guideline_matching.generic.guideline_previously_applied_actionable_batch import (
    GenericPreviouslyAppliedActionableGuidelineMatchesSchema,
    GenericPreviouslyAppliedActionableGuidelineMatchingBatch,
)
from parlant.core.engines.alpha.guideline_matching.generic.guideline_previously_applied_actionable_customer_dependent_batch import (
    GenericPreviouslyAppliedActionableCustomerDependentGuidelineMatchesSchema,
    GenericPreviouslyAppliedActionableCustomerDependentGuidelineMatchingBatch,
)
from parlant.core.engines.alpha.guideline_matching.generic.journey_node_selection_batch import (
    GenericJourneyNodeSelectionBatch,
    JourneyNodeSelectionSchema,
)
from parlant.core.engines.alpha.guideline_matching.generic.observational_batch import (
    GenericObservationalGuidelineMatchesSchema,
    GenericObservationalGuidelineMatchingBatch,
)
from parlant.core.engines.alpha.guideline_matching.generic.response_analysis_batch import (
    GenericResponseAnalysisBatch,
    GenericResponseAnalysisSchema,
)
from parlant.core.engines.alpha.guideline_matching.guideline_match import GuidelineMatch
from parlant.core.engines.alpha.guideline_matching.guideline_matcher import (
    GuidelineMatchingBatch,
    GuidelineMatchingStrategy,
    GuidelineMatchingContext,
    ResponseAnalysisContext,
)
from parlant.core.engines.alpha.optimization_policy import OptimizationPolicy
from parlant.core.entity_cq import EntityQueries
from parlant.core.guidelines import Guideline, GuidelineContent, GuidelineId, GuidelineStore
from parlant.core.journeys import Journey, JourneyId, JourneyStore
from parlant.core.loggers import Logger
from parlant.core.nlp.generation import SchematicGenerator
from parlant.core.relationships import RelationshipKind, RelationshipStore


class GenericGuidelineMatchingStrategy(GuidelineMatchingStrategy):
    def __init__(
        self,
        logger: Logger,
        optimization_policy: OptimizationPolicy,
        guideline_store: GuidelineStore,
        journey_store: JourneyStore,
        relationship_store: RelationshipStore,
        entity_queries: EntityQueries,
        observational_guideline_schematic_generator: SchematicGenerator[
            GenericObservationalGuidelineMatchesSchema
        ],
        previously_applied_actionable_guideline_schematic_generator: SchematicGenerator[
            GenericPreviouslyAppliedActionableGuidelineMatchesSchema
        ],
        previously_applied_actionable_customer_dependent_guideline_schematic_generator: SchematicGenerator[
            GenericPreviouslyAppliedActionableCustomerDependentGuidelineMatchesSchema
        ],
        actionable_guideline_schematic_generator: SchematicGenerator[
            GenericActionableGuidelineMatchesSchema
        ],
        disambiguation_guidelines_schematic_generator: SchematicGenerator[
            DisambiguationGuidelineMatchesSchema
        ],
        journey_step_selection_schematic_generator: SchematicGenerator[JourneyNodeSelectionSchema],
        response_analysis_schematic_generator: SchematicGenerator[GenericResponseAnalysisSchema],
    ) -> None:
        self._logger = logger

        self._guideline_store = guideline_store
        self._journey_store = journey_store
        self._relationship_store = relationship_store

        self._optimization_policy = optimization_policy
        self._entity_queries = entity_queries

        self._observational_guideline_schematic_generator = (
            observational_guideline_schematic_generator
        )
        self._actionable_guideline_schematic_generator = actionable_guideline_schematic_generator
        self._previously_applied_actionable_guideline_schematic_generator = (
            previously_applied_actionable_guideline_schematic_generator
        )
        self._previously_applied_actionable_customer_dependent_guideline_schematic_generator = (
            previously_applied_actionable_customer_dependent_guideline_schematic_generator
        )
        self._disambiguation_guidelines_schematic_generator = (
            disambiguation_guidelines_schematic_generator
        )
        self._journey_step_selection_schematic_generator = (
            journey_step_selection_schematic_generator
        )
        self._response_analysis_schematic_generator = response_analysis_schematic_generator
        self._current_context: GuidelineMatchingContext | None = None

    @override
    async def create_matching_batches(
        self,
        guidelines: Sequence[Guideline],
        context: GuidelineMatchingContext,
    ) -> Sequence[GuidelineMatchingBatch]:
        self._current_context = context
        observational_guidelines: list[Guideline] = []
        previously_applied_actionable_guidelines: list[Guideline] = []
        previously_applied_actionable_customer_dependent_guidelines: list[Guideline] = []
        actionable_guidelines: list[Guideline] = []
        disambiguation_groups: list[tuple[Guideline, list[Guideline]]] = []
        journey_step_selection_journeys: dict[Journey, list[Guideline]] = defaultdict(list)

        active_journeys_mapping = {journey.id: journey for journey in context.active_journeys}

        for g in guidelines:
            if g.metadata.get("journey_node") is not None:
                # If the guideline is associated with a journey node, we add the journey steps
                # to the list of journeys that need reevaluation.
                if journey_id := cast(
                    Mapping[str, JSONSerializable], g.metadata.get("journey_node", {})
                ).get("journey_id"):
                    journey_id = cast(JourneyId, journey_id)

                    if journey_id in active_journeys_mapping:
                        journey_step_selection_journeys[active_journeys_mapping[journey_id]].append(
                            g
                        )

            elif not g.content.action:
                if targets := await self._try_get_disambiguation_group_targets(g, guidelines):
                    disambiguation_groups.append((g, targets))
                else:
                    observational_guidelines.append(g)
            else:
                if g.metadata.get("continuous", False):
                    actionable_guidelines.append(g)
                else:
                    if (
                        context.session.agent_states
                        and g.id in context.session.agent_states[-1].applied_guideline_ids
                    ):
                        data = g.metadata.get("customer_dependent_action_data", False)
                        if isinstance(data, Mapping) and data.get("is_customer_dependent", False):
                            previously_applied_actionable_customer_dependent_guidelines.append(g)
                        else:
                            previously_applied_actionable_guidelines.append(g)
                    else:
                        actionable_guidelines.append(g)

        guideline_batches: list[GuidelineMatchingBatch] = []
        if observational_guidelines:
            guideline_batches.extend(
                self._create_batches_observational_guideline(observational_guidelines, context)
            )
        if previously_applied_actionable_guidelines:
            guideline_batches.extend(
                self._create_batches_previously_applied_actionable_guideline(
                    previously_applied_actionable_guidelines, context
                )
            )
        if previously_applied_actionable_customer_dependent_guidelines:
            guideline_batches.extend(
                self._create_batches_previously_applied_actionable_customer_dependent_guideline(
                    previously_applied_actionable_customer_dependent_guidelines, context
                )
            )
        if actionable_guidelines:
            guideline_batches.extend(
                self._create_batches_actionable_guideline(actionable_guidelines, context)
            )
        if disambiguation_groups:
            guideline_batches.extend(
                [
                    self._create_batch_disambiguation_guideline(source, targets, context)
                    for source, targets in disambiguation_groups
                ]
            )
        if journey_step_selection_journeys:
            guideline_batches.extend(
                await async_utils.safe_gather(
                    *[
                        self._create_batch_journey_step_selection(examined_journey, steps, context)
                        for examined_journey, steps in journey_step_selection_journeys.items()
                    ]
                )
            )

        return guideline_batches

    @override
    async def create_response_analysis_batches(
        self,
        guideline_matches: Sequence[GuidelineMatch],
        context: ResponseAnalysisContext,
    ) -> Sequence[GenericResponseAnalysisBatch]:
        if not guideline_matches:
            return []

        return [
            GenericResponseAnalysisBatch(
                logger=self._logger,
                optimization_policy=self._optimization_policy,
                schematic_generator=self._response_analysis_schematic_generator,
                context=context,
                guideline_matches=guideline_matches,
            )
        ]

    @override
    async def transform_matches(
        self,
        matches: Sequence[GuidelineMatch],
    ) -> Sequence[GuidelineMatch]:
        result: list[GuidelineMatch] = []
        guidelines_to_skip: set[GuidelineId] = set()

        for m in matches:
            if disambiguation := m.metadata.get("disambiguation"):
                # 需要澄清：添加clarification guideline，排除冲突的guidelines
                guidelines_to_skip.update(
                    cast(
                        list[GuidelineId],
                        cast(dict[str, JSONSerializable], disambiguation).get("targets"),
                    )
                )
                guidelines_to_skip.add(m.guideline.id)
                result.append(
                    GuidelineMatch(
                        guideline=Guideline(
                            id=cast(GuidelineId, f"<transient_{generate_id()}>"),
                            creation_utc=datetime.now(),
                            content=GuidelineContent(
                                condition=internal_representation(m.guideline).condition,
                                action=cast(
                                    str,
                                    cast(dict[str, JSONSerializable], disambiguation)[
                                        "enriched_action"
                                    ],
                                ),
                            ),
                            enabled=True,
                            tags=[],
                            metadata={},
                        ),
                        score=10,
                        rationale=m.rationale,
                        metadata=m.metadata,
                    )
                )

        # 收集激活的 Journey 入口（冲突检测只在入口级别进行）
        # journey nodes 不参与冲突检测，它们是执行层，在入口确定后才处理
        journey_entries: dict[str, GuidelineMatch] = {}  # journey_id -> 入口 match
        
        for m in matches:
            if m.guideline.id in guidelines_to_skip:
                continue
            for tag in m.guideline.tags:
                if tag.startswith("journey:") and m.score >= 10:
                    journey_id = tag[8:]
                    # 保留最高分的入口 guideline
                    if journey_id not in journey_entries or m.score > journey_entries[journey_id].score:
                        journey_entries[journey_id] = m
        
        # 冲突检测（只在入口级别）
        conflict_targets = self._collect_conflict_targets(matches, guidelines_to_skip, journey_entries)
        
        if len(conflict_targets) >= 2 and self._current_context:
            self._logger.debug(f"🤔 {len(conflict_targets)} conflicting options detected, using disambiguation batch")
            disambiguation_result = await self._process_disambiguation(conflict_targets)
            
            if disambiguation_result:
                result.append(disambiguation_result)
                if disambiguation_result.metadata.get("disambiguation"):
                    # 排除所有冲突相关的 guidelines（入口 + nodes）
                    conflict_ids = {g.id for g in conflict_targets}
                    conflict_journey_ids = set(journey_entries.keys())
                    for m in matches:
                        if m.guideline.id in conflict_ids:
                            guidelines_to_skip.add(m.guideline.id)
                        elif m.metadata.get("step_selection_journey_id") in conflict_journey_ids:
                            guidelines_to_skip.add(m.guideline.id)
                        elif any(t.startswith("journey:") for t in m.guideline.tags):
                            guidelines_to_skip.add(m.guideline.id)
        elif len(journey_entries) == 1:
            # 单 Journey 无冲突，排除其他 journey 相关的 guidelines
            journey_id = next(iter(journey_entries.keys()))
            for m in matches:
                if m.guideline.id in guidelines_to_skip:
                    continue
                if not m.guideline.content.action:
                    continue
                # 保留当前 journey 的 nodes
                if m.metadata.get("step_selection_journey_id") == journey_id:
                    continue
                # 保留当前 journey 的入口
                if any(t == f"journey:{journey_id}" for t in m.guideline.tags):
                    continue
                # 排除其他 journey 的入口和非 journey guidelines
                if any(t.startswith("journey:") for t in m.guideline.tags):
                    guidelines_to_skip.add(m.guideline.id)
                    continue
                # 排除其他普通 actionable guidelines
                guidelines_to_skip.add(m.guideline.id)
            self._logger.debug(f"🎯 Single journey active: {journey_id}")

        result.extend(m for m in matches if m.guideline.id not in guidelines_to_skip)

        return result

    def _create_batches_observational_guideline(
        self,
        guidelines: Sequence[Guideline],
        context: GuidelineMatchingContext,
    ) -> Sequence[GuidelineMatchingBatch]:
        journeys = list(
            chain.from_iterable(
                self._entity_queries.find_journeys_on_which_this_guideline_depends.get(g.id, [])
                for g in guidelines
            )
        )

        batches = []

        guidelines_dict = {g.id: g for g in guidelines}
        batch_size = self._get_optimal_batch_size(guidelines_dict)
        guidelines_list = list(guidelines_dict.items())
        batch_count = math.ceil(len(guidelines_dict) / batch_size)

        for batch_number in range(batch_count):
            start_offset = batch_number * batch_size
            end_offset = start_offset + batch_size
            batch = dict(guidelines_list[start_offset:end_offset])
            batches.append(
                self._create_batch_observational_guideline(
                    guidelines=list(batch.values()),
                    journeys=journeys,
                    context=GuidelineMatchingContext(
                        agent=context.agent,
                        session=context.session,
                        customer=context.customer,
                        context_variables=context.context_variables,
                        interaction_history=context.interaction_history,
                        terms=context.terms,
                        capabilities=context.capabilities,
                        staged_events=context.staged_events,
                        active_journeys=journeys,
                        journey_paths=context.journey_paths,
                    ),
                )
            )

        return batches

    def _create_batch_observational_guideline(
        self,
        guidelines: Sequence[Guideline],
        journeys: Sequence[Journey],
        context: GuidelineMatchingContext,
    ) -> GenericObservationalGuidelineMatchingBatch:
        return GenericObservationalGuidelineMatchingBatch(
            logger=self._logger,
            optimization_policy=self._optimization_policy,
            schematic_generator=self._observational_guideline_schematic_generator,
            guidelines=guidelines,
            journeys=journeys,
            context=context,
        )

    def _create_batches_previously_applied_actionable_guideline(
        self,
        guidelines: Sequence[Guideline],
        context: GuidelineMatchingContext,
    ) -> Sequence[GuidelineMatchingBatch]:
        journeys = list(
            chain.from_iterable(
                self._entity_queries.find_journeys_on_which_this_guideline_depends.get(g.id, [])
                for g in guidelines
            )
        )

        batches = []

        guidelines_dict = {g.id: g for g in guidelines}
        batch_size = self._get_optimal_batch_size(guidelines_dict)
        guidelines_list = list(guidelines_dict.items())
        batch_count = math.ceil(len(guidelines_dict) / batch_size)

        for batch_number in range(batch_count):
            start_offset = batch_number * batch_size
            end_offset = start_offset + batch_size
            batch = dict(guidelines_list[start_offset:end_offset])
            batches.append(
                self._create_batch_previously_applied_actionable_guideline(
                    guidelines=list(batch.values()),
                    journeys=journeys,
                    context=GuidelineMatchingContext(
                        agent=context.agent,
                        session=context.session,
                        customer=context.customer,
                        context_variables=context.context_variables,
                        interaction_history=context.interaction_history,
                        terms=context.terms,
                        capabilities=context.capabilities,
                        staged_events=context.staged_events,
                        active_journeys=journeys,
                        journey_paths=context.journey_paths,
                    ),
                )
            )

        return batches

    def _create_batch_previously_applied_actionable_guideline(
        self,
        guidelines: Sequence[Guideline],
        journeys: Sequence[Journey],
        context: GuidelineMatchingContext,
    ) -> GenericPreviouslyAppliedActionableGuidelineMatchingBatch:
        return GenericPreviouslyAppliedActionableGuidelineMatchingBatch(
            logger=self._logger,
            optimization_policy=self._optimization_policy,
            schematic_generator=self._previously_applied_actionable_guideline_schematic_generator,
            guidelines=guidelines,
            journeys=journeys,
            context=context,
        )

    def _create_batches_previously_applied_actionable_customer_dependent_guideline(
        self,
        guidelines: Sequence[Guideline],
        context: GuidelineMatchingContext,
    ) -> Sequence[GuidelineMatchingBatch]:
        journeys = list(
            chain.from_iterable(
                self._entity_queries.find_journeys_on_which_this_guideline_depends.get(g.id, [])
                for g in guidelines
            )
        )

        batches = []

        guidelines_dict = {g.id: g for g in guidelines}
        batch_size = self._get_optimal_batch_size(guidelines_dict)
        guidelines_list = list(guidelines_dict.items())
        batch_count = math.ceil(len(guidelines_dict) / batch_size)

        for batch_number in range(batch_count):
            start_offset = batch_number * batch_size
            end_offset = start_offset + batch_size
            batch = dict(guidelines_list[start_offset:end_offset])
            batches.append(
                self._create_batch_previously_applied_actionable_customer_dependent_guideline(
                    guidelines=list(batch.values()),
                    journeys=journeys,
                    context=GuidelineMatchingContext(
                        agent=context.agent,
                        session=context.session,
                        customer=context.customer,
                        context_variables=context.context_variables,
                        interaction_history=context.interaction_history,
                        terms=context.terms,
                        capabilities=context.capabilities,
                        staged_events=context.staged_events,
                        active_journeys=journeys,
                        journey_paths=context.journey_paths,
                    ),
                )
            )

        return batches

    def _create_batch_previously_applied_actionable_customer_dependent_guideline(
        self,
        guidelines: Sequence[Guideline],
        journeys: Sequence[Journey],
        context: GuidelineMatchingContext,
    ) -> GenericPreviouslyAppliedActionableCustomerDependentGuidelineMatchingBatch:
        return GenericPreviouslyAppliedActionableCustomerDependentGuidelineMatchingBatch(
            logger=self._logger,
            optimization_policy=self._optimization_policy,
            schematic_generator=self._previously_applied_actionable_customer_dependent_guideline_schematic_generator,
            guidelines=guidelines,
            journeys=journeys,
            context=context,
        )

    def _create_batches_actionable_guideline(
        self,
        guidelines: Sequence[Guideline],
        context: GuidelineMatchingContext,
    ) -> Sequence[GuidelineMatchingBatch]:
        journeys = list(
            chain.from_iterable(
                self._entity_queries.find_journeys_on_which_this_guideline_depends.get(g.id, [])
                for g in guidelines
            )
        )

        batches = []

        guidelines_dict = {g.id: g for g in guidelines}
        batch_size = self._get_optimal_batch_size(guidelines_dict)
        guidelines_list = list(guidelines_dict.items())
        batch_count = math.ceil(len(guidelines_dict) / batch_size)

        for batch_number in range(batch_count):
            start_offset = batch_number * batch_size
            end_offset = start_offset + batch_size
            batch = dict(guidelines_list[start_offset:end_offset])
            batches.append(
                self._create_batch_actionable_guideline(
                    guidelines=list(batch.values()),
                    journeys=journeys,
                    context=GuidelineMatchingContext(
                        agent=context.agent,
                        session=context.session,
                        customer=context.customer,
                        context_variables=context.context_variables,
                        interaction_history=context.interaction_history,
                        terms=context.terms,
                        capabilities=context.capabilities,
                        staged_events=context.staged_events,
                        active_journeys=journeys,
                        journey_paths=context.journey_paths,
                    ),
                )
            )

        return batches

    def _create_batch_actionable_guideline(
        self,
        guidelines: Sequence[Guideline],
        journeys: Sequence[Journey],
        context: GuidelineMatchingContext,
    ) -> GenericActionableGuidelineMatchingBatch:
        return GenericActionableGuidelineMatchingBatch(
            logger=self._logger,
            optimization_policy=self._optimization_policy,
            schematic_generator=self._actionable_guideline_schematic_generator,
            guidelines=guidelines,
            journeys=journeys,
            context=context,
        )

    async def _try_get_disambiguation_group_targets(
        self,
        candidate: Guideline,
        guidelines: Sequence[Guideline],
    ) -> Optional[list[Guideline]]:
        guidelines_dict = {g.id: g for g in guidelines}

        if relationships := await self._relationship_store.list_relationships(
            kind=RelationshipKind.DISAMBIGUATION,
            source_id=candidate.id,
        ):
            targets = [guidelines_dict[cast(GuidelineId, r.target.id)] for r in relationships]

            if len(targets) > 1:
                return targets

        return None

    def _create_batch_disambiguation_guideline(
        self,
        disambiguation_guideline: Guideline,
        disambiguation_targets: list[Guideline],
        context: GuidelineMatchingContext,
    ) -> GenericDisambiguationGuidelineMatchingBatch:
        journeys = list(
            chain.from_iterable(
                self._entity_queries.find_journeys_on_which_this_guideline_depends.get(g.id, [])
                for g in [disambiguation_guideline, *disambiguation_targets]
            )
        )

        return GenericDisambiguationGuidelineMatchingBatch(
            logger=self._logger,
            journey_store=self._journey_store,
            optimization_policy=self._optimization_policy,
            schematic_generator=self._disambiguation_guidelines_schematic_generator,
            disambiguation_guideline=disambiguation_guideline,
            disambiguation_targets=disambiguation_targets,
            context=GuidelineMatchingContext(
                agent=context.agent,
                session=context.session,
                customer=context.customer,
                context_variables=context.context_variables,
                interaction_history=context.interaction_history,
                terms=context.terms,
                capabilities=context.capabilities,
                staged_events=context.staged_events,
                active_journeys=journeys,
                journey_paths=context.journey_paths,
            ),
        )

    async def _create_batch_journey_step_selection(
        self,
        examined_journey: Journey,
        step_guidelines: Sequence[Guideline],
        context: GuidelineMatchingContext,
    ) -> GenericJourneyNodeSelectionBatch:
        return GenericJourneyNodeSelectionBatch(
            logger=self._logger,
            guideline_store=self._guideline_store,
            optimization_policy=self._optimization_policy,
            schematic_generator=self._journey_step_selection_schematic_generator,
            examined_journey=examined_journey,
            context=GuidelineMatchingContext(
                agent=context.agent,
                session=context.session,
                customer=context.customer,
                context_variables=context.context_variables,
                interaction_history=context.interaction_history,
                terms=context.terms,
                capabilities=context.capabilities,
                staged_events=context.staged_events,
                active_journeys=context.active_journeys,
                journey_paths=context.journey_paths,
            ),
            node_guidelines=step_guidelines,
            journey_path=context.journey_paths.get(examined_journey.id, []),
        )

    def _get_optimal_batch_size(self, guidelines: dict[GuidelineId, Guideline]) -> int:
        return self._optimization_policy.get_guideline_matching_batch_size(len(guidelines))

    def _collect_conflict_targets(
        self,
        matches: Sequence[GuidelineMatch],
        guidelines_to_skip: set[GuidelineId],
        journey_entries: dict[str, GuidelineMatch],  # journey_id -> 入口 match
    ) -> list[Guideline]:
        """收集需要用户澄清的冲突 targets（只在入口级别）
        
        设计原则：
        - 冲突检测只在入口级别进行
        - journey nodes 是执行层，不参与冲突检测
        - 入口和 nodes 不是同一维度的数据
        """
        # 多 Journey 场景：所有 journey 入口都是冲突 targets
        if len(journey_entries) > 1:
            return [m.guideline for m in journey_entries.values()
                    if m.guideline.id not in guidelines_to_skip]
        
        # 单 Journey 场景：检查 journey 入口和其他 actionable guidelines 是否冲突
        if len(journey_entries) == 1:
            journey_match = next(iter(journey_entries.values()))
            journey_score = journey_match.score
            journey_guideline = journey_match.guideline
            
            # 收集其他高分 actionable guidelines（排除所有 journey 相关）
            other_high_score: list[tuple[Guideline, int]] = []
            for m in matches:
                if m.guideline.id in guidelines_to_skip or not m.guideline.content.action:
                    continue
                # 排除 journey 入口
                if any(t.startswith("journey:") for t in m.guideline.tags):
                    continue
                # 排除 journey nodes
                if m.metadata.get("step_selection_journey_id"):
                    continue
                if m.score >= 10:
                    other_high_score.append((m.guideline, m.score))
            
            # 如果存在其他高分 guidelines 且 score 相近，需要澄清
            if other_high_score and journey_guideline:
                max_other = max(s for _, s in other_high_score)
                if abs(journey_score - max_other) <= 2:
                    return [journey_guideline] + [g for g, _ in other_high_score]
        
        return []

    async def _process_disambiguation(
        self,
        conflict_targets: list[Guideline],
    ) -> GuidelineMatch | None:
        """使用disambiguation batch处理冲突，包含状态管理
        
        状态管理逻辑（由disambiguation batch处理）：
        1. 如果之前已请求澄清且用户已回答 → is_ambiguous=false，不再澄清
        2. 如果之前已请求澄清但用户未回答 → is_ambiguous=true，重新澄清
        3. 如果是新的歧义 → is_ambiguous=true，请求澄清
        """
        if not self._current_context:
            return None
        
        # 创建临时的disambiguation guideline
        temp_guideline = Guideline(
            id=cast(GuidelineId, f"<auto_disambig_{generate_id()}>"),
            creation_utc=datetime.now(),
            content=GuidelineContent(
                condition="Multiple conflicting intents detected",
                action=None,
            ),
            enabled=True,
            tags=[],
            metadata={},
        )
        
        # 使用现有的disambiguation batch（包含状态管理）
        batch = self._create_batch_disambiguation_guideline(
            disambiguation_guideline=temp_guideline,
            disambiguation_targets=conflict_targets,
            context=self._current_context,
        )
        
        try:
            batch_result = await batch.process()
            if batch_result.matches:
                match = batch_result.matches[0]
                # 检查disambiguation结果
                if match.metadata.get("disambiguation"):
                    # 需要澄清：返回带有action的guideline
                    disambiguation_data = cast(dict[str, JSONSerializable], match.metadata["disambiguation"])
                    enriched_action = disambiguation_data.get("enriched_action", "")
                    if enriched_action:
                        self._logger.debug(f"🤔 Disambiguation needed: {match.rationale}")
                        return GuidelineMatch(
                            guideline=Guideline(
                                id=cast(GuidelineId, f"<disambig_{generate_id()}>"),
                                creation_utc=datetime.now(),
                                content=GuidelineContent(
                                    condition=match.guideline.content.condition,
                                    action=cast(str, enriched_action),
                                ),
                                enabled=True,
                                tags=[],
                                metadata={},
                            ),
                            score=10,
                            rationale=match.rationale,
                            metadata=match.metadata,
                        )
                else:
                    # 不需要澄清（用户已回答或意图清晰）
                    self._logger.debug(f"⏭️ No disambiguation needed: {match.rationale}")
                    return None
        except Exception as e:
            self._logger.warning(f"Disambiguation batch failed: {e}")
        
        return None
