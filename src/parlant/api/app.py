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

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from lagom import Container

from parlant.adapters.loggers.websocket import WebSocketLogger
from parlant.api import agents, capabilities
from parlant.api import evaluations
from parlant.api import index
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
from parlant.core.capabilities import CapabilityStore
from parlant.core.context_variables import ContextVariableStore
from parlant.core.contextual_correlator import ContextualCorrelator
from parlant.core.agents import AgentStore
from parlant.core.common import ItemNotFoundError, generate_id
from parlant.core.customers import CustomerStore
from parlant.core.evaluations import EvaluationStore, EvaluationListener
from parlant.core.journeys import JourneyStore
from parlant.core.canned_responses import CannedResponseStore
from parlant.core.relationships import RelationshipStore
from parlant.core.guidelines import GuidelineStore
from parlant.core.guideline_tool_associations import GuidelineToolAssociationStore
from parlant.core.nlp.service import NLPService
from parlant.core.services.tools.service_registry import ServiceRegistry
from parlant.core.sessions import SessionListener, SessionStore
from parlant.core.glossary import GlossaryStore
from parlant.core.services.indexing.behavioral_change_evaluation import (
    BehavioralChangeEvaluator,
    LegacyBehavioralChangeEvaluator,
)
from parlant.core.loggers import LogLevel, Logger
from parlant.core.application import Application
from parlant.core.tags import TagStore

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
    websocket_logger = container[WebSocketLogger]
    correlator = container[ContextualCorrelator]
    authorization_policy = container[AuthorizationPolicy]
    agent_store = container[AgentStore]
    customer_store = container[CustomerStore]
    tag_store = container[TagStore]
    session_store = container[SessionStore]
    session_listener = container[SessionListener]
    evaluation_store = container[EvaluationStore]
    evaluation_listener = container[EvaluationListener]
    legacy_evaluation_service = container[LegacyBehavioralChangeEvaluator]
    evaluation_service = container[BehavioralChangeEvaluator]
    glossary_store = container[GlossaryStore]
    guideline_store = container[GuidelineStore]
    relationship_store = container[RelationshipStore]
    guideline_tool_association_store = container[GuidelineToolAssociationStore]
    context_variable_store = container[ContextVariableStore]
    canned_response_store = container[CannedResponseStore]
    journey_store = container[JourneyStore]
    capability_store = container[CapabilityStore]
    service_registry = container[ServiceRegistry]
    nlp_service = container[NLPService]
    application = container[Application]

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
    ) -> HTTPException:
        logger.trace(f"Authorization error: {exc}")

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )

    @api_app.exception_handler(ItemNotFoundError)
    async def item_not_found_error_handler(
        request: Request, exc: ItemNotFoundError
    ) -> HTTPException:
        logger.info(str(exc))

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    @api_app.exception_handler(Exception)
    async def server_error_handler(request: Request, exc: ItemNotFoundError) -> HTTPException:
        logger.error(str(exc))
        logger.error(str(traceback.format_exception(exc)))

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    static_dir = os.path.join(os.path.dirname(__file__), "chat/dist")
    api_app.mount("/chat", StaticFiles(directory=static_dir, html=True), name="static")

    @api_app.get("/chat-test", include_in_schema=False)
    async def chat_test_page() -> Response:
        html = """
<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Parlant Chat Test</title>
  <style>
    body { font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, \"Apple Color Emoji\", \"Segoe UI Emoji\"; margin: 24px; color: #1f2937; }
    .card { max-width: 780px; margin: 0 auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }
    .row { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
    label { width: 140px; color: #6b7280; font-weight: 600; }
    input[type="text"], input[type="number"], textarea { flex: 1; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 10px; font-size: 14px; }
    textarea { min-height: 90px; resize: vertical; }
    button { background: linear-gradient(90deg, #6366f1, #22d3ee); color: white; border: 0; border-radius: 10px; padding: 10px 16px; font-weight: 600; cursor: pointer; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .output { white-space: pre-wrap; background: #0b1020; color: #e5e7eb; padding: 14px; border-radius: 10px; overflow: auto; }
    .muted { color: #9ca3af; font-size: 13px; }
  </style>
</head>
<body>
  <div class=\"card\">
    <h2>Parlant Chat Test</h2>
    <p class=\"muted\">Sends a request to <code>/sessions/chat</code> and displays the AI reply.</p>

    <div class=\"row\">
      <label for=\"customer_id\">Customer ID</label>
      <input id=\"customer_id\" type=\"text\" placeholder=\"cust_123xy (or leave to auto-create Guest)\" />
    </div>

    <div class=\"row\">
      <label for=\"session_title\">Session Title</label>
      <input id=\"session_title\" type=\"text\" placeholder=\"Chat Session\" />
    </div>

    <div class=\"row\">
      <label for=\"timeout\">Timeout (s)</label>
      <input id=\"timeout\" type=\"number\" value=\"30\" min=\"1\" />
    </div>

    <div class=\"row\" style=\"align-items:flex-start\">
      <label for=\"message\">Message</label>
      <textarea id=\"message\" placeholder=\"Hello, I need help with my order\"></textarea>
    </div>

    <div class=\"row\">
      <div style=\"width:140px\"></div>
      <button id=\"send\">Send</button>
      <button id=\"clear\" style=\"background:#e5e7eb;color:#111827;\">Clear</button>
    </div>

    <h3>Response</h3>
    <div id=\"status\" class=\"muted\"></div>
    <div id=\"answer\" class=\"output\"></div>
    <h3>Raw JSON</h3>
    <pre id=\"raw\" class=\"output\"></pre>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    const sendBtn = $("send");
    const clearBtn = $("clear");

    function setBusy(busy) {
      sendBtn.disabled = busy;
      sendBtn.textContent = busy ? "Sending..." : "Send";
    }

    clearBtn.addEventListener('click', () => {
      $("message").value = "";
      $("answer").textContent = "";
      $("raw").textContent = "";
      $("status").textContent = "";
    });

    sendBtn.addEventListener('click', async () => {
      setBusy(true);
      $("status").textContent = "";
      $("answer").textContent = "";
      $("raw").textContent = "";

      const customerId = $("customer_id").value.trim();
      const message = $("message").value.trim();
      const sessionTitle = $("session_title").value.trim();
      const timeout = parseInt($("timeout").value, 10) || 30;

      if (!message) {
        $("status").textContent = "Please type a message first.";
        setBusy(false);
        return;
      }

      const body = {
        message,
        customer_id: customerId,
        session_title: sessionTitle || undefined,
        timeout,
      };

      try {
        const res = await fetch('/sessions/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });

        const text = await res.text();
        $("raw").textContent = text;

        if (!res.ok) {
          $("status").textContent = `Error ${res.status}: ${res.statusText}`;
          return;
        }

        try {
          const json = JSON.parse(text);
          const aiMsg = json && json.data && json.data.message ? json.data.message : JSON.stringify(json, null, 2);
          $("answer").textContent = aiMsg;
          $("status").textContent = "OK";
        } catch (e) {
          $("status").textContent = "Received non-JSON response";
        }
      } catch (e) {
        $("status").textContent = `Network error: ${e}`;
      } finally {
        setBusy(false);
      }
    });
  </script>
</body>
</html>
"""
        return Response(content=html, media_type="text/html")

    @api_app.get("/", include_in_schema=False)
    async def root() -> Response:
        return RedirectResponse("/chat")

    agent_router = APIRouter(prefix="/agents")

    agent_router.include_router(
        guidelines.create_legacy_router(
            application=application,
            guideline_store=guideline_store,
            tag_store=tag_store,
            relationship_store=relationship_store,
            service_registry=service_registry,
            guideline_tool_association_store=guideline_tool_association_store,
        ),
    )
    agent_router.include_router(
        glossary.create_legacy_router(
            glossary_store=glossary_store,
        ),
    )
    agent_router.include_router(
        variables.create_legacy_router(
            context_variable_store=context_variable_store,
            service_registry=service_registry,
        ),
    )

    api_app.include_router(
        router=agents.create_router(
            policy=authorization_policy,
            agent_store=agent_store,
            tag_store=tag_store,
        ),
        prefix="/agents",
    )

    api_app.include_router(
        router=agent_router,
    )

    # Get agent factory from container if available
    from parlant.sdk import AgentFactory
    agent_factory = container[AgentFactory] if AgentFactory in container.defined_types else None
    
    api_app.include_router(
        prefix="/sessions",
        router=sessions.create_router(
            authorization_policy=authorization_policy,
            logger=logger,
            application=application,
            agent_store=agent_store,
            customer_store=customer_store,
            session_store=session_store,
            session_listener=session_listener,
            nlp_service=nlp_service,
            agent_factory=agent_factory,
        ),
    )

    api_app.include_router(
        prefix="/index",
        router=index.legacy_create_router(
            evaluation_service=legacy_evaluation_service,
            evaluation_store=evaluation_store,
            evaluation_listener=evaluation_listener,
            agent_store=agent_store,
        ),
    )

    api_app.include_router(
        prefix="/services",
        router=services.create_router(
            authorization_policy=authorization_policy,
            service_registry=service_registry,
        ),
    )

    api_app.include_router(
        prefix="/tags",
        router=tags.create_router(
            authorization_policy=authorization_policy,
            tag_store=tag_store,
        ),
    )

    api_app.include_router(
        prefix="/terms",
        router=glossary.create_router(
            authorization_policy=authorization_policy,
            glossary_store=glossary_store,
            agent_store=agent_store,
            tag_store=tag_store,
        ),
    )

    api_app.include_router(
        prefix="/customers",
        router=customers.create_router(
            authorization_policy=authorization_policy,
            customer_store=customer_store,
            tag_store=tag_store,
            agent_store=agent_store,
        ),
    )

    api_app.include_router(
        prefix="/canned_responses",
        router=canned_responses.create_router(
            authorization_policy=authorization_policy,
            canned_response_store=canned_response_store,
            tag_store=tag_store,
        ),
    )

    api_app.include_router(
        prefix="/context-variables",
        router=variables.create_router(
            authorization_policy=authorization_policy,
            context_variable_store=context_variable_store,
            service_registry=service_registry,
            agent_store=agent_store,
            tag_store=tag_store,
        ),
    )

    api_app.include_router(
        prefix="/guidelines",
        router=guidelines.create_router(
            authorization_policy=authorization_policy,
            guideline_store=guideline_store,
            relationship_store=relationship_store,
            service_registry=service_registry,
            guideline_tool_association_store=guideline_tool_association_store,
            agent_store=agent_store,
            tag_store=tag_store,
            journey_store=journey_store,
        ),
    )

    api_app.include_router(
        prefix="/relationships",
        router=relationships.create_router(
            authorization_policy=authorization_policy,
            relationship_store=relationship_store,
            tag_store=tag_store,
            guideline_store=guideline_store,
            agent_store=agent_store,
            journey_store=journey_store,
            service_registry=service_registry,
        ),
    )

    api_app.include_router(
        prefix="/journeys",
        router=journeys.create_router(
            authorization_policy=authorization_policy,
            journey_store=journey_store,
            guideline_store=guideline_store,
        ),
    )

    api_app.include_router(
        prefix="/evaluations",
        router=evaluations.create_router(
            authorization_policy=authorization_policy,
            evaluation_service=evaluation_service,
            evaluation_store=evaluation_store,
            evaluation_listener=evaluation_listener,
        ),
    )

    api_app.include_router(
        prefix="/capabilities",
        router=capabilities.create_router(
            authorization_policy=authorization_policy,
            capability_store=capability_store,
            tag_store=tag_store,
            agent_store=agent_store,
            journey_store=journey_store,
        ),
    )

    api_app.include_router(
        router=logs.create_router(
            websocket_logger,
        )
    )

    return AppWrapper(api_app)
