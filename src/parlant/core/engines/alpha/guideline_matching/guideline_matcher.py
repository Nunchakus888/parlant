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

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass
from functools import cached_property
from itertools import chain
import time
from typing import Optional, Sequence

from parlant.core import async_utils
from parlant.core.capabilities import Capability
from parlant.core.engines.alpha.loaded_context import LoadedContext
from parlant.core.journeys import Journey, JourneyId
from parlant.core.nlp.policies import policy, retry
from parlant.core.agents import Agent
from parlant.core.context_variables import ContextVariable, ContextVariableValue
from parlant.core.customers import Customer
from parlant.core.emissions import EmittedEvent
from parlant.core.nlp.generation_info import GenerationInfo


from parlant.core.engines.alpha.guideline_matching.guideline_match import (
    GuidelineMatch,
    AnalyzedGuideline,
)
from parlant.core.glossary import Term
from parlant.core.guidelines import Guideline, GuidelineId
from parlant.core.sessions import Event, Session
from parlant.core.loggers import Logger


class GuidelineMatchingBatchError(Exception):
    def __init__(self, message: str = "Guideline Matching Batch failed") -> None:
        super().__init__(message)


class ResponseAnalysisBatchError(Exception):
    def __init__(self, message: str = "Response Analysis Batch failed") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class GuidelineMatchingContext:
    agent: Agent
    session: Session
    customer: Customer
    context_variables: Sequence[tuple[ContextVariable, ContextVariableValue]]
    interaction_history: Sequence[Event]
    terms: Sequence[Term]
    capabilities: Sequence[Capability]
    staged_events: Sequence[EmittedEvent]
    active_journeys: Sequence[Journey]
    journey_paths: dict[JourneyId, list[Optional[GuidelineId]]]


@dataclass(frozen=True)
class ResponseAnalysisContext:
    agent: Agent
    session: Session
    customer: Customer
    context_variables: Sequence[tuple[ContextVariable, ContextVariableValue]]
    interaction_history: Sequence[Event]
    terms: Sequence[Term]
    staged_tool_events: Sequence[EmittedEvent]
    staged_message_events: Sequence[EmittedEvent]


@dataclass(frozen=True)
class GuidelineMatchingResult:
    total_duration: float
    batch_count: int
    batch_generations: Sequence[GenerationInfo]
    batches: Sequence[Sequence[GuidelineMatch]]
    matches: Sequence[GuidelineMatch]


@dataclass(frozen=True)
class ResponseAnalysisResult:
    total_duration: float
    batch_count: int
    batch_generations: Sequence[GenerationInfo]
    batches: Sequence[Sequence[AnalyzedGuideline]]

    @cached_property
    def analyzed_guidelines(self) -> Sequence[AnalyzedGuideline]:
        return list(chain.from_iterable(self.batches))


@dataclass(frozen=True)
class GuidelineMatchingBatchResult:
    matches: Sequence[GuidelineMatch]
    generation_info: GenerationInfo


@dataclass(frozen=True)
class ResponseAnalysisBatchResult:
    analyzed_guidelines: Sequence[AnalyzedGuideline]
    generation_info: GenerationInfo


class GuidelineMatchingBatch(ABC):
    @abstractmethod
    async def process(self) -> GuidelineMatchingBatchResult: ...


class ResponseAnalysisBatch(ABC):
    @abstractmethod
    async def process(self) -> ResponseAnalysisBatchResult: ...


class GuidelineMatchingStrategy(ABC):
    @abstractmethod
    async def create_matching_batches(
        self,
        guidelines: Sequence[Guideline],
        context: GuidelineMatchingContext,
    ) -> Sequence[GuidelineMatchingBatch]: ...

    @abstractmethod
    async def create_response_analysis_batches(
        self,
        guideline_matches: Sequence[GuidelineMatch],
        context: ResponseAnalysisContext,
    ) -> Sequence[ResponseAnalysisBatch]: ...

    @abstractmethod
    async def transform_matches(
        self,
        matches: Sequence[GuidelineMatch],
    ) -> Sequence[GuidelineMatch]: ...


class GuidelineMatchingStrategyResolver(ABC):
    @abstractmethod
    async def resolve(self, guideline: Guideline) -> GuidelineMatchingStrategy: ...


class GuidelineMatcher:
    def __init__(
        self,
        logger: Logger,
        strategy_resolver: GuidelineMatchingStrategyResolver,
    ) -> None:
        self._logger = logger
        self.strategy_resolver = strategy_resolver
        # 使用 session_id 作为 key，避免多 session 并发冲突
        self._partial_generations_by_session: dict[str, list[GenerationInfo]] = {}

    def pop_partial_generations(self, session_id: str) -> list[GenerationInfo]:
        """获取并清除指定 session 的部分完成的 generation_info"""
        return self._partial_generations_by_session.pop(session_id, [])
    
    @policy(
        [
            retry(
                exceptions=Exception,
                max_exceptions=3,
            )
        ]
    )
    async def _process_guideline_matching_batch_with_retry(
        self, batch: GuidelineMatchingBatch
    ) -> GuidelineMatchingBatchResult:
        with self._logger.scope(batch.__class__.__name__):
            return await batch.process()

    @policy(
        [
            retry(
                exceptions=Exception,
                max_exceptions=3,
            )
        ]
    )
    async def _process_response_analysis_batch_with_retry(
        self, batch: ResponseAnalysisBatch
    ) -> ResponseAnalysisBatchResult:
        with self._logger.scope(batch.__class__.__name__):
            return await batch.process()

    async def match_guidelines(
        self,
        context: LoadedContext,
        active_journeys: Sequence[Journey],
        guidelines: Sequence[Guideline],
    ) -> GuidelineMatchingResult:
        if not guidelines:
            return GuidelineMatchingResult(
                total_duration=0.0,
                batch_count=0,
                batch_generations=[],
                batches=[],
                matches=[],
            )

        t_start = time.time()

        with self._logger.scope("GuidelineMatcher"):
            guideline_strategies: dict[str, tuple[GuidelineMatchingStrategy, list[Guideline]]] = {}

            for guideline in guidelines:
                strategy = await self.strategy_resolver.resolve(guideline)
                if strategy.__class__.__name__ not in guideline_strategies:
                    guideline_strategies[strategy.__class__.__name__] = (strategy, [])
                guideline_strategies[strategy.__class__.__name__][1].append(guideline)

            batches = await async_utils.safe_gather(
                *[
                    strategy.create_matching_batches(
                        guidelines,
                        context=GuidelineMatchingContext(
                            agent=context.agent,
                            session=context.session,
                            customer=context.customer,
                            context_variables=context.state.context_variables,
                            interaction_history=context.interaction.history,
                            terms=list(context.state.glossary_terms),
                            capabilities=context.state.capabilities,
                            staged_events=context.state.tool_events,
                            active_journeys=active_journeys,
                            journey_paths=context.state.journey_paths,
                        ),
                    )
                    for _, (strategy, guidelines) in guideline_strategies.items()
                ]
            )

            with self._logger.operation("Processing batches", create_scope=False):
                batch_tasks = [
                    asyncio.create_task(self._process_guideline_matching_batch_with_retry(batch))
                    for strategy_batches in batches
                    for batch in strategy_batches
                ]
                
                try:
                    batch_results = await asyncio.gather(*batch_tasks)
                except asyncio.CancelledError:
                    # 任务被取消，收集已完成的 batch 结果
                    batch_results = []
                    for task in batch_tasks:
                        if task.done() and not task.cancelled():
                            try:
                                batch_results.append(task.result())
                            except Exception:
                                pass  # 忽略异常的 task
                        task.cancel()  # 取消未完成的 task
                    
                    # 保存部分完成的 generations（按 session_id 隔离）
                    session_id = context.session.id
                    if session_id:
                        self._partial_generations_by_session[session_id] = [
                            result.generation_info for result in batch_results
                        ]
                        self._logger.info(
                            f"💾 [inspection] Collected {len(batch_results)} completed batches "
                            f"out of {len(batch_tasks)} before cancellation, "
                            f"saved {len(self._partial_generations_by_session[session_id])} generation_info for session {session_id}"
                        )
                    else:
                        self._logger.warning(
                            f"💾 Collected {len(batch_results)} completed batches but no session_id provided, "
                            f"partial results will be lost"
                        )
                    
                    # 重新抛出 CancelledError
                    raise

        t_end = time.time()

        result_batches = [result.matches for result in batch_results]
        matches: Sequence[GuidelineMatch] = list(chain.from_iterable(result_batches))

        for strategy, _ in guideline_strategies.values():
            matches = await strategy.transform_matches(matches)

        return GuidelineMatchingResult(
            total_duration=t_end - t_start,
            batch_count=len(batches[0]),
            batch_generations=[result.generation_info for result in batch_results],
            batches=result_batches,
            matches=matches,
        )

    async def analyze_response(
        self,
        agent: Agent,
        session: Session,
        customer: Customer,
        context_variables: Sequence[tuple[ContextVariable, ContextVariableValue]],
        interaction_history: Sequence[Event],
        terms: Sequence[Term],
        staged_tool_events: Sequence[EmittedEvent],
        staged_message_events: Sequence[EmittedEvent],
        guideline_matches: Sequence[GuidelineMatch],
    ) -> ResponseAnalysisResult:
        if not guideline_matches:
            return ResponseAnalysisResult(
                total_duration=0.0,
                batch_count=0,
                batch_generations=[],
                batches=[],
            )

        t_start = time.time()

        with self._logger.scope("GuidelineMatcher"):
            guideline_strategies: dict[
                str, tuple[GuidelineMatchingStrategy, list[GuidelineMatch]]
            ] = {}
            for match in guideline_matches:
                strategy = await self.strategy_resolver.resolve(match.guideline)
                key = strategy.__class__.__name__
                if key not in guideline_strategies:
                    guideline_strategies[key] = (strategy, [])
                guideline_strategies[key][1].append(match)

            batches = await async_utils.safe_gather(
                *[
                    strategy.create_response_analysis_batches(
                        guideline_matches,
                        context=ResponseAnalysisContext(
                            agent,
                            session,
                            customer,
                            context_variables,
                            interaction_history,
                            terms,
                            staged_tool_events,
                            staged_message_events,
                        ),
                    )
                    for _, (strategy, guideline_matches) in guideline_strategies.items()
                ]
            )

            with self._logger.operation("Processing response analysis batches"):
                batch_tasks = [
                    self._process_response_analysis_batch_with_retry(batch)
                    for strategy_batches in batches
                    for batch in strategy_batches
                ]
                batch_results = await async_utils.safe_gather(*batch_tasks)

        t_end = time.time()

        return ResponseAnalysisResult(
            total_duration=t_end - t_start,
            batch_count=len(batch_results),
            batch_generations=[result.generation_info for result in batch_results],
            batches=[result.analyzed_guidelines for result in batch_results],
        )
