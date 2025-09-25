"""
HTTP配置模块

提供从HTTP请求获取Agent配置信息的功能
"""

import json
from typing import Dict, Any, Optional
from dataclasses import dataclass
import aiohttp
import os


class AgentConfigError(Exception):
    """Agent配置相关业务异常"""
    def __init__(self, message: str, code: int = None):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class AgentConfigRequest:
    """Agent配置请求参数结构"""
    tenant_id: str
    chatbot_id: str
    preview: bool = False
    action_book_id: Optional[str] = None
    extra_param: Optional[Dict[str, Any]] = None
    md5_checksum: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentConfigRequest':
        """
        从字典创建AgentConfigRequest实例
        
        Args:
            data: 包含请求参数的字典
            
        Returns:
            AgentConfigRequest实例
        """
        return cls(
            tenant_id=data["tenantId"],
            chatbot_id=data["chatbotId"],
            preview=data.get("preview", False),
            action_book_id=data.get("actionBookId"),
            extra_param=data.get("extraParam"),
            md5_checksum=data.get("md5Checksum")
        )


class HttpConfigLoader:
    """HTTP配置加载器"""
    
    def __init__(self, logger):
        self.logger = logger
    
    async def load_config_from_http(self, request: AgentConfigRequest) -> Dict[str, Any]:
        """
        从HTTP请求获取配置信息
        
        Args:
            request: 配置请求参数
            base_url: API基础URL
            
        Returns:
            配置字典，结构与本地配置文件一致
            
        Raises:
            httpx.HTTPError: HTTP请求失败
            AgentConfigError: 业务逻辑错误（如配置未找到、验证失败等）
            ValueError: 响应数据格式错误
        """
        api_path = "/chatbot/ai-inner/get-agent-config"
        url = f"{os.environ.get('AGENT_CONFIGS_HOST').rstrip('/')}{api_path}"
        
        # 构建请求体
        request_data = {
            "tenantId": request.tenant_id,
            "chatbotId": request.chatbot_id,
            "preview": request.preview,
            "actionBookId": request.action_book_id,
            "extraParam": request.extra_param or {}
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=10.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                self.logger.info(f"正在从 {url} 获取配置信息...")
                async with session.post(
                    url,
                    json=request_data,
                    headers={"Content-Type": "application/json"}
                ) as response:
                    response.raise_for_status()
                    
                    res = await response.json()
                    if res.get("code") != 0:
                        error_code = res.get("code")
                        error_message = res.get("message", "未知业务错误")
                        self.logger.error(f"业务请求失败: code={error_code}, message={error_message}")
                        raise AgentConfigError(error_message, error_code)
                    
                    self.logger.info(f"✅成功加载配置: {json.dumps(res.get('data'), ensure_ascii=False, indent=2)}")
                    return res.get("data")
                
        except aiohttp.ClientError as e:
            self.logger.error(f"HTTP请求失败: {e}")
            raise
        except json.JSONDecodeError as e:
            self.logger.error(f"响应数据格式错误: {e}")
            raise ValueError(f"Invalid JSON response: {e}")
        except Exception as e:
            self.logger.error(f"获取配置信息时发生未知错误: {e}")
            raise
