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

import json
from typing import Sequence

from parlant.core.context_variables import ContextVariable, ContextVariableValue


def context_variables_to_json(
    context_variables: Sequence[tuple[ContextVariable, ContextVariableValue]],
) -> str:
    context_values = {
        variable.name: {
            "value": value.data,
            **({"description": variable.description} if variable.description else {}),
        }
        for variable, value in context_variables
    }

    return json.dumps(context_values)


def is_llm_output_format_error(exc: Exception, llm_output_schema_name: str | None = None) -> bool:
    """
    Determine if an exception is an LLM output format error that may succeed on retry.
    
    LLM output format errors include:
    1. JSONDecodeError - JSON syntax errors (e.g., invalid Unicode escapes, malformed JSON)
    2. ValueError with "No JSON object found" - jsonfinder couldn't extract valid JSON
    3. Pydantic ValidationError - Only when it's for the specified LLM output schema
    
    Args:
        exc: The exception to check
        llm_output_schema_name: Optional schema name. If provided, ValidationError is only
            considered retryable if it's for this specific schema. This prevents retrying
            business logic validation errors.
    
    Returns:
        True if the exception is an LLM output format error (should retry)
        False otherwise
    """
    from pydantic import ValidationError
    
    # JSONDecodeError: JSON syntax errors from LLM output (e.g., invalid Unicode escapes)
    if isinstance(exc, json.JSONDecodeError):
        return True
    
    # ValueError from jsonfinder: couldn't find valid JSON in LLM output
    if isinstance(exc, ValueError) and "no json object found" in str(exc).lower():
        return True
    
    # Pydantic ValidationError: only retry if it's for the specified LLM output schema
    if isinstance(exc, ValidationError):
        if llm_output_schema_name is None:
            # No schema specified, assume all ValidationErrors are retryable
            return True
        # Only retry if it's for the specific LLM output schema
        # Pydantic v1: use exc.model.__name__, Pydantic v2: use exc.title
        model_name = getattr(exc.model, '__name__', None) if hasattr(exc, 'model') else getattr(exc, 'title', None)
        return model_name == llm_output_schema_name
    
    return False
