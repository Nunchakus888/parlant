# healthcare.py

import parlant.sdk as p
import asyncio
from datetime import datetime
import os
from app.tools.initialize_agent_factory import initialize_agent_factory

from dotenv import load_dotenv
load_dotenv()


async def main() -> None:
    mongodb_url = os.environ.get("MONGODB_SESSION_STORE", "mongodb://localhost:27017")
    async with p.Server(
        nlp_service=p.NLPServices.openrouter,
        log_level=p.LogLevel.TRACE,
        session_store=mongodb_url,
    ) as server:

        # 根据 lead-acquistion.json 创建智能体
        await initialize_agent_factory(server._container)
        
        # 将 server 对象存储到 container 中，供 AgentFactory 使用
        server._container._server_ref = server

        # create_agent_for_customer
        agent = await server._container[p.AgentFactory].create_agent_for_customer(p.CustomerId("123"))




        


if __name__ == "__main__":
    asyncio.run(main())
