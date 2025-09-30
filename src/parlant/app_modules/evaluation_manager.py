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
Unified Evaluation Manager

A single, cohesive module that handles all evaluation-related functionality:
- Caching and evaluation execution
- Task orchestration and progress tracking
- Result processing and metadata updates
- Agent-level evaluation management

This design eliminates the complexity of multiple service/module pairs
and provides a simple, unified interface for all evaluation operations.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import (
    Any,
    Coroutine,
    Dict,
    Literal,
    Optional,
    Sequence,
    Tuple,
    cast,
    TypeAlias
)
from contextlib import AsyncExitStack
from types import TracebackType

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

from parlant.core.agents import AgentId
from parlant.core.guidelines import GuidelineContent, GuidelineId, GuidelineStore
from parlant.core.journeys import Journey, JourneyId, JourneyNodeId, JourneyStore
from parlant.core.tools import ToolId
from parlant.core.loggers import Logger, LogLevel
from parlant.core.async_utils import safe_gather
from lagom import Container

JourneyStateId: TypeAlias = JourneyNodeId

from parlant.core.evaluations import (
    EvaluationStore,
    EvaluationStatus,
    PayloadDescriptor,
    PayloadKind,
    PayloadOperation,
    JourneyPayload,
    GuidelinePayload,
    InvoiceJourneyData,
    InvoiceGuidelineData,
)
from parlant.core.services.indexing.behavioral_change_evaluation import BehavioralChangeEvaluator
from parlant.core.tags import Tag
from parlant.adapters.db.json_file import JSONFileDocumentCollection, JSONFileDocumentDatabase
from parlant.core.persistence.common import ObjectId
from parlant.core.common import JSONSerializable, Version
from lagom import Container as LagomContainer


# Type aliases
JourneyTransitionId = str  # Simplified for now

# Cached data structures
class _CachedGuidelineEvaluation(dict):
    id: ObjectId
    version: Version.String
    properties: dict[str, JSONSerializable]


class _CachedJourneyEvaluation(dict):
    id: ObjectId
    version: Version.String
    node_properties: dict[JourneyStateId, dict[str, JSONSerializable]]
    edge_properties: dict[JourneyTransitionId, dict[str, JSONSerializable]]


@dataclass(frozen=True)
class EvaluationResult:
    """Unified evaluation result."""
    entity_type: Literal["guideline", "node", "journey"]
    entity_id: str
    properties: dict[str, JSONSerializable]


@dataclass
class EvaluationTask:
    """Simplified evaluation task."""
    entity_type: Literal["guideline", "node", "journey"]
    entity_id: str
    agent_id: Optional[AgentId]
    description: str
    # Store evaluation parameters instead of coroutine to avoid double awaiting
    evaluation_params: dict[str, Any]


class EvaluationManager:
    """
    Unified evaluation manager that handles all evaluation operations.
    
    This single class provides:
    - Caching and evaluation execution
    - Task orchestration and progress tracking  
    - Result processing and metadata updates
    - Agent-level evaluation management with isolation
    
    Design principles:
    - Single responsibility: Manage all evaluation-related operations
    - Simple interface: One class for all evaluation needs
    - Minimal state: Only essential state management
    - Clear separation: Internal complexity hidden from callers
    - Agent isolation: Each agent has independent cache collections
    """
    
    def __init__(
        self,
        db: JSONFileDocumentDatabase,
        guideline_store: GuidelineStore,
        journey_store: JourneyStore,
        container: LagomContainer,
        logger: Logger,
    ) -> None:
        self._db = db
        self._guideline_store = guideline_store
        self._journey_store = journey_store
        self._container = container
        self._logger = logger
        self._exit_stack = AsyncExitStack()
        
        # Agent-specific collections (initialized on demand)
        self._guideline_collections: Dict[AgentId, JSONFileDocumentCollection[_CachedGuidelineEvaluation]] = {}
        self._journey_collections: Dict[AgentId, JSONFileDocumentCollection[_CachedJourneyEvaluation]] = {}
        
        # Simple state management
        self._pending_tasks: Dict[str, EvaluationTask] = {}
        self._progress: Dict[str, float] = {}
        
        # UI components (only when needed)
        self._progress_ui: Optional[Progress] = None
        self._live_display: Optional[Live] = None
    
    async def __aenter__(self) -> "EvaluationManager":
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
    
    # ==================== Core Evaluation Methods ====================
    
    async def evaluate_guideline(
        self,
        guideline_id: GuidelineId,
        guideline_content: GuidelineContent,
        tool_ids: Sequence[ToolId] = [],
        agent_id: Optional[AgentId] = None,
    ) -> EvaluationResult:
        """Evaluate a guideline with agent-specific caching."""
        return await self._evaluate_guideline_impl(
            entity_id=guideline_id,
            g=guideline_content,
            tool_ids=tool_ids,
            agent_id=agent_id,
        )
    
    async def evaluate_state(
        self,
        state_id: JourneyStateId,
        guideline_content: GuidelineContent,
        tool_ids: Sequence[ToolId] = [],
        agent_id: Optional[AgentId] = None,
    ) -> EvaluationResult:
        """Evaluate a journey state with agent-specific caching."""
        return await self._evaluate_guideline_impl(
            entity_id=state_id,
            g=guideline_content,
            tool_ids=tool_ids,
            journey_state_proposition=True,
            properties_proposition=False,
            agent_id=agent_id,
        )
    
    async def evaluate_journey(self, journey: Journey, agent_id: Optional[AgentId] = None) -> EvaluationResult:
        """Evaluate a journey with agent-specific caching."""
        # Get agent-specific collection
        journey_collection = await self._get_journey_collection(agent_id)
        
        # Check cache first
        _hash = self._hash_journey_evaluation_request(journey, agent_id)
        
        if cached_evaluation := await journey_collection.find_one({"id": {"$eq": _hash}}):
            self._logger.trace(f"Using cached evaluation for journey: {journey.title}")
            return EvaluationResult(
                entity_type="journey",
                entity_id=str(journey.id),
                properties=cached_evaluation["node_properties"]
            )
        
        # Perform evaluation
        self._logger.trace(f"Evaluating journey: {journey.title}")
        
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
        
        # Wait for completion
        while True:
            evaluation = await self._container[EvaluationStore].read_evaluation(evaluation_id)
            self._set_progress(str(journey.id), evaluation.progress)
            
            if evaluation.status in [EvaluationStatus.PENDING, EvaluationStatus.RUNNING]:
                await asyncio.sleep(0.5)
                continue
            elif evaluation.status == EvaluationStatus.FAILED:
                raise RuntimeError(f"Journey evaluation failed: {evaluation.error}")
            elif evaluation.status == EvaluationStatus.COMPLETED:
                if not evaluation.invoices or not evaluation.invoices[0].approved:
                    raise RuntimeError("Journey evaluation not approved")
                
                invoice = evaluation.invoices[0]
                if not invoice.data:
                    raise RuntimeError("No evaluation data")
                
                # Cache result (replace existing if any)
                await journey_collection.update_one(
                    {"id": {"$eq": _hash}},
                    {
                        "id": ObjectId(_hash),
                        "version": Version.String("0.1.0"),
                        "node_properties": cast(InvoiceJourneyData, invoice.data).node_properties_proposition or {},
                        "edge_properties": cast(InvoiceJourneyData, invoice.data).edge_properties_proposition or {},
                    }
                )
                
                return EvaluationResult(
                    entity_type="journey",
                    entity_id=str(journey.id),
                    properties=cast(InvoiceJourneyData, invoice.data).node_properties_proposition or {}
                )
    
    # ==================== Task Management ====================
    
    def register_guideline_evaluation(
        self,
        guideline_id: GuidelineId,
        guideline_content: GuidelineContent,
        tool_ids: Sequence[ToolId] = [],
        agent_id: Optional[AgentId] = None,
    ) -> None:
        """Register a guideline evaluation task."""
        task = EvaluationTask(
            entity_type="guideline",
            entity_id=str(guideline_id),
            agent_id=agent_id,
            description=f"Guideline: {guideline_content.condition}",
            evaluation_params={
                "guideline_id": guideline_id,
                "guideline_content": guideline_content,
                "tool_ids": tool_ids,
            },
        )
        self._pending_tasks[f"guideline_{guideline_id}"] = task
    
    def register_state_evaluation(
        self,
        state_id: JourneyStateId,
        guideline_content: GuidelineContent,
        tool_ids: Sequence[ToolId] = [],
        agent_id: Optional[AgentId] = None,
    ) -> None:
        """Register a state evaluation task."""
        task = EvaluationTask(
            entity_type="node",
            entity_id=str(state_id),
            agent_id=agent_id,
            description=f"State: {guideline_content.condition}",
            evaluation_params={
                "state_id": state_id,
                "guideline_content": guideline_content,
                "tool_ids": tool_ids,
            },
        )
        self._pending_tasks[f"node_{state_id}"] = task
    
    def register_journey_evaluation(
        self,
        journey: Journey,
        agent_id: Optional[AgentId] = None,
    ) -> None:
        """Register a journey evaluation task."""
        task = EvaluationTask(
            entity_type="journey",
            entity_id=str(journey.id),
            agent_id=agent_id,
            description=f"Journey: {journey.title}",
            evaluation_params={
                "journey": journey,
            },
        )
        self._pending_tasks[f"journey_{journey.id}"] = task
    
    async def process_evaluations(
        self,
        log_level: LogLevel = LogLevel.INFO,
        max_visible_tasks: int = 5,
    ) -> Sequence[EvaluationResult]:
        """Process all registered evaluation tasks with agent isolation."""
        if not self._pending_tasks:
            self._logger.info("No evaluation tasks to process")
            return []
        
        self._logger.info(f"Processing {len(self._pending_tasks)} evaluation tasks")
        
        # Create async tasks with agent_id from task
        tasks = []
        for task_id, task in self._pending_tasks.items():
            async def task_wrapper() -> EvaluationResult:
                # Create coroutine based on task type and parameters, using task's agent_id
                if task.entity_type == "guideline":
                    result = await self.evaluate_guideline(
                        guideline_id=task.evaluation_params["guideline_id"],
                        guideline_content=task.evaluation_params["guideline_content"],
                        tool_ids=task.evaluation_params["tool_ids"],
                        agent_id=task.agent_id,  # Use task's agent_id
                    )
                elif task.entity_type == "node":
                    result = await self.evaluate_state(
                        state_id=task.evaluation_params["state_id"],
                        guideline_content=task.evaluation_params["guideline_content"],
                        tool_ids=task.evaluation_params["tool_ids"],
                        agent_id=task.agent_id,  # Use task's agent_id
                    )
                elif task.entity_type == "journey":
                    result = await self.evaluate_journey(
                        journey=task.evaluation_params["journey"],
                        agent_id=task.agent_id,  # Use task's agent_id
                    )
                else:
                    raise ValueError(f"Unknown entity type: {task.entity_type}")
                
                return result
            
            asyncio_task = asyncio.create_task(
                task_wrapper(),
                name=f"{task.entity_type}_evaluation_{task.entity_id}_agent_{task.agent_id or 'default'}"
            )
            tasks.append(asyncio_task)
        
        # Process with or without UI
        if log_level == LogLevel.TRACE:
            results = await safe_gather(*tasks)
        else:
            results = await self._process_with_ui(tasks, max_visible_tasks)
        
        # Process results and update metadata
        await self._process_results(results)
        
        # Cleanup
        self._pending_tasks.clear()
        self._progress.clear()
        
        return results
    
    
    # ==================== Agent-Specific Collection Management ====================
    
    async def _get_guideline_collection(self, agent_id: Optional[AgentId]) -> JSONFileDocumentCollection[_CachedGuidelineEvaluation]:
        """Get agent-specific guideline collection."""
        if agent_id is None:
            # Fallback to default collection for backward compatibility
            agent_id = AgentId("default")
        
        if agent_id not in self._guideline_collections:
            collection_name = f"guideline_evaluations_agent_{agent_id}"
            async def guideline_loader(doc):
                return doc
            
            self._guideline_collections[agent_id] = await self._db.get_or_create_collection(
                name=collection_name,
                schema=_CachedGuidelineEvaluation,
                document_loader=guideline_loader,
            )
        return self._guideline_collections[agent_id]
    
    async def _get_journey_collection(self, agent_id: Optional[AgentId]) -> JSONFileDocumentCollection[_CachedJourneyEvaluation]:
        """Get agent-specific journey collection."""
        if agent_id is None:
            # Fallback to default collection for backward compatibility
            agent_id = AgentId("default")
        
        if agent_id not in self._journey_collections:
            collection_name = f"journey_evaluations_agent_{agent_id}"
            async def journey_loader(doc):
                return doc
            
            self._journey_collections[agent_id] = await self._db.get_or_create_collection(
                name=collection_name,
                schema=_CachedJourneyEvaluation,
                document_loader=journey_loader,
            )
        return self._journey_collections[agent_id]
    
    # ==================== Cache Management ====================
    
    async def clear_cache_for_agent(self, agent_id: AgentId) -> None:
        """Clear all cached evaluations for a specific agent."""
        self._logger.info(f"Clearing cached evaluations for agent {agent_id}")
        
        # Clear agent-specific collections directly
        if agent_id in self._guideline_collections:
            await self._guideline_collections[agent_id].delete_many({})
            del self._guideline_collections[agent_id]
        
        if agent_id in self._journey_collections:
            await self._journey_collections[agent_id].delete_many({})
            del self._journey_collections[agent_id]
        
        self._logger.info(f"Cleared cached evaluations for agent {agent_id}")
    
    async def clear_all_cache(self) -> None:
        """Clear all cached evaluations for all agents."""
        # Clear all agent-specific collections
        for agent_id in list(self._guideline_collections.keys()):
            await self._guideline_collections[agent_id].delete_many({})
        
        for agent_id in list(self._journey_collections.keys()):
            await self._journey_collections[agent_id].delete_many({})
        
        # Clear collections cache
        self._guideline_collections.clear()
        self._journey_collections.clear()
        
        self._logger.info("Cleared all cached evaluations for all agents")
    
    # ==================== Private Implementation ====================
    
    async def _evaluate_guideline_impl(
        self,
        entity_id: GuidelineId | JourneyStateId,
        g: GuidelineContent,
        tool_ids: Sequence[ToolId] = [],
        journey_state_proposition: bool = False,
        properties_proposition: bool = True,
        agent_id: Optional[AgentId] = None,
    ) -> EvaluationResult:
        """Internal guideline evaluation implementation with agent isolation."""
        # Get agent-specific collection
        guideline_collection = await self._get_guideline_collection(agent_id)
        
        # Check cache with agent-specific hash
        _hash = self._hash_guideline_evaluation_request(
            g=g,
            tool_ids=tool_ids,
            journey_state_propositions=journey_state_proposition,
            properties_proposition=properties_proposition,
            agent_id=agent_id,
        )
        
        if cached_evaluation := await guideline_collection.find_one({"id": {"$eq": _hash}}):
            self._logger.trace(f"Using cached evaluation for guideline: {g.condition} (agent: {agent_id})")
            return EvaluationResult(
                entity_type="guideline",
                entity_id=str(entity_id),
                properties=cached_evaluation["properties"]
            )
        
        # Perform evaluation
        self._logger.trace(f"Evaluating guideline: {g.condition}")
        
        evaluation_id = await self._container[BehavioralChangeEvaluator].create_evaluation_task(
            payload_descriptors=[
                PayloadDescriptor(
                    PayloadKind.GUIDELINE,
                    GuidelinePayload(
                        content=g,
                        tool_ids=tool_ids,
                        operation=PayloadOperation.ADD,
                        action_proposition=True,
                        properties_proposition=properties_proposition,
                        journey_node_proposition=journey_state_proposition,
                    ),
                )
            ],
        )
        
        # Wait for completion
        while True:
            evaluation = await self._container[EvaluationStore].read_evaluation(evaluation_id)
            self._set_progress(str(entity_id), evaluation.progress)
            
            if evaluation.status in [EvaluationStatus.PENDING, EvaluationStatus.RUNNING]:
                await asyncio.sleep(0.5)
                continue
            elif evaluation.status == EvaluationStatus.FAILED:
                raise RuntimeError(f"Evaluation failed: {evaluation.error}")
            elif evaluation.status == EvaluationStatus.COMPLETED:
                if not evaluation.invoices or not evaluation.invoices[0].approved:
                    raise RuntimeError("Evaluation not approved")
                
                invoice = evaluation.invoices[0]
                if not invoice.data:
                    raise RuntimeError("No evaluation data")
                
                # Cache result in agent-specific collection (replace existing if any)
                properties = cast(InvoiceGuidelineData, invoice.data).properties_proposition or {}
                await guideline_collection.update_one(
                    {"id": {"$eq": _hash}},
                    {
                        "id": ObjectId(_hash),
                        "version": Version.String("0.1.0"),
                        "properties": properties,
                    }
                )
                
                return EvaluationResult(
                    entity_type="guideline",
                    entity_id=str(entity_id),
                    properties=properties
                )
    
    async def _process_with_ui(
        self,
        tasks: list[asyncio.Task[EvaluationResult]],
        max_visible_tasks: int,
    ) -> Sequence[EvaluationResult]:
        """Process tasks with progress UI."""
        self._progress_ui = Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            TaskProgressColumn(style="bold blue"),
            TimeElapsedColumn(),
        )
        
        # Create progress bars
        bar_id: Dict[str, int] = {}
        for task in tasks:
            entity_id = task.get_name().split("_")[-1]
            description = await self._render_entity_description(entity_id)
            bar_id[entity_id] = self._progress_ui.add_task(description[:50], total=100)
        
        # Process with live UI
        with Live(self._progress_ui, refresh_per_second=10) as self._live_display:
            gather = asyncio.create_task(safe_gather(*tasks))
            
            while not gather.done():
                await self._update_progress_display(bar_id, max_visible_tasks)
                await asyncio.sleep(0.2)
            
            return await gather
    
    async def _update_progress_display(
        self,
        bar_id: Dict[str, int],
        max_visible_tasks: int,
    ) -> None:
        """Update progress display."""
        unfinished = []
        for entity_id, rich_id in bar_id.items():
            pct = self._progress.get(entity_id, 0.0)
            self._progress_ui.update(TaskID(rich_id), completed=pct)
            if pct < 100.0:
                unfinished.append((entity_id, pct))
        
        # Show only most active tasks
        if unfinished:
            show = {e_id for e_id, _ in sorted(unfinished, key=lambda x: x[1])[:max_visible_tasks]}
        else:
            show = set()
        
        for entity_id, rich_id in bar_id.items():
            self._progress_ui.update(TaskID(rich_id), visible=(entity_id in show))
    
    async def _process_results(self, results: Sequence[EvaluationResult]) -> None:
        """Process evaluation results and update metadata."""
        for result in results:
            try:
                if result.entity_type == "guideline":
                    await self._update_guideline_metadata(
                        cast(GuidelineId, result.entity_id),
                        result.properties
                    )
                elif result.entity_type == "node":
                    await self._update_node_metadata(
                        cast(JourneyStateId, result.entity_id),
                        result.properties
                    )
                elif result.entity_type == "journey":
                    await self._update_journey_metadata(
                        cast(JourneyId, result.entity_id),
                        result.properties
                    )
            except Exception as e:
                self._logger.error(f"Failed to process result for {result.entity_id}: {e}")
    
    async def _update_guideline_metadata(
        self,
        guideline_id: GuidelineId,
        properties: dict[str, JSONSerializable],
    ) -> None:
        """Update guideline metadata with evaluation results."""
        guideline = await self._guideline_store.read_guideline(guideline_id)
        properties_to_add = {
            k: v for k, v in properties.items() if k not in guideline.metadata
        }
        
        for key, value in properties_to_add.items():
            await self._guideline_store.set_metadata(
                guideline_id=guideline_id,
                key=key,
                value=value,
            )
    
    async def _update_node_metadata(
        self,
        node_id: JourneyStateId,
        properties: dict[str, JSONSerializable],
    ) -> None:
        """Update journey node metadata with evaluation results."""
        node = await self._journey_store.read_node(node_id)
        properties_to_add = {
            k: v for k, v in properties.items() if k not in node.metadata
        }
        
        for key, value in properties_to_add.items():
            await self._journey_store.set_node_metadata(
                node_id=node_id,
                key=key,
                value=value,
            )
    
    async def _update_journey_metadata(
        self,
        journey_id: JourneyId,
        properties: dict[str, JSONSerializable],
    ) -> None:
        """Update journey metadata with evaluation results."""
        # For journey evaluations, properties are node-specific
        # This would need to be implemented based on the actual journey structure
        pass
    
    async def _render_entity_description(self, entity_id: str) -> str:
        """Render entity description for UI."""
        try:
            # Try to get entity info for better descriptions
            # This is a simplified version - could be enhanced
            return f"Evaluating {entity_id}"
        except Exception:
            return f"Entity {entity_id}"
    
    def _set_progress(self, key: str, pct: float) -> None:
        """Set progress for an entity."""
        self._progress[key] = max(0.0, min(pct, 100.0))
    
    def _hash_guideline_evaluation_request(
        self,
        g: GuidelineContent,
        tool_ids: Sequence[ToolId],
        journey_state_propositions: bool,
        properties_proposition: bool,
        agent_id: Optional[AgentId] = None,
    ) -> str:
        """Generate hash for guideline evaluation request with agent isolation."""
        tool_ids_str = ",".join(str(tool_id) for tool_id in tool_ids) if tool_ids else ""
        agent_suffix = f"_{agent_id}" if agent_id else ""
        return hashlib.md5(
            f"{g.condition or ''}:{g.action or ''}:{tool_ids_str}:{journey_state_propositions}:{properties_proposition}{agent_suffix}".encode()
        ).hexdigest()
    
    def _hash_journey_evaluation_request(self, journey: Journey, agent_id: Optional[AgentId] = None) -> str:
        """Generate hash for journey evaluation request with agent isolation."""
        node_ids_str = ",".join(str(node.id) for node in journey.states) if journey.states else ""
        edge_ids_str = ",".join(str(edge.id) for edge in journey.transitions) if journey.transitions else ""
        agent_suffix = f"_{agent_id}" if agent_id else ""
        return hashlib.md5(f"{journey.id}:{node_ids_str}:{edge_ids_str}{agent_suffix}".encode()).hexdigest()
    
    # ==================== Public Interface ====================
    
    def get_pending_task_count(self) -> int:
        """Get number of pending tasks."""
        return len(self._pending_tasks)
    
    def get_agent_tasks(self, agent_id: AgentId) -> list[EvaluationTask]:
        """Get tasks for a specific agent."""
        return [task for task in self._pending_tasks.values() if task.agent_id == agent_id]
    
    def clear_agent_tasks(self, agent_id: AgentId) -> None:
        """Clear tasks for a specific agent."""
        self._pending_tasks = {
            k: v for k, v in self._pending_tasks.items() 
            if v.agent_id != agent_id
        }
