import parlant.sdk as p
import asyncio
import os
from parlant.core.loggers import Logger, LogLevel, StdoutLogger
from parlant.core.contextual_correlator import ContextualCorrelator
from parlant.core.agents import AgentStore
from tools.initialize_agent_factory import initialize_agent_factory
from configs.apollo_config import load_apollo_config_from_env

# load env
from dotenv import load_dotenv
from utils.format import encode_mongodb_url

logger = None


async def main() -> None:
    """主函数"""
    print("🚀 开始启动 Parlant 服务器...")
    
    # 尝试从Apollo加载配置
    try:
        print("📡 正在从Apollo配置中心加载配置...")
        apollo_config = await load_apollo_config_from_env()
        print(f"✅ 成功从Apollo加载配置，包含 {len(apollo_config)} 个配置项")
    except Exception as e:
        print(f"⚠️  从Apollo加载配置失败: {e}")
        load_dotenv()

    async with p.Server(
        nlp_service=p.NLPServices.openrouter,
        log_level=LogLevel.TRACE if os.getenv("DEPLOY_ENV") != "online" else LogLevel.DEBUG,
        session_store=encode_mongodb_url(os.environ.get("MONGODB_SESSION_STORE")),
        initialize_container=initialize_agent_factory
    ) as server:


        global logger
        logger = server._container[p.Logger]
        
        server._container._server_ref = server

        logger.info("服务器已启动，等待客户请求...")
        logger.info("当客户发起会话时，将自动创建Agent并设置Actionbooks")


if __name__ == "__main__":
    asyncio.run(main())
