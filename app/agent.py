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

    async with p.Server(
        nlp_service=p.NLPServices.openrouter,
        log_level=LogLevel.DEBUG,
        session_store=mongodb_url,
        initialize_container=initialize_agent_factory
    ) as server:

        global logger
        logger = server._container[p.Logger]

        logger.info("服务器已启动，等待客户请求...")
        logger.info("当客户发起会话时，将自动创建个性化智能体并设置工具")


if __name__ == "__main__":
    asyncio.run(main())
