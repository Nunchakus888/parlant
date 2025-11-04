from abc import ABC, abstractmethod
import os
from typing import Any, Mapping, Sequence
from typing_extensions import override


class OptimizationPolicy(ABC):
    """An interface for defining optimization policies for the engine."""

    @abstractmethod
    def use_embedding_cache(
        self,
        hints: Mapping[str, Any] = {},
    ) -> bool:
        """Determines whether to use the embedding cache."""
        ...

    @abstractmethod
    def get_guideline_matching_batch_size(
        self,
        guideline_count: int,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Gets the batch size for guideline matching."""
        ...

    @abstractmethod
    def get_message_generation_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]: ...

    @abstractmethod
    def get_guideline_matching_batch_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        """Gets the retry temperatures (and number of generation attempts) for a guideline matching batch."""
        ...

    @abstractmethod
    def get_response_analysis_batch_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        """Gets the retry temperatures (and number of generation attempts) for a response analysis batch."""
        ...

    @abstractmethod
    def get_tool_calling_batch_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        """Gets the retry temperatures (and number of generation attempts) for a tool calling batch."""
        ...

    @abstractmethod
    def get_guideline_proposition_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        """Gets the retry temperatures (and number of generation attempts) for guideline propositions."""
        ...

    @abstractmethod
    def get_max_tool_evaluation_attempts(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Gets the maximum number of evaluation attempts for tool calling.
        
        This controls how many times a tool evaluation will be retried on failure.
        Can be configured via MAX_TOOL_EVALUATION_ATTEMPTS environment variable.
        """
        ...

    @abstractmethod
    def get_max_guideline_proposition_attempts(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Gets the maximum number of attempts for guideline propositions.
        
        This controls how many times a guideline proposition (like GuidelineContinuousProposer,
        AgentIntentionProposer, etc.) will be retried on failure (including timeouts).
        Can be configured via MAX_GUIDELINE_PROPOSITION_ATTEMPTS environment variable.
        """
        ...

    @abstractmethod
    def get_max_history_for_tool_calls(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Gets the maximum number of interaction events to include for tool calling.
        
        Tool calling primarily focuses on recent interactions and doesn't require full
        conversation history. Limiting history significantly reduces token consumption.
        Can be configured via MAX_HISTORY_FOR_TOOL_CALLS environment variable.
        """
        ...

    @abstractmethod
    def get_max_history_for_message_generation(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Gets the maximum number of interaction events to include for message generation.
        
        Message generation needs more context than tool calling but should still be limited
        to prevent unbounded token growth in long conversations.
        Can be configured via MAX_HISTORY_FOR_MESSAGE_GENERATION environment variable.
        """
        ...

    @abstractmethod
    def get_max_history_for_guideline_matching(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Gets the maximum number of interaction events for guideline matching.
        
        Guideline matching needs moderate context to evaluate applicability.
        Can be configured via MAX_HISTORY_FOR_GUIDELINE_MATCHING environment variable.
        """
        ...


class BasicOptimizationPolicy(OptimizationPolicy):
    """A basic optimization policy that defines default behaviors for the engine."""

    @override
    def use_embedding_cache(
        self,
        hints: Mapping[str, Any] = {},
    ) -> bool:
        return True

    @override
    def get_guideline_matching_batch_size(
        self,
        guideline_count: int,
        hints: Mapping[str, Any] = {},
    ) -> int:
        return 10
        if guideline_count <= 10:
            return 1
        elif guideline_count <= 20:
            return 2
        elif guideline_count <= 30:
            return 3
        else:
            return 5

    @override
    def get_message_generation_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        if hints.get("type") == "canned_response-selection":
            return [
                0.1,
                0.05,
                0.2,
            ]

        elif hints.get("type") == "follow-up_canned_response-selection":
            return [
                0.1,
                0.05,
                0.2,
            ]

        return [
            0.1,
            0.3,
            0.5,
        ]

    @override
    def get_guideline_matching_batch_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        return [
            0.15,
            0.3,
            0.1,
        ]

    @override
    def get_response_analysis_batch_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        return [
            0.15,
            0.3,
            0.1,
        ]

    @override
    def get_tool_calling_batch_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        return [
            0.15,
            0.3,
            0.1,
        ]

    @override
    def get_guideline_proposition_retry_temperatures(
        self,
        hints: Mapping[str, Any] = {},
    ) -> Sequence[float]:
        return [
            0.0,
            0.15,
            0.1,
        ]

    @override
    def get_max_tool_evaluation_attempts(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Get max tool evaluation attempts from environment variable.
        
        Defaults to 2 attempts to balance reliability and token cost.
        """
        return int(os.getenv("MAX_TOOL_EVALUATION_ATTEMPTS", "2"))

    @override
    def get_max_guideline_proposition_attempts(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Get max guideline proposition attempts from environment variable.
        
        Defaults to 2 attempts to balance reliability and token cost.
        Guideline propositions include: GuidelineContinuousProposer, AgentIntentionProposer,
        CustomerDependentActionDetector, ToolRunningActionDetector, GuidelineActionProposer.
        
        Setting to 1 may cause issues with timeouts or LLM output errors.
        """
        return int(os.getenv("MAX_GUIDELINE_PROPOSITION_ATTEMPTS", "2"))

    @override
    def get_max_history_for_tool_calls(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Get max history events for tool calling from environment variable.
        
        Defaults to 10 events (~5 rounds of conversation).
        Tool calling focuses on recent interactions for parameter extraction.
        Setting to 0 or negative disables the limit (uses full history).
        """
        return int(os.getenv("MAX_HISTORY_FOR_TOOL_CALLS", "10"))

    @override
    def get_max_history_for_message_generation(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Get max history events for message generation from environment variable.
        
        Defaults to 30 events (~15 rounds of conversation).
        Message generation needs more context than tool calling for coherent responses.
        Setting to 0 or negative disables the limit (uses full history).
        """
        return int(os.getenv("MAX_HISTORY_FOR_MESSAGE_GENERATION", "30"))

    @override
    def get_max_history_for_guideline_matching(
        self,
        hints: Mapping[str, Any] = {},
    ) -> int:
        """Get max history events for guideline matching from environment variable.
        
        Defaults to 10 events (~5 rounds of conversation).
        Guideline matching focuses on recent context to evaluate applicability.
        Setting to 0 or negative disables the limit (uses full history).
        """
        return int(os.getenv("MAX_HISTORY_FOR_GUIDELINE_MATCHING", "10"))
