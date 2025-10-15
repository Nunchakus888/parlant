"""
工具管理器

精简的工具管理实现，支持动态工具创建和配置管理。
纯函数式设计
"""

import json
import asyncio
import aiohttp
import time
from typing import Dict, Any, List, Optional, Annotated, Union
from dataclasses import dataclass, field
from inspect import Parameter, Signature
from pydantic import BaseModel


@dataclass(frozen=True)
class ToolEndpointConfig:
    """工具端点配置 - 不可变"""
    url: str
    method: str = "GET"
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "url": self.url,
            "method": self.method,
            "headers": dict(self.headers),
            "body": self.body
        }


@dataclass(frozen=True)
class ToolConfig:
    """工具配置 - 不可变"""
    name: str
    description: str
    parameters: Dict[str, Any]
    endpoint: ToolEndpointConfig


class ApiResponse(BaseModel):
    """API响应结构"""
    success: bool
    data: Optional[Any] = None
    error: Optional[Any] = None
    message: Optional[str] = None
    status_code: Optional[int] = None
    duration: Optional[float] = None


class ToolManager:
    """工具管理器"""
    
    def __init__(self, config_path: str = None, raw_configs: List[Dict[str, Any]] = None, logger=None, timeout: int = 10):
        self.config_path = config_path
        self.raw_configs = raw_configs
        self.logger = logger
        self.timeout = timeout
        self._tools: Dict[str, Any] = {}
    
    async def setup_tools(self, agent) -> None:
        """为智能体设置工具"""
        
        # 加载配置
        configs = await self._load_configs()
        if not configs:
            self._log_warning("没有找到工具配置，跳过工具设置")
            return
        
        # 创建工具
        successful_tools = 0
        for config in configs:
            try:
                tool = self._create_tool(config)
                if tool:
                    self._tools[config.name] = tool
                    successful_tools += 1
                    self._log_debug(f"✅ 成功设置工具: {config.name}")
            except Exception as e:
                self._log_error(f"❌ 设置工具 {config.name} 失败: {str(e)}")
                raise e
        
        self._log_info(f"✅successfully setup {successful_tools}/{len(configs)} tools")
    
    def get_tool(self, name: str) -> Optional[Any]:
        """获取指定工具"""
        return self._tools.get(name)
    
    def list_tools(self) -> List[str]:
        """列出所有工具名称"""
        return list(self._tools.keys())
    
    async def _load_configs(self) -> List[ToolConfig]:
        """加载工具配置"""
        try:
            # 优先使用直接传入的配置
            if self.raw_configs is not None:
                raw_configs = self.raw_configs
            elif self.config_path is not None:
                self._log_info(f"从配置文件加载工具配置: {self.config_path}")
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    raw_configs = json.load(f)
            else:
                self._log_warning("没有提供工具配置（config_path 或 raw_configs）")
                return []
            
            configs = []
            for raw_config in raw_configs:
                try:
                    endpoint_data = raw_config.get("endpoint", {})
                    endpoint = ToolEndpointConfig(
                        url=endpoint_data.get("url", ""),
                        method=endpoint_data.get("method", "GET"),
                        headers=dict(endpoint_data.get("headers", {})),
                        body=endpoint_data.get("body")
                    )
                    
                    config = ToolConfig(
                        name=raw_config["name"],
                        description=raw_config["description"],
                        parameters=raw_config.get("parameters", {}),
                        endpoint=endpoint
                    )
                    configs.append(config)
                except Exception as e:
                    self._log_error(f"解析工具配置失败: {str(e)}")
                    continue

            return configs
            
        except FileNotFoundError:
            self._log_warning(f"配置文件 {self.config_path} 不存在")
            return []
        except Exception as e:
            self._log_error(f"加载配置文件失败: {str(e)}")
            return []
    
    def _create_tool(self, config: ToolConfig):
        """
        创建工具函数
        纯函数式设计
        """
        import parlant.sdk as p
        from parlant.core.tools import ToolParameterOptions
        
        # 类型映射
        type_mapping = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict
        }
        
        # 构建函数签名
        sig_params = [Parameter('context', Parameter.POSITIONAL_OR_KEYWORD, annotation=p.ToolContext)]
        call_params = []
        
        properties = config.parameters.get("properties", {})
        required_params = config.parameters.get("required", [])
        
        for param_name, param_config in properties.items():
            param_type = type_mapping.get(param_config.get("type", "string"), str)
            
            has_default = "default" in param_config
            is_required = param_name in required_params and not has_default  # 有默认值就不算必需
            # 创建带注解的类型
            annotated_type = Annotated[param_type, ToolParameterOptions(
                description=param_config.get("description", f"Parameter {param_name}"),
                examples=param_config.get("examples", [])
            )]
            
            # 创建参数
            if is_required:
                sig_params.append(Parameter(param_name, Parameter.POSITIONAL_OR_KEYWORD, annotation=annotated_type))
            else:
                # 只有当配置中有default时才设置默认值
                if has_default:
                    sig_params.append(Parameter(param_name, Parameter.POSITIONAL_OR_KEYWORD, annotation=annotated_type, default=param_config["default"]))
                else:
                    sig_params.append(Parameter(param_name, Parameter.POSITIONAL_OR_KEYWORD, annotation=annotated_type))
            
            call_params.append(param_name)
        
        # 创建函数签名
        signature = Signature(sig_params, return_annotation=p.ToolResult)
        
        # 定义纯函数体，避免闭包
        async def dynamic_tool_func(*args, **kwargs):
            try:
                tool_config = dynamic_tool_func._tool_config

                bound_args = signature.bind(*args, **kwargs)
                bound_args.apply_defaults()
                
                # 提取参数
                params = {}
                for param_name in call_params:
                    if param_name in bound_args.arguments:
                        params[param_name] = bound_args.arguments[param_name]
                
                self._log_info(f"调用动态工具: {config.name}")
                self._log_debug(f"工具参数: {params}")
                
                # 调用API
                result = await self._call_api(tool_config.endpoint, params)
                
                if result.success:
                    duration_info = f" - duration: {result.duration:.3f}s" if result.duration else ""
                    self._log_info(f"工具 {tool_config.name} 执行成功{duration_info}")
                else:
                    duration_info = f" - duration: {result.duration:.3f}s" if result.duration else ""
                    self._log_error(f"工具 {tool_config.name} 执行失败: {result.message or result.error or '未知错误'}{duration_info}")
                
                return p.ToolResult(data=result.dict())
                
            except Exception as e:
                # 从函数属性获取配置
                tool_config = dynamic_tool_func._tool_config
                # 构建详细的错误消息
                detailed_message = f"Tool execution failed: {tool_config.name} - {type(e).__name__}: {str(e)}"
                
                self._log_error(f"工具 {tool_config.name} 执行失败: {str(e)}")
                
                error_response = ApiResponse(
                    success=False,
                    error=str(e),
                    message=detailed_message,
                    status_code=500,
                    duration=10
                )
                return p.ToolResult(data=error_response.dict())
        
        # 🔧 设置元数据和配置属性（避免闭包）
        dynamic_tool_func.__name__ = config.name
        dynamic_tool_func.__doc__ = config.description
        dynamic_tool_func.__signature__ = signature
        dynamic_tool_func._tool_config = config  # 不可变配置作为函数属性，无需担心共享
        
        return p.tool(dynamic_tool_func)
    
    async def _call_api(self, endpoint: ToolEndpointConfig, params: Dict[str, Any]) -> ApiResponse:
        start_time = time.time()

        def get_duration():
            return (time.time() - start_time) or self.timeout
        
        # 检查是否为静态响应
        if endpoint.url.startswith("static://"):
            self._log_info(f"🔧 static tool call")
            # 静态响应暂不实现，可以扩展
            return ApiResponse(success=True, data={}, duration=get_duration())
        
        # 🔧 替换工具参数占位符（如 {assignee_id}、{type} 等）
        url = self._replace_placeholders(endpoint.url, params)
        method = endpoint.method.upper()
        headers = self._replace_placeholders(endpoint.headers, params)
        body = self._replace_placeholders(endpoint.body, params) if endpoint.body else None
        
        # 处理body：如果是字符串，尝试解析为JSON对象
        if body is not None and isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                self._log_warning(f"Body字符串不是有效的JSON格式，将作为原始字符串发送: {str(e)}")
                # 如果解析失败，保持原样，但需要特殊处理
        
        # 处理查询参数
        query_params = {}
        if method == "GET":
            # 收集已使用的参数
            used_params = set()
            for param_name in params:
                if f"{{{param_name}}}" in endpoint.url:
                    used_params.add(param_name)
                if f"{{{param_name}}}" in str(endpoint.headers):
                    used_params.add(param_name)
                if endpoint.body and f"{{{param_name}}}" in str(endpoint.body):
                    used_params.add(param_name)
            
            # 剩余参数作为查询参数
            query_params = {k: v for k, v in params.items() if k not in used_params}
        
        # 记录请求信息
        self._log_info(f"🚀 API call: {method} {url}")
        if headers:
            self._log_debug(f"📋 request headers: {headers}")
        if query_params:
            self._log_debug(f"❓ Query parameters: {query_params}")
        if body:
            self._log_debug(f"📦 request body: {json.dumps(body, ensure_ascii=False, indent=2)}")
        
        # 发送请求
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        try:
            self._log_debug(f"🔧 创建aiohttp会话，超时设置: {self.timeout}s")
            async with aiohttp.ClientSession(timeout=timeout) as session:
                self._log_debug(f"🚀 开始发送{method}请求到: {url}")
                
                try:
                    if method == "GET":
                        self._log_debug("📤 执行GET请求")
                        async with session.get(url, params=query_params, headers=headers) as response:
                            self._log_debug(f"📥 收到响应，状态码: {response.status}")
                            result = await self._parse_response(response)
                            return self._format_response(response.status, result, get_duration())
                    else:
                        self._log_debug(f"📤 执行{method}请求")
                        # 根据body类型选择正确的发送方式
                        if body is None:
                            # 没有body
                            async with session.request(method, url, params=query_params, headers=headers) as response:
                                self._log_debug(f"📥 收到响应，状态码: {response.status}")
                                result = await self._parse_response(response)
                                return self._format_response(response.status, result, get_duration())
                        elif isinstance(body, (dict, list)):
                            # body是对象或数组，使用json参数
                            async with session.request(method, url, json=body, params=query_params, headers=headers) as response:
                                self._log_debug(f"📥 收到响应，状态码: {response.status}")
                                result = await self._parse_response(response)
                                return self._format_response(response.status, result, get_duration())
                        else:
                            # body是字符串或其他类型，使用data参数
                            async with session.request(method, url, data=body, params=query_params, headers=headers) as response:
                                self._log_debug(f"📥 收到响应，状态码: {response.status}")
                                result = await self._parse_response(response)
                                return self._format_response(response.status, result, get_duration())
                except Exception as inner_e:
                    self._log_error(f"🔥 请求执行过程中发生错误: {str(inner_e)}")
                    self._log_error(f"🔥 错误类型: {type(inner_e).__name__}")
                    raise
        except Exception as session_e:
            duration = get_duration()
            self._log_error(f"🔥 会话创建或管理过程中发生错误: {str(session_e)}")
            self._log_error(f"🔥 错误类型: {type(session_e).__name__}")
            
            # 检查是否是BaseException相关错误
            if "catching classes that do not inherit from BaseException" in str(session_e):
                self._log_error("🚨 检测到BaseException相关错误！")
                self._log_error("这可能是aiohttp库内部的问题或Python环境问题")
                
            # 如果到这里，说明是aiohttp相关的异常，重新抛出让外层处理
            raise
        except aiohttp.ClientTimeout as e:
            duration = get_duration()
            timeout_message = f"Request timeout after {self.timeout} seconds - {method} {url}"
            self._log_error(f"❌ API call timeout ({self.timeout} seconds) - duration: {duration:.3f}s")
            return ApiResponse(
                success=False, 
                error=str(e), 
                message=timeout_message,
                duration=duration
            )
        except aiohttp.ClientError as e:
            duration = get_duration()
            network_message = f"Network connection error - {method} {url}: {str(e)}"
            self._log_error(f"❌ API call network error: {str(e)} - duration: {duration:.3f}s")
            return ApiResponse(
                success=False, 
                error=str(e), 
                message=network_message,
                duration=duration
            )
        except Exception as e:
            duration = get_duration()
            
            unexpected_message = f"Unexpected error occurred - {method} {url}: {type(e).__name__}: {str(e)}"
            return ApiResponse(
                success=False, 
                error=str(e), 
                message=unexpected_message,
                duration=duration
            )
    
    async def _parse_response(self, response: aiohttp.ClientResponse) -> Any:
        """解析HTTP响应，支持多种格式"""
        content_type = response.headers.get('content-type', '').lower()
        
        try:
            if 'application/json' in content_type:
                return await response.json()
            elif 'text/' in content_type or 'application/xml' in content_type:
                return await response.text()
            elif 'application/octet-stream' in content_type:
                return await response.read()
            else:
                try:
                    return await response.json()
                except Exception:
                    self._log_debug("❓ JSON解析失败，回退到文本解析")
                    return await response.text()
        except Exception as e:
            self._log_error(f"Failed to parse response: {str(e)}")
            return await response.text()
    
    def _format_response(self, status_code: int, result: Any, duration: float) -> ApiResponse:
        """统一格式化API响应"""
        if status_code >= 400:
            # 提取API返回的错误信息
            if isinstance(result, dict):
                api_error_msg = result.get('message', 'API call failed')
            elif isinstance(result, str):
                api_error_msg = result[:200] + '...' if len(result) > 200 else result  # 截断过长的文本
            else:
                api_error_msg = str(result)[:200] + '...' if len(str(result)) > 200 else str(result)
            
            # 构建更详细的错误消息，包含请求信息
            detailed_error_msg = f"HTTP {status_code}: {api_error_msg}"
            self._log_error(f"❌ API call failed: HTTP {status_code} - {api_error_msg} - duration: {duration:.3f}s")
            return ApiResponse(
                success=False,
                error=result,  # 保存完整的API响应作为原始错误信息
                message=detailed_error_msg,  # 用户友好的错误说明
                status_code=status_code,
                data=result,
                duration=duration
            )
        else:
            self._log_info(f"✅ API call success: HTTP {status_code} - duration: {duration:.3f}s")
            self._log_debug(f"📨 response data: {result}")
            return ApiResponse(
                success=True,
                data=result,
                status_code=status_code,
                duration=duration
            )
    
    def _replace_placeholders(self, template: Any, params: Dict[str, Any]) -> Any:
        """递归替换模板中的占位符"""
        if isinstance(template, str):
            result = template
            for param_name, param_value in params.items():
                placeholder = f"{{{param_name}}}"
                if placeholder in result:
                    if result == placeholder:
                        return param_value
                    result = result.replace(placeholder, str(param_value))
            return result
        elif isinstance(template, dict):
            return {key: self._replace_placeholders(value, params) for key, value in template.items()}
        elif isinstance(template, list):
            return [self._replace_placeholders(item, params) for item in template]
        else:
            return template
    
    def _log_info(self, message: str):
        """记录信息日志"""
        if self.logger:
            self.logger.info(message)
    
    def _log_debug(self, message: str):
        """记录调试日志"""
        if self.logger:
            self.logger.debug(message)
    
    def _log_warning(self, message: str):
        """记录警告日志"""
        if self.logger:
            self.logger.warning(message)
    
    def _log_error(self, message: str):
        """记录错误日志"""
        if self.logger:
            self.logger.error(message)
