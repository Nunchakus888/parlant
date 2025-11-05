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
Journey构建器

负责根据结构化的Journey Graph调用Journey API完成Journey配置。
"""

import json
from typing import Any, TYPE_CHECKING

from parlant.core.loggers import Logger
from parlant.core.services.indexing.journey_structure_analysis import JourneyGraph, JourneyNode

if TYPE_CHECKING:
    from parlant.sdk import Journey, JourneyState, Tool


class JourneyBuilder:
    """
    Journey构建器
    
    根据结构化的Journey Graph调用Journey API完成配置。
    """
    
    def __init__(self, logger: Logger):
        self._logger = logger
    
    async def build_journey_from_graph(
        self,
        journey: "Journey",
        journey_graph: JourneyGraph,
        available_tools: dict[str, "Tool"],
    ) -> dict[str, "JourneyState"]:
        """
        从Journey Graph构建Journey的状态和转换
        
        Args:
            journey: 已创建的Journey对象
            journey_graph: 结构化的Journey Graph
            available_tools: 可用的工具字典 {tool_name: Tool}
            
        Returns:
            dict[str, JourneyState]: 节点ID到状态的映射
            
        Raises:
            ValueError: 当工具不存在或Graph无效时
        """
        self._logger.info(
            f"🔨 building journey: {journey_graph.title}, "
            f"{len(journey_graph.nodes)} nodes, {len(journey_graph.edges)} edges"
        )
        
        # 验证Graph
        self._validate_graph(journey_graph, available_tools)
        
        # 1. 创建所有节点(states)
        state_map: dict[str, "JourneyState"] = {}
        
        for node in journey_graph.nodes:
            state = await self._create_state_from_node(
                journey, node, available_tools
            )
            state_map[node.id] = state
            self._logger.trace(f"creating state: {node.id} ({node.type})")
        
        # 2. 连接root到第一个节点
        # 找到第一个节点（没有incoming edge的节点）
        nodes_with_incoming = {edge.to_node for edge in journey_graph.edges}
        first_nodes = [node for node in journey_graph.nodes if node.id not in nodes_with_incoming]
        
        if first_nodes:
            first_node_state = state_map[first_nodes[0].id]
            await journey.create_transition(
                condition=None,
                source=journey.initial_state,
                target=first_node_state,
            )
            self._logger.trace(
                f"  ✓ creating transition: root -> {first_nodes[0].id} (from root to first node)"
            )
        
        # 3. 创建所有图中定义的转换(transitions)
        for edge in journey_graph.edges:
            source_state = state_map.get(edge.from_node)
            target_state = state_map.get(edge.to_node)
            
            if not source_state:
                raise ValueError(f"Source state not found: {edge.from_node}")
            if not target_state:
                raise ValueError(f"Target state not found: {edge.to_node}")
            
            await journey.create_transition(
                condition=edge.condition,
                source=source_state,
                target=target_state,
            )
            
            condition_text = edge.condition or "unconditional"
            self._logger.trace(
                f"creating transition: {edge.from_node} -> {edge.to_node} ({condition_text})"
            )
        
        # 4. 输出完整的 Journey Graph (JSON格式) 便于调试和排查问题
        self._logger.info(
            f"🚕 create journey successfully: {journey_graph.title}\n"
            f"  📐 Complete Journey Graph (JSON):\n"
            f"{json.dumps(journey_graph.to_dict(), indent=2, ensure_ascii=False)}"
        )
        
        return state_map
    
    def _validate_graph(
        self,
        journey_graph: JourneyGraph,
        available_tools: dict[str, "Tool"],
    ) -> None:
        """validate the validity of the Journey Graph"""
        # 验证节点ID唯一性
        node_ids = [node.id for node in journey_graph.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Duplicate node IDs found in Journey Graph")
        
        # 验证边引用的节点存在
        node_id_set = set(node_ids)
        for edge in journey_graph.edges:
            if edge.from_node not in node_id_set:
                raise ValueError(f"Edge references non-existent source node: {edge.from_node}")
            if edge.to_node not in node_id_set:
                raise ValueError(f"Edge references non-existent target node: {edge.to_node}")
        
        # 验证工具节点的工具存在
        for node in journey_graph.nodes:
            if node.type == "tool" and node.tool:
                if node.tool not in available_tools:
                    self._logger.warning(
                        f"⚠️ Tool '{node.tool}' not found in available tools for node '{node.id}'"
                    )
    
    async def _create_state_from_node(
        self,
        journey: "Journey",
        node: JourneyNode,
        available_tools: dict[str, "Tool"],
    ) -> "JourneyState":
        """根据节点类型创建对应的state"""
        if node.type == "tool":
            # 工具状态
            if not node.tool:
                raise ValueError(f"Tool node '{node.id}' must specify a tool name")
            
            tool = available_tools.get(node.tool)
            if not tool:
                raise ValueError(
                    f"Tool '{node.tool}' not found in available tools for node '{node.id}'"
                )
            
            # 导入SDK类型（运行时）
            from parlant.sdk import ToolJourneyState
            
            # Debug: 记录工具传递
            self._logger.trace(f"🛠️ [journey_builder] Node '{node.id}' will use tool: {node.tool}")
            
            # 工具参数将由Journey引擎在运行时自动推断
            # 直接传递Tool对象（ToolEntry）
            return await journey._create_state(
                ToolJourneyState,
                action=node.action,
                tools=[tool],
                metadata=node.metadata,
            )
        
        elif node.type == "fork":
            # 分支状态
            from parlant.sdk import ForkJourneyState
            return await journey._create_state(
                ForkJourneyState,
                action=node.action,
                metadata=node.metadata,
            )
        
        else:  # chat
            # 聊天状态
            from parlant.sdk import ChatJourneyState
            return await journey._create_state(
                ChatJourneyState,
                action=node.action,
                metadata=node.metadata,
            )
    

