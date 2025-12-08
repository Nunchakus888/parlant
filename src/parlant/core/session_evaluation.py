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
    score: int = Field(..., ge=0, le=10)
    rationale: str = Field(...)  # No length limit - allow any length


class SessionEvaluationSchema(DefaultBaseModel):
    """Five-dimension evaluation (industry-aligned)."""
    
    task_completion: DimensionScore = Field(..., description="Goal achieved? (CSAT)")
    response_quality: DimensionScore = Field(..., description="Accurate & relevant?")
    user_experience: DimensionScore = Field(..., description="Satisfied? (NPS)")
    efficiency: DimensionScore = Field(..., description="Fast resolution? (CES)")
    boundary_handling: DimensionScore = Field(..., description="Proper escalation?")
    summary: str = Field(...)
    
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
        """Agent context for evaluation - simple, no directive labels."""
        name = self.name or "Assistant"
        desc = f" - {self.description}" if self.description else ""
        bg = f"\n{self.background}" if self.background else ""
        return f"{name}{desc}{bg}"


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
    
    # Evaluation prompt - focused on objective conversation quality assessment
    PROMPT = """You are a strict conversation evaluator. Score objectively based on concrete criteria.

**CRITICAL RULES**:
1. Score based ONLY on actual [U]/[A] exchanges shown below
2. Missing [A] responses = automatic 0-3 scores across all dimensions
3. Use the EXACT scoring criteria - do NOT adjust based on subjective feelings
4. Each score MUST have clear evidence from the conversation
5. Be consistent: similar conversations = similar scores

**Agent Context**: {agent_context}

**Stats**: {stats}

**Markers** (these are NORMAL operations, not errors):
- ho000001: Human handover request (appropriate escalation, not a failure)
- un000001: Knowledge boundary acknowledgment (honest limitation, not a failure)
- Unanswered: No agent response (THIS IS A FAILURE - score 1-3)

---

**SCORING DIMENSIONS** (Use these EXACT criteria):

**1. Task Completion (30% weight)**
YOU MUST count the conversation turns and check if goal was achieved:
- 9-10: User's explicit goal fully achieved in conversation (must see resolution)
- 7-8: User's goal mostly achieved, or appropriately escalated with clear next steps
- 5-6: Made progress but goal not completed, OR unclear if goal was achieved
- 3-4: Minimal progress, user still has unresolved needs
- 1-2: Failed to address user's need, OR no agent response (Unanswered)

**2. Response Quality (25% weight)**
Check EVERY agent response for accuracy and relevance:
- 9-10: ALL responses accurate, relevant, and complete (no errors found)
- 7-8: Most responses accurate (1 minor error acceptable)
- 5-6: Some accurate responses, but noticeable gaps or errors (2-3 errors)
- 3-4: Multiple inaccuracies or irrelevant responses (4+ errors)
- 1-2: Wrong information, OR no agent response (Unanswered)

**3. User Experience (20% weight)**
Evaluate tone, clarity, and user satisfaction signals:
- 9-10: User explicitly satisfied (e.g., "thanks", "perfect"), professional tone throughout
- 7-8: Positive interaction, helpful tone, no user frustration detected
- 5-6: Neutral interaction, acceptable but not engaging
- 3-4: User shows frustration (e.g., repeating, asking "why?"), or impersonal tone
- 1-2: User clearly frustrated/dissatisfied, OR no agent response (Unanswered)

**4. Efficiency (15% weight)**
COUNT actual turns - this is objective:
- 9-10: Resolved in 1-2 user turns (count [U] messages)
- 7-8: Resolved in 3-4 user turns
- 5-6: Resolved in 5-7 user turns
- 3-4: Took 8+ turns but eventually resolved
- 1-2: Not resolved after many turns, OR no agent response (Unanswered)

**5. Boundary Handling (10% weight)**
Check if agent communicated capabilities clearly:
- 9-10: Proactively clarified capabilities/limitations when relevant
- 7-8: Adequately communicated boundaries when asked
- 5-6: Basic communication, could be clearer
- 3-4: Vague or confusing about capabilities
- 1-2: Misleading about capabilities, OR no agent response (Unanswered)

---

**Conversation**:
{conversation}

---

**OUTPUT FORMAT** (you MUST follow this exact JSON structure):
All rationales: max 100 chars. Summary: max 150 chars.

```json
{{
  "task_completion": {{"score": <1-10>, "rationale": "<explain with evidence from conversation>"}},
  "response_quality": {{"score": <1-10>, "rationale": "<count errors/accuracies found>"}},
  "user_experience": {{"score": <1-10>, "rationale": "<cite user signals from conversation>"}},
  "efficiency": {{"score": <1-10>, "rationale": "<count turns: resolved in X turns>"}},
  "boundary_handling": {{"score": <1-10>, "rationale": "<cite boundary communication>"}},
  "summary": "<Overall assessment in 1-2 sentences, max 300 chars>"
}}
```

**CONSISTENCY CHECK**: Before responding, ask yourself:
- Did I base scores on the EXACT criteria above?
- Did I count turns objectively for efficiency?
- Did I cite specific evidence from the conversation?
- Would I score a similar conversation the same way?

Provide ONLY the JSON output, no additional text."""

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
        
        if stats.total_messages < 1:
            raise ValueError("Need at least 1 message")
        
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
            f"🍎 Eval {session_id}: 💯 {e.score} "
            f"[T:{e.task_completion.score} Q:{e.response_quality.score} "
            f"U:{e.user_experience.score} E:{e.efficiency.score} B:{e.boundary_handling.score}]"
        )

        
        return SessionEvaluationResult(
            session_id=session_id,
            evaluation=result.content,
            stats=stats,
            token_usage=token_usage,
        )