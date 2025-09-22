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

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional

from openai.types.completion_usage import PromptTokensDetails, CompletionTokensDetails


# todo optimize the generation type
class GenerationType(Enum):
    """枚举类型，标识不同的 LLM 交互类型"""
    # 指导原则匹配相关
    GUIDELINE_EVALUATION = "guideline_evaluation"              # 指导原则评估
    OBSERVATIONAL_GUIDELINE_MATCHING = "observational_guideline_matching"  # 观察性指导原则匹配
    DISAMBIGUATION_GUIDELINE_MATCHING = "disambiguation_guideline_matching"  # 消歧指导原则匹配
    
    # 指导原则评估相关（_process_evaluations 中使用）
    GUIDELINE_ACTION_PROPOSITION = "guideline_action_proposition"  # 指导原则动作提议
    GUIDELINE_CONTINUOUS_PROPOSITION = "guideline_continuous_proposition"  # 指导原则连续性提议
    CUSTOMER_DEPENDENT_ACTION_DETECTION = "customer_dependent_action_detection"  # 客户依赖动作检测
    AGENT_INTENTION_PROPOSITION = "agent_intention_proposition"  # 代理意图提议
    TOOL_RUNNING_ACTION_DETECTION = "tool_running_action_detection"  # 工具运行动作检测
    RELATIVE_ACTION_PROPOSITION = "relative_action_proposition"  # 相对动作提议
    
    # 旅程相关
    JOURNEY_SELECTION = "journey_selection"                    # 旅程节点选择
    
    # 工具调用相关
    SINGLE_TOOL_CALLING = "single_tool_calling"                # 单工具调用
    OVERLAPPING_TOOLS_CALLING = "overlapping_tools_calling"    # 重叠工具调用
    
    # 消息生成相关
    MESSAGE_GENERATION = "message_generation"                  # 消息生成
    DRAFT_GENERATION = "draft_generation"                      # 草稿生成
    INSPECTION = "inspection"                                  # 检查/验证
    
    # Canned Response 相关
    CANNED_RESPONSE_DRAFT = "canned_response_draft"            # Canned Response 起草
    CANNED_RESPONSE_SELECTION = "canned_response_selection"    # Canned Response 选择
    CANNED_RESPONSE_REVISION = "canned_response_revision"      # Canned Response 修订
    CANNED_RESPONSE_PREAMBLE = "canned_response_preamble"      # Canned Response 前言
    CANNED_RESPONSE_FIELD_EXTRACTION = "canned_response_field_extraction"  # Canned Response 字段提取
    FOLLOW_UP_CANNED_RESPONSE_SELECTION = "follow_up_canned_response_selection"  # 后续 Canned Response 选择
    
    # 响应分析相关
    RESPONSE_ANALYSIS = "response_analysis"                    # 响应分析
    
    # 其他
    EMBEDDING = "embedding"                                    # 嵌入生成
    GENERAL = "general"                                        # 通用生成


@dataclass(frozen=True)
class UsageInfo:
    input_tokens: int
    output_tokens: int
    extra: Optional[Mapping[str, int]] = None
    total_tokens: int | None = 0
    prompt_tokens_details: Optional[PromptTokensDetails] = None
    completion_tokens_details: Optional[CompletionTokensDetails] = None


@dataclass(frozen=True)
class GenerationInfo:
    schema_name: str
    model: str
    duration: float
    usage: UsageInfo | None = None
