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
Journey结构分析Proposer

负责使用LLM分析guideline并生成结构化的Journey Graph。
"""

import traceback
from typing import Optional, Sequence
from pydantic import Field

from parlant.core.common import DefaultBaseModel
from parlant.core.services.indexing.common import EvaluationError, ProgressReport
from parlant.core.guidelines import GuidelineContent
from parlant.core.loggers import Logger
from parlant.core.nlp.generation import SchematicGenerator
from parlant.core.engines.alpha.optimization_policy import OptimizationPolicy
from parlant.core.services.tools.service_registry import ServiceRegistry
from parlant.core.services.indexing.journey_structure_analysis import (
    JourneyGraph,
    JourneyNode,
    JourneyEdge,
    JourneyStructureProposition,
)
from parlant.core.tools import ToolId
from parlant.core.engines.alpha.prompt_builder import PromptBuilder


class JourneyNodeSchema(DefaultBaseModel):
    """Journey节点的Schema定义"""
    id: str
    type: str  # "chat", "tool", or "fork"
    action: str
    tool: Optional[str] = None
    metadata: dict[str, str] = {}


class JourneyEdgeSchema(DefaultBaseModel):
    """Journey边的Schema定义"""
    from_node: str = Field(alias="from")  # LLM生成"from"，内部使用from_node
    to_node: str = Field(alias="to")      # LLM生成"to"，内部使用to_node
    condition: Optional[str] = None
    
    model_config = {"populate_by_name": True}  # 允许使用字段名或alias


class JourneyGraphSchema(DefaultBaseModel):
    """Journey Graph的Schema定义"""
    title: str
    description: str
    nodes: list[JourneyNodeSchema]
    edges: list[JourneyEdgeSchema]


class JourneyStructurePropositionSchema(DefaultBaseModel):
    """LLM输出的完整Schema"""
    is_journey_candidate: bool
    confidence: float
    reasoning: str
    journey_graph: Optional[JourneyGraphSchema] = None


class JourneyStructureProposer:
    """
    Journey结构分析器
    
    使用LLM分析guideline，判断是否适合转换为Journey，
    并生成结构化的Journey Graph表示。
    """
    
    def __init__(
        self,
        logger: Logger,
        optimization_policy: OptimizationPolicy,
        schematic_generator: SchematicGenerator[JourneyStructurePropositionSchema],
        service_registry: ServiceRegistry,
    ) -> None:
        self._logger = logger
        self._optimization_policy = optimization_policy
        self._schematic_generator = schematic_generator
        self._service_registry = service_registry
    
    async def propose_journey_structure(
        self,
        guideline: GuidelineContent,
        tool_ids: Sequence[ToolId],
        progress_report: Optional[ProgressReport] = None,
    ) -> JourneyStructureProposition:
        """
        分析guideline并生成Journey结构
        
        Args:
            guideline: 要分析的guideline
            tool_ids: 关联的工具ID列表
            progress_report: 进度报告器
            
        Returns:
            JourneyStructureProposition: 包含分析结果和Journey Graph
        """
        if progress_report:
            await progress_report.stretch(1)
        
        with self._logger.scope("JourneyStructureProposer"):
            generation_attempt_temperatures = (
                self._optimization_policy.get_guideline_proposition_retry_temperatures(
                    hints={"type": self.__class__.__name__}
                )
            )
            
            max_attempts = self._optimization_policy.get_max_guideline_proposition_attempts(
                hints={"type": self.__class__.__name__}
            )
            
            last_generation_exception: Exception | None = None
            
            for generation_attempt in range(max_attempts):
                try:
                    proposition = await self._generate_journey_structure(
                        guideline,
                        tool_ids,
                        temperature=generation_attempt_temperatures[generation_attempt],
                    )
                    
                    if progress_report:
                        await progress_report.increment(1)
                    
                    # 转换Schema为领域模型
                    journey_graph = None
                    if proposition.journey_graph:
                        journey_graph = self._convert_schema_to_graph(proposition.journey_graph)
                    
                    return JourneyStructureProposition(
                        is_journey_candidate=proposition.is_journey_candidate,
                        confidence=proposition.confidence,
                        reasoning=proposition.reasoning,
                        journey_graph=journey_graph,
                    )
                    
                except Exception as exc:
                    self._logger.warning(
                        f"JourneyStructureProposer attempt {generation_attempt} failed: {traceback.format_exception(exc)}"
                    )
                    last_generation_exception = exc
            
            raise EvaluationError() from last_generation_exception
    
    def _convert_schema_to_graph(self, schema: JourneyGraphSchema) -> JourneyGraph:
        """将Schema转换为JourneyGraph领域模型"""
        return JourneyGraph(
            title=schema.title,
            description=schema.description,
            nodes=[
                JourneyNode(
                    id=node.id,
                    type=node.type,  # type: ignore
                    action=node.action,
                    tool=node.tool,
                    metadata=node.metadata,
                )
                for node in schema.nodes
            ],
            edges=[
                JourneyEdge(
                    from_node=edge.from_node,
                    to_node=edge.to_node,
                    condition=edge.condition,
                )
                for edge in schema.edges
            ],
        )
    
    async def _generate_journey_structure(
        self,
        guideline: GuidelineContent,
        tool_ids: Sequence[ToolId],
        temperature: float,
    ) -> JourneyStructurePropositionSchema:
        """调用LLM生成Journey结构"""
        prompt = await self._build_prompt(guideline, tool_ids)
        
        result = await self._schematic_generator.generate(
            prompt=prompt,
            hints={"temperature": temperature},
        )
        
        # 收集 GenerationInfo
        from parlant.core.services.indexing.behavioral_change_evaluation import add_generation_info
        add_generation_info(result.info)
        
        return result.content
    
    async def _build_prompt(
        self,
        guideline: GuidelineContent,
        tool_ids: Sequence[ToolId],
    ) -> PromptBuilder:
        """构建分析prompt"""
        builder = PromptBuilder(
            # on_build=lambda prompt: self._logger.trace(f"Prompt:\n\n{prompt}")
        )
        
        # 获取工具信息
        tool_descriptions = []
        for tool_id in tool_ids:
            try:
                service = await self._service_registry.read_tool_service(tool_id.service_name)
                tool = await service.read_tool(tool_id.tool_name)
                tool_descriptions.append(f"- {tool.name}: {tool.description}")
            except Exception:
                tool_descriptions.append(f"- {tool_id.tool_name}")
        
        tools_text = "\n".join(tool_descriptions) if tool_descriptions else "None"
        
        builder.add_section(
            name="journey-structure-analysis-general-instructions",
            template="""
In our system, the behavior of a conversational AI agent is guided by "guidelines". 
Each guideline has a "condition" (when it applies) and an "action" (what to do).

Some guidelines describe complex multi-step processes that are better represented as "Journeys" 
- state machines with multiple steps, transitions, and state tracking.

Your task is to analyze a guideline and determine:
1. Whether it should be converted to a Journey
2. If yes, generate a structured Journey Graph representation
""",
        )
        
        builder.add_section(
            name="journey-structure-analysis-criteria",
            template="""
## When to Convert to Journey

Convert to Journey if the guideline exhibits:

1. **Multiple Sequential Steps**
   - Contains sequence indicators: "first...then...", "after that...", "next...", "先...然后...", "接着..."
   - Describes a process with clear ordering
   - Multiple actions that must happen in sequence

2. **Tool Chain Dependencies**
   - Multiple tool calls where later tools depend on earlier results
   - Data flow between operations
   - One tool's output feeds into another tool's input

3. **State Management Requirements**
   - Needs to track completion of steps
   - Requires branching or conditional logic
   - Customer input needed at specific points

4. **Conditional Branching**
   - Different paths based on conditions
   - If-then-else logic
   - Decision points that affect the flow

## When NOT to Convert

Keep as simple guideline if:
- Single action or tool call
- No dependencies between operations
- Simple condition-action pair
- No state tracking needed
- Tools can run independently
- **Multiple steps BUT no step dependencies** - If steps don't need data passed between them, use simple matching
- **Can work with both approaches** - When either journey or simple matching would work, PREFER simple matching
- Steps are mostly parallel or independent operations

**General Principle: Minimize Journey Usage**
- Journeys add complexity - use ONLY when truly necessary
- Use Journey ONLY for clear step dependencies (one step's output → next step's input)
- Use Journey ONLY when strict sequential ordering is mandatory
- **When in doubt, use simple guideline matching instead of journey**
""",
        )
        
        builder.add_section(
            name="journey-structure-analysis-graph-format",
            template="""
## Journey Graph Structure

A Journey Graph is a DAG (Directed Acyclic Graph) with:

### Node Types

1. **chat**: Conversational interaction
   - For asking questions, providing information, or responding
   - Action describes what the agent should do

2. **tool**: Tool execution
   - For calling external tools/services
   - Must specify tool name from available tools
   - Tool parameters are automatically inferred at runtime
   - Action describes what this tool accomplishes

3. **fork**: Decision point
   - For conditional branching
   - Action describes the decision being made

### Edges

- **condition: null** - Unconditional transition (automatic progression)
- **condition: "description"** - Conditional transition (e.g., "用户提供了城市名称")

### CRITICAL Guidelines

**Node Structure:**
- DO NOT create virtual start/root/init/begin nodes
- The system provides an automatic initial_state - you only define business nodes
- **IMPORTANT**: If the task involves collecting user input, consider that users may provide information proactively
  - Add a node to PARSE/EXTRACT information from user messages BEFORE asking for it
  - Example: For weather query, first try to extract city from user's message, THEN ask if missing
- Use descriptive, unique node IDs that reflect the actual business action
- Be specific and clear in action descriptions
- Use exact tool names from the available tools list - verify each tool name exists before using it
- Do NOT specify tool parameters - they are inferred automatically
- The first node in your graph should be the first real step in the process

**Prevent Infinite Loops:**
- MUST ensure the journey graph has at least one terminal node (exit point)
- DO NOT create circular paths where nodes loop back indefinitely
- Every branch must eventually lead to a terminal state
- If using conditional branches (fork nodes), ensure all paths can reach an end
- Terminal nodes should be chat or tool nodes with no outgoing edges
- Example: In a 4-step process, the last step (e.g., "respond_result") should have no outgoing edges
""",
        )
        
        builder.add_section(
            name="journey-structure-analysis-examples",
            template="""
## Examples

### Example 1: Multi-step Process with Proactive User Input (Journey Candidate)

Input:
- Condition: "用户询问天气情况"
- Action: "先让用户提供城市名称，然后使用city_geo_info工具将城市转为坐标，获取经纬度后，立即使用get_weather_by_geo工具查询天气，最后用用户的语言回答天气情况"
- Tools: ["city_geo_info", "get_weather_by_geo"]

Analysis: Clear sequential steps with tool chain dependencies. Users may provide city name proactively (e.g., "上海天气").

### Example 2: Simple Response (NOT Journey)

Input:
- Condition: "用户说你好"
- Action: "友好地回应用户的问候"
- Tools: []

Analysis: Single-step interaction with no tool dependencies or state management.

### Example 3: Single Tool Call (NOT Journey)

Input:
- Condition: "用户询问账户余额"
- Action: "使用check_balance工具查询并告知用户余额"
- Tools: ["check_balance"]

Analysis: Single tool call with no dependencies or sequential steps.
""",
        )
        
        builder.add_section(
            name="journey-structure-analysis-guideline",
            template="""
## Guideline to Analyze

Base your evaluation exclusively on the condition, action, and tools provided here.

Condition: {condition}

Action: {action}

Available Tools: 
{tools}

**CRITICAL REMINDER**: 
- The above list is the COMPLETE and ONLY list of available tools
- If a tool is NOT in this list, it does NOT exist and CANNOT be used
- Do NOT create tool nodes for tools not in this exact list
- Use chat/fork nodes for logic that doesn't require a tool
- Every tool name in your journey_graph MUST exactly match a name from the list above
""",
            props={
                "condition": guideline.condition,
                "action": guideline.action or "",
                "tools": tools_text,
            },
        )
        
        builder.add_section(
            name="journey-structure-analysis-output-format",
            template="""
Analyze the guideline above and provide your evaluation.

Expected output (JSON):
```json
{{
  "is_journey_candidate": <BOOLEAN>,
  "confidence": <FLOAT 0.0-1.0>,
  "reasoning": "<EXPLANATION>",
  "journey_graph": {{
    "title": "<JOURNEY_TITLE>",
    "description": "<JOURNEY_DESCRIPTION>",
    "nodes": [
      {{
        "id": "<NODE_ID>",
        "type": "<chat|tool|fork>",
        "action": "<ACTION_DESCRIPTION>",
        "tool": "<TOOL_NAME or null>",
        "metadata": {{}}
      }}
    ],
    "edges": [
      {{
        "from": "<SOURCE_NODE_ID>",
        "to": "<TARGET_NODE_ID>",
        "condition": "<CONDITION or null>"
      }}
    ]
  }} or null
}}
```

Note: If is_journey_candidate is false, set journey_graph to null.
""",
        )
        
        return builder

