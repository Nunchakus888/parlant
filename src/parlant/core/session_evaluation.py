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
Session Evaluation Module - Five Dimension Model

Based on industry standards (CSAT, NPS, CES):
1. Task Completion (30%) - Goal achievement, CSAT-aligned
2. Response Quality (25%) - Accuracy and relevance
3. User Experience (20%) - Satisfaction and communication, NPS-aligned
4. Efficiency (15%) - Resolution speed, CES-aligned
5. Boundary Handling (10%) - Proper escalation and limits

Special markers:
- ho000001: Human handover
- un000001: Knowledge boundary
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence
from pydantic import Field

from parlant.core.common import DefaultBaseModel
from parlant.core.loggers import Logger
from parlant.core.nlp.generation import SchematicGenerator, SchematicGenerationResult
from parlant.core.nlp.service import NLPService
from parlant.core.sessions import Event, EventKind, EventSource


HANDOVER_PREFIX = "ho000001:"
UNKNOWN_PREFIX = "un000001:"


@dataclass
class MessageStats:
    """Conversation statistics."""
    total_messages: int = 0
    user_messages: int = 0
    agent_messages: int = 0
    handover_count: int = 0
    unknown_count: int = 0
    unanswered_count: int = 0
    
    @property
    def turns(self) -> int:
        """Number of conversation turns (Q&A pairs)."""
        return min(self.user_messages, self.agent_messages)
    
    def to_context(self) -> str:
        lines = [
            f"- Turns: {self.turns} (user: {self.user_messages}, agent: {self.agent_messages})",
        ]
        if self.handover_count:
            lines.append(f"- Handover (ho000001:): {self.handover_count}")
        if self.unknown_count:
            lines.append(f"- Boundary (un000001:): {self.unknown_count}")
        if self.unanswered_count:
            lines.append(f"- Unanswered: {self.unanswered_count}")
        return "\n".join(lines)


def analyze_messages(events: Sequence[Event]) -> MessageStats:
    """Analyze conversation for statistics."""
    stats = MessageStats()
    last_was_user = False
    
    for event in events:
        if event.kind != EventKind.MESSAGE or event.deleted:
            continue
        data = event.data
        if not isinstance(data, dict):
            continue
        
        message = data.get("message", "")
        stats.total_messages += 1
        
        if event.source == EventSource.CUSTOMER:
            stats.user_messages += 1
            if last_was_user:
                stats.unanswered_count += 1
            last_was_user = True
        elif event.source == EventSource.AI_AGENT:
            stats.agent_messages += 1
            last_was_user = False
            if message.startswith(HANDOVER_PREFIX):
                stats.handover_count += 1
            elif message.startswith(UNKNOWN_PREFIX):
                stats.unknown_count += 1
    
    if last_was_user:
        stats.unanswered_count += 1
    
    return stats


class DimensionScore(DefaultBaseModel):
    """Score with rationale."""
    score: int = Field(..., ge=1, le=10)
    rationale: str = Field(..., max_length=200)


class SessionEvaluationSchema(DefaultBaseModel):
    """Five-dimension evaluation (industry-aligned)."""
    
    task_completion: DimensionScore = Field(..., description="Goal achieved? (CSAT)")
    response_quality: DimensionScore = Field(..., description="Accurate & relevant?")
    user_experience: DimensionScore = Field(..., description="Satisfied? (NPS)")
    efficiency: DimensionScore = Field(..., description="Fast resolution? (CES)")
    boundary_handling: DimensionScore = Field(..., description="Proper escalation?")
    summary: str = Field(..., max_length=300)
    
    @property
    def score(self) -> float:
        """Weighted average: task(30%) + quality(25%) + UX(20%) + efficiency(15%) + boundary(10%)."""
        return round(
            self.task_completion.score * 0.30 +
            self.response_quality.score * 0.25 +
            self.user_experience.score * 0.20 +
            self.efficiency.score * 0.15 +
            self.boundary_handling.score * 0.10, 1
        )


@dataclass(frozen=True)
class AgentContext:
    """Agent background."""
    name: Optional[str] = None
    description: Optional[str] = None
    background: Optional[str] = None
    
    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "AgentContext":
        basic = config.get("basic_settings", {})
        return cls(
            name=basic.get("name"),
            description=basic.get("description"),
            background=basic.get("background"),
        )
    
    def to_prompt(self) -> str:
        parts = []
        if self.name:
            parts.append(f"Name: {self.name}")
        if self.description:
            parts.append(f"Role: {self.description}")
        if self.background:
            parts.append(f"Scope: {self.background}")
        return " | ".join(parts) if parts else "General assistant"


@dataclass(frozen=True)
class TokenUsage:
    """Token statistics."""
    input_tokens: int = 0
    output_tokens: int = 0
    
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class SessionEvaluationResult:
    """Evaluation result."""
    session_id: str
    evaluation: SessionEvaluationSchema
    stats: MessageStats
    token_usage: TokenUsage
    
    @property
    def score(self) -> float:
        return self.evaluation.score


class SessionEvaluator:
    """Session evaluator using five-dimension model."""
    
    # Simple, clear prompt for accurate inference
    PROMPT = """Rate this chatbot conversation (1-10 each dimension).

**Agent**: {agent_context}

**Stats**: {stats}

**Markers** (normal responses, not errors):
- ho000001: Human handover request (normal)
- un000001: Knowledge boundary acknowledgment (normal)
- Unanswered: No agent response (failure, low scores)

**Dimensions** (weight):

1. **Task Completion** (30%): Did user achieve their goal?
   - 8-10: Goal achieved or proper escalation
   - 4-7: Partial success
   - 1-3: Failed / unanswered

2. **Response Quality** (25%): Were answers accurate and relevant?
   - 8-10: Accurate, relevant, complete
   - 4-7: Mostly correct
   - 1-3: Wrong / irrelevant / unanswered

3. **User Experience** (20%): Would user recommend? (NPS)
   - 8-10: Friendly, professional, satisfying
   - 4-7: Acceptable
   - 1-3: Frustrating / unanswered

4. **Efficiency** (15%): How fast was resolution? (CES)
   - 8-10: Resolved in 1-2 turns
   - 4-7: Resolved in 3-5 turns
   - 1-3: Too many turns / not resolved

5. **Boundary Handling** (10%): Clear about scope and limits?
   - 8-10: Clearly communicates what it can/cannot do
   - 4-7: Adequate boundary communication
   - 1-3: Unclear or misleading about capabilities

**Conversation**:
{conversation}

**Output Example**:
```json
{{
  "task_completion": {{"score": 8, "rationale": "User goal achieved with proper guidance"}},
  "response_quality": {{"score": 9, "rationale": "Accurate and relevant responses"}},
  "user_experience": {{"score": 8, "rationale": "Professional and friendly tone"}},
  "efficiency": {{"score": 7, "rationale": "Resolved in 3 turns"}},
  "boundary_handling": {{"score": 9, "rationale": "Clear about capabilities"}},
  "summary": "Agent performed well overall with accurate responses and good communication."
}}
```

Follow this exact format. Score 1-10, rationale <200 chars, summary <300 chars."""

    def __init__(self, logger: Logger, nlp_service: NLPService) -> None:
        self._logger = logger
        self._nlp_service = nlp_service
        self._generator: Optional[SchematicGenerator[SessionEvaluationSchema]] = None

    async def _get_generator(self) -> SchematicGenerator[SessionEvaluationSchema]:
        if self._generator is None:
            self._generator = await self._nlp_service.get_schematic_generator(
                SessionEvaluationSchema
            )
        return self._generator

    def _format_conversation(self, events: Sequence[Event]) -> str:
        lines = []
        for event in events:
            if event.kind != EventKind.MESSAGE or event.deleted:
                continue
            data = event.data
            if not isinstance(data, dict):
                continue
            message = data.get("message", "")
            role = "U" if event.source == EventSource.CUSTOMER else "A"
            lines.append(f"[{role}] {message}")
        return "\n".join(lines)

    async def evaluate(
        self,
        session_id: str,
        events: Sequence[Event],
        agent_context: Optional[AgentContext] = None,
    ) -> SessionEvaluationResult:
        """Evaluate session with five-dimension model."""
        stats = analyze_messages(events)
        
        if stats.total_messages < 2:
            raise ValueError("Need at least 2 messages")
        
        conversation = self._format_conversation(events)
        if not conversation.strip():
            raise ValueError("No valid messages")
        
        prompt = self.PROMPT.format(
            agent_context=agent_context.to_prompt() if agent_context else "General assistant",
            stats=stats.to_context(),
            conversation=conversation,
        )
        
        # Output final prompt for debugging
        self._logger.debug(f"🍎 Evaluation prompt for session {session_id}:\n{prompt}")
        
        generator = await self._get_generator()
        
        # Retry on parse error (max 2 attempts)
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                result: SchematicGenerationResult[SessionEvaluationSchema] = await generator.generate(
                    prompt=prompt,
                    hints={"temperature": 0.2 + attempt * 0.1},  # Slightly increase temp on retry
                )
                break
            except Exception as e:
                last_error = e
                self._logger.warning(f"🍎 Eval attempt {attempt + 1} failed: {e}")
        else:
            raise RuntimeError(f"🍎 Evaluation failed after 2 attempts: {last_error}")
        
        usage = result.info.usage
        token_usage = TokenUsage(
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )
        
        e = result.content
        self._logger.info(
            f"🍎 Eval {session_id}: {e.score} "
            f"[T:{e.task_completion.score} Q:{e.response_quality.score} "
            f"U:{e.user_experience.score} E:{e.efficiency.score} B:{e.boundary_handling.score}]"
        )
        
        return SessionEvaluationResult(
            session_id=session_id,
            evaluation=result.content,
            stats=stats,
            token_usage=token_usage,
        )
