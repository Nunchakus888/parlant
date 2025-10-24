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
Journey结构分析模块

该模块负责分析guideline并生成结构化的Journey Graph表示。
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Sequence

from parlant.core.common import JSONSerializable


@dataclass(frozen=True)
class JourneyNode:
    """
    Journey节点定义
    
    表示Journey中的一个状态节点，可以是聊天、工具调用或分支。
    
    注意：工具节点不需要指定parameters，参数将由Journey引擎在运行时自动推断。
    """
    id: str
    type: Literal["chat", "tool", "fork"]
    action: str
    tool: Optional[str] = None
    metadata: dict[str, JSONSerializable] = field(default_factory=dict)


@dataclass(frozen=True)
class JourneyEdge:
    """
    Journey边定义
    
    表示Journey中两个状态之间的转换关系。
    """
    from_node: str  # source node id
    to_node: str    # target node id
    condition: Optional[str] = None


@dataclass(frozen=True)
class JourneyGraph:
    """
    Journey Graph (DAG)
    
    表示完整的Journey结构，包含所有节点和边。
    """
    title: str
    description: str
    nodes: Sequence[JourneyNode]
    edges: Sequence[JourneyEdge]
    
    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "title": self.title,
            "description": self.description,
            "nodes": [
                {
                    "id": node.id,
                    "type": node.type,
                    "action": node.action,
                    "tool": node.tool,
                    "metadata": node.metadata,
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "from": edge.from_node,
                    "to": edge.to_node,
                    "condition": edge.condition,
                }
                for edge in self.edges
            ],
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JourneyGraph":
        """从字典创建JourneyGraph"""
        return cls(
            title=data["title"],
            description=data["description"],
            nodes=[
                JourneyNode(
                    id=node["id"],
                    type=node["type"],
                    action=node["action"],
                    tool=node.get("tool"),
                    metadata=node.get("metadata", {}),
                )
                for node in data["nodes"]
            ],
            edges=[
                JourneyEdge(
                    from_node=edge["from"],
                    to_node=edge["to"],
                    condition=edge.get("condition"),
                )
                for edge in data["edges"]
            ],
        )


@dataclass(frozen=True)
class JourneyStructureProposition:
    """
    Journey结构分析结果
    
    包含LLM分析的结果，判断guideline是否适合转换为Journey，
    以及生成的结构化Journey Graph。
    """
    is_journey_candidate: bool
    confidence: float
    reasoning: str
    journey_graph: Optional[JourneyGraph] = None

