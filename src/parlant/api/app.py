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

import asyncio
import os
import traceback
from typing import Awaitable, Callable, TypeAlias
from datetime import datetime

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from lagom import Container

# from parlant.adapters.loggers.websocket import WebSocketLogger
from parlant.api import agents, capabilities
from parlant.api import evaluations
from parlant.api import journeys
from parlant.api import relationships
from parlant.api import sessions
from parlant.api import glossary
from parlant.api import guidelines
from parlant.api import context_variables as variables
from parlant.api import services
from parlant.api import tags
from parlant.api import customers
from parlant.api import logs
from parlant.api import canned_responses
from parlant.api.authorization import (
    AuthorizationException,
    AuthorizationPolicy,
    Operation,
    RateLimitExceededException,
)
from parlant.core.contextual_correlator import ContextualCorrelator
from parlant.core.common import ItemNotFoundError, generate_id
from parlant.core.sessions import SessionListener
from parlant.core.loggers import LogLevel, Logger
from parlant.core.application import Application
from parlant.core.tags import TagStore
from parlant.core.agent_factory import AgentFactory


def is_restful_api_request(request: Request) -> bool:
    """Check if the request is for an API endpoint that should return RESTful format."""
    path = request.url.path
    
    # Define patterns for RESTful API endpoints
    restful_patterns = [
        "/sessions/chat",
        # Add more patterns as needed:
        # "/api/v2/chat",
        # "/conversation",
        # "/chat/stream",
    ]
    
    # Check for exact matches
    if path in restful_patterns:
        return True
    
    # Check for path endings (for sub-paths)
    restful_endings = [
        "/chat",
        # Add more endings as needed
    ]
    
    return any(path.endswith(ending) for ending in restful_endings)


def create_chat_error_response(request: Request, status_code: int, message: str, detail: str = None):
    """Create a ChatResponseDTO error response for RESTful APIs, or raise HTTPException for other APIs."""
    if is_restful_api_request(request):
        from parlant.api.sessions import ChatResponseDTO
        from fastapi.responses import JSONResponse
        
        response_data = ChatResponseDTO(
            status=status_code,
            code=status_code,
            message=message,
            data=None
        )
        return JSONResponse(
            status_code=status_code,
            content=response_data.model_dump()
        )
    else:
        # For other APIs, raise HTTPException
        raise HTTPException(
            status_code=status_code,
            detail=detail or message
        )


ASGIApplication: TypeAlias = Callable[
    [
        Scope,
        Receive,
        Send,
    ],
    Awaitable[None],
]


class AppWrapper:
    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """FastAPI's built-in exception handling doesn't catch BaseExceptions
        such as asyncio.CancelledError. This causes the server process to terminate
        with an ugly traceback. This wrapper addresses that by specifically allowing
        asyncio.CancelledError to gracefully exit.
        """
        try:
            return await self.app(scope, receive, send)
        except asyncio.CancelledError:
            pass


async def create_api_app(container: Container) -> ASGIApplication:
    logger = container[Logger]
    # websocket_logger = container[WebSocketLogger]
    correlator = container[ContextualCorrelator]
    authorization_policy = container[AuthorizationPolicy]
    session_listener = container[SessionListener]
    application = container[Application]
    if AgentFactory in container.defined_types:
        agent_factory = container[AgentFactory]
    else:
        agent_factory = None

    api_app = FastAPI()

    @api_app.middleware("http")
    async def handle_cancellation(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        try:
            return await call_next(request)
        except asyncio.CancelledError:
            return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    api_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api_app.middleware("http")
    async def add_correlation_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            request.url.path.startswith("/docs")
            or request.url.path.startswith("/redoc")
            or request.url.path.startswith("/openapi.json")
        ):
            await authorization_policy.authorize(
                request=request,
                operation=Operation.ACCESS_API_DOCS,
            )
            return await call_next(request)

        if request.url.path.startswith("/chat/"):
            await authorization_policy.authorize(
                request=request,
                operation=Operation.ACCESS_INTEGRATED_UI,
            )

            return await call_next(request)

        request_id = generate_id()
        with correlator.scope(f"R{request_id}", {"request_id": request_id}):
            with logger.operation(
                f"HTTP Request: {request.method} {request.url.path}",
                level=LogLevel.TRACE,
                create_scope=False,
            ):
                return await call_next(request)

    @api_app.exception_handler(RateLimitExceededException)
    async def rate_limit_exceeded_handler(
        request: Request, exc: RateLimitExceededException
    ) -> HTTPException:
        logger.trace(f"Rate limit exceeded: {exc}")

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )

    @api_app.exception_handler(AuthorizationException)
    async def authorization_error_handler(
        request: Request, exc: AuthorizationException
    ):
        logger.trace(f"Authorization error: {exc}")
        return create_chat_error_response(request, status.HTTP_403_FORBIDDEN, "AUTHORIZATION_ERROR", str(exc))

    @api_app.exception_handler(ItemNotFoundError)
    async def item_not_found_error_handler(
        request: Request, exc: ItemNotFoundError
    ):
        logger.error(str(exc))
        return create_chat_error_response(request, status.HTTP_404_NOT_FOUND, "NOT_FOUND", str(exc))

    @api_app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ):
        logger.error(f"Validation error: {exc}")
        return create_chat_error_response(request, status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", exc.errors())

    @api_app.exception_handler(Exception)
    async def server_error_handler(request: Request, exc: Exception):
        logger.error(str(exc))
        logger.error(str(traceback.format_exception(exc)))
        return create_chat_error_response(request, status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_SERVER_ERROR", str(exc))

    static_dir = os.path.join(os.path.dirname(__file__), "chat/dist")
    api_app.mount("/chat", StaticFiles(directory=static_dir, html=True), name="static")

    @api_app.get("/chat-test", include_in_schema=False)
    async def chat_test_page() -> Response:
        """Chat test page - serves the static HTML file for testing chat functionality."""
        chat_test_file = os.path.join(static_dir, "chat-test.html")
        if os.path.exists(chat_test_file):
            with open(chat_test_file, "r", encoding="utf-8") as f:
                html_content = f.read()
            return Response(content=html_content, media_type="text/html")
        else:
            return Response(
                content="<h1>Chat Test Page Not Found</h1><p>The chat-test.html file is missing.</p>",
                media_type="text/html",
                status_code=404
            )

    @api_app.get("/", include_in_schema=False)
    async def root() -> Response:
        return RedirectResponse("/chat")

    @api_app.get("/health")
    async def health_check():
        return {"status": "healthy", "timestamp": datetime.now()}


    # agent_router = APIRouter(prefix="/agents")

    # agent_router.include_router(
    #     guidelines.create_legacy_router(
    #         application=application,
    #         guideline_store=guideline_store,
    #         tag_store=tag_store,
    #         relationship_store=relationship_store,
    #         service_registry=service_registry,
    #         guideline_tool_association_store=guideline_tool_association_store,
    #     ),
    # )
    # agent_router.include_router(
    #     glossary.create_legacy_router(
    #         glossary_store=glossary_store,
    #     ),
    # )
    # agent_router.include_router(
    #     variables.create_legacy_router(
    #         context_variable_store=context_variable_store,
    #         service_registry=service_registry,
    #     ),
    # )

    # api_app.include_router(
    #     router=agent_router,
    # )

    # api_app.include_router(
    #     router=agents.create_router(
    #         policy=authorization_policy,
    #         app=application,
    #     ),
    #     prefix="/agents",
    # )

  
    
    api_app.include_router(
        prefix="/sessions",
        router=sessions.create_router(
            authorization_policy=authorization_policy,
            app=application,
            logger=logger,
            session_listener=session_listener,
            agent_factory=agent_factory,
        ),
    )

    # api_app.include_router(
    #     prefix="/index",
    #     router=index.legacy_create_router(
    #         evaluation_service=legacy_evaluation_service,
    #         evaluation_store=evaluation_store,
    #         evaluation_listener=evaluation_listener,
    #         agent_store=agent_store,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/services",
    #     router=services.create_router(
    #         authorization_policy=authorization_policy,
    #         service_registry=service_registry,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/tags",
    #     router=tags.create_router(
    #         authorization_policy=authorization_policy,
    #         tag_store=tag_store,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/terms",
    #     router=glossary.create_router(
    #         authorization_policy=authorization_policy,
    #         glossary_store=glossary_store,
    #         agent_store=agent_store,
    #         tag_store=tag_store,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/customers",
    #     router=customers.create_router(
    #         authorization_policy=authorization_policy,
    #         app=application,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/canned_responses",
    #     router=canned_responses.create_router(
    #         authorization_policy=authorization_policy,
    #         canned_response_store=canned_response_store,
    #         tag_store=tag_store,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/context-variables",
    #     router=variables.create_router(
    #         authorization_policy=authorization_policy,
    #         context_variable_store=context_variable_store,
    #         service_registry=service_registry,
    #         agent_store=agent_store,
    #         tag_store=tag_store,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/guidelines",
    #     router=guidelines.create_router(
    #         authorization_policy=authorization_policy,
    #         guideline_store=guideline_store,
    #         relationship_store=relationship_store,
    #         service_registry=service_registry,
    #         guideline_tool_association_store=guideline_tool_association_store,
    #         agent_store=agent_store,
    #         tag_store=tag_store,
    #         journey_store=journey_store,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/relationships",
    #     router=relationships.create_router(
    #         authorization_policy=authorization_policy,
    #         relationship_store=relationship_store,
    #         tag_store=tag_store,
    #         guideline_store=guideline_store,
    #         agent_store=agent_store,
    #         journey_store=journey_store,
    #         service_registry=service_registry,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/journeys",
    #     router=journeys.create_router(
    #         authorization_policy=authorization_policy,
    #         journey_store=journey_store,
    #         guideline_store=guideline_store,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/evaluations",
    #     router=evaluations.create_router(
    #         authorization_policy=authorization_policy,
    #         evaluation_service=evaluation_service,
    #         evaluation_store=evaluation_store,
    #         evaluation_listener=evaluation_listener,
    #     ),
    # )

    # api_app.include_router(
    #     prefix="/capabilities",
    #     router=capabilities.create_router(
    #         authorization_policy=authorization_policy,
    #         capability_store=capability_store,
    #         tag_store=tag_store,
    #         agent_store=agent_store,
    #         journey_store=journey_store,
    #     ),
    # )

    # api_app.include_router(
    #     router=logs.create_router(
    #         websocket_logger,
    #     )
    # )

    return AppWrapper(api_app)
