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
## Journey Evaluation Criteria

Journey should be rare. Use Journey only when all of the following are true:

### Mandatory Requirements (all must be met)

1. Multiple API Calls with Serial Dependencies
   - Not just collecting information then calling one API
   - Multiple distinct tool or API calls where later calls depend on earlier results
   - Data from API 1 must feed into API 2, which feeds into API 3
   - Example: get_city_coordinates → get_weather_by_geo → send_email_alert
   
2. Strict Sequential Ordering
   - Steps must happen in a specific order due to technical or business constraints
   - Not just "collect info then call API" (guideline can handle this)
   - Step 2 cannot execute before Step 1 is complete
   - Clear cause-and-effect chain between steps
   
3. Complex State Management
   - Need to track which specific steps are completed
   - Different paths based on intermediate results
   - Backtracking or conditional branching based on step outcomes
   - Not just "collect fields and call API" (guideline handles this naturally)

## When NOT to Convert to Journey

Use simple guideline if any of these apply:

### Information Gathering + Single API
- Collecting user input then calling one tool or API
- Example: "Get user's name, email, phone → call save_customer_info"
- Guideline handles multi-turn information gathering naturally
  
### Single or Independent Operations
- Single action or single tool call
- No dependencies between operations
- Simple condition-action pair
- Tools that can run independently or in parallel
- Multiple information fields with only one API call at the end

### No Step Dependencies
- Multiple steps but no data passing between them
- Steps can happen in any order
- No parameter dependencies between steps
- Information gathering with no specific order required

### Ambiguous Cases (Default to Guideline)
- When both journey and guideline could work, choose guideline
- When step ordering is suggested but not mandatory, use guideline
- When collecting information without strict sequence, use guideline
- Steps that are mostly parallel or independent, use guideline

## Specific Anti-Patterns (DO NOT Convert to Journey)

Anti-Pattern 1: Collection + Single API
- Pattern: "Get field1, field2, field3... then call single_api"
- Keep as guideline even if action mentions "first", "then", "finally"
- Guideline naturally handles multi-turn collection
- Example: "Ask for name → Ask for email → Call save_user_info"

Anti-Pattern 2: Conditional but Single Decision
- Pattern: "If X then call api_a, else call api_b"
- Single conditional with no chaining
- Guideline can handle simple if-else logic
- Example: "If premium user call premium_api else call standard_api"

Anti-Pattern 3: Parallel or Independent Steps
- Steps don't depend on each other
- Can execute in any order
- Example: "Check user status AND send notification"

General Principle: Minimize Journey Usage
- Journeys add significant complexity and execution cost
- Use Journey only for complex orchestration with clear dependencies
- Default to guideline unless journey is absolutely necessary
- When analyzing, actively look for reasons not to use journey
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

### Example 1: API Chain with Dependencies [JOURNEY]

Input:
- Condition: "用户询问天气情况"
- Action: "先让用户提供城市名称，然后使用city_geo_info工具将城市转为坐标，获取经纬度后，立即使用get_weather_by_geo工具查询天气，最后用用户的语言回答天气情况"
- Tools: ["city_geo_info", "get_weather_by_geo"]

Decision: JOURNEY
Rationale: Has API chain with dependencies where step 1 output (coordinates) feeds into step 2 input (weather query). Multiple APIs with data flowing between them. Sequential constraint enforced by technical requirement.

### Example 2: Information Collection + Single API [GUIDELINE]

Input:
- Condition: "Customer wants demo/pricing info/trial OR provides contact info"
- Action: "If customer has not provided all required information fields, politely guide them to provide all required information. Once all required fields are collected, save the customer's information using the save_customer_information tool"
- Tools: ["save_customer_information"]

Decision: GUIDELINE
Rationale: Only one API call at the end. Information collection has no strict ordering requirement. No API dependencies or chaining. Guideline naturally handles multi-turn information gathering without complex state management.

### Example 3: Simple Response [GUIDELINE]

Input:
- Condition: "用户说你好"
- Action: "友好地回应用户的问候"
- Tools: []

Decision: GUIDELINE
Rationale: Single-step interaction with no tool dependencies or state management requirements.

### Example 4: Single Tool Call [GUIDELINE]

Input:
- Condition: "用户询问账户余额"
- Action: "使用check_balance工具查询并告知用户余额"
- Tools: ["check_balance"]

Decision: GUIDELINE
Rationale: Single tool call with no dependencies or sequential steps.

### Example 5: Multiple Fields Collection [GUIDELINE]

Input:
- Condition: "用户想要预订餐厅"
- Action: "首先询问用户的姓名、电话号码、用餐人数和时间，收集完所有信息后使用book_restaurant工具预订"
- Tools: ["book_restaurant"]

Decision: GUIDELINE
Rationale: Despite "首先...然后" keywords, only one API at the end. Fields can be collected in any order. No API dependencies. Guideline handles parameter collection naturally.

### Example 6: Multi-API with Branching [JOURNEY]

Input:
- Condition: "用户需要完整的旅行规划"
- Action: "首先用check_user_tier检查用户等级，如果是VIP用户使用premium_travel_api获取高级方案并用send_vip_email发送，否则用standard_travel_api获取标准方案"
- Tools: ["check_user_tier", "premium_travel_api", "standard_travel_api", "send_vip_email"]

Decision: JOURNEY
Rationale: Multiple APIs with dependencies. check_user_tier output determines which API path to follow. Conditional branching based on API results. Different execution paths based on intermediate step outcomes.

### Decision Rules

Pattern: "Collect information + single API" → GUIDELINE
Pattern: "API1 output → API2 input → API3 input" → JOURNEY
Pattern: "Single API call" → GUIDELINE
Pattern: "API with conditional branching + subsequent APIs" → JOURNEY
Pattern: "Multiple fields, no API dependencies" → GUIDELINE
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

