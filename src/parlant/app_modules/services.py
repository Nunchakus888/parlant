from typing import Sequence

from parlant.core.loggers import Logger
from parlant.core.services.tools.service_registry import ServiceRegistry, ToolServiceKind
from parlant.core.tools import ToolService


class ServiceModule:
    def __init__(
        self,
        logger: Logger,
        service_registry: ServiceRegistry,
    ):
        self._logger = logger
        self._service_registry = service_registry

    async def read(self, name: str) -> ToolService:
        service = await self._service_registry.read_tool_service(name)
        return service

    async def update(
        self,
        name: str,
        kind: ToolServiceKind,
        url: str,
        source: str | None,
    ) -> ToolService:
        service = await self._service_registry.update_tool_service(
            name=name,
            kind=kind,
            url=url,
            source=source,
        )

        return service

    async def delete(self, name: str) -> None:
        await self._service_registry.read_tool_service(name)
        await self._service_registry.delete_service(name)

    async def find(self) -> Sequence[tuple[str, ToolService]]:
        return await self._service_registry.list_tool_services()
    
    async def cleanup_agent_tools(self, agent_id: str) -> None:
        """清理指定Agent的所有工具"""
        try:
            # 获取"built-in"服务（PluginClient）
            service = await self._service_registry.read_tool_service("built-in")
            
            # 🔧 FIX: PluginClient 通过 HTTP 调用 PluginServer 的清理接口
            from parlant.core.services.tools.plugins import PluginClient
            if isinstance(service, PluginClient):
                await service.delete_agent_tools(agent_id)
                self._logger.debug(f"🔧 已清理Agent {agent_id} 的所有工具")
            else:
                self._logger.debug(f"🔧 服务类型为 {type(service).__name__}，跳过工具清理")
                
        except Exception as e:
            # 不抛出异常，避免影响Agent删除流程
            self._logger.debug(f"🔧 清理Agent {agent_id} 工具时出现异常（已忽略）: {str(e)}")