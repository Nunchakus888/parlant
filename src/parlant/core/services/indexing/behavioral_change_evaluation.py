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
import contextvars
import traceback
from typing import Optional, Sequence, cast

from parlant.core import async_utils
from parlant.core.agents import AgentStore
from parlant.core.background_tasks import BackgroundTaskService
from parlant.core.common import JSONSerializable, md5_checksum
from parlant.core.evaluations import (
    Evaluation,
    EvaluationStatus,
    EvaluationId,
    GuidelinePayload,
    InvoiceData,
    InvoiceJourneyData,
    JourneyPayload,
    Invoice,
    InvoiceGuidelineData,
    EvaluationStore,
    PayloadDescriptor,
    PayloadKind,
)
from parlant.core.guidelines import Guideline, GuidelineContent, GuidelineStore
from parlant.core.journey_guideline_projection import (
    JourneyGuidelineProjection,
    extract_node_id_from_journey_node_guideline_id,
)
from parlant.core.journeys import Journey, JourneyId, JourneyStore
from parlant.core.services.indexing.common import EvaluationError, ProgressReport
from parlant.core.services.indexing.customer_dependent_action_detector import (
    CustomerDependentActionDetector,
    CustomerDependentActionProposition,
)
from parlant.core.services.indexing.guideline_action_proposer import (
    GuidelineActionProposer,
    GuidelineActionProposition,
)
from parlant.core.services.indexing.guideline_agent_intention_proposer import (
    AgentIntentionProposer,
    AgentIntentionProposition,
)
from parlant.core.services.indexing.guideline_continuous_proposer import (
    GuidelineContinuousProposer,
    GuidelineContinuousProposition,
)
from parlant.core.loggers import Logger
from parlant.core.entity_cq import EntityQueries
from parlant.core.services.indexing.relative_action_proposer import (
    RelativeActionProposer,
    RelativeActionProposition,
)
from parlant.core.services.indexing.tool_running_action_detector import (
    ToolRunningActionDetector,
    ToolRunningActionProposition,
)
from parlant.core.services.indexing.journey_structure_proposer import (
    JourneyStructureProposer,
)
from parlant.core.services.indexing.journey_structure_analysis import (
    JourneyStructureProposition,
)
from parlant.core.nlp.generation_info import GenerationInfo


# 模块级别的当前 evaluator 实例（用于 proposers 调用）
# 注意：通过 contextvars 确保异步安全，每个 evaluation 任务独立
_current_evaluator: contextvars.ContextVar["BehavioralChangeEvaluator | None"] = (
    contextvars.ContextVar('_current_evaluator', default=None)
)


def add_generation_info(info: GenerationInfo) -> None:
    """
    添加 GenerationInfo 到当前评估任务
    注意：此函数通过 contextvars 获取当前的 evaluator 实例，确保异步安全
    影响范围：仅限于当前 evaluation 任务的调用链
    """
    evaluator = _current_evaluator.get()
    if evaluator is not None:
        evaluator.add_generation_info(info)


class EvaluationValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class JourneyEvaluator:
    def __init__(
        self,
        logger: Logger,
        guideline_store: GuidelineStore,
        journey_store: JourneyStore,
        journey_guideline_projection: JourneyGuidelineProjection,
        relative_action_proposer: RelativeActionProposer,
    ) -> None:
        self._logger = logger

        self._guideline_store = guideline_store
        self._journey_store = journey_store
        self._journey_guideline_projection = journey_guideline_projection

        self._relative_action_proposer = relative_action_proposer

    async def _build_invoice_data(
        self,
        relative_action_propositions: Sequence[RelativeActionProposition],
        journey_projections: dict[JourneyId, tuple[Journey, Sequence[Guideline], tuple[Guideline]]],
    ) -> Sequence[InvoiceJourneyData]:
        index_to_node_ids = {
            journey_id: {
                cast(dict[str, JSONSerializable], g.metadata["journey_node"])[
                    "index"
                ]: extract_node_id_from_journey_node_guideline_id(g.id)
                for g in journey_projections[journey_id][1]
            }
            for journey_id in journey_projections
        }

        result = []

        for proposition, journey_id in zip(
            relative_action_propositions, journey_projections.keys()
        ):
            invoice_data = InvoiceJourneyData(
                node_properties_proposition={
                    index_to_node_ids[journey_id][r.index]: {
                        "internal_action": r.rewritten_actions,
                    }
                    for r in proposition.actions
                },
                edge_properties_proposition={},
            )

            result.append(invoice_data)

        return result

    async def evaluate(
        self,
        payloads: Sequence[JourneyPayload],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[InvoiceJourneyData]:
        journeys: dict[JourneyId, Journey] = {
            j.id: j
            for j in await async_utils.safe_gather(
                *[
                    self._journey_store.read_journey(journey_id=payload.journey_id)
                    for payload in payloads
                ]
            )
        }

        journey_conditions = [
            await async_utils.safe_gather(
                *[
                    self._guideline_store.read_guideline(guideline_id=condition)
                    for condition in journey.conditions
                ]
            )
            for journey in journeys.values()
        ]

        journey_projections = {
            payload.journey_id: (journeys[payload.journey_id], projection, conditions)
            for payload, projection, conditions in zip(
                payloads,
                await async_utils.safe_gather(
                    *[
                        self._journey_guideline_projection.project_journey_to_guidelines(
                            journey_id=payload.journey_id
                        )
                        for payload in payloads
                    ]
                ),
                journey_conditions,
            )
        }

        relative_action_propositions = await self._propose_relative_actions(
            journey_projections,
            progress_report,
        )

        invoices = await self._build_invoice_data(
            relative_action_propositions,
            journey_projections,
        )

        return invoices

    async def _propose_relative_actions(
        self,
        journey_projections: dict[JourneyId, tuple[Journey, Sequence[Guideline], tuple[Guideline]]],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[RelativeActionProposition]:
        tasks: list[asyncio.Task[RelativeActionProposition]] = []

        for journey_id, (
            journey,
            step_guidelines,
            journey_conditions,
        ) in journey_projections.items():
            if not step_guidelines:
                continue

            tasks.append(
                asyncio.create_task(
                    self._relative_action_proposer.propose_relative_action(
                        examined_journey=journey,
                        step_guidelines=step_guidelines,
                        journey_conditions=journey_conditions,
                        progress_report=progress_report,
                    )
                )
            )

        sparse_results = list(await async_utils.safe_gather(*tasks))

        return sparse_results


class GuidelineEvaluator:
    def __init__(
        self,
        logger: Logger,
        entity_queries: EntityQueries,
        guideline_action_proposer: GuidelineActionProposer,
        guideline_continuous_proposer: GuidelineContinuousProposer,
        customer_dependent_action_detector: CustomerDependentActionDetector,
        agent_intention_proposer: AgentIntentionProposer,
        tool_running_action_detector: ToolRunningActionDetector,
        journey_structure_proposer: JourneyStructureProposer,
    ) -> None:
        self._logger = logger
        self._entity_queries = entity_queries
        self._guideline_action_proposer = guideline_action_proposer
        self._guideline_continuous_proposer = guideline_continuous_proposer
        self._customer_dependent_action_detector = customer_dependent_action_detector
        self._agent_intention_proposer = agent_intention_proposer
        self._tool_running_action_detector = tool_running_action_detector
        self._journey_structure_proposer = journey_structure_proposer

    def _build_invoice_data(
        self,
        action_propositions: Sequence[Optional[GuidelineActionProposition]],
        continuous_propositions: Sequence[Optional[GuidelineContinuousProposition]],
        customer_dependant_action_detections: Sequence[
            Optional[CustomerDependentActionProposition]
        ],
        agent_intention_propositions: Sequence[Optional[AgentIntentionProposition]],
        tool_running_action_propositions: Sequence[Optional[ToolRunningActionProposition]],
        journey_structure_propositions: Sequence[Optional[JourneyStructureProposition]],
    ) -> Sequence[InvoiceGuidelineData]:
        results = []
        for (
            payload_action,
            payload_continuous,
            payload_customer_dependent,
            agent_intention,
            tool_running_action,
            journey_structure,
        ) in zip(
            action_propositions,
            continuous_propositions,
            customer_dependant_action_detections,
            agent_intention_propositions,
            tool_running_action_propositions,
            journey_structure_propositions,
        ):
            properties_prop: dict[str, JSONSerializable] = {
                **{
                    "continuous": payload_continuous.is_continuous if payload_continuous else None,
                    "customer_dependent_action_data": payload_customer_dependent.model_dump()
                    if payload_customer_dependent
                    else None,
                    "agent_intention_condition": agent_intention.rewritten_condition
                    if agent_intention
                    and agent_intention.rewritten_condition
                    and agent_intention.is_agent_intention
                    else None,
                    "internal_action": payload_action.content.action if payload_action else None,
                },
                **(
                    {"tool_running_only": tool_running_action.is_tool_running_only}
                    if tool_running_action
                    else {}
                ),
                **(
                    {
                        "is_journey_candidate": journey_structure.is_journey_candidate,
                        "journey_confidence": journey_structure.confidence,
                        "journey_reasoning": journey_structure.reasoning,
                        "journey_graph": journey_structure.journey_graph.to_dict()
                        if journey_structure.journey_graph
                        else None,
                    }
                    if journey_structure
                    else {}
                ),
            }

            invoice_data = InvoiceGuidelineData(
                properties_proposition=properties_prop,
            )

            results.append(invoice_data)

        return results

    async def evaluate(
        self,
        payloads: Sequence[GuidelinePayload],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[InvoiceGuidelineData]:
        action_propositions = await self._propose_actions(
            payloads,
            progress_report,
        )

        continuous_propositions = await self._propose_continuous(
            payloads,
            action_propositions,
            progress_report,
        )

        customer_dependant_action_detections = await self._detect_customer_dependant_actions(
            payloads, action_propositions, progress_report
        )

        agent_intention_propositions = await self._propose_agent_intention(
            payloads, progress_report
        )

        tool_running_action_propositions = await self._detect_tool_running_actions(
            payloads, progress_report
        )

        journey_structure_propositions = await self._propose_journey_structures(
            payloads, progress_report
        )

        return self._build_invoice_data(
            action_propositions,
            continuous_propositions,
            customer_dependant_action_detections,
            agent_intention_propositions,
            tool_running_action_propositions,
            journey_structure_propositions,
        )

    async def _propose_actions(
        self,
        payloads: Sequence[GuidelinePayload],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[Optional[GuidelineActionProposition]]:
        tasks: list[asyncio.Task[Optional[GuidelineActionProposition]]] = []
        indices: list[int] = []

        for i, p in enumerate(payloads):
            if p.action_proposition:
                indices.append(i)
                tasks.append(
                    asyncio.create_task(
                        self._guideline_action_proposer.propose_action(
                            guideline=p.content,
                            tool_ids=p.tool_ids or [],
                            progress_report=progress_report,
                        )
                    )
                )

        sparse_results = await async_utils.safe_gather(*tasks)
        results: list[Optional[GuidelineActionProposition]] = [None] * len(payloads)
        for i, res in zip(indices, sparse_results):
            results[i] = res

        return results

    async def _detect_customer_dependant_actions(
        self,
        payloads: Sequence[GuidelinePayload],
        proposed_actions: Sequence[Optional[GuidelineActionProposition]],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[Optional[CustomerDependentActionProposition]]:
        tasks: list[asyncio.Task[CustomerDependentActionProposition]] = []
        indices: list[int] = []
        for i, (p, action_prop) in enumerate(zip(payloads, proposed_actions)):
            if not p.properties_proposition and not p.journey_node_proposition:
                continue
            action_to_use = (
                action_prop.content.action if action_prop is not None else p.content.action
            )
            guideline_content = GuidelineContent(
                condition=p.content.condition,
                action=action_to_use,
            )
            indices.append(i)
            tasks.append(
                asyncio.create_task(
                    self._customer_dependent_action_detector.detect_if_customer_dependent(
                        guideline=guideline_content,
                        progress_report=progress_report,
                    )
                )
            )
        sparse_results = await async_utils.safe_gather(*tasks)
        results: list[Optional[CustomerDependentActionProposition]] = [None] * len(payloads)
        for i, res in zip(indices, sparse_results):
            results[i] = res
        return results

    async def _propose_continuous(
        self,
        payloads: Sequence[GuidelinePayload],
        proposed_actions: Sequence[Optional[GuidelineActionProposition]],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[Optional[GuidelineContinuousProposition]]:
        tasks: list[asyncio.Task[GuidelineContinuousProposition]] = []
        indices: list[int] = []

        for i, (p, action_prop) in enumerate(zip(payloads, proposed_actions)):
            if not p.properties_proposition:
                continue

            action_to_use = (
                action_prop.content.action if action_prop is not None else p.content.action
            )
            guideline_content = GuidelineContent(
                condition=p.content.condition,
                action=action_to_use,
            )

            indices.append(i)
            tasks.append(
                asyncio.create_task(
                    self._guideline_continuous_proposer.propose_continuous(
                        guideline=guideline_content,
                        progress_report=progress_report,
                    )
                )
            )

        sparse_results = await async_utils.safe_gather(*tasks)
        results: list[Optional[GuidelineContinuousProposition]] = [None] * len(payloads)
        for i, res in zip(indices, sparse_results):
            results[i] = res
        return results

    async def _propose_agent_intention(
        self,
        payloads: Sequence[GuidelinePayload],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[Optional[AgentIntentionProposition]]:
        tasks: list[asyncio.Task[AgentIntentionProposition]] = []
        indices: list[int] = []

        for i, p in enumerate(payloads):
            if not p.properties_proposition:
                continue

            guideline_content = GuidelineContent(
                condition=p.content.condition,
                action=p.content.action,
            )

            indices.append(i)
            tasks.append(
                asyncio.create_task(
                    self._agent_intention_proposer.propose_agent_intention(
                        guideline=guideline_content,
                        progress_report=progress_report,
                    )
                )
            )

        sparse_results = await async_utils.safe_gather(*tasks)
        results: list[Optional[AgentIntentionProposition]] = [None] * len(payloads)
        for i, res in zip(indices, sparse_results):
            results[i] = res
        return results

    async def _detect_tool_running_actions(
        self,
        payloads: Sequence[GuidelinePayload],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[Optional[ToolRunningActionProposition]]:
        tasks: list[asyncio.Task[ToolRunningActionProposition]] = []
        indices: list[int] = []

        for i, p in enumerate(payloads):
            if not p.journey_node_proposition:
                continue

            tasks.append(
                asyncio.create_task(
                    self._tool_running_action_detector.detect_if_tool_running(
                        guideline=p.content,
                        tool_ids=p.tool_ids,
                        progress_report=progress_report,
                        id=p.id,
                    )
                )
            )
            indices.append(i)

        sparse_results = await async_utils.safe_gather(*tasks)
        results: list[Optional[ToolRunningActionProposition]] = [None] * len(payloads)

        for i, res in zip(indices, sparse_results):
            results[i] = res

        return results

    async def _propose_journey_structures(
        self,
        payloads: Sequence[GuidelinePayload],
        progress_report: Optional[ProgressReport] = None,
    ) -> Sequence[Optional[JourneyStructureProposition]]:
        """分析guideline的Journey结构"""
        tasks: list[asyncio.Task[JourneyStructureProposition]] = []
        indices: list[int] = []

        for i, p in enumerate(payloads):
            # 只对需要properties_proposition的payload处理
            if p.properties_proposition:
                indices.append(i)
                tasks.append(
                    asyncio.create_task(
                        self._journey_structure_proposer.propose_journey_structure(
                            guideline=p.content,
                            tool_ids=p.tool_ids or [],
                            progress_report=progress_report,
                        )
                    )
                )

        sparse_results = await async_utils.safe_gather(*tasks)
        results: list[Optional[JourneyStructureProposition]] = [None] * len(payloads)
        for i, res in zip(indices, sparse_results):
            results[i] = res

        return results


class BehavioralChangeEvaluator:
    def __init__(
        self,
        logger: Logger,
        background_task_service: BackgroundTaskService,
        agent_store: AgentStore,
        guideline_store: GuidelineStore,
        journey_store: JourneyStore,
        evaluation_store: EvaluationStore,
        entity_queries: EntityQueries,
        journey_guideline_projection: JourneyGuidelineProjection,
        guideline_action_proposer: GuidelineActionProposer,
        guideline_continuous_proposer: GuidelineContinuousProposer,
        customer_dependent_action_detector: CustomerDependentActionDetector,
        agent_intention_proposer: AgentIntentionProposer,
        tool_running_action_detector: ToolRunningActionDetector,
        relative_action_proposer: RelativeActionProposer,
        journey_structure_proposer: JourneyStructureProposer,
    ) -> None:
        self._logger = logger
        self._background_task_service = background_task_service

        self._agent_store = agent_store

        self._evaluation_store = evaluation_store
        self._entity_queries = entity_queries
        
        # 存储评估任务的 GenerationInfo（按 evaluation_id 管理）
        # 注意：每个 evaluation_id 唯一，支持并发多个 evaluation 任务，无污染风险
        # key: EvaluationId（唯一标识一次评估任务）
        # value: list[GenerationInfo]（该评估的所有LLM调用记录）
        self._evaluation_generations: dict[EvaluationId, list[GenerationInfo]] = {}
        
        # 当前评估任务的 GenerationInfo 收集器（本地变量，按 evaluation_id 隔离）
        # 使用 contextvars 支持异步上下文，但限定在实例级别
        self._current_generation_collector: contextvars.ContextVar[list[GenerationInfo] | None] = (
            contextvars.ContextVar('_current_generation_collector', default=None)
        )

        self._guideline_evaluator = GuidelineEvaluator(
            logger=logger,
            entity_queries=entity_queries,
            guideline_action_proposer=guideline_action_proposer,
            guideline_continuous_proposer=guideline_continuous_proposer,
            customer_dependent_action_detector=customer_dependent_action_detector,
            agent_intention_proposer=agent_intention_proposer,
            tool_running_action_detector=tool_running_action_detector,
            journey_structure_proposer=journey_structure_proposer,
        )

        self._journey_evaluator = JourneyEvaluator(
            logger=logger,
            guideline_store=guideline_store,
            journey_store=journey_store,
            journey_guideline_projection=journey_guideline_projection,
            relative_action_proposer=relative_action_proposer,
        )

    def add_generation_info(self, info: GenerationInfo) -> None:
        """
        添加 GenerationInfo 到当前评估任务的收集器
        注意：只在 run_evaluation 上下文中有效，影响范围限定在当前 evaluation 任务
        """
        collector = self._current_generation_collector.get()
        if collector is not None:
            collector.append(info)
    
    def get_evaluation_generations(self, evaluation_id: EvaluationId) -> Sequence[GenerationInfo]:
        """获取指定评估任务收集到的 GenerationInfo"""
        return self._evaluation_generations.get(evaluation_id, [])
    
    def clear_evaluation_generations(self, evaluation_id: EvaluationId) -> None:
        """清除指定评估任务的 GenerationInfo，释放内存"""
        self._evaluation_generations.pop(evaluation_id, None)
    
    async def validate_payloads(
        self,
        payload_descriptors: Sequence[PayloadDescriptor],
    ) -> None:
        if not payload_descriptors:
            raise EvaluationValidationError("No payloads provided for the evaluation task.")

    async def create_evaluation_task(
        self,
        payload_descriptors: Sequence[PayloadDescriptor],
    ) -> EvaluationId:
        await self.validate_payloads(payload_descriptors)

        evaluation = await self._evaluation_store.create_evaluation(
            payload_descriptors,
        )

        await self._background_task_service.start(
            self.run_evaluation(evaluation),
            tag=f"evaluation({evaluation.id})",
        )

        return evaluation.id

    async def run_evaluation(
        self,
        evaluation: Evaluation,
    ) -> None:
        async def _update_progress(percentage: float) -> None:
            await self._evaluation_store.update_evaluation(
                evaluation_id=evaluation.id,
                params={"progress": percentage},
            )

        progress_report = ProgressReport(_update_progress)

        try:
            await self._evaluation_store.update_evaluation(
                evaluation_id=evaluation.id,
                params={"status": EvaluationStatus.RUNNING},
            )

            # 创建本地 GenerationInfo 收集器（每次评估任务独立）
            # 使用 contextvars 支持异步调用链，但作用域限定在当前 evaluation
            generation_infos: list[GenerationInfo] = []
            collector_token = self._current_generation_collector.set(generation_infos)
            evaluator_token = _current_evaluator.set(self)

            guideline_evaluation_data, journey_evaluation_data = await async_utils.safe_gather(
                self._guideline_evaluator.evaluate(
                    payloads=[
                        cast(GuidelinePayload, invoice.payload)
                        for invoice in evaluation.invoices
                        if invoice.kind == PayloadKind.GUIDELINE
                    ],
                    progress_report=progress_report,
                ),
                self._journey_evaluator.evaluate(
                    payloads=[
                        cast(JourneyPayload, invoice.payload)
                        for invoice in evaluation.invoices
                        if invoice.kind == PayloadKind.JOURNEY
                    ],
                    progress_report=progress_report,
                ),
            )

            evaluation_data: Sequence[InvoiceData] = list(guideline_evaluation_data) + list(
                journey_evaluation_data
            )

            invoices: list[Invoice] = []
            for i, result in enumerate(evaluation_data):
                invoice_checksum = md5_checksum(str(evaluation.invoices[i].payload))
                state_version = str(hash("Temporarily"))

                # logger the journey evaluation data
                if evaluation.invoices[i].kind == PayloadKind.JOURNEY:
                    self._logger.info(f"DAG evaluation data: {result.model_dump_json(indent=2)}")

                invoices.append(
                    Invoice(
                        kind=evaluation.invoices[i].kind,
                        payload=evaluation.invoices[i].payload,
                        checksum=invoice_checksum,
                        state_version=state_version,
                        approved=True,
                        data=result,
                        error=None,
                    )
                )

            await self._evaluation_store.update_evaluation(
                evaluation_id=evaluation.id,
                params={"invoices": invoices},
            )
            
            # 存储收集到的 GenerationInfo
            # 按 evaluation_id 存储，支持并发多个 evaluation 任务
            if generation_infos:
                self._evaluation_generations[evaluation.id] = generation_infos
                total_tokens = sum(g.usage.total_tokens or 0 for g in generation_infos)
                self._logger.info(
                    f"📊 Evaluation {evaluation.id} collected {len(generation_infos)} generations, "
                    f"total tokens: {total_tokens}, "
                    f"models: {', '.join(set(g.model for g in generation_infos))}"
                )
            
            # 重置 context variables（清理上下文，防止泄漏）
            self._current_generation_collector.reset(collector_token)
            _current_evaluator.reset(evaluator_token)

            await self._evaluation_store.update_evaluation(
                evaluation_id=evaluation.id,
                params={"status": EvaluationStatus.COMPLETED},
            )

        except Exception as exc:
            logger_level = "info" if isinstance(exc, EvaluationError) else "error"
            getattr(self._logger, logger_level)(
                f"Evaluation task '{evaluation.id}' failed due to the following error: '{str(exc)}'"
            )

            await self._evaluation_store.update_evaluation(
                evaluation_id=evaluation.id,
                params={
                    "status": EvaluationStatus.FAILED,
                    "error": str(exc) + str(traceback.format_exception(exc)),
                },
            )

            raise
