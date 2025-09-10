"""
精简工具示例

使用单一文件的工具管理器，代码简洁易维护。
"""

import parlant.sdk as p
import asyncio
import os
from parlant.core.loggers import Logger, LogLevel, StdoutLogger
from parlant.core.contextual_correlator import ContextualCorrelator
from parlant.core.agents import AgentStore
from tools.initialize_agent_factory import initialize_agent_factory

# load env
from dotenv import load_dotenv
load_dotenv()

logger = None


async def main() -> None:
    """主函数"""
    # 使用mongodb存储会话和智能体
    mongodb_url = os.environ.get("MONGODB_SESSION_STORE", "mongodb://localhost:27017")

    print("🚀 开始启动 Parlant 服务器...")
    print(f"📁 配置文件路径: app/lead-acquistion.json")
    print(f"🔧 初始化函数: {initialize_agent_factory}")

    async with p.Server(
        nlp_service=p.NLPServices.openrouter,
        log_level=LogLevel.DEBUG,
        session_store=mongodb_url,
        initialize_container=initialize_agent_factory
    ) as server:


        # @p.tool
        # async def initiate_human_handoff(context: p.ToolContext, reason: str) -> p.ToolResult:
        #     """Initiate handoff to a human agent when the AI cannot adequately help the customer."""
            
        #     return p.ToolResult(
        #         data=f"Human handoff initiated because: {reason}",
        #         control={
        #             "mode": "manual"  # 关键：设置会话为手动模式
        #         }
        #     )

        # # 关联到 guideline
        # await agent.create_guideline(
        #     condition="Customer requests human assistance",
        #     action="Initiate human handoff and explain the transition professionally",
        #     tools=[initiate_human_handoff]
        # )
        # 
        # 发送人工代理消息
        # async def send_human_message(session_id: str, message: str, operator_name: str):
        #     event = await client.sessions.create_event(
        #         session_id=session_id,
        #         kind="message",
        #         source="human_agent",  # 标识为人工代理消息
        #         message=message,
        #         participant={
        #             "id": OPTIONAL_ID_FOR_EXTERNAL_SYSTEM_REFERENCE,
        #             "display_name": operator_name
        #         }
        #     )
        # 


        # 人工代理以 AI 身份发送消息（保持无缝体验）
        # async def send_message_as_ai(session_id: str, message: str):
        #     event = await client.sessions.create_event(
        #         session_id=session_id,
        #         kind="message",
        #         source="human_agent_on_behalf_of_ai_agent",  # 人工发送但显示为 AI
        #         message=message
        #     )
        # 
        # 
        # 

        global logger
        logger = server._container[p.Logger]
        
        # 将 server 对象存储到 container 中，供 AgentFactory 使用
        server._container._server_ref = server
        logger.info("✅ 已将 server 对象存储到 container 中")

        logger.info("服务器已启动，等待客户请求...")
        logger.info("当客户发起会话时，将自动创建个性化智能体并设置工具")


if __name__ == "__main__":
    asyncio.run(main())
