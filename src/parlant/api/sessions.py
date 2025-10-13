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

from datetime import datetime
from enum import Enum
from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from itertools import chain
from pydantic import Field
from typing import Annotated, Any, Awaitable, Callable, Mapping, Optional, Sequence, Set, TypeAlias, cast


from parlant.api.authorization import AuthorizationPolicy, Operation
from parlant.api.common import GuidelineIdField, ExampleJson, JSONSerializableDTO, apigen_config
from parlant.api.glossary import TermSynonymsField, TermIdPath, TermNameField, TermDescriptionField
from parlant.app_modules.customers import CustomerMetadataUpdateParams
from parlant.app_modules.sessions import Moderation
from parlant.core.agents import AgentId
from parlant.core.agents import AgentId, AgentStore, CompositionMode, Agent
from parlant.core.agent_factory import AgentFactory
from parlant.core.application import Application
from parlant.core.async_utils import Timeout
from parlant.core.common import DefaultBaseModel, ItemNotFoundError
from parlant.core.customers import CustomerId, CustomerStore
from parlant.core.engines.types import UtteranceRationale, UtteranceRequest
from parlant.core.loggers import Logger
from parlant.core.nlp.generation_info import GenerationInfo
from parlant.core.nlp.moderation import ModerationService
from parlant.core.nlp.service import NLPService
from parlant.core.sessions import (
    Event,
    EventId,
    EventKind,
    EventSource,
    MessageEventData,
    MessageGenerationInspection,
    Participant,
    PreparationIteration,
    SessionId,
    SessionListener,
    SessionStatus,
    SessionUpdateParams,
)
from parlant.core.canned_responses import CannedResponseId

API_GROUP = "sessions"


class EventKindDTO(Enum):
    """
    Type of event in a session.

    Represents different types of interactions that can occur within a conversation.
    """

    MESSAGE = "message"
    TOOL = "tool"
    STATUS = "status"
    CUSTOM = "custom"


class EventSourceDTO(Enum):
    """
    Source of an event in the session.

    Identifies who or what generated the event.
    """

    CUSTOMER = "customer"
    CUSTOMER_UI = "customer_ui"
    HUMAN_AGENT = "human_agent"
    HUMAN_AGENT_ON_BEHALF_OF_AI_AGENT = "human_agent_on_behalf_of_ai_agent"
    AI_AGENT = "ai_agent"
    SYSTEM = "system"


class ModerationDTO(Enum):
    """Content moderation settings."""

    AUTO = "auto"
    PARANOID = "paranoid"
    NONE = "none"


class SessionStatusDTO(Enum):
    """
    Type of status in a session.
    """

    ACKNOWLEDGED = "acknowledged"
    CANCELLED = "cancelled"
    PROCESSING = "processing"
    READY = "ready"
    TYPING = "typing"
    ERROR = "error"


ConsumptionOffsetClientField: TypeAlias = Annotated[
    int,
    Field(
        description="Latest event offset processed by the client",
        examples=[42, 100],
        ge=0,
    ),
]

consumption_offsets_example = {"client": 42}


class ConsumptionOffsetsDTO(
    DefaultBaseModel,
    json_schema_extra={"example": consumption_offsets_example},
):
    """Tracking for message consumption state."""

    client: ConsumptionOffsetClientField | None = None


SessionIdPath: TypeAlias = Annotated[
    SessionId,
    Path(
        description="Unique identifier for the session",
        examples=["sess_123yz"],
    ),
]

SessionAgentIdPath: TypeAlias = Annotated[
    AgentId,
    Path(
        description="Unique identifier for the agent associated with the session.",
        examples=["ag-123Txyz"],
    ),
]

SessionCustomerIdField: TypeAlias = Annotated[
    CustomerId,
    Field(
        description="ID of the customer associated with this session.",
        examples=["cust_123xy"],
    ),
]

SessionCreationUTCField: TypeAlias = Annotated[
    datetime,
    Field(
        description="UTC timestamp of when the session was created",
        examples=["2024-03-24T12:00:00Z"],
    ),
]

SessionTitleField: TypeAlias = Annotated[
    str,
    Field(
        description="Descriptive title for the session",
        examples=["Support inquiry about product X"],
        max_length=200,
    ),
]


class SessionModeDTO(Enum):
    """Defines the reason for the action"""

    AUTO = "auto"
    MANUAL = "manual"


SessionModeField: TypeAlias = Annotated[
    SessionModeDTO,
    Field(
        description="The mode of the session, either 'auto' or 'manual'. In manual mode, events added to a session will not be responded to automatically by the agent.",
        examples=["auto", "manual"],
    ),
]


session_example: ExampleJson = {
    "id": "sess_123yz",
    "agent_id": "ag_123xyz",
    "customer_id": "cust_123xy",
    "creation_utc": "2024-03-24T12:00:00Z",
    "title": "Product inquiry session",
    "mode": "auto",
    "consumption_offsets": consumption_offsets_example,
    "tenant_id": "tenant_001",
}


class SessionDTO(
    DefaultBaseModel,
    json_schema_extra={"example": session_example},
):
    """A session represents an ongoing conversation between an agent and a customer."""

    id: SessionIdPath
    agent_id: SessionAgentIdPath
    customer_id: SessionCustomerIdField
    creation_utc: SessionCreationUTCField
    title: SessionTitleField | None = None
    mode: SessionModeField
    consumption_offsets: ConsumptionOffsetsDTO
    tenant_id: Optional[str] = None


SessionCreationParamsCustomerIdField: TypeAlias = Annotated[
    CustomerId | None,
    Field(
        default=None,
        description=" ID of the customer this session belongs to. If not provided, a guest customer will be created.",
        examples=[None, "cust_123xy"],
    ),
]


session_creation_params_example: ExampleJson = {
    "agent_id": "ag_123xyz",
    "customer_id": "cust_123xy",
    "title": "Product inquiry session",
}


class SessionCreationParamsDTO(
    DefaultBaseModel,
    json_schema_extra={"example": session_creation_params_example},
):
    """Parameters for creating a new session."""

    agent_id: SessionAgentIdPath
    customer_id: SessionCreationParamsCustomerIdField = None
    title: SessionTitleField | None = None


message_example = "Hello, I need help with my order"


SessionEventCreationParamsMessageField: TypeAlias = Annotated[
    str,
    Field(
        description="Event payload data, format depends on kind",
        examples=[message_example],
    ),
]

AgentMessageGuidelineActionField: TypeAlias = Annotated[
    str,
    Field(
        description='A single action that explains what to say; i.e. "Tell the customer that you are thinking and will be right back with an answer."',
        examples=[message_example],
    ),
]

event_creation_params_example: ExampleJson = {
    "kind": "message",
    "source": "customer",
    "message": message_example,
}


class AgentMessageGuidelineRationaleDTO(Enum):
    """Defines the rationale for the guideline"""

    UNSPECIFIED = "unspecified"
    BUY_TIME = "buy_time"
    FOLLOW_UP = "follow_up"


class AgentMessageGuidelineDTO(DefaultBaseModel):
    action: AgentMessageGuidelineActionField
    rationale: AgentMessageGuidelineRationaleDTO = AgentMessageGuidelineRationaleDTO.UNSPECIFIED


ParticipantIdDTO = AgentId | CustomerId | None

ParticipantDisplayNameField: TypeAlias = Annotated[
    str,
    Field(
        description="Name to display for the participant",
        examples=["John Doe", "Alice"],
    ),
]


participant_example = {
    "id": "cust_123xy",
    "display_name": "John Doe",
}


class ParticipantDTO(DefaultBaseModel):
    """
    Represents the participant information in a message event.
    """

    id: ParticipantIdDTO = None
    display_name: ParticipantDisplayNameField


class EventCreationParamsDTO(
    DefaultBaseModel,
    json_schema_extra={"example": event_creation_params_example},
):
    """Parameters for creating a new event in a session."""

    kind: EventKindDTO
    source: EventSourceDTO
    message: SessionEventCreationParamsMessageField | None = None
    data: JSONSerializableDTO | None = None
    guidelines: list[AgentMessageGuidelineDTO] | None = None
    participant: ParticipantDTO | None = None
    status: SessionStatusDTO | None = None


EventIdPath: TypeAlias = Annotated[
    EventId,
    Path(
        description="Unique identifier for the event",
        examples=["evt_123xyz"],
    ),
]

EventOffsetField: TypeAlias = Annotated[
    int,
    Field(
        description="Sequential position of the event in the session",
        examples=[0, 1, 2],
        ge=0,
    ),
]

EventCreationUTCField: TypeAlias = Annotated[
    datetime,
    Field(description="UTC timestamp of when the event was created"),
]


EventCorrelationIdField: TypeAlias = Annotated[
    str,
    Field(
        description="ID linking related events together",
        examples=["corr_13xyz"],
    ),
]

event_example: ExampleJson = {
    "id": "evt_123xyz",
    "source": "customer",
    "kind": "message",
    "offset": 0,
    "creation_utc": "2024-03-24T12:00:00Z",
    "correlation_id": "corr_13xyz",
    "data": {
        "message": "Hello, I need help with my account",
        "total_tokens": 100,
        "participant": {"id": "cust_123xy", "display_name": "John Doe"},
    },
}


class EventDTO(
    DefaultBaseModel,
    json_schema_extra={"example": event_example},
):
    """Represents a single event within a session."""

    id: EventIdPath
    source: EventSourceDTO
    kind: EventKindDTO
    offset: EventOffsetField
    creation_utc: EventCreationUTCField
    correlation_id: EventCorrelationIdField
    data: JSONSerializableDTO
    deleted: bool = False

class ChatDataDTO(DefaultBaseModel):
    """Chat data."""  
    generate: EventDTO | None = Field(default=None, description="AI generated event data")
    events: list[EventDTO] | None = Field(default=[], description="Custom events for AI generation process exceptions")
    total_tokens: int = Field(description="Total tokens")

class CapabilityChatDataDTO(DefaultBaseModel):
    """Capability chat data."""
    total_tokens: int = Field(description="Total tokens")
    message: str = Field(description="Message")
    # if kind is message, success is True
    success: bool = Field(description="Success")
    correlation_id: str = Field(description="Correlation ID")
    creation_utc: datetime = Field(description="Creation UTC")
    id: str = Field(description="ID")
    session_id: str = Field(description="Session ID")


# RESTful API Response Wrapper for Chat endpoint
class ChatResponseDTO(DefaultBaseModel):
    """RESTful response wrapper for chat endpoint."""
    
    status: int = Field(description="HTTP status code")
    code: int = Field(description="Business status code")
    message: str = Field(description="Response message")
    data: CapabilityChatDataDTO | None = Field(default=None, description="Chat event data")


chat_response_success_example = {
    "status": 200,
    "code": 0,
    "message": "SUCCESS",
    "data": {
        "generate": event_example,
    }
}

chat_response_error_example = {
    "status": 504,
    "code": 504, 
    "message": "TIMEOUT_ERROR",
    "data": None
}


class ConsumptionOffsetsUpdateParamsDTO(
    DefaultBaseModel,
    json_schema_extra={"example": consumption_offsets_example},
):
    """Parameters for updating consumption offsets."""

    client: ConsumptionOffsetClientField | None = None


session_update_params_example: ExampleJson = {
    "title": "Updated session title",
    "consumption_offsets": {"client": 42},
}


class SessionUpdateParamsDTO(
    DefaultBaseModel,
    json_schema_extra={"example": session_update_params_example},
):
    """Parameters for updating a session."""

    consumption_offsets: ConsumptionOffsetsUpdateParamsDTO | None = None
    title: SessionTitleField | None = None
    mode: SessionModeField | None = None


chat_request_example: ExampleJson = {
    "message": "Hello, I need help with my order",
    "customer_id": "cust_123xy",
    "tenant_id": "xxx",
    "chatbot_id": "xxx",
    "md5_checksum": "xxx",
    "is_preview": False,
    "preview_action_book_ids": [],
    "autofill_params": {},
    "session_title": "",
    "timeout": 60,
}


class ChatRequestDTO(
    DefaultBaseModel,
    json_schema_extra={"example": chat_request_example},
):
    """Parameters for simplified chat endpoint."""
    
    message: Annotated[
        str,
        Field(
            description="The message to send to the AI agent",
            examples=["Hello, I need help", "What can you do?"],
        ),
    ]
    customer_id: CustomerId = Field(
      default=None,
      description="unique identifier for the customer, if not provided, a new customer will be created."
    )
    session_id: Optional[str] = Field(
        default=None,
        description="unique identifier for the session. If not provided, a new session will be created.",
        examples=["ses_123xyz"],
    )
    session_title: Optional[str] = Field(
        default=None,
        description="Title for new sessions. Defaults to 'Chat Session'.",
        examples=["Product Support", "General Inquiry"],
    )
    autofill_params: Optional[dict] = Field(
        default={},
        description="Parameters to automatically fill for the data-connector when they are required by the connector but not provided by the user, and the system has access to them.",
        examples=[{"dialogId": "123", "tenantId": "1bgrs2d1sxef47d23a91x4s6z7y9gt8"}],
    )
    preview_action_book_ids: Optional[list[str]] = Field(
        default=[],
        description="IDs of the actionbooks to preview. If not provided, no actionbooks will be previewed.",
        examples=["act_123xyz", "act_456xyz"],
    )
    is_preview: Optional[bool] = Field(
        default=False,
        description="Whether to preview the actionbooks. If not provided, return all actionbooks.",
        examples=[True, False],
    )
    md5_checksum: Optional[str] = Field(
        default=None,
        description="MD5 checksum of the agent configs. if it changes, the agent will be reload the configs.",
        examples=["1234567890"],
    )
    tenant_id: str = Field(
        default="",
        description="Tenant ID. Required.",
        examples=["tenant_123xyz"],
    )
    chatbot_id: str = Field(
        default="",
        description="Chatbot ID. Required.",
        examples=["chatbot_123xyz"],
    )
    timeout: Optional[int] = Field(
        default=57,
        description="Timeout in seconds for waiting for AI response. Defaults to 60 seconds.",
        examples=[57, 120],
    )


ToolResultDataField: TypeAlias = Annotated[
    JSONSerializableDTO,
    Field(
        description="The json content returned from the tool",
        examples=["yes", '{"answer"="42"}', "[ 1, 1, 2, 3 ]"],
    ),
]


tool_result_metadata_example = {
    "duration_ms": 150,
    "cache_hit": False,
    "rate_limited": False,
}


ToolResultMetadataField: TypeAlias = Annotated[
    Mapping[str, JSONSerializableDTO],
    Field(
        description="A `dict` of the metadata associated with the tool's execution",
        examples=[tool_result_metadata_example],
    ),
]


tool_result_example = {
    "data": {
        "balance": 5000.50,
        "currency": "USD",
        "last_updated": "2024-03-24T12:00:00Z",
    },
    "metadata": tool_result_metadata_example,
}


class ToolResultDTO(
    DefaultBaseModel,
    json_schema_extra={"example": tool_result_example},
):
    """Result from a tool execution."""

    data: ToolResultDataField
    metadata: ToolResultMetadataField


ToolIdField: TypeAlias = Annotated[
    str,
    Field(
        description="Unique identifier for the tool in format 'service_name:tool_name'",
        examples=["email-service:send_email", "payment-service:process_payment"],
    ),
]

tool_call_arguments_example = {"account_id": "acc_123xyz", "currency": "USD"}

ToolCallArgumentsField: TypeAlias = Annotated[
    Mapping[str, JSONSerializableDTO],
    Field(
        description="A `dict` of the arguments to the tool call",
        examples=[tool_call_arguments_example],
    ),
]


tool_call_example = {
    "tool_id": "finance_service:check_balance",
    "arguments": tool_call_arguments_example,
    "result": {
        "data": {
            "balance": 5000.50,
            "currency": "USD",
            "last_updated": "2024-03-24T12:00:00Z",
        },
        "metadata": tool_result_metadata_example,
    },
}


class ToolCallDTO(
    DefaultBaseModel,
    json_schema_extra={"example": tool_call_example},
):
    """Information about a tool call."""

    tool_id: ToolIdField
    arguments: ToolCallArgumentsField
    result: ToolResultDTO


GuidelineMatchConditionField: TypeAlias = Annotated[
    str,
    Field(
        description="The condition for the guideline",
        examples=["when customer asks about their balance"],
    ),
]

GuidelineMatchActionField: TypeAlias = Annotated[
    str,
    Field(
        description="The action for the guideline",
        examples=["check their current balance and provide the amount with currency"],
    ),
]

GuidelineMatchScoreField: TypeAlias = Annotated[
    int,
    Field(
        description="The score for the guideline",
        examples=[95],
    ),
]

GuidelineMatchRationaleField: TypeAlias = Annotated[
    str,
    Field(
        description="The rationale for the guideline",
        examples=["This guideline directly addresses balance inquiries with specific actions"],
    ),
]

guideline_match_example = {
    "guideline_id": "guide_123x",
    "condition": "when customer asks about their balance",
    "action": "check their current balance and provide the amount with currency",
    "score": 95,
    "rationale": "This guideline directly addresses balance inquiries with specific actions",
}


class GuidelineMatchDTO(
    DefaultBaseModel,
    json_schema_extra={"example": guideline_match_example},
):
    """A matched guideline."""

    guideline_id: GuidelineIdField
    condition: GuidelineMatchConditionField
    action: GuidelineMatchActionField
    score: GuidelineMatchScoreField
    rationale: GuidelineMatchRationaleField


ContextVariableIdPath: TypeAlias = Annotated[
    str,
    Path(
        description="Unique identifier for the context variable",
        examples=["var_123xyz"],
    ),
]

ContextVariableNameField: TypeAlias = Annotated[
    str,
    Field(
        description="The name of the context variable",
        examples=["user_preferences", "account_status"],
        min_length=1,
        max_length=100,
    ),
]

ContextVariableDescriptionField: TypeAlias = Annotated[
    str,
    Field(
        description="The description text assigned to this variable",
        examples=["`c` counts the cost of the count cutting costs"],
    ),
]

ContextVariableKeyField: TypeAlias = Annotated[
    str,
    Field(
        description="This is the key which can be used to identify the variable",
        examples=["cool_variable_name", "melupapepkin"],
    ),
]

context_variable_and_value_example = {
    "id": "var_123xyz",
    "name": "AccountBalance",
    "description": "Customer's current account balance and currency",
    "key": "user_123",
    "value": {
        "balance": 5000.50,
        "currency": "USD",
        "last_updated": "2024-03-24T12:00:00Z",
    },
}


class ContextVariableAndValueDTO(
    DefaultBaseModel,
    json_schema_extra={"example": context_variable_and_value_example},
):
    """A context variable and its current value."""

    id: ContextVariableIdPath
    name: ContextVariableNameField
    description: ContextVariableDescriptionField
    key: ContextVariableKeyField
    value: JSONSerializableDTO


UsageInfoInputTokensField: TypeAlias = Annotated[
    int,
    Field(
        description="Amount of token received from user over the session",
        examples=[256],
    ),
]

UsageInfoOutputTokensField: TypeAlias = Annotated[
    int,
    Field(
        description="Amount of token sent to user over the session",
        examples=[128],
    ),
]
usage_info_extra_example = {
    "prompt_tokens": 200,
    "completion_tokens": 128,
}

UsageInfoExtraField: TypeAlias = Annotated[
    Mapping[str, int],
    Field(
        description="Extra data associated with the usage information",
        examples=[usage_info_extra_example],
    ),
]

usage_info_example = {
    "input_tokens": 256,
    "output_tokens": 128,
    "extra": usage_info_extra_example,
}


class UsageInfoDTO(
    DefaultBaseModel,
    json_schema_extra={"example": usage_info_example},
):
    """Token usage information."""

    input_tokens: UsageInfoInputTokensField
    output_tokens: UsageInfoOutputTokensField
    extra: UsageInfoExtraField | None = None


GenerationInfoSchemaNameField: TypeAlias = Annotated[
    str,
    Field(
        description="The name of the schema used for the generation",
        examples=["customer_response_v2"],
    ),
]

GenerationInfoModelField: TypeAlias = Annotated[
    str,
    Field(
        description="Id of the model used for the generation",
        examples=["gpt-4-turbo"],
    ),
]

GenerationInfoDurationField: TypeAlias = Annotated[
    float,
    Field(
        description="Amount of time spent generating",
        examples=[2.5],
    ),
]


generation_info_example = {
    "schema_name": "customer_response_v2",
    "model": "gpt-4-turbo",
    "duration": 2.5,
    "usage": usage_info_example,
}


class GenerationInfoDTO(
    DefaultBaseModel,
    json_schema_extra={"example": generation_info_example},
):
    """Information about a text generation."""

    schema_name: GenerationInfoSchemaNameField
    model: GenerationInfoModelField
    duration: GenerationInfoDurationField
    usage: UsageInfoDTO


MessageGenerationInspectionMessagesField: TypeAlias = Annotated[
    Sequence[str | None],
    Field(
        description="The messages that were generated",
    ),
]


MessageEventDataMessageField: TypeAlias = Annotated[
    str,
    Field(
        description="Text content of the message",
        examples=["Hello, I need help with my order"],
    ),
]

MessageEventDataFlaggedField: TypeAlias = Annotated[
    bool | None,
    Field(
        description="Indicates whether the message was flagged by moderation",
        examples=[True, False, None],
    ),
]

MessageEventDataTagsField: TypeAlias = Annotated[
    Sequence[str] | None,
    Field(
        description="Sequence of tags providing additional context about the message",
        examples=[["greeting", "urgent"], ["support-request"]],
    ),
]

MessageEventDataCannedResponsesField: TypeAlias = Annotated[
    Sequence[CannedResponseId] | None,
    Field(
        description="List of associated canned response references, if any",
        examples=[["frag_123xyz", "frag_789abc"]],
    ),
]

message_event_data_example = {
    "message": "Hello, I need help with my order",
    "participant": participant_example,
    "flagged": False,
    "tags": ["greeting", "help-request"],
    "canned_responses": ["frag_123xyz", "frag_789abc"],
}


class MessageEventDataDTO(
    DefaultBaseModel,
    json_schema_extra={"example": message_event_data_example},
):
    """
    DTO for data carried by a 'message' event.
    """

    message: MessageEventDataMessageField
    participant: ParticipantDTO
    flagged: MessageEventDataFlaggedField = None
    tags: MessageEventDataTagsField = None
    canned_responses: MessageEventDataCannedResponsesField = None


message_generation_inspection_example = {
    "generation": {
        "schema_name": "customer_response_v2",
        "model": "gpt-4-turbo",
        "duration": 2.5,
        "usage": {
            "input_tokens": 256,
            "output_tokens": 128,
            "extra": {"prompt_tokens": 200, "completion_tokens": 128},
        },
    },
    "messages": [
        message_event_data_example,
        None,
        {
            "message": "Based on your request, I can confirm that your order is being processed.",
            "participant": participant_example,
            "flagged": False,
            "tags": ["order-status"],
            "canned_responses": ["frag_987abc"],
        },
    ],
}


class MessageGenerationInspectionDTO(
    DefaultBaseModel,
    json_schema_extra={"example": message_generation_inspection_example},
):
    """Inspection data for message generation."""

    generations: Mapping[str, GenerationInfoDTO]
    messages: Sequence[str | None]


GuidelineMatchingInspectionTotalDurationField: TypeAlias = Annotated[
    float,
    Field(
        description="Amount of time spent matching guidelines",
        examples=[3.5],
    ),
]


GuidelineMatchingInspectionBatchesField: TypeAlias = Annotated[
    Sequence[GenerationInfoDTO],
    Field(
        description="A list of `GenerationInfoDTO` describing the batches of generation executed",
    ),
]


guideline_matching_inspection_example = {
    "total_duration": 3.5,
    "batches": [generation_info_example],
}


class GuidelineMatchingInspectionDTO(
    DefaultBaseModel,
    json_schema_extra={"example": guideline_matching_inspection_example},
):
    """Inspection data for guideline matching."""

    total_duration: GuidelineMatchingInspectionTotalDurationField
    batches: GuidelineMatchingInspectionBatchesField


PreparationIterationGenerationsToolCallsField: TypeAlias = Annotated[
    Sequence[GenerationInfoDTO],
    Field(
        description="A list of `GenerationInfoDTO` describing the executed tool calls",
    ),
]

preparation_iteration_generations_example = {
    "guideline_matching": guideline_matching_inspection_example,
    "tool_calls": [generation_info_example],
}


class PreparationIterationGenerationsDTO(
    DefaultBaseModel,
    json_schema_extra={"example": preparation_iteration_generations_example},
):
    """Generation information for a preparation iteration."""

    guideline_matching: GuidelineMatchingInspectionDTO
    tool_calls: PreparationIterationGenerationsToolCallsField


PreparationIterationGuidelineMatchField: TypeAlias = Annotated[
    Sequence[GuidelineMatchDTO],
    Field(
        description="List of guideline matches used in preparation for this iteration",
    ),
]


PreparationIterationToolCallsField: TypeAlias = Annotated[
    Sequence[ToolCallDTO],
    Field(
        description="List of tool calls made in preparation for this iteration",
    ),
]

term_example = {
    "id": "term_123xyz",
    "name": "balance",
    "description": "The current amount of money in an account",
    "synonyms": ["funds", "account balance", "available funds"],
}


class PreparationIterationTermDTO(
    DefaultBaseModel,
    json_schema_extra={"example": term_example},
):
    """A term participating in the preparation for an iteration."""

    id: TermIdPath
    name: TermNameField
    description: TermDescriptionField
    synonyms: TermSynonymsField


PreparationIterationTermsField: TypeAlias = Annotated[
    Sequence[PreparationIterationTermDTO],
    Field(
        description="List of terms participating in the preparation for this iteration",
    ),
]


PreparationIterationContextVariablesField: TypeAlias = Annotated[
    Sequence[ContextVariableAndValueDTO],
    Field(
        description="List of context variables (and their values) that participated in the preparation for this iteration",
    ),
]

preparation_iteration_example = {
    "generations": preparation_iteration_generations_example,
    "guideline_matches": [guideline_match_example],
    "tool_calls": [tool_call_example],
    "terms": [
        {
            "id": "term_123xyz",
            "name": "balance",
            "description": "The current amount of money in an account",
            "synonyms": ["funds", "account balance", "available funds"],
        }
    ],
    "context_variables": [context_variable_and_value_example],
}


class PreparationIterationDTO(
    DefaultBaseModel,
    json_schema_extra={"example": preparation_iteration_example},
):
    """Information about a preparation iteration."""

    generations: PreparationIterationGenerationsDTO
    guideline_matches: PreparationIterationGuidelineMatchField
    tool_calls: PreparationIterationToolCallsField
    terms: PreparationIterationTermsField
    context_variables: PreparationIterationContextVariablesField


EventTraceToolCallsField: TypeAlias = Annotated[
    Sequence[ToolCallDTO],
    Field(
        description="List of tool calls made for the traced event",
    ),
]

EventTraceMessageGenerationsField: TypeAlias = Annotated[
    Sequence[MessageGenerationInspectionDTO],
    Field(
        description="List of message generations made for the traced event",
    ),
]

EventTracePreparationIterationsField: TypeAlias = Annotated[
    Sequence[PreparationIterationDTO],
    Field(
        description="List of preparation iterations made for the traced event",
    ),
]

event_trace_example = {
    "tool_calls": [tool_call_example],
    "message_generations": [message_generation_inspection_example],
    "preparation_iterations": [preparation_iteration_example],
}


class EventTraceDTO(
    DefaultBaseModel,
    json_schema_extra={"example": event_trace_example},
):
    """Trace information for an event."""

    tool_calls: EventTraceToolCallsField
    message_generations: EventTraceMessageGenerationsField
    preparation_iterations: EventTracePreparationIterationsField


event_inspection_example = {
    "session_id": "sess_123yz",
    "event": event_example,
    "trace": event_trace_example,
}


class EventInspectionResult(
    DefaultBaseModel,
    json_schema_extra={"example": event_inspection_example},
):
    """Result of inspecting an event."""

    session_id: SessionIdPath
    event: EventDTO
    trace: EventTraceDTO | None = None


def event_to_dto(event: Event) -> EventDTO:
    return EventDTO(
        id=event.id,
        source=_event_source_to_event_source_dto(event.source),
        kind=_event_kind_to_event_kind_dto(event.kind),
        offset=event.offset,
        creation_utc=event.creation_utc,
        correlation_id=event.correlation_id,
        data=cast(JSONSerializableDTO, event.data),
        deleted=event.deleted,
    )


def generation_info_to_dto(gi: GenerationInfo) -> GenerationInfoDTO:
    return GenerationInfoDTO(
        schema_name=gi.schema_name,
        model=gi.model,
        duration=gi.duration,
        usage=UsageInfoDTO(
            input_tokens=gi.usage.input_tokens,
            output_tokens=gi.usage.output_tokens,
            extra=gi.usage.extra,
        ),
    )


def participant_to_dto(participant: Participant) -> ParticipantDTO:
    return ParticipantDTO(
        id=participant["id"],
        display_name=participant["display_name"],
    )


def message_generation_inspection_to_dto(
    m: MessageGenerationInspection,
) -> MessageGenerationInspectionDTO:
    return MessageGenerationInspectionDTO(
        generations={
            name: generation_info_to_dto(generation) for name, generation in m.generations.items()
        },
        messages=[message for message in m.messages if message is not None],
    )


def preparation_iteration_to_dto(iteration: PreparationIteration) -> PreparationIterationDTO:
    return PreparationIterationDTO(
        generations=PreparationIterationGenerationsDTO(
            guideline_matching=GuidelineMatchingInspectionDTO(
                total_duration=iteration.generations.guideline_matching.total_duration,
                batches=[
                    generation_info_to_dto(generation)
                    for generation in iteration.generations.guideline_matching.batches
                ],
            ),
            tool_calls=[
                generation_info_to_dto(generation)
                for generation in iteration.generations.tool_calls
            ],
        ),
        guideline_matches=[
            GuidelineMatchDTO(
                guideline_id=match["guideline_id"],
                condition=match["condition"],
                action=match["action"],
                score=match["score"],
                rationale=match["rationale"],
            )
            for match in iteration.guideline_matches
        ],
        tool_calls=[
            ToolCallDTO(
                tool_id=tool_call["tool_id"],
                arguments=cast(Mapping[str, JSONSerializableDTO], tool_call["arguments"]),
                result=ToolResultDTO(
                    data=cast(JSONSerializableDTO, tool_call["result"]["data"]),
                    metadata=cast(
                        Mapping[str, JSONSerializableDTO], tool_call["result"]["metadata"]
                    ),
                ),
            )
            for tool_call in iteration.tool_calls
        ],
        terms=[
            PreparationIterationTermDTO(
                id=term["id"],
                name=term["name"],
                description=term["description"],
                synonyms=term["synonyms"],
            )
            for term in iteration.terms
        ],
        context_variables=[
            ContextVariableAndValueDTO(
                id=cv["id"],
                name=cv["name"],
                description=cv["description"] or "",
                key=cv["key"],
                value=cast(JSONSerializableDTO, cv["value"]),
            )
            for cv in iteration.context_variables
        ],
    )


AllowGreetingQuery: TypeAlias = Annotated[
    bool,
    Query(
        description="Whether to allow the agent to send an initial greeting",
    ),
]

AgentIdQuery: TypeAlias = Annotated[
    AgentId,
    Query(
        description="Unique identifier of the agent",
        examples=["ag_123xyz"],
    ),
]

CustomerIdQuery: TypeAlias = Annotated[
    CustomerId,
    Query(
        description="Unique identifier of the customers",
        examples=["cust_123xy"],
    ),
]

ModerationQuery: TypeAlias = Annotated[
    ModerationDTO,
    Query(
        description="Content moderation level for the event",
    ),
]

MinOffsetQuery: TypeAlias = Annotated[
    int,
    Query(
        description="Only return events with offset >= this value",
        examples=[0, 42],
    ),
]

CorrelationIdQuery: TypeAlias = Annotated[
    str,
    Query(
        description="ID linking related events together",
        examples=["corr_13xyz"],
    ),
]

KindsQuery: TypeAlias = Annotated[
    str,
    Query(
        description="If set, only list events of the specified kinds (separated by commas)",
        examples=["message,tool", "message,status"],
    ),
]


async def _ensure_session_and_customer(
    params: ChatRequestDTO,
    app: Application,
    logger: Logger,
    agent_creator: Callable[[ChatRequestDTO], Awaitable[Agent]],
) -> tuple[Any, Any, AgentId]:
    """
    Args:
        params: The parameters for the chat request
        app: Application instance for creating sessions
        logger: Logger instance for logging
        agent_creator: Function to create agents for customers
        
    Returns:
        Tuple of (session, customer, agent_id) ready for chat interaction
    """
    
    # Extract parameters
    customer_id = params.customer_id
    session_id = params.session_id
    md5_checksum = params.md5_checksum
    agent_id = params.chatbot_id

    try:
        customer = await app.customers.read(customer_id)
        logger.info(f"👤 Customer found: {customer_id}")
    except ItemNotFoundError as e:
        customer = await app.customers.create(
            id=customer_id,
            name=None,
            extra={},
            tags=None,
        )
        logger.info(f"✅ created new customer: {customer.id}")
    
    try:
        agent = await app.agents.read(agent_id)

        if not md5_checksum or agent.metadata.get('md5_checksum') != md5_checksum:
            logger.info(f"🔄 MD5 checksum changed from {agent.metadata.get('md5_checksum')} to {md5_checksum}, updating agent...")

            await app.delete_agent_cascade(agent_id)
            logger.info(f"✅ deleted agent: {agent_id}")
            agent = await agent_creator(params)
            logger.info(f"✅ created new agent: {agent.id}")
        else:
          logger.info(f"🤖 🎯🎯 Agent found: {agent}")

    except ItemNotFoundError as e:
        agent = await agent_creator(params)
        logger.info(f"✅ created new agent: {agent.id}")

    try:
        session = await app.sessions.read(session_id)

        session = await app.sessions.update(
            session_id=session_id,
            params=SessionUpdateParams(
              agent_id=agent.id,
              customer_id=customer_id,
            )
        )
        logger.info(f"✅ updated session to the latest status: {session}")

    except ItemNotFoundError as e:
        session = await app.sessions.create(
            session_id=session_id,
            customer_id=customer_id,
            agent_id=agent.id,
            allow_greeting=False,
            tenant_id=params.tenant_id,
        )
        logger.info(f"✅ created new session: {session.id}")

    return session, customer, agent.id


def _get_jailbreak_moderation_service(logger: Logger) -> ModerationService:
    from parlant.adapters.nlp.lakera import LakeraGuard

    return LakeraGuard(logger)


def agent_message_guideline_dto_to_utterance_request(
    guideline: AgentMessageGuidelineDTO,
) -> UtteranceRequest:
    rationale_to_reason = {
        AgentMessageGuidelineRationaleDTO.UNSPECIFIED: UtteranceRationale.UNSPECIFIED,
        AgentMessageGuidelineRationaleDTO.BUY_TIME: UtteranceRationale.BUY_TIME,
        AgentMessageGuidelineRationaleDTO.FOLLOW_UP: UtteranceRationale.FOLLOW_UP,
    }

    return UtteranceRequest(
        action=guideline.action,
        rationale=rationale_to_reason[guideline.rationale],
    )


def _event_kind_dto_to_event_kind(dto: EventKindDTO) -> EventKind:
    if kind := {
        EventKindDTO.MESSAGE: EventKind.MESSAGE,
        EventKindDTO.TOOL: EventKind.TOOL,
        EventKindDTO.STATUS: EventKind.STATUS,
        EventKindDTO.CUSTOM: EventKind.CUSTOM,
    }.get(dto):
        return kind

    raise ValueError(f"Invalid event kind: {dto}")


def _event_kind_to_event_kind_dto(kind: EventKind) -> EventKindDTO:
    if dto := {
        EventKind.MESSAGE: EventKindDTO.MESSAGE,
        EventKind.TOOL: EventKindDTO.TOOL,
        EventKind.STATUS: EventKindDTO.STATUS,
        EventKind.CUSTOM: EventKindDTO.CUSTOM,
    }.get(kind):
        return dto

    raise ValueError(f"Invalid event kind: {kind}")


def _event_source_dto_to_event_source(dto: EventSourceDTO) -> EventSource:
    if source := {
        EventSourceDTO.CUSTOMER: EventSource.CUSTOMER,
        EventSourceDTO.CUSTOMER_UI: EventSource.CUSTOMER_UI,
        EventSourceDTO.HUMAN_AGENT: EventSource.HUMAN_AGENT,
        EventSourceDTO.HUMAN_AGENT_ON_BEHALF_OF_AI_AGENT: EventSource.HUMAN_AGENT_ON_BEHALF_OF_AI_AGENT,
        EventSourceDTO.AI_AGENT: EventSource.AI_AGENT,
        EventSourceDTO.SYSTEM: EventSource.SYSTEM,
    }.get(dto):
        return source

    raise ValueError(f"Invalid event source: {dto}")


def _event_source_to_event_source_dto(source: EventSource) -> EventSourceDTO:
    if dto := {
        EventSource.CUSTOMER: EventSourceDTO.CUSTOMER,
        EventSource.CUSTOMER_UI: EventSourceDTO.CUSTOMER_UI,
        EventSource.HUMAN_AGENT: EventSourceDTO.HUMAN_AGENT,
        EventSource.HUMAN_AGENT_ON_BEHALF_OF_AI_AGENT: EventSourceDTO.HUMAN_AGENT_ON_BEHALF_OF_AI_AGENT,
        EventSource.AI_AGENT: EventSourceDTO.AI_AGENT,
        EventSource.SYSTEM: EventSourceDTO.SYSTEM,
    }.get(source):
        return dto

    raise ValueError(f"Invalid event source: {source}")


def _moderation_dto_to_moderation(dto: ModerationDTO) -> Moderation:
    if moderation := {
        ModerationDTO.AUTO: Moderation.AUTO,
        ModerationDTO.PARANOID: Moderation.PARANOID,
        ModerationDTO.NONE: Moderation.NONE,
    }.get(dto):
        return moderation

    raise ValueError(f"Invalid moderation: {dto}")


def _participant_dto_to_participant(dto: ParticipantDTO) -> Participant:
    return Participant(
        id=AgentId(dto.id) if dto.id else None,
        display_name=dto.display_name,
    )


def create_router(
    authorization_policy: AuthorizationPolicy,
    app: Application,
    logger: Logger,
    session_listener: SessionListener,
    agent_factory: AgentFactory | None = None,
) -> APIRouter:
    router = APIRouter()

    # Agent factory creates or retrieves agents for customers
    async def _agent_creator(params: ChatRequestDTO) -> Agent:
        if agent_factory:
            
            # 构建AgentConfigRequest
            from app.tools.http_config import AgentConfigRequest
            config_request = AgentConfigRequest(
                tenant_id=params.tenant_id,
                chatbot_id=params.chatbot_id,
                preview=params.is_preview or False,
                action_book_id=params.preview_action_book_ids[0] if params.preview_action_book_ids else None,
                extra_param=params.autofill_params or {},
                md5_checksum=params.md5_checksum
            )
            
            agent = await agent_factory.create_agent_for_customer(config_request)
            return agent
        else:
            raise ValueError("No agent factory provided")
            # logger.info(f"No agent factory provided, using default agent for customer: {customer_id}")
            # agent = await app.agents.create(
            #     name=f"Default Agent for {customer_id}",
            #     description=f"Default agent for customer {customer_id}",
            # )
            # logger.info(f"🤖 Created default agent: {agent.id}")
            # return agent


    async def _get_total_tokens_for_event(session_id: str, correlation_id: str) -> int:
        """获取指定事件的总token消耗"""
        try:
            logger.info(f"🔍 Attempting to read inspection for session_id={session_id}, correlation_id={correlation_id}")
            inspection = await app.sessions._session_store.read_inspection(
                session_id=session_id,
                correlation_id=correlation_id,
            )
            
            total_tokens = inspection.usage_info.total_tokens if inspection.usage_info else 0
            logger.info(f"✅ Successfully read inspection, total_tokens={total_tokens}")
            return total_tokens
        except Exception as e:
            logger.warning(f"❌ Failed to get total tokens for correlation_id {correlation_id}: {e}")
            return 0


    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        operation_id="create_session",
        response_model=SessionDTO,
        responses={
            status.HTTP_201_CREATED: {
                "description": "Session successfully created. Returns the complete session object.",
                "content": {"application/json": {"example": session_example}},
            },
            status.HTTP_422_UNPROCESSABLE_ENTITY: {
                "description": "Validation error in request parameters"
            },
        },
        **apigen_config(group_name=API_GROUP, method_name="create"),
    )
    async def create_session(
        request: Request,
        params: SessionCreationParamsDTO,
        allow_greeting: AllowGreetingQuery = False,
    ) -> SessionDTO:
        """Creates a new session between an agent and customer.

        The session will be initialized with the specified agent and optional customer.
        If no customer_id is provided, a guest customer will be created.
        """
        _ = await app.agents.read(agent_id=params.agent_id)

        logger.info(f"👤 Creating session, customer: {params.customer_id}, agent: {params.agent_id}, read_agent: {_}")


        if params.customer_id:
            await authorization_policy.authorize(
                request=request, operation=Operation.CREATE_CUSTOMER_SESSION
            )

        else:
            await authorization_policy.authorize(
                request=request, operation=Operation.CREATE_GUEST_SESSION
            )

        session = await app.sessions.create(
            customer_id=params.customer_id or CustomerId.GUEST_ID,
            agent_id=params.agent_id,
            title=params.title,
            allow_greeting=allow_greeting,
            tenant_id=params.tenant_id,
        )

        return SessionDTO(
            id=session.id,
            agent_id=session.agent_id,
            customer_id=session.customer_id,
            creation_utc=session.creation_utc,
            consumption_offsets=ConsumptionOffsetsDTO(client=session.consumption_offsets["client"]),
            title=session.title,
            mode=SessionModeDTO(session.mode),
            tenant_id=session.tenant_id,
        )

    @router.get(
        "/{session_id}",
        operation_id="read_session",
        response_model=SessionDTO,
        responses={
            status.HTTP_200_OK: {
                "description": "Session details successfully retrieved",
                "content": {"application/json": {"example": session_example}},
            },
            status.HTTP_404_NOT_FOUND: {"description": "Session not found"},
        },
        **apigen_config(group_name=API_GROUP, method_name="retrieve"),
    )
    async def read_session(
        request: Request,
        session_id: SessionIdPath,
    ) -> SessionDTO:
        """Retrieves details of a specific session by ID."""
        await authorization_policy.authorize(request=request, operation=Operation.READ_SESSION)

        session = await app.sessions.read(session_id=session_id)

        return SessionDTO(
            id=session.id,
            agent_id=session.agent_id,
            creation_utc=session.creation_utc,
            title=session.title,
            customer_id=session.customer_id or app.customers.GUEST_ID,
            consumption_offsets=ConsumptionOffsetsDTO(
                client=session.consumption_offsets["client"],
            ),
            mode=SessionModeDTO(session.mode),
            tenant_id=session.tenant_id,
        )

    @router.get(
        "",
        operation_id="list_sessions",
        response_model=Sequence[SessionDTO],
        responses={
            status.HTTP_200_OK: {
                "description": "List of all matching sessions",
                "content": {"application/json": {"example": [session_example]}},
            },
            status.HTTP_422_UNPROCESSABLE_ENTITY: {
                "description": "Validation error in request parameters"
            },
        },
        **apigen_config(group_name=API_GROUP, method_name="list"),
    )
    async def list_sessions(
        request: Request,
        agent_id: AgentIdQuery | None = None,
        customer_id: CustomerIdQuery | None = None,
    ) -> Sequence[SessionDTO]:
        """Lists all sessions matching the specified filters.

        Can filter by agent_id and/or customer_id. Returns all sessions if no
        filters are provided."""
        await authorization_policy.authorize(request=request, operation=Operation.LIST_SESSIONS)

        sessions = await app.sessions.find(
            agent_id=agent_id,
            customer_id=customer_id,
        )

        return [
            SessionDTO(
                id=s.id,
                agent_id=s.agent_id,
                creation_utc=s.creation_utc,
                title=s.title,
                customer_id=s.customer_id or app.customers.GUEST_ID,
                consumption_offsets=ConsumptionOffsetsDTO(
                    client=s.consumption_offsets["client"],
                ),
                mode=SessionModeDTO(s.mode),
                tenant_id=s.tenant_id,
            )
            for s in sessions
        ]

    @router.delete(
        "/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        operation_id="delete_session",
        responses={
            status.HTTP_204_NO_CONTENT: {"description": "Session successfully deleted"},
            status.HTTP_404_NOT_FOUND: {"description": "Session not found"},
        },
        **apigen_config(group_name=API_GROUP, method_name="delete"),
    )
    async def delete_session(
        request: Request,
        session_id: SessionIdPath,
    ) -> None:
        """Deletes a session and all its associated events.

        The operation is idempotent - deleting a non-existent session will return 404."""
        await authorization_policy.authorize(request=request, operation=Operation.DELETE_SESSION)

        await app.sessions.delete(session_id=session_id)

    @router.delete(
        "",
        status_code=status.HTTP_204_NO_CONTENT,
        operation_id="delete_sessions",
        responses={
            status.HTTP_204_NO_CONTENT: {
                "description": "All matching sessions successfully deleted"
            },
            status.HTTP_422_UNPROCESSABLE_ENTITY: {
                "description": "Validation error in request parameters"
            },
        },
        **apigen_config(group_name=API_GROUP, method_name="delete_many"),
    )
    async def delete_sessions(
        request: Request,
        agent_id: AgentIdQuery | None = None,
        customer_id: CustomerIdQuery | None = None,
    ) -> None:
        """Deletes all sessions matching the specified filters.

        Can filter by agent_id and/or customer_id. Will delete all sessions if no
        filters are provided."""
        await authorization_policy.authorize(request=request, operation=Operation.DELETE_SESSIONS)

        sessions = await app.sessions.find(
            agent_id=agent_id,
            customer_id=customer_id,
        )

        for s in sessions:
            await app.sessions.delete(s.id)

    @router.patch(
        "/{session_id}",
        operation_id="update_session",
        responses={
            status.HTTP_200_OK: {"description": "Session successfully updated"},
            status.HTTP_404_NOT_FOUND: {"description": "Session not found"},
            status.HTTP_422_UNPROCESSABLE_ENTITY: {
                "description": "Validation error in update parameters"
            },
        },
        **apigen_config(group_name=API_GROUP, method_name="update"),
    )
    async def update_session(
        request: Request,
        session_id: SessionIdPath,
        params: SessionUpdateParamsDTO,
    ) -> SessionDTO:
        """Updates an existing session's attributes.

        Only provided attributes will be updated; others remain unchanged."""
        await authorization_policy.authorize(request=request, operation=Operation.UPDATE_SESSION)

        async def from_dto(dto: SessionUpdateParamsDTO) -> SessionUpdateParams:
            params: SessionUpdateParams = {}

            if dto.consumption_offsets:
                session = await app.sessions.read(session_id)

                if dto.consumption_offsets.client:
                    params["consumption_offsets"] = {
                        **session.consumption_offsets,
                        "client": dto.consumption_offsets.client,
                    }

            if dto.title:
                params["title"] = dto.title

            if dto.mode:
                params["mode"] = dto.mode.value

            return params

        session = await app.sessions.update(session_id=session_id, params=await from_dto(params))

        return SessionDTO(
            id=session.id,
            agent_id=session.agent_id,
            creation_utc=session.creation_utc,
            title=session.title,
            customer_id=session.customer_id,
            consumption_offsets=ConsumptionOffsetsDTO(
                client=session.consumption_offsets["client"],
            ),
            mode=SessionModeDTO(session.mode),
            tenant_id=session.tenant_id,
        )

    @router.post(
        "/{session_id}/events",
        status_code=status.HTTP_201_CREATED,
        operation_id="create_event",
        response_model=EventDTO,
        responses={
            status.HTTP_201_CREATED: {
                "description": "Event successfully created",
                "content": {"application/json": {"example": event_example}},
            },
            status.HTTP_404_NOT_FOUND: {"description": "Session not found"},
            status.HTTP_422_UNPROCESSABLE_ENTITY: {
                "description": "Validation error in event parameters"
            },
        },
        **apigen_config(group_name=API_GROUP, method_name="create_event"),
    )
    async def create_event(
        request: Request,
        session_id: SessionIdPath,
        params: EventCreationParamsDTO,
        moderation: ModerationQuery = ModerationDTO.NONE,
    ) -> EventDTO:
        """Creates a new event in the specified session.

        Currently supports creating message events from customer and human agent sources."""

        if params.kind == EventKindDTO.MESSAGE:
            if params.source == EventSourceDTO.CUSTOMER:
                await authorization_policy.authorize(
                    request=request, operation=Operation.CREATE_CUSTOMER_EVENT
                )
                return await _add_customer_message(session_id, params, moderation)
            elif params.source == EventSourceDTO.AI_AGENT:
                await authorization_policy.authorize(
                    request=request, operation=Operation.CREATE_AGENT_EVENT
                )
                return await _add_agent_message(session_id, params)
            elif params.source == EventSourceDTO.HUMAN_AGENT:
                await authorization_policy.authorize(
                    request=request,
                    operation=Operation.CREATE_HUMAN_AGENT_EVENT,
                )
                return await _add_human_agent_message(session_id, params)
            elif params.source == EventSourceDTO.HUMAN_AGENT_ON_BEHALF_OF_AI_AGENT:
                await authorization_policy.authorize(
                    request=request,
                    operation=Operation.CREATE_HUMAN_AGENT_ON_BEHALF_OF_AI_AGENT_EVENT,
                )
                return await _add_human_agent_message_on_behalf_of_ai_agent(session_id, params)
            else:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail='Only "customer", "human_agent", and "human_agent_on_behalf_of_ai_agent" sources are supported for direct posting.',
                )

        elif params.kind == EventKindDTO.CUSTOM:
            await authorization_policy.authorize(
                request=request, operation=Operation.CREATE_CUSTOM_EVENT
            )
            return await _add_custom_event(session_id, params)

        elif params.kind == EventKindDTO.STATUS:
            await authorization_policy.authorize(
                request=request, operation=Operation.CREATE_STATUS_EVENT
            )
            return await _add_status_event(session_id, params)

        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only message, custom and status events can currently be added manually",
            )

    async def _add_status_event(
        session_id: SessionIdPath,
        params: EventCreationParamsDTO,
    ) -> EventDTO:
        def status_dto_to_status(dto: SessionStatusDTO) -> SessionStatus:
            match dto:
                case SessionStatusDTO.ACKNOWLEDGED:
                    return "acknowledged"
                case SessionStatusDTO.CANCELLED:
                    return "cancelled"
                case SessionStatusDTO.PROCESSING:
                    return "processing"
                case SessionStatusDTO.READY:
                    return "ready"
                case SessionStatusDTO.TYPING:
                    return "typing"
                case SessionStatusDTO.ERROR:
                    return "error"

        if params.status is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail='Missing "status" field for status event',
            )

        raw_data = params.data or {}
        if not isinstance(raw_data, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail='Status event "data" must be a JSON object',
            )

        event = await app.sessions.create_status_event(
            session_id=session_id,
            status=status_dto_to_status(params.status),
            data=raw_data,
            source=_event_source_dto_to_event_source(params.source),
        )

        return event_to_dto(event)

    async def _add_customer_message(
        session_id: SessionIdPath,
        params: EventCreationParamsDTO,
        moderation: ModerationDTO = ModerationDTO.NONE,
    ) -> EventDTO:
        if not params.message:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing 'message' field for event",
            )

        event = await app.sessions.create_customer_message(
            session_id=session_id,
            moderation=_moderation_dto_to_moderation(moderation),
            message=params.message,
            source=EventSource.CUSTOMER,
            trigger_processing=True,
        )

        return event_to_dto(event)

    async def _add_agent_message(
        session_id: SessionIdPath,
        params: EventCreationParamsDTO,
    ) -> EventDTO:
        if params.message:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="If you add an agent message, you cannot specify what the message will be, as it will be auto-generated by the agent.",
            )

        if params.guidelines:
            requests = [
                agent_message_guideline_dto_to_utterance_request(a) for a in params.guidelines
            ]
            event = await app.sessions.utter(session_id, requests)
            return event_to_dto(event)
        else:
            event = await app.sessions.process(session_id)
            return event_to_dto(event)

    async def _add_human_agent_message(
        session_id: SessionIdPath,
        params: EventCreationParamsDTO,
    ) -> EventDTO:
        if not params.message:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing 'message' field for event",
            )
        if not params.participant or not params.participant.display_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing 'participant' with 'display_name' for human agent message",
            )

        event = await app.sessions.create_human_agent_message_event(
            session_id=session_id,
            message=params.message,
            participant=_participant_dto_to_participant(params.participant),
        )

        return event_to_dto(event)

    async def _add_human_agent_message_on_behalf_of_ai_agent(
        session_id: SessionIdPath,
        params: EventCreationParamsDTO,
    ) -> EventDTO:
        if not params.message:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing 'data' field for message",
            )

        event = await app.sessions.create_human_agent_on_behalf_of_ai_agent_message_event(
            session_id=session_id,
            message=params.message,
        )

        return EventDTO(
            id=event.id,
            source=_event_source_to_event_source_dto(event.source),
            kind=_event_kind_to_event_kind_dto(event.kind),
            offset=event.offset,
            creation_utc=event.creation_utc,
            correlation_id=event.correlation_id,
            data=cast(JSONSerializableDTO, event.data),
            deleted=event.deleted,
        )

    async def _add_custom_event(
        session_id: SessionIdPath,
        params: EventCreationParamsDTO,
    ) -> EventDTO:
        if not params.data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing 'data' field for custom event",
            )

        event = await app.sessions.create_event(
            session_id=session_id,
            kind=_event_kind_dto_to_event_kind(params.kind),
            data=params.data,
            source=_event_source_dto_to_event_source(params.source),
            trigger_processing=False,
        )

        return EventDTO(
            id=event.id,
            source=_event_source_to_event_source_dto(event.source),
            kind=_event_kind_to_event_kind_dto(event.kind),
            offset=event.offset,
            creation_utc=event.creation_utc,
            correlation_id=event.correlation_id,
            data=cast(JSONSerializableDTO, event.data),
            deleted=event.deleted,
        )

    @router.get(
        "/{session_id}/events",
        operation_id="list_events",
        response_model=Sequence[EventDTO],
        responses={
            status.HTTP_200_OK: {
                "description": "List of events matching the specified criteria",
                "content": {"application/json": {"example": [event_example]}},
            },
            status.HTTP_404_NOT_FOUND: {
                "description": "Session not found",
            },
            status.HTTP_422_UNPROCESSABLE_ENTITY: {
                "description": "Validation error in request parameters"
            },
            status.HTTP_504_GATEWAY_TIMEOUT: {
                "description": "Request timeout waiting for new events"
            },
        },
        **apigen_config(group_name=API_GROUP, method_name="list_events"),
    )
    async def list_events(
        request: Request,
        session_id: SessionIdPath,
        min_offset: MinOffsetQuery | None = None,
        source: EventSourceDTO | None = None,
        correlation_id: CorrelationIdQuery | None = None,
        kinds: KindsQuery | None = None,
        wait_for_data: int = 60,
    ) -> Sequence[EventDTO]:
        """Lists events from a session with optional filtering and waiting capabilities.

        This endpoint retrieves events from a specified session and can:
        1. Filter events by their offset, source, type, and correlation ID
        2. Wait for new events to arrive if requested
        3. Return events in chronological order based on their offset

        Notes:
            Long Polling Behavior:
            - When wait_for_data = 0:
                Returns immediately with any existing events that match the criteria
            - When wait_for_data > 0:
                - If new matching events arrive within the timeout period, returns with those events
                - If no new events arrive before timeout, raises 504 Gateway Timeout
                - If matching events already exist, returns immediately with those events
        """
        await authorization_policy.authorize(request=request, operation=Operation.LIST_EVENTS)

        kind_list: Sequence[EventKind] = [
            _event_kind_dto_to_event_kind(EventKindDTO(k))
            for k in (kinds.split(",") if kinds else [])
        ]

        event_source = _event_source_dto_to_event_source(source) if source else None

        if wait_for_data > 0:
            if not await app.sessions.wait_for_update(
                session_id=session_id,
                min_offset=min_offset or 0,
                source=event_source,
                kinds=kind_list,
                correlation_id=correlation_id,
                timeout=Timeout(wait_for_data),
            ):
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Request timed out",
                )

        events = await app.sessions.find_events(
            session_id=session_id,
            min_offset=min_offset or 0,
            source=event_source,
            kinds=kind_list,
            correlation_id=correlation_id,
        )

        return [
            EventDTO(
                id=e.id,
                source=_event_source_to_event_source_dto(e.source),
                kind=_event_kind_to_event_kind_dto(e.kind),
                offset=e.offset,
                creation_utc=e.creation_utc,
                correlation_id=e.correlation_id,
                data=cast(JSONSerializableDTO, e.data),
                deleted=e.deleted,
            )
            for e in events
        ]

    @router.delete(
        "/{session_id}/events",
        status_code=status.HTTP_204_NO_CONTENT,
        operation_id="delete_events",
        responses={
            status.HTTP_204_NO_CONTENT: {"description": "Events successfully deleted"},
            status.HTTP_404_NOT_FOUND: {"description": "Session not found"},
            status.HTTP_422_UNPROCESSABLE_ENTITY: {
                "description": "Validation error in request parameters"
            },
        },
        **apigen_config(group_name=API_GROUP, method_name="delete_events"),
    )
    async def delete_events(
        request: Request,
        session_id: SessionIdPath,
        min_offset: MinOffsetQuery,
    ) -> None:
        """Deletes events from a session with offset >= the specified value.

        This operation is permanent and cannot be undone."""
        await authorization_policy.authorize(request=request, operation=Operation.DELETE_EVENTS)

        try:
            await app.sessions.delete_events(session_id=session_id, min_offset=min_offset)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{e}")


    
    # Simple chat endpoint
    @router.post(
        "/chat",
        operation_id="chat",
        response_model=ChatResponseDTO,
        responses={
            status.HTTP_200_OK: {
                "description": "AI response to the chat message (success or business error)",
                "content": {"application/json": {"example": chat_response_success_example}},
            },
            status.HTTP_403_FORBIDDEN: {
                "description": "Authorization failed",
                "content": {"application/json": {"example": {
                    "status": 403,
                    "code": 403,
                    "message": "AUTHORIZATION_ERROR",
                    "data": None
                }}},
            },
            status.HTTP_422_UNPROCESSABLE_ENTITY: {
                "description": "Validation error in request parameters",
                "content": {"application/json": {"example": {
                    "status": 422,
                    "code": 422,
                    "message": "VALIDATION_ERROR",
                    "data": None
                }}},
            },
            status.HTTP_500_INTERNAL_SERVER_ERROR: {
                "description": "Internal server error",
                "content": {"application/json": {"example": {
                    "status": 500,
                    "code": 500,
                    "message": "NO_EVENTS_FOUND",
                    "data": None
                }}},
            },
            status.HTTP_504_GATEWAY_TIMEOUT: {
                "description": "Request timeout waiting for AI response",
                "content": {"application/json": {"example": chat_response_error_example}},
            },
        },
        **apigen_config(group_name=API_GROUP, method_name="chat"),
    )
    async def chat(
        request: Request,
        params: ChatRequestDTO,
    ) -> ChatResponseDTO:
        """Simple chat endpoint that handles session management automatically.
        If session_id is provided, it will be used, otherwise a new session will be created.
        """
        logger.info(f"🚀 Chat request started - {params}")
        
        try:
            await authorization_policy.authorize(request=request, operation=Operation.CREATE_CUSTOMER_EVENT)
            logger.info("✅ Authorization successful")
            params.chatbot_id = AgentId(f"{params.chatbot_id}_{params.session_id}")

            logger.info("👤 Step 2: Session and Customer management")

            customers = await app.customers.find()
            logger.info(f"👤 Customers list: {len(customers)}")
            # sessions = await app.sessions.find(None, None)
            # logger.info(f"🔍 Sessions list: {len(sessions)} \n{sessions}")
            agents = await app.agents.find()
            logger.info(f"🤖 Agents list: {len(agents)}")
            
            # Get or create session and customer in one unified flow
            session, customer, agent_id = await _ensure_session_and_customer(
                params=params,
                app=app,
                logger=logger,
                agent_creator=_agent_creator,
            )
        except Exception as e:
            logger.error(f"❌ Error during session/customer setup: {e}")
            return ChatResponseDTO(
                status=500,
                code=-1,
                message=str(e),
                data=None
            )
        
        logger.info(f"✅ Session and customer ready - session: {session.id}, customer: {customer.id}, agent: {agent_id}")

        logger.info("📤 Step 4: Sending customer message")
        logger.info(f"📝 Message content: '{params.message}'")
        
        # Build proper message event data (same as create_event API)
        message_data: MessageEventData = {
            "message": params.message,
            "participant": {
                "id": params.customer_id,
                "display_name": params.customer_id,
            },
            "flagged": False,
            "tags": [],
        }

        customer_event = await app.sessions.create_event(
            session_id=session.id,
            kind=EventKind.MESSAGE,
            data=message_data,
            source=EventSource.CUSTOMER,
            trigger_processing=True,
        )
        logger.info(f"✅ Customer message posted successfully - event_id: {customer_event.id}, offset: {customer_event.offset}")
        
        # Step 6: Wait for AI response or system status
        logger.info("⏳ Step 6: Waiting for AI response or system status")
        timeout = params.timeout or 60
        logger.info(f"⏰ Timeout set to: {timeout} seconds")
        
        # Use a single efficient wait for any relevant events
        # This avoids multiple polling and reduces performance overhead
        wait_result = await session_listener.wait_for_events(
            session_id=session.id,
            min_offset=customer_event.offset + 1,
            source=EventSource.AI_AGENT,
            kinds=[EventKind.MESSAGE],
            timeout=Timeout(timeout),
        )
        logger.info(f"⏳ Wait result: {wait_result}")
        
        if not wait_result:
            # Timeout occurred - no AI response received
            logger.warning("⏰ Step 7: Handling timeout")
            return ChatResponseDTO(
                status=504,
                code=504,
                message="TIMEOUT_ERROR",
                data=None
            )
        
        logger.info("✅ AI response detected, retrieving events")
        # Get the AI response
        ai_events = await app.sessions.find_events(
            session_id=session.id,
            min_offset=customer_event.offset + 1,
            source=EventSource.AI_AGENT,
            kinds=[EventKind.MESSAGE],
            correlation_id=None,
        )
        logger.info(f"📊 Retrieved {len(ai_events)} AI events")
        
        if not ai_events:
            logger.warning("⚠️ Wait returned True but no AI events found")
            return ChatResponseDTO(
                status=500,
                code=500,
                message="NO_EVENTS_FOUND",
                data=None
            )

        extra_events = await app.sessions.find_events(
            session_id=session.id,
            min_offset=customer_event.offset + 1,
            source=EventSource.AI_AGENT,
            kinds=[EventKind.CUSTOM],
            correlation_id=None,
        )

        code = 0
        message = "SUCCESS"
        if extra_events and extra_events[0] and extra_events[0].data:
          logger.debug(f"✅ Extra events data: {extra_events[0].data}")
          type = extra_events[0].data.get('type')
          if type == "tool_error":
            code = -1
            message = "CALL TOOL ERROR"
          elif type == "skipped":
            message = "NO ACTIONBOOK MATCHED"
            code = 1

        
        first_event = ai_events[0]

        if not first_event:
            return ChatResponseDTO(
                status=500,
                code=500,
                message="NO_EVENTS_FOUND",
                data=None
            )
        
        logger.info(f"✅ Processing event - event_id: {first_event.id}, source: {first_event.source}, kind: {first_event.kind}")

        total_tokens = await _get_total_tokens_for_event(session.id, first_event.correlation_id)
        logger.info(f"✅ Total tokens: {total_tokens}")

        
        response = ChatResponseDTO(
            status=200,
            code=code,
            message=message,
            data=CapabilityChatDataDTO(
                message=cast(MessageEventData, first_event.data)["message"],
                success=code == 0,
                id=first_event.id,
                correlation_id=first_event.correlation_id,
                creation_utc=first_event.creation_utc,
                total_tokens=total_tokens,
                session_id=session.id
            )
        )

        logger.info(f"✅ Response: {response.model_dump()}")
        return response

    return router
