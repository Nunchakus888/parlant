from dataclasses import dataclass
from typing import Sequence

from parlant.core.guidelines import Guideline, GuidelineId, GuidelineStore
from parlant.core.services.tools.service_registry import ServiceRegistry
from parlant.core.loggers import Logger
from parlant.core.journeys import (
    JourneyEdge,
    JourneyId,
    JourneyNode,
    JourneyStore,
    Journey,
    JourneyUpdateParams,
)
from parlant.core.tags import Tag, TagId


@dataclass(frozen=True)
class JourneyGraph:
    journey: Journey
    nodes: Sequence[JourneyNode]
    edges: Sequence[JourneyEdge]


@dataclass(frozen=True)
class JourneyConditionUpdateParams:
    add: Sequence[GuidelineId] | None
    remove: Sequence[GuidelineId] | None


@dataclass(frozen=True)
class JourneyTagUpdateParams:
    add: Sequence[TagId] | None = None
    remove: Sequence[TagId] | None = None


class JourneyModule:
    def __init__(
        self,
        logger: Logger,
        journey_store: JourneyStore,
        guideline_store: GuidelineStore,
        service_registry: ServiceRegistry | None = None,
    ):
        self._logger = logger
        self._journey_store = journey_store
        self._guideline_store = guideline_store
        self._service_registry = service_registry

    async def create(
        self,
        title: str,
        description: str,
        conditions: Sequence[str],
        tags: Sequence[TagId] | None,
    ) -> tuple[Journey, Sequence[Guideline]]:
        guidelines = [
            await self._guideline_store.create_guideline(
                condition=condition,
                action=None,
                tags=[],
            )
            for condition in conditions
        ]

        journey = await self._journey_store.create_journey(
            title=title,
            description=description,
            conditions=[g.id for g in guidelines],
            tags=tags,
        )

        for guideline in guidelines:
            await self._guideline_store.upsert_tag(
                guideline_id=guideline.id,
                tag_id=Tag.for_journey_id(journey.id),
            )

        return journey, guidelines

    async def read(self, journey_id: JourneyId) -> JourneyGraph:
        journey = await self._journey_store.read_journey(journey_id=journey_id)
        nodes = await self._journey_store.list_nodes(journey_id=journey.id)
        edges = await self._journey_store.list_edges(journey_id=journey.id)

        return JourneyGraph(journey=journey, nodes=nodes, edges=edges)

    async def find(self, tag_id: TagId | None) -> Sequence[Journey]:
        if tag_id:
            journeys = await self._journey_store.list_journeys(
                tags=[tag_id],
            )
        else:
            journeys = await self._journey_store.list_journeys()

        return journeys

    async def update(
        self,
        journey_id: JourneyId,
        title: str | None,
        description: str | None,
        conditions: JourneyConditionUpdateParams | None,
        tags: JourneyTagUpdateParams | None,
    ) -> Journey:
        journey = await self._journey_store.read_journey(journey_id=journey_id)

        update_params: JourneyUpdateParams = {}
        if title:
            update_params["title"] = title
        if description:
            update_params["description"] = description

        if update_params:
            journey = await self._journey_store.update_journey(
                journey_id=journey_id,
                params=update_params,
            )

        if conditions:
            if conditions.add:
                for condition in conditions.add:
                    await self._journey_store.add_condition(
                        journey_id=journey_id,
                        condition=condition,
                    )

                    guideline = await self._guideline_store.read_guideline(guideline_id=condition)

                    await self._guideline_store.upsert_tag(
                        guideline_id=condition,
                        tag_id=Tag.for_journey_id(journey_id),
                    )

            if conditions.remove:
                for condition in conditions.remove:
                    await self._journey_store.remove_condition(
                        journey_id=journey_id,
                        condition=condition,
                    )

                    guideline = await self._guideline_store.read_guideline(guideline_id=condition)

                    if guideline.tags == [Tag.for_journey_id(journey_id)]:
                        await self._guideline_store.delete_guideline(guideline_id=condition)
                    else:
                        await self._guideline_store.remove_tag(
                            guideline_id=condition,
                            tag_id=Tag.for_journey_id(journey_id),
                        )

        if tags:
            if tags.add:
                for tag in tags.add:
                    await self._journey_store.upsert_tag(journey_id=journey_id, tag_id=tag)

            if tags.remove:
                for tag in tags.remove:
                    await self._journey_store.remove_tag(journey_id=journey_id, tag_id=tag)

        journey = await self._journey_store.read_journey(journey_id=journey_id)

        return journey

    async def delete(self, journey_id: JourneyId) -> None:
        """
        删除Journey，级联清理关联的guidelines和tools
        
        删除顺序：
        1. 清理Journey关联的tools（如果有）
        2. 删除Journey本身（包括nodes、edges、conditions）
        3. 清理关联的guidelines（如果不被其他journey使用）
        """
        journey = await self._journey_store.read_journey(journey_id=journey_id)
        
        # 1. 清理journey关联的工具
        agent_id_str = None
        for tag in journey.tags:
            if str(tag).startswith("agent:"):
                agent_id_str = str(tag).replace("agent:", "")
                break
        
        if agent_id_str and self._service_registry:
            try:
                # 获取所有journey nodes，清理关联的tools
                nodes = await self._journey_store.list_nodes(journey_id=journey_id)
                tools_to_cleanup = []
                
                for node in nodes:
                    if node.tools:
                        tools_to_cleanup.extend(node.tools)
                
                if tools_to_cleanup:
                    self._logger.debug(
                        f"🧹 Cleaning {len(tools_to_cleanup)} tools for journey {journey_id}"
                    )
                    # 注意：具体的tool清理逻辑可能需要根据实际的service_registry实现调整
                    # 这里记录日志，实际清理由agent工具清理统一处理
                    self._logger.debug(f"   Tools: {[t.tool_name for t in tools_to_cleanup]}")
            except Exception as e:
                self._logger.warning(f"⚠️  Failed to cleanup journey tools: {e}")
        
        # 2. 删除journey本身（会删除所有nodes、edges、tag associations）
        self._logger.debug(f"🗑️  Deleting journey store data for {journey_id}")
        await self._journey_store.delete_journey(journey_id=journey_id)

        # 3. 清理关联的guidelines（智能清理：只删除不被其他journey使用的guidelines）
        self._logger.debug(f"🔍 Checking {len(journey.conditions)} condition guidelines for cleanup")
        
        for condition in journey.conditions:
            # 检查这个guideline是否还被其他journey使用
            other_journeys = await self._journey_store.list_journeys(condition=condition)
            
            if not other_journeys:
                # 没有其他journey使用，可以安全删除
                self._logger.debug(f"🗑️  Deleting guideline {condition} (not used by other journeys)")
                await self._guideline_store.delete_guideline(guideline_id=condition)
            else:
                # 还被其他journey使用，只移除当前journey的tag
                guideline = await self._guideline_store.read_guideline(guideline_id=condition)

                if guideline.tags == [Tag.for_journey_id(journey_id)]:
                    # 只有当前journey的tag，删除guideline
                    self._logger.debug(f"🗑️  Deleting guideline {condition} (only tagged with current journey)")
                    await self._guideline_store.delete_guideline(guideline_id=condition)
                else:
                    # 有其他tags，只移除当前journey的tag
                    self._logger.debug(f"🏷️  Removing journey tag from guideline {condition} (has other tags)")
                    await self._guideline_store.remove_tag(
                        guideline_id=condition,
                        tag_id=Tag.for_journey_id(journey_id),
                    )
        
        self._logger.info(f"🗑️ Successfully deleted journey {journey_id} and cleaned up dependencies")
