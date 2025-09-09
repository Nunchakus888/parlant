import json
import os
from typing import Dict, Any, List
from parlant.core.agent_factory import AgentFactory
import parlant.sdk as p
from parlant.core.agents import AgentStore
from app.tools import ToolManager

class CustomAgentFactory(AgentFactory):
    def __init__(self, agent_store: AgentStore, logger, container):
        super().__init__(agent_store, logger)
        self.config_path = "app/lead-acquistion.json"
        self._config_cache = None
        self.container = container

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件，使用缓存避免重复读取"""
        if self._config_cache is None:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self._config_cache = json.load(f)
                self._logger.info(f"成功加载配置文件: {self.config_path}")
            except FileNotFoundError:
                self._logger.error(f"配置文件不存在: {self.config_path}")
                raise
            except json.JSONDecodeError as e:
                self._logger.error(f"配置文件格式错误: {e}")
                raise
        return self._config_cache
    
    def _get_server_from_container(self):
        """从 container 中获取 Server 对象"""
        # 在 parlant 的架构中，Server 对象没有直接注册到 container 中
        # 但是我们可以通过一个巧妙的方法：在 container 中存储 Server 对象的引用
        
        # 方法1：检查 container 是否有 Server 对象的引用
        if hasattr(self.container, '_server_ref') and self.container._server_ref:
            return self.container._server_ref
        
        # 方法2：通过其他方式获取（这里我们暂时返回 None，使用回退方案）
        return None
    
    async def create_agent_for_customer(self, customer_id: p.CustomerId) -> p.Agent:
        """从配置文件创建个性化智能体"""
        self._logger.info(f"为客户 {customer_id} 创建个性化智能体...")
        
        # 加载配置
        config = self._load_config()
        basic_settings = config.get("basic_settings", {})
        
        # 动态获取 Server 对象（在运行时，Server 已经创建）
        server = self._get_server_from_container()
        
        if server:
            # 使用 server.create_agent() 创建具有完整功能的 Agent
            agent = await server.create_agent(
                name=basic_settings.get("name"),
                description=f"{basic_settings.get('description', '')} {basic_settings.get('background', '')}",
                max_engine_iterations=3,
            )
            self._logger.info("✅ 使用 server.create_agent() 创建 Agent（完整功能）")
        else:
            # 回退到使用 agent_store（兼容性）
            self._logger.warning("⚠️ 无法获取 Server 对象，使用 agent_store 创建 Agent（功能受限）")
            agent = await self._agent_store.create_agent(
                name=basic_settings.get("name"),
                description=f"{basic_settings.get('description', '')} {basic_settings.get('background', '')}",
                max_engine_iterations=3,
            )
        
        # 设置工具
        tools = await self._setup_tools(agent, config.get("tools", []))
        
        # 创建指导原则
        await self._create_guidelines(agent, config.get("action_books", []), tools)
        
        self._logger.info(f"成功创建智能体 {agent.id} for customer {customer_id}")
        return agent
    
    async def _setup_tools(self, agent: p.Agent, tools_config: List[Dict[str, Any]]) -> Dict[str, Any]:
        """设置工具并返回工具映射"""
        if not tools_config:
            self._logger.warning("没有工具配置，跳过工具设置")
            return {}
        
        tool_manager = ToolManager(
            raw_configs=tools_config,
            logger=self._logger,
            timeout=30
        )
        await tool_manager.setup_tools(agent)
        
        self._logger.info(f"成功设置 {len(tools_config)} 个工具")
        return tool_manager._tools
    
    async def _create_guidelines(self, agent: p.Agent, action_books: List[Dict[str, Any]], available_tools: Dict[str, Any]) -> None:
        if not action_books:
            self._logger.warning("没有指导原则配置，跳过创建")
            return
        
        for action_book in action_books:
            try:
                condition = action_book.get("condition", "")
                action = action_book.get("action", "")
                tool_names = action_book.get("tools", [])
                
                if not condition and not action:
                    self._logger.warning(f"跳过无效的指导原则: condition={condition}, action={action}")
                    continue
                
                # 获取关联的工具
                associated_tools = []
                for tool_name in tool_names:
                    if tool_name in available_tools:
                        associated_tools.append(available_tools[tool_name])
                    else:
                        self._logger.warning(f"工具 {tool_name} 未找到，跳过关联")
                
                # 创建指导原则
                await agent.create_guideline(
                    condition=condition,
                    action=action,
                    tools=associated_tools
                )
                
                self._logger.debug(f"创建指导原则: {condition} -> {action} (关联 {len(associated_tools)} 个工具)")
                
            except Exception as e:
                self._logger.error(f"创建指导原则失败: {e}")
                continue
        
        self._logger.info(f"成功创建 {len(action_books)} 个指导原则")
    




async def initialize_agent_factory(container: p.Container) -> None:
    container[p.AgentFactory] = CustomAgentFactory(
        agent_store=container[AgentStore],
        container=container,
        logger=container[p.Logger]
    )