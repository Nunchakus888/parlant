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

from parlant.app_modules.agents import AgentModule
from parlant.app_modules.capabilities import CapabilityModule
from parlant.app_modules.canned_responses import CannedResponseModule
from parlant.app_modules.context_variables import ContextVariableModule
from parlant.app_modules.evaluations import EvaluationModule
from parlant.app_modules.journeys import JourneyModule
from parlant.app_modules.relationships import RelationshipModule
from parlant.app_modules.services import ServiceModule
from parlant.app_modules.sessions import SessionModule
from parlant.app_modules.tags import TagModule
from parlant.app_modules.customers import CustomerModule
from parlant.app_modules.guidelines import GuidelineModule
from parlant.app_modules.glossary import GlossaryModule


class Application:
    def __init__(
        self,
        session_id: SessionId,
        min_offset: int,
        kinds: Sequence[EventKind] = [],
        source: Optional[EventSource] = None,
        timeout: Timeout = Timeout.infinite(),
    ) -> bool:
        return await self._session_listener.wait_for_events(
            session_id=session_id,
            min_offset=min_offset,
            kinds=kinds,
            source=source,
            timeout=timeout,
        )

    async def create_customer_session(
        self,
        customer_id: CustomerId,
        agent_id: AgentId,
        title: Optional[str] = None,
        session_id: Optional[SessionId] = None,
        allow_greeting: bool = False,
    ) -> Session:
        session = await self._session_store.create_session(
            creation_utc=datetime.now(timezone.utc),
            customer_id=customer_id,
            agent_id=agent_id,
            title=title,
            session_id=session_id,
        )

        if allow_greeting:
            await self.dispatch_processing_task(session)

        return session

    async def post_event(
        self,
        session_id: SessionId,
        kind: EventKind,
        data: Mapping[str, Any],
        source: EventSource = EventSource.CUSTOMER,
        trigger_processing: bool = True,
    ) -> Event:
        event = await self._session_store.create_event(
            session_id=session_id,
            source=source,
            kind=kind,
            correlation_id=self._correlator.correlation_id,
            data=data,
        )

        if trigger_processing:
            session = await self._session_store.read_session(session_id)
            await self.dispatch_processing_task(session)

        return event

    async def dispatch_processing_task(self, session: Session) -> str:
        with self._correlator.scope("process", {"session": session}):
            await self._background_task_service.restart(
                self._process_session(session),
                tag=f"process-session({session.id})",
            )

            return self._correlator.correlation_id

    async def _process_session(self, session: Session) -> None:
        event_emitter = await self._event_emitter_factory.create_event_emitter(
            emitting_agent_id=session.agent_id,
            session_id=session.id,
        )

        await self._engine.process(
            Context(
                session_id=session.id,
                agent_id=session.agent_id,
            ),
            event_emitter=event_emitter,
        )

    async def utter(
        self,
        session: Session,
        requests: Sequence[UtteranceRequest],
    ) -> str:
        with self._correlator.scope("utter", {"session": session}):
            event_emitter = await self._event_emitter_factory.create_event_emitter(
                emitting_agent_id=session.agent_id,
                session_id=session.id,
            )

            await self._engine.utter(
                context=Context(session_id=session.id, agent_id=session.agent_id),
                event_emitter=event_emitter,
                requests=requests,
            )

            return self._correlator.correlation_id

    async def create_guidelines(
        self,
        invoices: Sequence[Invoice],
    ) -> Iterable[GuidelineId]:
        async def _create_with_existing_guideline(
            source_key: str,
            target_key: str,
            content_guidelines: dict[str, GuidelineId],
            proposition: EntailmentRelationshipProposition,
        ) -> None:
            if source_key in content_guidelines:
                source_guideline_id = content_guidelines[source_key]
                target_guideline_id = (
                    await self._guideline_store.find_guideline(
                        guideline_content=proposition.target,
                    )
                ).id
            else:
                source_guideline_id = (
                    await self._guideline_store.find_guideline(
                        guideline_content=proposition.source,
                    )
                ).id
                target_guideline_id = content_guidelines[target_key]

            await self._relationship_store.create_relationship(
                source=RelationshipEntity(
                    id=source_guideline_id,
                    kind=RelationshipEntityKind.GUIDELINE,
                ),
                target=RelationshipEntity(
                    id=target_guideline_id,
                    kind=RelationshipEntityKind.GUIDELINE,
                ),
                kind=RelationshipKind.ENTAILMENT,
            )

        content_guidelines: dict[str, GuidelineId] = {}

        for invoice in invoices:
            payload = cast(GuidelinePayload, invoice.payload)

            content_guidelines[
                f"{cast(GuidelinePayload, invoice.payload).content.condition}_{cast(GuidelinePayload, invoice.payload).content.action}"
            ] = (
                await self._guideline_store.create_guideline(
                    condition=payload.content.condition,
                    action=payload.content.action,
                )
                if invoice.payload.operation == PayloadOperation.ADD
                else await self._guideline_store.update_guideline(
                    guideline_id=cast(GuidelineId, payload.updated_id),
                    params={
                        "condition": payload.content.condition,
                        "action": payload.content.action or None,
                    },
                )
            ).id

        for invoice in invoices:
            payload = cast(GuidelinePayload, invoice.payload)

            if payload.operation == PayloadOperation.UPDATE and payload.connection_proposition:
                guideline_id = cast(GuidelineId, payload.updated_id)

                relationships_to_delete = list(
                    await self._relationship_store.list_relationships(
                        kind=RelationshipKind.ENTAILMENT,
                        indirect=False,
                        source_id=guideline_id,
                    )
                )

                relationships_to_delete.extend(
                    await self._relationship_store.list_relationships(
                        kind=RelationshipKind.ENTAILMENT,
                        indirect=False,
                        target_id=guideline_id,
                    )
                )

                for relationship in relationships_to_delete:
                    await self._relationship_store.delete_relationship(relationship.id)

        entailment_propositions: set[EntailmentRelationshipProposition] = set([])

        for invoice in invoices:
            assert invoice.data
            data = cast(InvoiceGuidelineData, invoice.data)

            if not data.entailment_propositions:
                continue

            for proposition in data.entailment_propositions:
                source_key = f"{proposition.source.condition}_{proposition.source.action}"
                target_key = f"{proposition.target.condition}_{proposition.target.action}"

                if proposition not in entailment_propositions:
                    if (
                        proposition.check_kind
                        == EntailmentRelationshipPropositionKind.CONNECTION_WITH_ANOTHER_EVALUATED_GUIDELINE
                    ):
                        await self._relationship_store.create_relationship(
                            source=RelationshipEntity(
                                id=content_guidelines[source_key],
                                kind=RelationshipEntityKind.GUIDELINE,
                            ),
                            target=RelationshipEntity(
                                id=content_guidelines[target_key],
                                kind=RelationshipEntityKind.GUIDELINE,
                            ),
                            kind=RelationshipKind.ENTAILMENT,
                        )
                    else:
                        await _create_with_existing_guideline(
                            source_key,
                            target_key,
                            content_guidelines,
                            proposition,
                        )

                    entailment_propositions.add(proposition)

        return content_guidelines.values()
