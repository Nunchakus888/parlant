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

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import enum
from hashlib import md5
import importlib.util
from itertools import chain
from pathlib import Path
import sys
import rich
from rich.console import Group
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TimeElapsedColumn,
    TaskID,
    TextColumn,
)
from rich.live import Live
from rich.text import Text
from types import TracebackType
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Generic,
    Iterable,
    Literal,
    Mapping,
    NoReturn,
    Optional,
    Sequence,
    TypeVar,
    TypeAlias,
    TypedDict,
    cast,
    Dict,
)
from typing_extensions import overload
from lagom import Container


from parlant.adapters.db.json_file import JSONFileDocumentCollection, JSONFileDocumentDatabase
from parlant.adapters.db.transient import TransientDocumentDatabase
from parlant.adapters.vector_db.transient import TransientVectorDatabase
from parlant.api.authorization import (
    AuthorizationException,
    Operation,
    AuthorizationPolicy,
    BasicRateLimiter,
    DevelopmentAuthorizationPolicy,
    ProductionAuthorizationPolicy,
    RateLimitExceededException,
    RateLimiter,
)

from parlant.core.services.indexing.behavioral_change_evaluation import BehavioralChangeEvaluator
from parlant.core import async_utils
from parlant.core.agents import (
    AgentDocumentStore,
    AgentId,
    Agent,
    AgentStore,
    CompositionMode as _CompositionMode,
)
from parlant.core.async_utils import Timeout, default_done_callback
from parlant.core.capabilities import CapabilityId, CapabilityStore, CapabilityVectorStore
from parlant.core.common import IdGenerator, ItemNotFoundError, JSONSerializable, Version
from parlant.core.context_variables import (
    ContextVariable,
    ContextVariableDocumentStore,
    ContextVariableId,
    ContextVariableStore,
)
from parlant.core.contextual_correlator import ContextualCorrelator

from parlant.core.customers import (
    Customer as _Customer,
    CustomerDocumentStore,
    CustomerId,
    CustomerStore,
)
from parlant.core.emissions import EmittedEvent, EventEmitterFactory
from parlant.core.entity_cq import EntityQueries
from parlant.core.engines.alpha.prompt_builder import PromptBuilder, PromptSection
from parlant.core.engines.alpha.hooks import EngineHook, EngineHookResult, EngineHooks
from parlant.core.engines.alpha.loaded_context import LoadedContext, Interaction, InteractionMessage
from parlant.core.glossary import GlossaryStore, GlossaryVectorStore, TermId
from parlant.core.guideline_tool_associations import (
    GuidelineToolAssociationDocumentStore,
    GuidelineToolAssociationStore,
)
from parlant.core.nlp.embedding import (
    Embedder,
    EmbedderFactory,
    EmbeddingCache,
    EmbeddingResult,
)
from parlant.core.nlp.generation import (
    FallbackSchematicGenerator,
    SchematicGenerationResult,
    SchematicGenerator,
)
from parlant.core.nlp.tokenization import EstimatingTokenizer
from parlant.core.persistence.common import ObjectId
from parlant.core.persistence.document_database import DocumentDatabase, identity_loader_for
from parlant.core.relationships import (
    RelationshipKind,
    RelationshipDocumentStore,
    RelationshipEntity,
    RelationshipEntityId,
    RelationshipEntityKind,
    RelationshipId,
    RelationshipStore,
)
from parlant.core.services.tools.service_registry import ServiceDocumentRegistry, ServiceRegistry
from parlant.core.sessions import (
    EventKind,
    EventSource,
    MessageEventData,
    Session,
    SessionId,
    SessionDocumentStore,
    SessionStore,
    StatusEventData,
    ToolCall as _SessionToolCall,
    ToolEventData,
    ToolResult as _SessionToolResult,
)
from parlant.core.canned_responses import (
    CannedResponseVectorStore,
    CannedResponseId,
    CannedResponseStore,
)
from parlant.core.evaluations import (
    EvaluationDocumentStore,
    EvaluationId,
    EvaluationStatus,
    EvaluationStore,
    GuidelinePayload,
    InvoiceGuidelineData,
    InvoiceJourneyData,
    JourneyPayload,
    PayloadOperation,
    PayloadDescriptor,
    PayloadKind,
)
from parlant.core.nlp.generation_info import GenerationInfo
from parlant.core.guidelines import (
    GuidelineContent,
    GuidelineDocumentStore,
    GuidelineId,
    GuidelineStore,
)
from parlant.core.journeys import (
    JourneyEdgeId,
    JourneyId,
    JourneyNodeId,
    JourneyStore,
    JourneyVectorStore,
)
from parlant.core.loggers import LogLevel, Logger
from parlant.core.nlp.service import NLPService
from parlant.core.nlp.moderation import (
    ModerationCheck,
    ModerationService,
    ModerationTag,
    NoModeration,
)
from parlant.core.engines.alpha.canned_response_generator import (
    NoMatchResponseProvider,
    BasicNoMatchResponseProvider,
)
from parlant.core.engines.alpha.optimization_policy import (
    OptimizationPolicy,
    BasicOptimizationPolicy,
)
from parlant.core.engines.alpha.perceived_performance_policy import (
    PerceivedPerformancePolicy,
    NullPerceivedPerformancePolicy,
    BasicPerceivedPerformancePolicy,
)
from parlant.bin.server import PARLANT_HOME_DIR, start_parlant, StartupParameters
from parlant.core.services.tools.plugins import PluginServer, ToolEntry, tool
from parlant.core.tags import Tag as _Tag, TagDocumentStore, TagId, TagStore
from parlant.core.tools import (
    ControlOptions,
    Lifespan,
    SessionMode,
    SessionStatus,
    Tool,
    ToolContext,
    ToolId,
    ToolParameterDescriptor,
    ToolParameterOptions,
    ToolParameterType,
    ToolResult,
)
from parlant.core.version import VERSION

INTEGRATED_TOOL_SERVICE_NAME = "built-in"

T = TypeVar("T")

# AgentFactory moved to parlant.core.agent_factory to avoid circular imports
from parlant.core.agent_factory import AgentFactory

JourneyStateId: TypeAlias = JourneyNodeId
JourneyTransitionId: TypeAlias = JourneyEdgeId


class SDKError(Exception):
    """Main class for SDK-related errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class NLPServices:
    """A collection of static methods to create built-in NLPService instances for the SDK."""

    @staticmethod
    def azure(container: Container) -> NLPService:
        """Creates an Azure NLPService instance using the provided container."""
        from parlant.adapters.nlp.azure_service import AzureService

        if error := AzureService.verify_environment():
            raise SDKError(error)

        return AzureService(container[Logger])

    @staticmethod
    def openai(container: Container) -> NLPService:
        """Creates an OpenAI NLPService instance using the provided container."""
        from parlant.adapters.nlp.openai_service import OpenAIService

        if error := OpenAIService.verify_environment():
            raise SDKError(error)

        return OpenAIService(container[Logger])

    @staticmethod
    def anthropic(container: Container) -> NLPService:
        """Creates an Anthropic NLPService instance using the provided container."""
        from parlant.adapters.nlp.anthropic_service import AnthropicService

        if error := AnthropicService.verify_environment():
            raise SDKError(error)

        return AnthropicService(container[Logger])

    @staticmethod
    def cerebras(container: Container) -> NLPService:
        """Creates a Cerebras NLPService instance using the provided container."""
        from parlant.adapters.nlp.cerebras_service import CerebrasService

        if error := CerebrasService.verify_environment():
            raise SDKError(error)

        return CerebrasService(container[Logger])

    @staticmethod
    def together(container: Container) -> NLPService:
        """Creates a Together NLPService instance using the provided container."""
        from parlant.adapters.nlp.together_service import TogetherService

        if error := TogetherService.verify_environment():
            raise SDKError(error)

        return TogetherService(container[Logger])

    @staticmethod
    def gemini(container: Container) -> NLPService:
        """Creates a Gemini NLPService instance using the provided container."""
        from parlant.adapters.nlp.gemini_service import GeminiService

        if error := GeminiService.verify_environment():
            raise SDKError(error)

        return GeminiService(container[Logger])

    @staticmethod
    def litellm(container: Container) -> NLPService:
        """Creates a Litellm NLPService instance using the provided container."""
        from parlant.adapters.nlp.litellm_service import LiteLLMService

        if error := LiteLLMService.verify_environment():
            raise SDKError(error)

        return LiteLLMService(container[Logger])

    @staticmethod
    def openrouter(container: Container) -> NLPService:
        """Creates an OpenRouter NLPService instance using the provided container."""
        from parlant.adapters.nlp.openrouter_service import OpenRouterService

        if error := OpenRouterService.verify_environment():
            raise SDKError(error)

        return OpenRouterService(container[Logger])

    @staticmethod
    def vertex(container: Container) -> NLPService:
        """Creates a Vertex NLPService instance using the provided container."""
        from parlant.adapters.nlp.vertex_service import VertexAIService

        if error := VertexAIService.verify_environment():
            raise SDKError(error)

        if err := VertexAIService.validate_adc():
            raise SDKError(err)

        return VertexAIService(container[Logger])

    @staticmethod
    def ollama(container: Container) -> NLPService:
        """Creates a Ollama NLPService instance using the provided container."""
        from parlant.adapters.nlp.ollama_service import OllamaService

        if error := OllamaService.verify_environment():
            raise SDKError(error)

        if err := OllamaService.verify_models():
            raise SDKError(err)

        return OllamaService(container[Logger])

    @staticmethod
    def glm(container: Container) -> NLPService:
        """Creates a GLM NLPService instance using the provided container."""
        from parlant.adapters.nlp.glm_service import GLMService

        if error := GLMService.verify_environment():
            raise SDKError(error)

        return GLMService(container[Logger])

    @staticmethod
    def snowflake(container: Container) -> NLPService:
        """Creates a SnowflakeCortexService instance using the provided container."""
        from parlant.adapters.nlp.snowflake_cortex_service import SnowflakeCortexService

        if error := SnowflakeCortexService.verify_environment():
            raise SDKError(error)

        return SnowflakeCortexService(container[Logger])


class _CachedGuidelineEvaluation(TypedDict, total=False):
    id: ObjectId
    version: Version.String
    properties: dict[str, JSONSerializable]


class _CachedJourneyEvaluation(TypedDict, total=False):
    id: ObjectId
    version: Version.String
    node_properties: dict[JourneyStateId, dict[str, JSONSerializable]]
    edge_properties: dict[JourneyTransitionId, dict[str, JSONSerializable]]


class _CachedEvaluator:
    @dataclass(frozen=True)
    class JourneyEvaluation:
        node_properties: dict[JourneyStateId, dict[str, JSONSerializable]]
        edge_properties: dict[JourneyTransitionId, dict[str, JSONSerializable]]
        evaluation_id: EvaluationId | None = None

    @dataclass(frozen=True)
    class GuidelineEvaluation:
        properties: dict[str, JSONSerializable]
        evaluation_id: EvaluationId | None = None

    def __init__(
        self,
        db: JSONFileDocumentDatabase,
        container: Container,
    ) -> None:
        self._db: JSONFileDocumentDatabase = db
        # 按 chatbot_id 缓存集合，实现多租户隔离
        self._guideline_collections: dict[str, JSONFileDocumentCollection[_CachedGuidelineEvaluation]] = {}
        self._journey_collections: dict[str, JSONFileDocumentCollection[_CachedJourneyEvaluation]] = {}

        self._container = container
        self._logger = container[Logger]
        self._exit_stack = AsyncExitStack()
        self._progress: dict[str, float] = {}

    def _set_progress(self, key: str, pct: float) -> None:
        self._progress[key] = max(0.0, min(pct, 100.0))

    def _progress_for(self, key: str) -> float:
        return self._progress.get(key, 0.0)

    async def _get_chatbot_id_from_agent(self, agent_id: AgentId) -> str:
        """从 agent_id 获取 chatbot_id"""
        try:
            agent = await self._container[AgentStore].read_agent(agent_id)
            if agent.metadata and "chatbot_id" in agent.metadata:
                return str(agent.metadata["chatbot_id"])
        except Exception as e:
            self._logger.warning(f"Failed to get chatbot_id from agent {agent_id}: {e}")
        
        # 默认使用 agent_id 作为 chatbot_id
        return str(agent_id)

    async def _get_guideline_collection(self, chatbot_id: str) -> JSONFileDocumentCollection[_CachedGuidelineEvaluation]:
        """根据 chatbot_id 获取或创建 guideline 集合"""
        if chatbot_id not in self._guideline_collections:
            self._guideline_collections[chatbot_id] = await self._db.get_or_create_collection(
                name=f"guideline_evaluations_{chatbot_id}",
                schema=_CachedGuidelineEvaluation,
                document_loader=identity_loader_for(_CachedGuidelineEvaluation),
            )
        return self._guideline_collections[chatbot_id]

    async def _get_journey_collection(self, chatbot_id: str) -> JSONFileDocumentCollection[_CachedJourneyEvaluation]:
        """根据 chatbot_id 获取或创建 journey 集合"""
        if chatbot_id not in self._journey_collections:
            self._journey_collections[chatbot_id] = await self._db.get_or_create_collection(
                name=f"journey_evaluations_{chatbot_id}",
                schema=_CachedJourneyEvaluation,
                document_loader=identity_loader_for(_CachedJourneyEvaluation),
            )
        return self._journey_collections[chatbot_id]

    async def __aenter__(self) -> _CachedEvaluator:
        await self._exit_stack.enter_async_context(self._db)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        await self._exit_stack.aclose()
        return False

    def _hash_guideline_evaluation_request(
        self,
        g: GuidelineContent,
        tool_ids: Sequence[ToolId],
        journey_state_propositions: bool,
        properties_proposition: bool,
    ) -> str:
        """Generate a hash for the guideline evaluation request."""
        tool_ids_str = ",".join(str(tool_id) for tool_id in tool_ids) if tool_ids else ""

        return md5(
            f"{g.condition or ''}:{g.action or ''}:{tool_ids_str}:{journey_state_propositions}:{properties_proposition}".encode()
        ).hexdigest()

    def _hash_journey_evaluation_request(
        self,
        journey: Journey,
    ) -> str:
        """Generate a hash for the journey evaluation request."""
        node_ids_str = ",".join(str(node.id) for node in journey.states) if journey.states else ""
        edge_ids_str = (
            ",".join(str(edge.id) for edge in journey.transitions) if journey.transitions else ""
        )

        return md5(f"{journey.id}:{node_ids_str}:{edge_ids_str}".encode()).hexdigest()

    async def evaluate_state(
        self,
        entity_id: JourneyStateId,
        g: GuidelineContent,
        tool_ids: Sequence[ToolId] = [],
        agent_id: AgentId | None = None,
    ) -> _CachedEvaluator.GuidelineEvaluation:
        return await self._evaluate_guideline(
            entity_id=entity_id,
            g=g,
            tool_ids=tool_ids,
            journey_state_proposition=True,
            properties_proposition=False,
            agent_id=agent_id,
        )

    async def evaluate_guideline(
        self,
        entity_id: GuidelineId,
        g: GuidelineContent,
        tool_ids: Sequence[ToolId] = [],
        agent_id: AgentId | None = None,
    ) -> _CachedEvaluator.GuidelineEvaluation:
        return await self._evaluate_guideline(
            entity_id=entity_id,
            g=g,
            tool_ids=tool_ids,
            agent_id=agent_id,
        )

    async def _evaluate_guideline(
        self,
        entity_id: GuidelineId | JourneyStateId,
        g: GuidelineContent,
        tool_ids: Sequence[ToolId] = [],
        action_proposition: bool = True,
        journey_state_proposition: bool = False,
        properties_proposition: bool = True,
        agent_id: AgentId | None = None,
    ) -> _CachedEvaluator.GuidelineEvaluation:
        # 获取 chatbot_id 并获取对应的集合
        chatbot_id = await self._get_chatbot_id_from_agent(agent_id) if agent_id else "default"
        guideline_collection = await self._get_guideline_collection(chatbot_id)
        
        # First check if we have a cached evaluation for this guideline
        _hash = self._hash_guideline_evaluation_request(
            g=g,
            tool_ids=tool_ids,
            journey_state_propositions=journey_state_proposition,
            properties_proposition=properties_proposition,
        )

        if cached_evaluation := await guideline_collection.find_one({"id": {"$eq": _hash}}):
            self._logger.trace(
                f"Cached: C:{(g.condition or '')[:30]} A:{(g.action or '')[:30]}"
            )

            return self.GuidelineEvaluation(
                properties=cached_evaluation["properties"],
                evaluation_id=None,  # 缓存的评估没有evaluation_id
            )

        self._logger.trace(
            f"Evaluating guideline: Condition: {g.condition or 'None'}, Action: {g.action or 'None'}"
        )

        evaluation_id = await self._container[BehavioralChangeEvaluator].create_evaluation_task(
            payload_descriptors=[
                PayloadDescriptor(
                    PayloadKind.GUIDELINE,
                    GuidelinePayload(
                        content=GuidelineContent(
                            condition=g.condition,
                            action=g.action,
                        ),
                        tool_ids=tool_ids,
                        operation=PayloadOperation.ADD,
                        action_proposition=action_proposition,
                        properties_proposition=properties_proposition,
                        journey_node_proposition=journey_state_proposition,
                        id=agent_id,  # 传递 agent_id 用于工具查找
                    ),
                )
            ],
        )

        while True:
            evaluation = await self._container[EvaluationStore].read_evaluation(
                evaluation_id=evaluation_id,
            )

            self._set_progress(entity_id, evaluation.progress)

            if evaluation.status in [EvaluationStatus.PENDING, EvaluationStatus.RUNNING]:
                await asyncio.sleep(0.5)
                continue
            elif evaluation.status == EvaluationStatus.FAILED:
                raise SDKError(f"Evaluation failed: {evaluation.error}")
            elif evaluation.status == EvaluationStatus.COMPLETED:
                if not evaluation.invoices:
                    raise SDKError("Evaluation completed with no invoices.")
                if not evaluation.invoices[0].approved:
                    raise SDKError("Evaluation completed with unapproved invoice.")

                invoice = evaluation.invoices[0]

                if not invoice.data:
                    raise SDKError(
                        "Evaluation completed with no properties_proposition in the invoice."
                    )

            assert invoice.data

            # Cache the evaluation result
            await guideline_collection.insert_one(
                {
                    "id": ObjectId(_hash),
                    "version": Version.String(VERSION),
                    "properties": cast(InvoiceGuidelineData, invoice.data).properties_proposition
                    or {},
                }
            )

            # Return the evaluation result
            return self.GuidelineEvaluation(
                properties=cast(InvoiceGuidelineData, invoice.data).properties_proposition or {},
                evaluation_id=evaluation_id,
            )

    async def evaluate_journey(
        self,
        journey: Journey,
        agent_id: AgentId | None = None,
    ) -> _CachedEvaluator.JourneyEvaluation:
        # 获取 chatbot_id 并获取对应的集合
        chatbot_id = await self._get_chatbot_id_from_agent(agent_id) if agent_id else "default"
        journey_collection = await self._get_journey_collection(chatbot_id)
        
        # First check if we have a cached evaluation for this journey
        _hash = self._hash_journey_evaluation_request(
            journey=journey,
        )

        if cached_evaluation := await journey_collection.find_one({"id": {"$eq": _hash}}):
            self._logger.trace(
                f"Using cached evaluation for journey: Title: {journey.title or 'None'};"
            )

            return self.JourneyEvaluation(
                node_properties=cached_evaluation["node_properties"],
                edge_properties=cached_evaluation["edge_properties"],
                evaluation_id=None,  # 缓存的评估没有evaluation_id
            )

        self._logger.trace(f"Evaluating journey: Title: {journey.title or 'None'}")

        evaluation_id = await self._container[BehavioralChangeEvaluator].create_evaluation_task(
            payload_descriptors=[
                PayloadDescriptor(
                    PayloadKind.JOURNEY,
                    JourneyPayload(
                        journey_id=journey.id,
                        operation=PayloadOperation.ADD,
                    ),
                )
            ],
        )

        while True:
            evaluation = await self._container[EvaluationStore].read_evaluation(
                evaluation_id=evaluation_id,
            )

            self._set_progress(journey.id, evaluation.progress)

            if evaluation.status in [EvaluationStatus.PENDING, EvaluationStatus.RUNNING]:
                await asyncio.sleep(0.5)
                continue
            elif evaluation.status == EvaluationStatus.FAILED:
                raise SDKError(f"Journey Evaluation failed: {evaluation.error}")
            elif evaluation.status == EvaluationStatus.COMPLETED:
                if not evaluation.invoices:
                    raise SDKError("Journey Evaluation completed with no invoices.")
                if not evaluation.invoices[0].approved:
                    raise SDKError("Journey Evaluation completed with unapproved invoice.")

                invoice = evaluation.invoices[0]

                if not invoice.data:
                    raise SDKError("Journey Evaluation completed with no data in the invoice.")

            assert invoice.data

            # Cache the evaluation result
            await journey_collection.insert_one(
                {
                    "id": ObjectId(_hash),
                    "version": Version.String(VERSION),
                    "node_properties": cast(
                        InvoiceJourneyData, invoice.data
                    ).node_properties_proposition,
                    "edge_properties": cast(
                        InvoiceJourneyData, invoice.data
                    ).edge_properties_proposition
                    or {},
                }
            )

            # Return the evaluation result
            return self.JourneyEvaluation(
                node_properties=cast(InvoiceJourneyData, invoice.data).node_properties_proposition
                or {},
                edge_properties=cast(InvoiceJourneyData, invoice.data).edge_properties_proposition
                or {},
                evaluation_id=evaluation_id,
            )


@dataclass(frozen=True)
class Tag:
    """A tag used to categorize and link entities."""

    @staticmethod
    def preamble() -> TagId:
        return _Tag.preamble()

    id: TagId
    name: str


@dataclass(frozen=True)
class Relationship:
    """A relationship between two entities in the system."""

    id: RelationshipId
    kind: RelationshipKind
    source: RelationshipEntityId
    target: RelationshipEntityId


@dataclass(frozen=True)
class Guideline:
    """A guideline that defines a condition and an action to be taken."""

    id: GuidelineId
    condition: str
    action: str | None
    tags: Sequence[TagId]
    metadata: Mapping[str, JSONSerializable]

    _server: Server
    _container: Container

    async def entail(self, guideline: Guideline) -> Relationship:
        """Creates an entailment relationship with another guideline."""
        return await self._create_relationship(
            target=guideline,
            kind=RelationshipKind.ENTAILMENT,
            direction="source",
        )

    async def prioritize_over(self, target: Guideline | Journey) -> Relationship:
        """Creates a priority relationship with another guideline or journey."""
        if isinstance(target, Guideline):
            return await self._create_relationship(
                target=target,
                kind=RelationshipKind.PRIORITY,
                direction="source",
            )
        elif isinstance(target, Journey):
            return await self._create_relationship(
                target=target,
                kind=RelationshipKind.PRIORITY,
                direction="source",
            )
        else:
            raise SDKError("Either guideline or journey must be provided for prioritization.")

    async def depend_on(
        self,
        target: Guideline | Journey,
    ) -> Relationship:
        if isinstance(target, Guideline):
            return await self._create_relationship(
                target=target,
                kind=RelationshipKind.DEPENDENCY,
                direction="source",
            )
        elif isinstance(target, Journey):
            return await self._create_relationship(
                target=target,
                kind=RelationshipKind.DEPENDENCY,
                direction="source",
            )
        else:
            raise SDKError("Either guideline or journey must be provided for dependency.")

    async def disambiguate(
        self,
        targets: Sequence[Guideline | Journey],
    ) -> Sequence[Relationship]:
        if len(targets) < 2:
            raise SDKError(
                f"At least two targets are required for disambiguation (got {len(targets)})."
            )

        guideline_targets = [t for t in targets if isinstance(t, Guideline)]
        journey_conditions = list(
            chain.from_iterable([t.conditions for t in targets if isinstance(t, Journey)])
        )

        return [
            await self._create_relationship(
                target=t,
                kind=RelationshipKind.DISAMBIGUATION,
                direction="source",
            )
            for t in guideline_targets + journey_conditions
        ]

    async def reevaluate_after(self, tool: ToolEntry) -> Relationship:
        """Creates a reevaluation relationship with a tool."""
        relationship = await self._container[RelationshipStore].create_relationship(
            source=RelationshipEntity(
                id=self.id,
                kind=RelationshipEntityKind.GUIDELINE,
            ),
            target=RelationshipEntity(
                id=ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=tool.tool.name),
                kind=RelationshipEntityKind.TOOL,
            ),
            kind=RelationshipKind.REEVALUATION,
        )

        return Relationship(
            id=relationship.id,
            kind=relationship.kind,
            source=relationship.source.id,
            target=relationship.target.id,
        )

    async def _create_relationship(
        self,
        target: Guideline | Journey,
        kind: RelationshipKind,
        direction: Literal["source", "target"],
    ) -> Relationship:
        if direction == "source":
            entity_source = RelationshipEntity(id=self.id, kind=RelationshipEntityKind.GUIDELINE)
            entity_target = (
                RelationshipEntity(id=target.id, kind=RelationshipEntityKind.GUIDELINE)
                if isinstance(target, Guideline)
                else RelationshipEntity(
                    id=_Tag.for_journey_id(target.id), kind=RelationshipEntityKind.TAG
                )
            )
        else:
            entity_source = (
                RelationshipEntity(id=target.id, kind=RelationshipEntityKind.GUIDELINE)
                if isinstance(target, Guideline)
                else RelationshipEntity(
                    id=_Tag.for_journey_id(target.id), kind=RelationshipEntityKind.TAG
                )
            )
            entity_target = RelationshipEntity(id=self.id, kind=RelationshipEntityKind.GUIDELINE)

        relationship = await self._container[RelationshipStore].create_relationship(
            source=entity_source,
            target=entity_target,
            kind=kind,
        )

        return Relationship(
            id=relationship.id,
            kind=relationship.kind,
            source=relationship.source.id,
            target=relationship.target.id,
        )


TState = TypeVar("TState", bound="JourneyState")


@dataclass(frozen=True)
class JourneyTransition(Generic[TState]):
    """A transition between two states in a journey."""

    id: JourneyTransitionId
    condition: str | None
    source: JourneyState
    target: TState
    metadata: Mapping[str, JSONSerializable]


@dataclass(frozen=True)
class JourneyState:
    """A state in a journey that can be transitioned to or from."""

    id: JourneyStateId
    action: str | None
    tools: Sequence[ToolEntry]
    metadata: Mapping[str, JSONSerializable]

    _journey: Journey | None

    @property
    def _internal_action(self) -> str | None:
        return self.action or cast(str | None, self.metadata.get("internal_action"))

    async def _fork(self) -> JourneyTransition[ForkJourneyState]:
        return cast(
            JourneyTransition[ForkJourneyState],
            await self._transition(
                condition=None,
                state=None,
                action=None,
                tools=[],
                fork=True,
            ),
        )

    async def _transition(
        self,
        *,
        condition: str | None = None,
        state: TState | None = None,
        action: str | None = None,
        tools: Sequence[ToolEntry] = [],
        fork: bool = False,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[JourneyState]:
        if not self._journey:
            raise SDKError("EndState cannot be connected to any other states.")

        actual_state: JourneyState | None = None

        if state is not None:
            actual_state = state
        elif tools:
            actual_state = await self._journey._create_state(
                ToolJourneyState,
                action=action,
                tools=tools,
                metadata=metadata,
            )

            [
                await self._journey._container[RelationshipStore].create_relationship(
                    source=RelationshipEntity(
                        id=_Tag.for_journey_node_id(actual_state.id),
                        kind=RelationshipEntityKind.TAG,
                    ),
                    target=RelationshipEntity(
                        id=ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=t.tool.name),
                        kind=RelationshipEntityKind.TOOL,
                    ),
                    kind=RelationshipKind.REEVALUATION,
                )
                for t in tools
            ]

        elif action:
            actual_state = await self._journey._create_state(
                ChatJourneyState,
                action=action,
                tools=[],
                metadata=metadata,
            )
        elif fork:
            actual_state = await self._journey._create_state(
                ForkJourneyState,
                metadata=metadata,
            )

        transitions = [t for t in self._journey.transitions if t.source == self]

        if len(transitions) > 0 and (not condition or any(not e.condition for e in transitions)):
            raise SDKError(
                "Cannot connect a new state without a condition if there are already connected states without conditions."
            )

        transition = await self._journey.create_transition(
            condition=condition, source=self, target=actual_state or END_JOURNEY
        )

        if actual_state:
            cast(list[JourneyState], self._journey.states).append(actual_state)

            for id in canned_responses:
                await self._journey._container[CannedResponseStore].upsert_tag(
                    canned_response_id=id, tag_id=_Tag.for_journey_node_id(actual_state.id)
                )

        cast(list[JourneyTransition[JourneyState]], self._journey.transitions).append(transition)

        return transition


END_JOURNEY = JourneyState(
    id=JourneyStore.END_NODE_ID,
    action=None,
    tools=[],
    metadata={},
    _journey=None,
)
"""A special state used to indicate the end of a journey."""


class InitialJourneyState(JourneyState):
    """A special state used to indicate the initial state of a journey."""

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        state: TState,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[TState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        chat_state: str,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ChatJourneyState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        tool_instruction: str | None = None,
        tool_state: ToolEntry,
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ToolJourneyState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        tool_instruction: str | None = None,
        tool_state: Sequence[ToolEntry],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ToolJourneyState]: ...

    async def transition_to(
        self,
        *,
        condition: str | None = None,
        chat_state: str | None = None,
        tool_instruction: str | None = None,
        state: TState | None = None,
        tool_state: ToolEntry | Sequence[ToolEntry] = [],
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[Any]:
        return await self._transition(
            condition=condition,
            state=state,
            action=chat_state or tool_instruction,
            tools=[tool_state] if isinstance(tool_state, ToolEntry) else tool_state,
            canned_responses=canned_responses,
            metadata=metadata,
        )


class ToolJourneyState(JourneyState):
    """A state in a journey that represents a tool being used."""

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        state: TState,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[TState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        chat_state: str,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ChatJourneyState]: ...

    async def transition_to(
        self,
        *,
        condition: str | None = None,
        chat_state: str | None = None,
        state: TState | None = None,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[Any]:
        return await self._transition(
            condition=condition,
            state=state,
            action=chat_state,
            canned_responses=canned_responses,
            metadata=metadata,
        )

    async def fork(self) -> JourneyTransition[ForkJourneyState]:
        return await super()._fork()


class ChatJourneyState(JourneyState):
    """A state in a journey that represents a chat interaction."""

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        state: TState,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[TState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        chat_state: str,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ChatJourneyState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        tool_instruction: str | None = None,
        tool_state: ToolEntry,
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ToolJourneyState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str | None = None,
        tool_instruction: str | None = None,
        tool_state: Sequence[ToolEntry],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ToolJourneyState]: ...

    async def transition_to(
        self,
        *,
        condition: str | None = None,
        chat_state: str | None = None,
        tool_instruction: str | None = None,
        state: TState | None = None,
        tool_state: ToolEntry | Sequence[ToolEntry] = [],
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[Any]:
        return await self._transition(
            condition=condition,
            state=state,
            action=chat_state or tool_instruction,
            tools=[tool_state] if isinstance(tool_state, ToolEntry) else tool_state,
            canned_responses=canned_responses,
            metadata=metadata,
        )

    async def fork(self) -> JourneyTransition[ForkJourneyState]:
        return await super()._fork()


class ForkJourneyState(JourneyState):
    """A state in a journey that represents a conditional fork in the journey."""

    @overload
    async def transition_to(
        self,
        *,
        condition: str,
        state: TState,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[TState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str,
        chat_state: str,
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ChatJourneyState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str,
        tool_instruction: str | None = None,
        tool_state: ToolEntry,
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ToolJourneyState]: ...

    @overload
    async def transition_to(
        self,
        *,
        condition: str,
        tool_instruction: str | None = None,
        tool_state: Sequence[ToolEntry],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[ToolJourneyState]: ...

    async def transition_to(
        self,
        *,
        condition: str,
        chat_state: str | None = None,
        tool_instruction: str | None = None,
        state: TState | None = None,
        tool_state: ToolEntry | Sequence[ToolEntry] = [],
        canned_responses: Sequence[CannedResponseId] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> JourneyTransition[Any]:
        return await self._transition(
            condition=condition,
            state=state,
            action=chat_state or tool_instruction,
            tools=[tool_state] if isinstance(tool_state, ToolEntry) else tool_state,
            canned_responses=canned_responses,
            metadata=metadata,
        )


@dataclass(frozen=True)
class Journey:
    """A journey that consists of multiple states and transitions."""

    id: JourneyId
    title: str
    description: str
    conditions: list[Guideline]
    states: Sequence[JourneyState]
    transitions: Sequence[JourneyTransition[JourneyState]]
    tags: Sequence[TagId]
    agent_id: AgentId | None

    _start_state_id: JourneyStateId
    _server: Server
    _container: Container

    @property
    def initial_state(self) -> InitialJourneyState:
        """Returns the initial state of the journey."""
        return cast(
            InitialJourneyState, next(n for n in self.states if n.id == self._start_state_id)
        )

    async def _create_state(
        self,
        state_type: type[TState],
        action: str | None = None,
        tools: Sequence[ToolEntry] = [],
        metadata: Mapping[str, JSONSerializable] = {},
    ) -> TState:
        metadata_type = {
            ForkJourneyState: "fork",
            ToolJourneyState: "tool",
            ChatJourneyState: "chat",
        }[state_type]

        for t in list(tools):
            await self._server._plugin_server.enable_tool(t, id=self.agent_id)

        if len(tools) == 1 and not action:
            action = f"Use the tool {tools[0].tool.name}"

        node = await self._container[JourneyStore].create_node(
            journey_id=self.id,
            action=action,
            tools=[
                ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=t.tool.name)
                for t in tools
            ],
        )

        node = await self._container[JourneyStore].set_node_metadata(
            node_id=node.id,
            key="journey_node",
            value={"kind": metadata_type},
        )

        for k, v in metadata.items():
            node = await self._container[JourneyStore].set_node_metadata(
                node_id=node.id,
                key=k,
                value=v,
            )

        return state_type(
            id=node.id,
            action=action,
            tools=tools,
            metadata=node.metadata,
            _journey=self,
        )

    async def create_transition(
        self,
        condition: str | None,
        source: JourneyState,
        target: TState,
    ) -> JourneyTransition[TState]:
        """Creates a transition between two states in the journey."""

        self._server._advance_creation_progress()

        if target is not None and target.id != END_JOURNEY.id:
            target_tool_ids = {
                t.tool.name: ToolId(
                    service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=t.tool.name
                )
                for t in target.tools
            }

            self._server._add_state_evaluation(
                target.id,
                GuidelineContent(condition=condition or "",
                action=target._internal_action),
                list(target_tool_ids.values()),
                agent_id=self.agent_id,
            )

        transition = await self._container[JourneyStore].create_edge(
            journey_id=self.id,
            source=source.id,
            target=target.id if target else END_JOURNEY.id,
            condition=condition,
        )

        return JourneyTransition[TState](
            id=transition.id,
            condition=condition,
            source=source,
            target=target,
            metadata=transition.metadata,
        )

    async def create_guideline(
        self,
        condition: str,
        action: str | None = None,
        tools: Iterable[ToolEntry] = [],
        metadata: dict[str, JSONSerializable] = {},
        canned_responses: Sequence[CannedResponseId] = [],
    ) -> Guideline:
        """Creates a guideline with the specified condition and action, as well as (optionally) tools to achieve its task."""

        self._server._advance_creation_progress()

        tool_ids = [
            ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=t.tool.name) for t in tools
        ]

        for t in list(tools):
            await self._server._plugin_server.enable_tool(t, id=self.agent_id)

        guideline = await self._container[GuidelineStore].create_guideline(
            condition=condition,
            action=action,
            metadata=metadata,
        )

        if canned_responses:
            tag_id = _Tag.for_guideline_id(guideline.id)
            for id in canned_responses:
                await self._container[CannedResponseStore].upsert_tag(
                    canned_response_id=id,
                    tag_id=tag_id,
                )

        self._server._add_guideline_evaluation(
            guideline.id,
            GuidelineContent(condition=condition, action=action),
            tool_ids,
            agent_id=self.agent_id,
        )

        await self._container[RelationshipStore].create_relationship(
            source=RelationshipEntity(
                id=guideline.id,
                kind=RelationshipEntityKind.GUIDELINE,
            ),
            target=RelationshipEntity(
                id=_Tag.for_journey_id(self.id),
                kind=RelationshipEntityKind.TAG,
            ),
            kind=RelationshipKind.DEPENDENCY,
        )

        for t in list(tools):
            await self._container[GuidelineToolAssociationStore].create_association(
                guideline_id=guideline.id,
                tool_id=ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=t.tool.name),
            )

        return Guideline(
            id=guideline.id,
            condition=condition,
            action=action,
            tags=guideline.tags,
            metadata=guideline.metadata,
            _server=self._server,
            _container=self._container,
        )

    async def create_observation(
        self,
        condition: str,
        canned_responses: Sequence[CannedResponseId] = [],
    ) -> Guideline:
        """A shorthand for creating an observational guideline with the specified condition."""

        return await self.create_guideline(condition=condition, canned_responses=canned_responses)

    async def attach_tool(
        self,
        tool: ToolEntry,
        condition: str,
    ) -> GuidelineId:
        """Attaches a tool to the journey, to be usable by the agent under the specified condition."""

        await self._server._plugin_server.enable_tool(tool, id=self.agent_id)

        guideline = await self._container[GuidelineStore].create_guideline(
            condition=condition,
            action=None,
        )

        self._server._add_guideline_evaluation(
            guideline.id,
            GuidelineContent(condition=condition, action=None),
            [ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=tool.tool.name)],
            agent_id=self.agent_id,  # Pass agent_id for Journey's tool attachments
        )

        await self._container[RelationshipStore].create_relationship(
            source=RelationshipEntity(
                id=guideline.id,
                kind=RelationshipEntityKind.GUIDELINE,
            ),
            target=RelationshipEntity(
                id=_Tag.for_journey_id(self.id),
                kind=RelationshipEntityKind.TAG,
            ),
            kind=RelationshipKind.DEPENDENCY,
        )

        await self._container[GuidelineToolAssociationStore].create_association(
            guideline_id=guideline.id,
            tool_id=ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=tool.tool.name),
        )

        return guideline.id

    async def create_canned_response(
        self,
        template: str,
        tags: list[TagId] = [],
        signals: list[str] = [],
    ) -> CannedResponseId:
        """Creates a journey-scoped canned response with the specified template, tags, and signals."""

        self._server._advance_creation_progress()

        canrep = await self._container[CannedResponseStore].create_canned_response(
            value=template,
            tags=[_Tag.for_journey_id(self.id), *tags],
            fields=[],
            signals=signals,
        )

        return canrep.id

    async def prioritize_over(
        self,
        target: Guideline | Journey,
    ) -> Relationship:
        """Creates a priority relationship with another guideline or journey."""
        if isinstance(target, Guideline):
            return await self._create_relationship(
                target=target,
                kind=RelationshipKind.PRIORITY,
                direction="source",
            )
        else:
            return await self._create_relationship(
                target=target,
                kind=RelationshipKind.PRIORITY,
                direction="source",
            )

    async def depend_on(
        self,
        target: Guideline,
    ) -> Relationship:
        """Creates a dependency relationship with a guideline."""
        return await self._create_relationship(
            target=target,
            kind=RelationshipKind.DEPENDENCY,
            direction="source",
        )

    async def _create_relationship(
        self,
        target: Guideline | Journey,
        kind: RelationshipKind,
        direction: Literal["source", "target"],
    ) -> Relationship:
        if direction == "source":
            entity_source = RelationshipEntity(
                id=_Tag.for_journey_id(self.id), kind=RelationshipEntityKind.TAG
            )
            entity_target = (
                RelationshipEntity(id=target.id, kind=RelationshipEntityKind.GUIDELINE)
                if isinstance(target, Guideline)
                else RelationshipEntity(
                    id=_Tag.for_journey_id(target.id), kind=RelationshipEntityKind.TAG
                )
            )
        else:
            entity_source = (
                RelationshipEntity(id=target.id, kind=RelationshipEntityKind.GUIDELINE)
                if isinstance(target, Guideline)
                else RelationshipEntity(
                    id=_Tag.for_journey_id(target.id), kind=RelationshipEntityKind.TAG
                )
            )
            entity_target = RelationshipEntity(
                id=_Tag.for_journey_id(self.id), kind=RelationshipEntityKind.TAG
            )

        relationship = await self._container[RelationshipStore].create_relationship(
            source=entity_source,
            target=entity_target,
            kind=kind,
        )

        return Relationship(
            id=relationship.id,
            kind=relationship.kind,
            source=relationship.source.id,
            target=relationship.target.id,
        )


@dataclass(frozen=True)
class Capability:
    """A capability informs the agent about a specific functionality it can provide."""

    id: CapabilityId
    title: str
    description: str
    signals: Sequence[str]
    tags: Sequence[TagId]


@dataclass(frozen=True)
class Term:
    """A glossary term defines a specific concept in the agent's domain."""

    id: TermId
    name: str
    description: str
    synonyms: Sequence[str]
    tags: Sequence[TagId]


@dataclass(frozen=True)
class Variable:
    """A variable that can hold values for customers or customer groups."""

    id: ContextVariableId
    name: str
    description: str | None
    tool: ToolEntry | None
    freshness_rules: str | None
    tags: Sequence[TagId]
    _server: Server
    _container: Container

    async def set_value_for_customer(self, customer: Customer, value: JSONSerializable) -> None:
        """Sets the value of the variable for a specific customer."""

        await self._container[ContextVariableStore].update_value(
            variable_id=self.id,
            key=customer.id,
            data=value,
        )

    async def set_value_for_tag(self, tag: TagId, value: JSONSerializable) -> None:
        """Sets the value of the variable for a specific tag (e.g., a customer group tag)."""

        await self._container[ContextVariableStore].update_value(
            variable_id=self.id,
            key=f"tag:{tag}",
            data=value,
        )

    async def set_global_value(self, value: JSONSerializable) -> None:
        """Sets the global value of the variable, which is accessible to all customers by default."""

        await self._container[ContextVariableStore].update_value(
            variable_id=self.id,
            key=ContextVariableStore.GLOBAL_KEY,
            data=value,
        )

    async def get_value_for_customer(self, customer: Customer) -> JSONSerializable | None:
        """Retrieves the value of the variable for a specific customer."""

        value = await self._container[ContextVariableStore].read_value(
            variable_id=self.id,
            key=customer.id,
        )

        return value.data if value else None

    async def get_value_for_tag(self, tag: TagId) -> JSONSerializable | None:
        """Retrieves the value of the variable for a specific tag (e.g., a customer group tag)."""
        value = await self._container[ContextVariableStore].read_value(
            variable_id=self.id,
            key=f"tag:{tag}",
        )

        return value.data if value else None

    async def get_global_value(self) -> JSONSerializable | None:
        """Retrieves the global value of the variable, which is accessible to all customers by default."""

        value = await self._container[ContextVariableStore].read_value(
            variable_id=self.id,
            key=ContextVariableStore.GLOBAL_KEY,
        )

        return value.data if value else None


@dataclass(frozen=True)
class Customer:
    """A customer represents an individual or entity interacting with the agent."""

    @staticmethod
    def guest() -> Customer:
        return Customer(
            id=CustomerStore.GUEST_ID,
            name="Guest",
            metadata={},
            tags=[],
        )

    id: CustomerId
    name: str
    metadata: Mapping[str, str]
    tags: Sequence[TagId]


@dataclass(frozen=True)
class RetrieverContext:
    """Context for retriever functions, providing helpful information for data retrieval."""

    server: Server
    container: Container
    logger: Logger
    correlator: ContextualCorrelator
    session: Session
    agent: Agent
    customer: Customer
    variables: Mapping[Variable, JSONSerializable]
    interaction: Interaction


@dataclass(frozen=True)
class RetrieverResult:
    """Result of a retriever function, containing the retrieved data and metadata, as well (optionally) as canned response information."""

    data: JSONSerializable
    metadata: Mapping[str, JSONSerializable] = field(default_factory=dict)
    canned_responses: Sequence[str] = field(default_factory=list)
    canned_response_fields: Mapping[str, Any] = field(default_factory=dict)


class CompositionMode(enum.Enum):
    """Defines the composition mode for the agent, which determines how responses are generated."""

    FLUID = _CompositionMode.CANNED_FLUID
    """Responses are generated fluidly, allowing for dynamic composition of responses."""

    COMPOSITED = _CompositionMode.CANNED_COMPOSITED
    """Responses are generated in such a way as to mimic the style of the provided set of canned responses."""

    STRICT = _CompositionMode.CANNED_STRICT
    """Responses are generated strictly based on the provided canned responses, without fluidity."""


class ExperimentalAgentFeatures:
    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def create_capability(
        self,
        title: str,
        description: str,
        signals: Sequence[str] | None = None,
    ) -> Capability:
        """Creates a capability with the specified title, description, and signals."""

        self._agent._server._advance_creation_progress()

        capability = await self._agent._container[CapabilityStore].create_capability(
            title=title,
            description=description,
            signals=signals,
            tags=[_Tag.for_agent_id(self._agent.id)],
        )

        return Capability(
            id=capability.id,
            title=capability.title,
            description=capability.description,
            signals=capability.signals,
            tags=capability.tags,
        )


@dataclass(frozen=True)
class Agent:
    """An agent represents an entity that can interact with customers, manage journeys, and perform various tasks."""

    _server: Server
    _container: Container

    id: AgentId
    name: str
    description: str | None
    max_engine_iterations: int
    composition_mode: CompositionMode
    tags: Sequence[TagId]
    metadata: Dict[str, Any] | None

    retrievers: Mapping[str, Callable[[RetrieverContext], Awaitable[JSONSerializable]]] = field(
        default_factory=dict
    )

    @property
    def experimental_features(self) -> ExperimentalAgentFeatures:
        """Provides access to experimental features of the agent."""
        return ExperimentalAgentFeatures(self)

    async def create_journey(
        self,
        title: str,
        description: str,
        conditions: list[str | Guideline],
        agent_id: AgentId | None = None,
    ) -> Journey:
        """Creates a new journey with the specified title, description, and conditions."""

        self._server._advance_creation_progress()

        journey = await self._server.create_journey(title, description, conditions, agent_id=self.id)

        await self.attach_journey(journey)

        return Journey(
            id=journey.id,
            title=journey.title,
            description=description,
            conditions=journey.conditions,
            tags=journey.tags,
            states=journey.states,
            transitions=journey.transitions,
            agent_id=self.id,
            _start_state_id=journey._start_state_id,
            _server=self._server,
            _container=self._container,
        )

    async def attach_journey(self, journey: Journey) -> None:
        """Attaches an existing journey to the agent, allowing it to be used in interactions."""

        await self._container[JourneyStore].upsert_tag(
            journey.id,
            _Tag.for_agent_id(self.id),
        )

    async def create_guideline(
        self,
        condition: str,
        action: str | None = None,
        tools: Iterable[ToolEntry] = [],
        metadata: dict[str, JSONSerializable] = {},
        canned_responses: Sequence[CannedResponseId] = [],
    ) -> Guideline:
        """Creates a guideline with the specified condition and action, as well as (optionally) tools to achieve its task."""
        self._server._advance_creation_progress()

        tool_ids = [
            ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=t.tool.name) for t in tools
        ]

        for t in list(tools):
            await self._server._plugin_server.enable_tool(t, id=self.id)
        guideline = await self._container[GuidelineStore].create_guideline(
            condition=condition,
            action=action,
            metadata=metadata,
            tags=[_Tag.for_agent_id(self.id)],
        )

        if canned_responses:
            tag_id = _Tag.for_guideline_id(guideline.id)
            for id in canned_responses:
                await self._container[CannedResponseStore].upsert_tag(
                    canned_response_id=id,
                    tag_id=tag_id,
                )

        self._server._add_guideline_evaluation(
            guideline.id,
            GuidelineContent(condition=condition, action=action),
            tool_ids,
            agent_id=self.id,
        )

        for t in list(tools):
            await self._container[GuidelineToolAssociationStore].create_association(
                guideline_id=guideline.id,
                tool_id=ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=t.tool.name),
            )

        return Guideline(
            id=guideline.id,
            condition=condition,
            action=action,
            tags=guideline.tags,
            metadata=guideline.metadata,
            _server=self._server,
            _container=self._container,
        )

    async def create_observation(
        self,
        condition: str,
        canned_responses: Sequence[CannedResponseId] = [],
    ) -> Guideline:
        """A shorthand for creating an observational guideline with the specified condition."""

        return await self.create_guideline(condition=condition, canned_responses=canned_responses)

    async def attach_tool(
        self,
        tool: ToolEntry,
        condition: str,
    ) -> GuidelineId:
        """Attaches a tool to the agent, to be usable under the specified condition."""

        await self._server._plugin_server.enable_tool(tool, id=self.id)

        guideline = await self._container[GuidelineStore].create_guideline(
            condition=condition,
            action=None,
        )

        self._server._add_guideline_evaluation(
            guideline.id,
            GuidelineContent(condition=condition, action=None),
            [ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=tool.tool.name)],
            agent_id=self.id,  # Pass agent_id for tool attachments
        )

        await self._container[GuidelineToolAssociationStore].create_association(
            guideline_id=guideline.id,
            tool_id=ToolId(service_name=INTEGRATED_TOOL_SERVICE_NAME, tool_name=tool.tool.name),
        )

        return guideline.id

    async def create_canned_response(
        self,
        template: str,
        tags: list[TagId] = [],
        signals: list[str] = [],
    ) -> CannedResponseId:
        """Creates a canned response with the specified template, tags, and signals."""

        self._server._advance_creation_progress()

        canrep = await self._container[CannedResponseStore].create_canned_response(
            value=template,
            tags=[_Tag.for_agent_id(self.id), *tags],
            fields=[],
            signals=signals,
        )

        return canrep.id

    async def create_term(
        self,
        name: str,
        description: str,
        synonyms: Sequence[str] = [],
    ) -> Term:
        """Creates a glossary term with the specified name, description, and synonyms."""

        self._server._advance_creation_progress()

        term = await self._container[GlossaryStore].create_term(
            name=name,
            description=description,
            synonyms=synonyms,
            tags=[_Tag.for_agent_id(self.id)],
        )

        return Term(
            id=term.id,
            name=term.name,
            description=term.description,
            synonyms=term.synonyms,
            tags=term.tags,
        )

    async def create_variable(
        self,
        name: str,
        description: str | None = None,
        tool: ToolEntry | None = None,
        freshness_rules: str | None = None,
    ) -> Variable:
        """Creates a variable with the specified name, description, tool, and freshness rules."""

        self._server._advance_creation_progress()

        if tool:
            await self._server._plugin_server.enable_tool(tool)

        variable = await self._container[ContextVariableStore].create_variable(
            name=name,
            description=description,
            tool_id=ToolId(INTEGRATED_TOOL_SERVICE_NAME, tool.tool.name) if tool else None,
            freshness_rules=freshness_rules,
            tags=[_Tag.for_agent_id(self.id)],
        )

        return Variable(
            id=variable.id,
            name=variable.name,
            description=variable.description,
            tool=tool,
            freshness_rules=variable.freshness_rules,
            tags=variable.tags,
            _server=self._server,
            _container=self._container,
        )

    async def list_variables(self) -> Sequence[Variable]:
        """Lists all variables associated with the agent."""

        variables = await self._container[ContextVariableStore].list_variables(
            tags=[_Tag.for_agent_id(self.id)]
        )

        return [
            Variable(
                id=variable.id,
                name=variable.name,
                description=variable.description,
                tool=self._server._plugin_server.tools[variable.tool_id.tool_name]
                if variable.tool_id
                else None,
                freshness_rules=variable.freshness_rules,
                tags=variable.tags,
                _server=self._server,
                _container=self._container,
            )
            for variable in variables
        ]

    async def find_variable(
        self,
        *,
        id: str | None = None,
        name: str | None = None,
    ) -> Variable | None:
        """Finds a variable by its ID or name."""

        if not id and not name:
            raise SDKError("Either id or name must be provided to find a variable.")

        variable: ContextVariable | None = None

        if id:
            try:
                variable = await self._container[ContextVariableStore].read_variable(
                    ContextVariableId(id)
                )
            except ItemNotFoundError:
                return None
        else:
            variable = next(
                (
                    v
                    for v in await self._container[ContextVariableStore].list_variables(
                        tags=[_Tag.for_agent_id(self.id)]
                    )
                    if v.name == name
                ),
                None,
            )

            if not variable:
                return None

        return Variable(
            id=variable.id,
            name=variable.name,
            description=variable.description,
            tool=self._server._plugin_server.tools[variable.tool_id.tool_name]
            if variable.tool_id
            else None,
            freshness_rules=variable.freshness_rules,
            tags=variable.tags,
            _server=self._server,
            _container=self._container,
        )

    async def get_variable(self, id: ContextVariableId | str) -> Variable:
        """Retrieves a variable by its ID, raising an error if not found."""

        if variable := await self.find_variable(id=id):
            return variable
        raise SDKError(f"Variable with id {id} not found.")

    async def attach_retriever(
        self,
        retriever: Callable[[RetrieverContext], Awaitable[JSONSerializable | RetrieverResult]],
        id: str | None = None,
    ) -> None:
        """Attaches a retriever function to the agent, allowing it to be used in interactions."""

        if not id:
            id = f"retriever-{len(self.retrievers) + 1}"

        cast(
            dict[str, Callable[[RetrieverContext], Awaitable[JSONSerializable | RetrieverResult]]],
            self.retrievers,
        )[id] = retriever

        self._server._retrievers[self.id][id] = retriever

        # Immediately setup hooks for this retriever
        await self._server._setup_retriever_hooks(self.id, id, retriever)

class ToolContextAccessor:
    """A context accessor for tools, providing access to the server and other relevant data."""

    def __init__(self, context: ToolContext) -> None:
        self.context = context

    @property
    def server(self) -> Server:
        """Returns the server associated with the tool context."""
        return cast(Server, self.context.plugin_data["server"])

    @property
    def logger(self) -> Logger:
        """Returns the logger associated with the context."""
        return self.server._container[Logger]


def _die(message: str, exc: Exception | None) -> NoReturn:
    if exc:
        import traceback

        traceback.print_exception(exc)
    rich.print(Text(message, style="bold red"), file=sys.stderr)
    sys.exit(1)


class Server:
    """The main server class that manages the agent, journeys, tools, and other components.

    This class is responsible for initializing the server, managing the lifecycle of the agent, and providing access to various services and components.

    Args:
        port: The port on which the server will run.
        tool_service_port: The port for the integrated tool service.
        nlp_service: A factory function to create an NLP service instance. See `NLPServiceFactories` for available options.
        session_store: The session store to use for managing sessions.
        customer_store: The customer store to use for managing customers.
        log_level: The logging level for the server.
        modules: A list of module names to load for the server.
        migrate: Whether to allow database migrations on startup (if needed).
        configure_hooks: A callable to configure engine hooks.
        configure_container: A callable to configure the dependency injection container.
        initialize_container: A callable to perform additional initialization after the container is set up.
    """

    def __init__(
        self,
        port: int = 8800,
        tool_service_port: int = 8818,
        nlp_service: Callable[[Container], NLPService] = NLPServices.openai,
        session_store: Literal["transient", "local"] | str | SessionStore = "transient",
        customer_store: Literal["transient", "local"] | str | CustomerStore = "transient",
        log_level: LogLevel = LogLevel.INFO,
        modules: list[str] = [],
        migrate: bool = False,
        configure_hooks: Callable[[EngineHooks], Awaitable[EngineHooks]] | None = None,
        configure_container: Callable[[Container], Awaitable[Container]] | None = None,
        initialize_container: Callable[[Container], Awaitable[None]] | None = None,
    ) -> None:
        self.port = port
        self.tool_service_port = tool_service_port
        self.log_level = log_level
        self.modules = modules

        self._migrate = migrate
        self._nlp_service_func = nlp_service
        self._evaluator: _CachedEvaluator
        self._session_store = session_store
        self._customer_store = customer_store
        self._configure_hooks = configure_hooks
        self._configure_container = configure_container
        self._initialize = initialize_container
        self._retrievers: dict[
            AgentId,
            dict[str, Callable[[RetrieverContext], Awaitable[JSONSerializable | RetrieverResult]]],
        ] = defaultdict(dict)
        self._retriever_hooks: dict[
            AgentId,
            list[tuple[EngineHook, EngineHook]],  # (on_acknowledged, on_generating_messages)
        ] = defaultdict(list)
        self._exit_stack = AsyncExitStack()

        self._plugin_server: PluginServer
        self._container: Container

        # 按 agent_id 分组的评估队列，实现隔离
        self._guideline_evaluations: dict[
            AgentId,
            dict[
                GuidelineId,
                tuple[Any, Callable[..., Coroutine[Any, Any, _CachedEvaluator.GuidelineEvaluation]]],
            ],
        ] = defaultdict(dict)
        self._node_evaluations: dict[
            AgentId,
            dict[
                JourneyStateId,
                tuple[Any, Callable[..., Coroutine[Any, Any, _CachedEvaluator.GuidelineEvaluation]]],
            ],
        ] = defaultdict(dict)
        self._journey_evaluations: dict[
            AgentId,
            dict[
                JourneyId,
                tuple[Any, Callable[..., Coroutine[Any, Any, _CachedEvaluator.JourneyEvaluation]]],
            ],
        ] = defaultdict(dict)

        self._creation_progress: Progress | None = Progress(
            TextColumn("{task.description}"),
            BarColumn(pulse_style="bold green"),
            TimeElapsedColumn(),
        )
        self._creation_progress_k = 0
        self._creation_progress_task_id: TaskID

    def _advance_creation_progress(self) -> None:
        if self._creation_progress is None:
            return

        self._creation_progress_k += 1

        self._creation_progress.update(
            self._creation_progress_task_id,
            description=f"Caching entity embeddings ({self._creation_progress_k})",
        )

    async def __aenter__(self) -> Server:
        try:
            self._startup_context_manager = start_parlant(self._get_startup_params())
            self._container = await self._startup_context_manager.__aenter__()

            assert self._creation_progress
            self._creation_progress = self._creation_progress.__enter__()
            self._creation_progress_task_id = self._creation_progress.add_task(
                "Caching entity embeddings", total=None
            )

            return self
        except SDKError as e:
            _die(str(e), e)
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        assert self._creation_progress
        self._creation_progress.__exit__(None, None, None)
        self._creation_progress = None

        await self._startup_context_manager.__aexit__(exc_type, exc_value, tb)
        await self._exit_stack.aclose()
        return False

    def _add_guideline_evaluation(
        self,
        guideline_id: GuidelineId,
        guideline_content: GuidelineContent,
        tool_ids: Sequence[ToolId],
        agent_id: AgentId | None = None,
    ) -> None:
        """添加 guideline 评估任务到队列，按 agent_id 隔离"""
        # 使用空字符串作为全局队列的 key（兼容没有 agent_id 的场景）
        key = agent_id if agent_id else AgentId("")
        # 存储 agent_id 到参数中，以便评估时使用
        self._guideline_evaluations[key][guideline_id] = (
            (guideline_id, guideline_content, tool_ids, agent_id),
            lambda gid, gc, tids, aid: self._evaluator.evaluate_guideline(gid, gc, tids, aid),
        )

    def _add_state_evaluation(
        self,
        state_id: JourneyStateId,
        guideline_content: GuidelineContent,
        tools: Sequence[ToolId],
        agent_id: AgentId | None = None,
    ) -> None:
        """添加 state 评估任务到队列，按 agent_id 隔离"""
        key = agent_id if agent_id else AgentId("")
        # 传递 agent_id 给 evaluate_state，用于工具查找
        self._node_evaluations[key][state_id] = (
            (state_id, guideline_content, tools, agent_id),
            lambda sid, gc, tids, aid: self._evaluator.evaluate_state(sid, gc, tids, aid),
        )

    def _add_journey_evaluation(
        self,
        journey: Journey,
        agent_id: AgentId | None = None,
    ) -> None:
        """添加 journey 评估任务到队列，按 agent_id 隔离"""
        key = agent_id if agent_id else AgentId("")
        # 存储 agent_id 到参数中，以便评估时使用
        self._journey_evaluations[key][journey.id] = (
            (journey, agent_id),
            lambda j, aid: self._evaluator.evaluate_journey(j, aid),
        )

    async def _render_guideline(self, guideline_id: GuidelineId) -> str:
        guideline = await self._container[GuidelineStore].read_guideline(guideline_id)

        return f"When {guideline.content.condition}" + (
            f", then {guideline.content.action}" if guideline.content.action else ""
        )

    async def _render_state(self, state_id: JourneyStateId) -> str:
        state = await self._container[JourneyStore].read_node(state_id)

        return f"State: {state.action}"

    async def _render_journey(self, journey_id: JourneyId) -> str:
        journey = await self._container[JourneyStore].read_journey(journey_id)

        return f"Journey: {journey.title}"

    async def _process_evaluations(
        self, 
        agent_id: AgentId | None = None,
        session_id: SessionId | None = None,
    ) -> None:
        """
        处理评估队列，支持按 agent_id 隔离处理
        
        Args:
            agent_id: 如果指定，只处理该 agent 的评估；否则处理所有评估
            session_id: 如果指定，evaluation tokens 将写入该 session 的 inspection
        """
        _render_functions: dict[
            Literal["guideline", "node", "journey"],
            Callable[[GuidelineId | JourneyStateId | JourneyId], Awaitable[str]],
        ] = {
            "guideline": self._render_guideline,  # type: ignore
            "node": self._render_state,  # type: ignore
            "journey": self._render_journey,  # type: ignore
        }

        def create_evaluation_task(
            evaluation: Coroutine[
                Any, Any, _CachedEvaluator.GuidelineEvaluation | _CachedEvaluator.JourneyEvaluation
            ],
            entity_type: Literal["guideline", "node", "journey"],
            entity_id: GuidelineId | JourneyStateId | JourneyId,
        ) -> asyncio.Task[
            tuple[
                Literal["guideline", "node", "journey"],
                GuidelineId | JourneyStateId | JourneyId,
                _CachedEvaluator.GuidelineEvaluation | _CachedEvaluator.JourneyEvaluation,
            ]
        ]:
            async def task_wrapper() -> tuple[
                Literal["guideline", "node", "journey"],
                GuidelineId | JourneyStateId | JourneyId,
                _CachedEvaluator.GuidelineEvaluation | _CachedEvaluator.JourneyEvaluation,
            ]:
                result = await evaluation
                return (entity_type, entity_id, result)

            return asyncio.create_task(task_wrapper(), name=f"{entity_type}_evaluation_{entity_id}")

        tasks: list[
            asyncio.Task[
                tuple[
                    Literal["guideline", "node", "journey"],
                    GuidelineId | JourneyStateId | JourneyId,
                    _CachedEvaluator.GuidelineEvaluation | _CachedEvaluator.JourneyEvaluation,
                ]
            ]
        ] = []

        # 确定要处理的 agent_ids
        if agent_id is not None:
            # 只处理指定 agent 的评估
            agent_keys = [agent_id]
        else:
            # 处理所有 agent 的评估
            agent_keys = list(
                set(self._guideline_evaluations.keys())
                | set(self._node_evaluations.keys())
                | set(self._journey_evaluations.keys())
            )

        # 收集指定 agent(s) 的所有评估任务
        for key in agent_keys:
            for guideline_id, (args, func) in self._guideline_evaluations.get(key, {}).items():
                tasks.append((create_evaluation_task(func(*args), "guideline", guideline_id)))

            for node_id, (args, func) in self._node_evaluations.get(key, {}).items():
                tasks.append((create_evaluation_task(func(*args), "node", node_id)))

            for journey_id, (args, journey_func) in self._journey_evaluations.get(key, {}).items():
                tasks.append((create_evaluation_task(journey_func(*args), "journey", journey_id)))

        if not tasks:
            return

        evaluation_results = await async_utils.safe_gather(*tasks)

        # 收集所有评估的 GenerationInfo（本次agent的所有评估）
        # 注意：all_evaluation_generations 是本地变量，每次调用独立，无并发污染风险
        all_evaluation_generations: list[GenerationInfo] = []
        
        # 单次遍历完成数据收集和处理
        for entity_type, entity_id, result in evaluation_results:
            # 1. 收集 GenerationInfo（如果有）
            if hasattr(result, 'evaluation_id') and result.evaluation_id:
                generations = self._container[BehavioralChangeEvaluator].get_evaluation_generations(
                    result.evaluation_id
                )
                all_evaluation_generations.extend(generations)
                # 清除已使用的 GenerationInfo，防止内存泄漏
                self._container[BehavioralChangeEvaluator].clear_evaluation_generations(
                    result.evaluation_id
                )
            
            # 2. 处理评估结果并更新元数据
            if entity_type == "guideline":
                guideline = await self._container[GuidelineStore].read_guideline(
                    guideline_id=cast(GuidelineId, entity_id)
                )

                properties = cast(_CachedEvaluator.GuidelineEvaluation, result).properties

                properties_to_add = {
                    k: v for k, v in properties.items() if k not in guideline.metadata
                }

                for key, value in properties_to_add.items():
                    await self._container[GuidelineStore].set_metadata(
                        guideline_id=cast(GuidelineId, entity_id),
                        key=key,
                        value=value,
                    )

            elif entity_type == "node":
                node = await self._container[JourneyStore].read_node(
                    node_id=cast(JourneyStateId, entity_id)
                )
                properties = cast(_CachedEvaluator.GuidelineEvaluation, result).properties

                properties_to_add = {k: v for k, v in properties.items() if k not in node.metadata}

                for key, value in properties_to_add.items():
                    await self._container[JourneyStore].set_node_metadata(
                        node_id=cast(JourneyStateId, entity_id),
                        key=key,
                        value=value,
                    )

            elif entity_type == "journey":
                for node_id, properties in cast(
                    _CachedEvaluator.JourneyEvaluation, result
                ).node_properties.items():
                    node = await self._container[JourneyStore].read_node(node_id)
                    properties_to_add = {
                        k: v
                        for k, v in properties.items()
                        if k not in node.metadata or node.metadata[k] is None
                    }

                    for key, value in properties_to_add.items():
                        await self._container[JourneyStore].set_node_metadata(
                            node_id=node_id,
                            key=key,
                            value=value,
                        )

        # 记录评估 tokens 统计
        if all_evaluation_generations:
            total_input_tokens = sum(g.usage.input_tokens for g in all_evaluation_generations)
            total_output_tokens = sum(g.usage.output_tokens for g in all_evaluation_generations)
            total_tokens = sum(g.usage.total_tokens or 0 for g in all_evaluation_generations)
            
            self._container[Logger].info(
                f"Processed {len(evaluation_results)} evaluations for agent(s): {agent_keys}. "
                f"Total evaluation tokens: input={total_input_tokens}, output={total_output_tokens}, "
                f"total={total_tokens}, generations={len(all_evaluation_generations)}"
            )
            
            # 如果提供了 session_id，直接写入该 session 的 inspection
            if session_id:
                try:
                    from parlant.core.contextual_correlator import ContextualCorrelator
                    
                    # 从上下文获取当前请求的 correlation_id
                    correlation_id = self._container[ContextualCorrelator].correlation_id
                    
                    # 记录详细的追溯信息
                    self._container[Logger].debug(
                        f"📊 Evaluation details for agent {agent_keys}:\n"
                        + "\n".join([
                            f"  [{i+1}] {g.schema_name} - {g.model}: "
                            f"input={g.usage.input_tokens}, output={g.usage.output_tokens}, "
                            f"total={g.usage.total_tokens or 0}, duration={g.duration:.3f}s"
                            for i, g in enumerate(all_evaluation_generations)
                        ])
                    )
                    
                    # 将 evaluation_generations 写入 session 的 inspection
                    # 注意：每个 generation 包含完整的追溯信息（model, schema, duration, tokens）
                    await self._container[SessionStore].create_inspection(
                        session_id=session_id,
                        correlation_id=correlation_id,
                        message_generations=[],
                        preparation_iterations=[],
                        response_analysis_generations=all_evaluation_generations,
                    )
                    self._container[Logger].info(
                        f"✅ Evaluation inspection saved to session {session_id}: "
                        f"{len(all_evaluation_generations)} generations, {total_tokens} tokens, "
                        f"correlation_id={correlation_id}"
                    )
                    
                except Exception as e:
                    self._container[Logger].warning(
                        f"⚠️ Failed to create evaluation inspection for session {session_id}: {e}"
                    )
            else:
                # 没有 session_id，只记录日志
                self._container[Logger].info(
                    f"⚠️ No session_id provided, evaluation tokens not persisted to inspection"
                )
        else:
            self._container[Logger].info(
                f"Processed {len(evaluation_results)} evaluations for agent(s): {agent_keys} "
                "(no generation info collected, possibly from cache)"
            )

        # 清空已处理的评估队列
        for key in agent_keys:
            if key in self._guideline_evaluations:
                del self._guideline_evaluations[key]
            if key in self._node_evaluations:
                del self._node_evaluations[key]
            if key in self._journey_evaluations:
                del self._journey_evaluations[key]

        
    async def _setup_retriever_hooks(
        self,
        agent_id: AgentId,
        retriever_id: str,
        retriever: Callable[[RetrieverContext], Awaitable[JSONSerializable | RetrieverResult]],
    ) -> None:
        """Setup hooks for a single retriever immediately when it's attached"""
        c = self._container
        
        async def setup_retriever(
            c: Container,
            agent_id: AgentId,
            retriever_id: str,
            retriever: Callable[[RetrieverContext], Awaitable[JSONSerializable | RetrieverResult]],
        ) -> None:
            tasks_for_this_retriever: dict[
                str,
                tuple[Timeout, asyncio.Task[JSONSerializable | RetrieverResult]],
            ] = {}

            async def on_message_acknowledged(
                ctx: LoadedContext,
                payload: Any,
                exc: Optional[Exception],
            ) -> EngineHookResult:
                # First do some garbage collection if needed.
                # This might be needed if tasks were not awaited
                # because of exceptions during engine processing.
                for correlation_id in list(tasks_for_this_retriever.keys()):
                    if tasks_for_this_retriever[correlation_id][0].expired():
                        # Very, very little change that this task is still meant to be running,
                        # or that anyone is still waiting for it. It's 99.999% garbage.
                        try:
                            tasks_for_this_retriever[correlation_id][1].add_done_callback(
                                default_done_callback()
                            )
                            tasks_for_this_retriever[correlation_id][1].cancel()
                            del tasks_for_this_retriever[correlation_id]
                        except BaseException:
                            # If anything went unexpectedly here, whatever. Carry on.
                            pass

                # 🔧 FIX: Reload interaction to ensure it includes the newly created message
                # This is critical for retriever to get the latest customer message
                from parlant.core.engines.alpha.loaded_context import Interaction
                
                c[Logger].info(
                    f"🔍[KB] Hook triggered: agent={agent_id}, retriever={retriever_id}"
                )
                
                entity_queries = c[EntityQueries]
                latest_history = await entity_queries.find_events(ctx.session.id)
                ctx.interaction = Interaction(history=latest_history)
                
                c[Logger].debug(
                    f"🔍[KB] Interaction reloaded: events={len(latest_history)}, last_msg={ctx.interaction.last_customer_message.content if ctx.interaction.last_customer_message else 'None'}"
                )

                agent = await self.get_agent(id=ctx.agent.id)
                customer = await self.get_customer(id=ctx.customer.id)

                coroutine = retriever(
                    RetrieverContext(
                        server=self,
                        container=self._container,
                        logger=self._container[Logger],
                        correlator=self._container[ContextualCorrelator],
                        session=ctx.session,
                        agent=agent,
                        customer=customer,
                        variables={
                            await agent.get_variable(id=var.id): val.data
                            for var, val in ctx.state.context_variables
                        },
                        interaction=ctx.interaction,
                    )
                )

                c[Logger].info(
                    f"🔍[KB] Starting: agent={agent_id}, retriever={retriever_id}, session={ctx.session.id}"
                )

                tasks_for_this_retriever[ctx.correlator.correlation_id] = (
                    Timeout(600),  # Expiration timeout for garbage collection purposes
                    asyncio.create_task(
                        cast(Coroutine[Any, Any, JSONSerializable | RetrieverResult], coroutine),
                        name=f"Retriever {retriever_id} for agent {agent_id}",
                    ),
                )

                return EngineHookResult.CALL_NEXT

            async def on_generating_messages(
                ctx: LoadedContext,
                payload: Any,
                exc: Optional[Exception],
            ) -> EngineHookResult:
                if timeout_and_task := tasks_for_this_retriever.pop(
                    ctx.correlator.correlation_id, None
                ):
                    _, task = timeout_and_task
                    task_result = await task

                    if isinstance(task_result, RetrieverResult):
                        retriever_result = task_result
                    else:
                        retriever_result = RetrieverResult(
                            data=task_result,
                            metadata={},
                            canned_responses=[],
                            canned_response_fields={},
                        )

                    if not (
                        retriever_result.data
                        or retriever_result.metadata
                        or retriever_result.canned_responses
                        or retriever_result.canned_response_fields
                    ):
                        # No need to emit tool event if nothing was retrieved.
                        return EngineHookResult.CALL_NEXT

                    ctx.state.tool_events.append(
                        await ctx.response_event_emitter.emit_tool_event(
                            ctx.correlator.correlation_id,
                            ToolEventData(
                                tool_calls=[
                                    _SessionToolCall(
                                        tool_id=ToolId(
                                            service_name=INTEGRATED_TOOL_SERVICE_NAME,
                                            tool_name=retriever_id,
                                        ).to_string(),
                                        arguments={},
                                        result=_SessionToolResult(
                                            data=retriever_result.data,
                                            metadata=retriever_result.metadata,
                                            control={"lifespan": "response"},
                                            canned_responses=[
                                                u for u in retriever_result.canned_responses
                                            ],
                                            canned_response_fields=retriever_result.canned_response_fields,
                                        ),
                                    )
                                ]
                            ),
                        )
                    )

                return EngineHookResult.CALL_NEXT

            c[EngineHooks].on_acknowledged.append(on_message_acknowledged)
            c[EngineHooks].on_generating_messages.append(on_generating_messages)
            
            # Track hooks for cleanup
            self._retriever_hooks[agent_id].append(
                (on_message_acknowledged, on_generating_messages)
            )

        await setup_retriever(c, agent_id, retriever_id, retriever)
        
    async def _setup_retrievers(self) -> None:
        """Setup hooks for all existing retrievers (called at server startup)"""
        for agent in self._retrievers:
            for retriever_id, retriever in self._retrievers[agent].items():
                await self._setup_retriever_hooks(agent, retriever_id, retriever)
    
    async def cleanup_agent_retrievers(self, agent_id: AgentId) -> None:
        """Clean up retrievers and hooks for the specified agent.
        
        This method should be called when an agent is deleted to prevent memory leaks.
        It removes:
        1. Retriever references from self._retrievers
        2. Hook functions from EngineHooks lists
        
        Args:
            agent_id: The ID of the agent to clean up
        """
        try:
            # 1. Clean up retrievers dictionary
            if agent_id in self._retrievers:
                retriever_count = len(self._retrievers[agent_id])
                del self._retrievers[agent_id]
                self._container[Logger].info(
                    f"🔍[KB] Cleaned up: agent={agent_id}, count={retriever_count}"
                )
            
            # 2. Clean up hooks from EngineHooks lists
            if agent_id in self._retriever_hooks:
                hooks_to_remove = self._retriever_hooks[agent_id]
                engine_hooks = self._container[EngineHooks]
                
                for on_ack_hook, on_gen_hook in hooks_to_remove:
                    try:
                        engine_hooks.on_acknowledged.remove(on_ack_hook)
                        engine_hooks.on_generating_messages.remove(on_gen_hook)
                    except ValueError:
                        # Hook already removed, ignore
                        pass
                
                hook_pair_count = len(hooks_to_remove)
                del self._retriever_hooks[agent_id]
                
                self._container[Logger].info(
                    f"🧹 Cleaned up {hook_pair_count * 2} hook(s) for agent {agent_id}, "
                    f"remaining on_acknowledged hooks: {len(engine_hooks.on_acknowledged)}"
                )
        
        except Exception as e:
            self._container[Logger].error(
                f"❌ Failed to cleanup retrievers for agent {agent_id}: {e}"
            )



    async def create_tag(self, name: str) -> Tag:
        self._advance_creation_progress()

        tag = await self._container[TagStore].create_tag(name=name)

        return Tag(
            id=tag.id,
            name=tag.name,
        )

    async def create_agent(
        self,
        name: str,
        description: str,
        composition_mode: CompositionMode = CompositionMode.FLUID,
        max_engine_iterations: int | None = None,
        tags: Sequence[TagId] = [],
        metadata: Dict[str, Any] | None = None,
        id: AgentId | None = None,
    ) -> Agent:
        """Creates a new agent with the specified name, description, and composition mode."""

        self._advance_creation_progress()

        agent = await self._container[AgentStore].create_agent(
            id=id,
            name=name,
            description=description,
            max_engine_iterations=max_engine_iterations or 3,
            composition_mode=composition_mode.value,
            tags=tags,
            metadata=metadata,
        )

        return Agent(
            id=agent.id,
            name=agent.name,
            description=agent.description,
            max_engine_iterations=agent.max_engine_iterations,
            composition_mode=CompositionMode(agent.composition_mode),
            tags=tags,
            metadata=agent.metadata,
            _server=self,
            _container=self._container,
        )

    async def list_agents(self) -> Sequence[Agent]:
        """Lists all agents."""

        agents = await self._container[AgentStore].list_agents()

        return [
            Agent(
                id=a.id,
                name=a.name,
                description=a.description,
                max_engine_iterations=a.max_engine_iterations,
                composition_mode=CompositionMode(a.composition_mode),
                tags=a.tags,
                metadata=a.metadata,
                _server=self,
                _container=self._container,
            )
            for a in agents
        ]

    async def find_agent(self, *, id: str) -> Agent | None:
        """Finds an agent by its ID."""

        try:
            agent = await self._container[AgentStore].read_agent(AgentId(id))

            return Agent(
                id=agent.id,
                name=agent.name,
                description=agent.description,
                max_engine_iterations=agent.max_engine_iterations,
                composition_mode=CompositionMode(agent.composition_mode),
                tags=agent.tags,
                metadata=agent.metadata,
                _server=self,
                _container=self._container,
            )
        except ItemNotFoundError:
            return None

    async def get_agent(self, *, id: str) -> Agent:
        """Retrieves an agent by its ID, raising an error if not found."""

        if agent := await self.find_agent(id=id):
            return agent
        raise SDKError(f"Agent with id {id} not found.")

    async def create_customer(
        self,
        name: str,
        metadata: Mapping[str, str] = {},
        tags: Sequence[TagId] = [],
    ) -> Customer:
        """Creates a new customer with the specified name and metadata."""

        self._advance_creation_progress()

        customer = await self._container[CustomerStore].create_customer(
            name=name,
            extra=metadata,
            tags=tags,
        )

        return Customer(
            id=customer.id,
            name=customer.name,
            metadata=customer.extra,
            tags=customer.tags,
        )

    async def list_customers(self) -> Sequence[Customer]:
        """Lists all customers."""

        customers = await self._container[CustomerStore].list_customers()

        return [
            Customer(
                id=c.id,
                name=c.name,
                metadata=c.extra,
                tags=c.tags,
            )
            for c in customers
        ]

    async def find_customer(
        self,
        *,
        id: str | None = None,
        name: str | None = None,
    ) -> Customer | None:
        """Finds a customer by its ID or name."""

        if not id and not name:
            raise SDKError("Either id or name must be provided to find a customer.")

        customer: _Customer | None = None

        if id:
            try:
                customer = await self._container[CustomerStore].read_customer(CustomerId(id))
            except ItemNotFoundError:
                return None

            return Customer(
                id=customer.id,
                name=customer.name,
                metadata=customer.extra,
                tags=customer.tags,
            )

        if name:
            customers = await self._container[CustomerStore].list_customers()

            if customer := next((c for c in customers if c.name == name), None):
                return Customer(
                    id=customer.id,
                    name=customer.name,
                    metadata=customer.extra,
                    tags=customer.tags,
                )

        return None

    async def get_customer(self, *, id: CustomerId) -> Customer:
        """Retrieves a customer by its ID, raising an error if not found."""

        if customer := await self.find_customer(id=id):
            return customer
        raise SDKError(f"Customer with id {id} not found.")

    async def create_journey(
        self,
        title: str,
        description: str,
        conditions: list[str | Guideline],
        tags: Sequence[TagId] = [],
        agent_id: AgentId | None = None,
    ) -> Journey:
        """Creates a new journey with the specified title, description, and conditions."""

        self._advance_creation_progress()

        condition_guidelines = [c for c in conditions if isinstance(c, Guideline)]

        str_conditions = [c for c in conditions if isinstance(c, str)]

        for str_condition in str_conditions:
            guideline = await self._container[GuidelineStore].create_guideline(
                condition=str_condition,
            )

            self._add_guideline_evaluation(
                guideline.id,
                GuidelineContent(condition=str_condition, action=None),
                tool_ids=[],
                agent_id=agent_id,
            )

            condition_guidelines.append(
                Guideline(
                    id=guideline.id,
                    condition=guideline.content.condition,
                    action=guideline.content.action,
                    tags=guideline.tags,
                    metadata=guideline.metadata,
                    _server=self,
                    _container=self._container,
                )
            )

        stored_journey = await self._container[JourneyStore].create_journey(
            title=title,
            description=description,
            conditions=[c.id for c in condition_guidelines],
            tags=[],
        )

        journey = Journey(
            id=stored_journey.id,
            title=title,
            description=description,
            conditions=condition_guidelines,
            states=[],
            transitions=[],
            tags=tags,
            agent_id=agent_id,
            _start_state_id=stored_journey.root_id,
            _server=self,
            _container=self._container,
        )

        start_state = await self._container[JourneyStore].read_node(node_id=stored_journey.root_id)

        cast(list[JourneyState], journey.states).append(
            InitialJourneyState(
                id=start_state.id,
                action=start_state.action,
                tools=[],
                metadata=start_state.metadata,
                _journey=journey,
            )
        )

        for c in condition_guidelines:
            await self._container[GuidelineStore].upsert_tag(
                guideline_id=c.id,
                tag_id=_Tag.for_journey_id(journey_id=journey.id),
            )

        self._add_journey_evaluation(journey, agent_id=agent_id)

        return journey

    async def create_canned_response(
        self,
        template: str,
        tags: list[TagId] = [],
        signals: list[str] = [],
    ) -> CannedResponseId:
        """Creates a canned response with the specified template, tags, and signals."""

        self._advance_creation_progress()

        canrep = await self._container[CannedResponseStore].create_canned_response(
            value=template,
            tags=tags,
            fields=[],
            signals=signals,
        )

        return canrep.id

    def _get_startup_params(self) -> StartupParameters:
        async def override_stores_with_transient_versions(c: Callable[[], Container]) -> None:
            c()[NLPService] = self._nlp_service_func(c())

            for interface, implementation in [
                (AgentStore, AgentDocumentStore),
                (ContextVariableStore, ContextVariableDocumentStore),
                (TagStore, TagDocumentStore),
                (GuidelineStore, GuidelineDocumentStore),
                (GuidelineToolAssociationStore, GuidelineToolAssociationDocumentStore),
                (RelationshipStore, RelationshipDocumentStore),
            ]:
                c()[interface] = await self._exit_stack.enter_async_context(
                    implementation(c()[IdGenerator], TransientDocumentDatabase())  #  type: ignore
                )

            c()[EvaluationStore] = await self._exit_stack.enter_async_context(
                EvaluationDocumentStore(TransientDocumentDatabase())
            )

            def make_transient_db() -> Awaitable[DocumentDatabase]:
                async def shim() -> DocumentDatabase:
                    return TransientDocumentDatabase()

                return shim()

            def make_json_db(file_path: Path) -> Awaitable[DocumentDatabase]:
                return self._exit_stack.enter_async_context(
                    JSONFileDocumentDatabase(
                        c()[Logger],
                        file_path,
                    ),
                )

            mongo_client: object | None = None

            async def make_mongo_db(url: str, name: str) -> DocumentDatabase:
                nonlocal mongo_client

                if importlib.util.find_spec("pymongo") is None:
                    raise SDKError(
                        "MongoDB requires an additional package to be installed. "
                        "Please install parlant[mongo] to use MongoDB."
                    )

                from pymongo import AsyncMongoClient
                from parlant.adapters.db.mongo_db import MongoDocumentDatabase

                if mongo_client is None:
                    # 配置MongoDB超时参数（防止无限等待）
                    mongo_client = await self._exit_stack.enter_async_context(
                        AsyncMongoClient[Any](
                            url,
                            serverSelectionTimeoutMS=30000,  # 30秒服务器选择超时
                            connectTimeoutMS=20000,          # 20秒连接超时
                            socketTimeoutMS=30000,           # 30秒socket超时（关键！）
                            maxPoolSize=100,                 # 连接池大小
                            minPoolSize=10,
                            maxIdleTimeMS=60000,             # 60秒空闲连接回收
                            heartbeatFrequencyMS=10000,      # 10秒心跳检查
                        )
                    )

                db = await self._exit_stack.enter_async_context(
                    MongoDocumentDatabase(
                        mongo_client=cast(AsyncMongoClient[Any], mongo_client),
                        database_name=f"omni_agent_{name}",
                        logger=c()[Logger],
                    )
                )

                return db

            async def make_persistable_store(t: type[T], spec: str, name: str, **kwargs: Any) -> T:
                store: T

                if spec in ["transient", "local"]:
                    store = await self._exit_stack.enter_async_context(
                        t(
                            database=await cast(
                                dict[str, Callable[[], Awaitable[DocumentDatabase]]],
                                {
                                    "transient": make_transient_db,
                                    "local": lambda: make_json_db(
                                        PARLANT_HOME_DIR / f"{name}.json"
                                    ),
                                },
                            )[spec](),
                            allow_migration=self._migrate,
                            **kwargs,
                        )  # type: ignore
                    )

                    return store
                elif spec.startswith("mongodb://") or spec.startswith("mongodb+srv://"):
                    store = await self._exit_stack.enter_async_context(
                        t(
                            database=await make_mongo_db(spec, name),
                            allow_migration=self._migrate,
                            **kwargs,
                        )  # type: ignore
                    )

                    return store
                else:
                    raise SDKError(
                        f"Invalid session store type: {self._session_store}. "
                        "Expected 'transient', 'local', or a MongoDB connection string."
                    )

            if isinstance(self._session_store, SessionStore):
                c()[SessionStore] = self._session_store
            else:
                c()[SessionStore] = await make_persistable_store(
                    SessionDocumentStore, self._session_store, "sessions"
                )

            if isinstance(self._customer_store, CustomerStore):
                c()[CustomerStore] = self._customer_store
            else:
                c()[CustomerStore] = await make_persistable_store(
                    CustomerDocumentStore,
                    self._customer_store,
                    "customers",
                    id_generator=c()[IdGenerator],
                )

            c()[ServiceRegistry] = await self._exit_stack.enter_async_context(
                ServiceDocumentRegistry(
                    database=TransientDocumentDatabase(),
                    event_emitter_factory=c()[EventEmitterFactory],
                    logger=c()[Logger],
                    correlator=c()[ContextualCorrelator],
                    nlp_services_provider=lambda: {"__nlp__": c()[NLPService]},
                    allow_migration=False,
                )
            )

            embedder_factory = EmbedderFactory(c())

            async def get_embedder_type() -> type[Embedder]:
                return type(await c()[NLPService].get_embedder())

            for vector_store_interface, vector_store_type in [
                (GlossaryStore, GlossaryVectorStore),
                (CannedResponseStore, CannedResponseVectorStore),
                (CapabilityStore, CapabilityVectorStore),
                (JourneyStore, JourneyVectorStore),
            ]:
                c()[vector_store_interface] = await self._exit_stack.enter_async_context(
                    vector_store_type(
                        id_generator=c()[IdGenerator],
                        vector_db=TransientVectorDatabase(
                            c()[Logger],
                            embedder_factory,
                            lambda: c()[EmbeddingCache],
                        ),
                        document_db=TransientDocumentDatabase(),
                        embedder_factory=embedder_factory,
                        embedder_type_provider=get_embedder_type,
                    )  # type: ignore
                )

        async def configure(c: Container) -> Container:
            latest_container = c

            def get_latest_container() -> Container:
                return latest_container

            await override_stores_with_transient_versions(get_latest_container)

            if self._configure_container:
                latest_container = await self._configure_container(latest_container.clone())

            if self._configure_hooks:
                hooks = await self._configure_hooks(c[EngineHooks])
                latest_container[EngineHooks] = hooks


            return latest_container

        async def async_nlp_service_shim(c: Container) -> NLPService:
            return c[NLPService]

        async def initialize(c: Container) -> None:
            host = "127.0.0.1"
            port = self.tool_service_port

            self._plugin_server = PluginServer(
                tools=[],
                port=port,
                host=host,
                hosted=True,
                plugin_data={
                    "server": self,
                    "container": c,
                },
            )

            await c[ServiceRegistry].update_tool_service(
                name=INTEGRATED_TOOL_SERVICE_NAME,
                kind="sdk",
                url=f"http://{host}:{port}",
                transient=True,
            )

            await self._exit_stack.enter_async_context(self._plugin_server)
            self._exit_stack.push_async_callback(self._plugin_server.shutdown)

            self._evaluator = _CachedEvaluator(
                db=JSONFileDocumentDatabase(c[Logger], PARLANT_HOME_DIR / "evaluation_cache.json"),
                container=c,
            )
            await self._exit_stack.enter_async_context(self._evaluator)

            if self._initialize:
                await self._initialize(c)
            
            # Setup retriever hooks after all initialization is complete
            await self._setup_retrievers()
            
            # Register retriever cleanup callback with Application
            try:
                from parlant.core.application import Application
                app = c[Application]
                app.set_retriever_cleanup_callback(self.cleanup_agent_retrievers)
            except Exception:
                # Application may not be in container (e.g., in test mode)
                pass

        return StartupParameters(
            port=self.port,
            nlp_service=async_nlp_service_shim,
            log_level=self.log_level,
            modules=self.modules,
            migrate=self._migrate,
            configure=configure,
            initialize=initialize,
        )


__all__ = [
    "Agent",
    "AgentFactory",
    "AgentId",
    "AgentStore",
    "AuthorizationException",
    "Operation",
    "AuthorizationPolicy",
    "DevelopmentAuthorizationPolicy",
    "ProductionAuthorizationPolicy",
    "Capability",
    "CapabilityId",
    "CompositionMode",
    "Container",
    "Customer",
    "CustomerId",
    "Variable",
    "ContextVariableId",
    "ControlOptions",
    "Embedder",
    "EmbedderFactory",
    "EmbeddingResult",
    "EmittedEvent",
    "EngineHook",
    "EngineHookResult",
    "EngineHooks",
    "EstimatingTokenizer",
    "EventKind",
    "EventSource",
    "FallbackSchematicGenerator",
    "Guideline",
    "GuidelineId",
    "Interaction",
    "InteractionMessage",
    "Journey",
    "JourneyId",
    "JourneyState",
    "JourneyStateId",
    "END_JOURNEY",
    "JourneyTransition",
    "JourneyTransitionId",
    "JSONSerializable",
    "Lifespan",
    "LoadedContext",
    "LogLevel",
    "Logger",
    "MessageEventData",
    "ModerationCheck",
    "ModerationService",
    "ModerationTag",
    "NoModeration",
    "NLPService",
    "NLPServices",
    "OptimizationPolicy",
    "PromptBuilder",
    "PromptSection",
    "BasicOptimizationPolicy",
    "PerceivedPerformancePolicy",
    "BasicPerceivedPerformancePolicy",
    "NullPerceivedPerformancePolicy",
    "NoMatchResponseProvider",
    "BasicNoMatchResponseProvider",
    "PluginServer",
    "RateLimiter",
    "RateLimitExceededException",
    "BasicRateLimiter",
    "RelationshipEntity",
    "RelationshipEntityId",
    "RelationshipEntityKind",
    "RelationshipId",
    "RelationshipKind",
    "RetrieverContext",
    "RetrieverResult",
    "SchematicGenerationResult",
    "SchematicGenerator",
    "Server",
    "ServiceRegistry",
    "Session",
    "SessionId",
    "SessionMode",
    "SessionStatus",
    "StatusEventData",
    "T",
    "Tag",
    "TagId",
    "Term",
    "TermId",
    "Tool",
    "ToolContext",
    "ToolContextAccessor",
    "ToolEntry",
    "ToolEventData",
    "ToolId",
    "ToolParameterDescriptor",
    "ToolParameterOptions",
    "ToolParameterType",
    "ToolResult",
    "CannedResponseId",
    "tool",
]
