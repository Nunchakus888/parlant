"""
知识库检索器模块

提供基于用户消息的实时知识库检索功能。
"""
from typing import Dict, Any, Optional
import httpx
import parlant.sdk as p
from parlant.core.loggers import Logger


class KnowledgeRetriever:
    """知识库检索器，负责从外部知识库API检索相关信息"""
    
    def __init__(
        self, 
        chatbot_id: str, 
        retrieve_url: str,
        logger: Logger,
        timeout: int = 10
    ):
        """
        初始化知识库检索器
        
        Args:
            chatbot_id: 机器人ID
            retrieve_url: 知识库检索API地址
            logger: 日志记录器
            timeout: HTTP请求超时时间(秒)
        """
        self.chatbot_id = chatbot_id
        self.retrieve_url = retrieve_url.strip()
        self.logger = logger
        self.timeout = timeout
        
    async def retrieve(self, context: p.RetrieverContext) -> p.RetrieverResult:
        """
        基于对话上下文检索知识库
        
        Args:
            context: Parlant提供的检索器上下文，包含对话历史和用户消息
            
        Returns:
            检索结果，包含从知识库获取的相关信息
        """
        import time
        start_time = time.time()
        # 获取用户最后一条消息作为检索关键词
        last_message = context.interaction.last_customer_message
        if not last_message or not last_message.content:
            self.logger.debug("🔍[KB] Skip: no customer message")
            return p.RetrieverResult(None)
        
        keywords = last_message.content.strip()
        if not keywords:
            return p.RetrieverResult(None)
            
        try:
            # 构造请求
            payload = {
                "chatbotId": self.chatbot_id,
                "keywords": keywords
            }
            
            self.logger.info(f"🔍[KB] Retrieving: payload={payload}")
            
            # 发送HTTP请求到知识库API
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.retrieve_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                
                result = response.json()
                elapsed = time.time() - start_time
                code = result.get('code')
                msg = result.get('msg', '')
                data_count = len(result.get('data', []))
                
                # 根据返回码判断成功或失败
                if code == 200 or code == 0:
                    self.logger.info(f"🔍[KB]✅ Success: items={data_count}, time={elapsed:.2f}s")
                else:
                    self.logger.error(f"🔍[KB] ❌ Failed: code={code}, msg={msg}, time={elapsed:.2f}s")
                
                # 返回检索结果，让Agent可以使用这些信息来回答用户
                return p.RetrieverResult(result)
                
        except httpx.TimeoutException:
            elapsed = time.time() - start_time
            self.logger.error(f"🔍[KB] ❌ Timeout: {self.timeout}s exceeded, time={elapsed:.2f}s")
            return p.RetrieverResult(None)
            
        except httpx.HTTPStatusError as e:
            elapsed = time.time() - start_time
            self.logger.error(f"🔍[KB] ❌ HTTP Error: status={e.response.status_code}, time={elapsed:.2f}s")
            return p.RetrieverResult(None)
            
        except Exception as e:
            elapsed = time.time() - start_time
            self.logger.error(f"🔍[KB] ❌ Error: {type(e).__name__} - {str(e)}, time={elapsed:.2f}s")
            return p.RetrieverResult(None)


def create_knowledge_retriever(
    chatbot_id: str,
    retrieve_url: str, 
    logger: Logger,
    timeout: int = 10
) -> KnowledgeRetriever:
    """
    工厂函数：创建知识库检索器实例
    
    Args:
        chatbot_id: 机器人ID
        retrieve_url: 知识库检索API地址
        logger: 日志记录器
        timeout: HTTP请求超时时间(秒)
        
    Returns:
        配置好的知识库检索器实例
    """
    return KnowledgeRetriever(
        chatbot_id=chatbot_id,
        retrieve_url=retrieve_url,
        logger=logger,
        timeout=timeout
    )