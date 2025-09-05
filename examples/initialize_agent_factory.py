from parlant.core.agent_factory import AgentFactory
import parlant.sdk as p
from parlant.core.agents import AgentStore

class CustomAgentFactory(AgentFactory):
    async def create_agent_for_customer(self, customer_id: p.CustomerId) -> p.Agent:

        self._logger.error(f"重写创建方法，添加个性化逻辑，为客户 {customer_id} 创建个性化智能体...")
        
        customer_config = {
            "name": f"Agent for {customer_id}",
            "description": f"Personalized agent for customer {customer_id}",
            "max_engine_iterations": 3,
        }

        agent = await self._agent_store.create_agent(
            name=customer_config["name"],
            description=customer_config["description"],
            max_engine_iterations=customer_config.get("max_engine_iterations", 3),
        )
        
        # await setup_agent_with_tools(agent)
        
        self._logger.error(f"重写创建方法，添加个性化逻辑，成功创建智能体 {agent.id} for customer {customer_id}")
        return agent
    




async def initialize_agent_factory(container: p.Container) -> None:
    container[p.AgentFactory] = CustomAgentFactory(
        agent_store=container[AgentStore],
        logger=container[p.Logger]
    )