from parlant.core.agent_factory import AgentFactory
import parlant.sdk as p
from parlant.core.agents import AgentStore
from app.tools import ToolManager

class CustomAgentFactory(AgentFactory):
    async def create_agent_for_customer(self, customer_id: p.CustomerId) -> p.Agent:

        self._logger.error(f"重写创建方法，添加个性化逻辑，为客户 {customer_id} 创建个性化智能体...")
        
        customer_config = {
            "name": f"Agent for personalized",
            "description": f"agent for personalized",
            "max_engine_iterations": 3,
        }

        agent = await self._agent_store.create_agent(
            name=customer_config["name"],
            description=customer_config["description"],
            max_engine_iterations=customer_config.get("max_engine_iterations", 3),
        )
        
        # 使用精简的工具管理器设置工具
        tool_manager = ToolManager(
            config_path="tools_config.json",
            logger=self._logger,
            timeout=30
        )
        await tool_manager.setup_tools(agent)

        # tool_manager._tools
        # agent.guideline_store.create_guideline(
        #     condition="The user is a customer of the product",
        #     action="Provide current price list and any active discounts",
        #     tools=tool_manager._tools.keys()
        # )
        
        self._logger.error(f"重写创建方法，添加个性化逻辑，成功创建智能体 {agent.id} for customer {customer_id}")
        return agent
    




async def initialize_agent_factory(container: p.Container) -> None:
    container[p.AgentFactory] = CustomAgentFactory(
        agent_store=container[AgentStore],
        logger=container[p.Logger]
    )