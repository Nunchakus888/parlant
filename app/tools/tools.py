"""
工具管理器

精简的工具管理实现，支持动态工具创建和配置管理。
"""

import json
import asyncio
import aiohttp
from typing import Dict, Any, List, Optional, Annotated
from dataclasses import dataclass
from inspect import Parameter, Signature


@dataclass
class ToolConfig:
    """工具配置"""
    name: str
    description: str
    parameters: Dict[str, Any]
    endpoint: Dict[str, Any]


class ToolManager:
    """工具管理器"""
    
    def __init__(self, config_path: str = None, raw_configs: List[Dict[str, Any]] = None, logger=None, timeout: int = 60):
        self.config_path = config_path
        self.raw_configs = raw_configs
        self.logger = logger
        self.timeout = timeout
        self._tools: Dict[str, Any] = {}
    
    async def setup_tools(self, agent) -> None:
        """为智能体设置工具"""
        self._log_info("开始为智能体设置工具...")
        
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
        
        self._log_info(f"工具设置完成，成功设置 {successful_tools}/{len(configs)} 个工具")
    
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
                self._log_info(f"使用直接传入的工具配置，共 {len(self.raw_configs)} 个工具")
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
                    config = ToolConfig(
                        name=raw_config["name"],
                        description=raw_config["description"],
                        parameters=raw_config.get("parameters", {}),
                        endpoint=raw_config.get("endpoint", {})
                    )
                    configs.append(config)
                except Exception as e:
                    self._log_error(f"解析工具配置失败: {str(e)}")
                    continue
            
            self._log_info(f"成功加载 {len(configs)} 个工具配置")
            return configs
            
        except FileNotFoundError:
            self._log_warning(f"配置文件 {self.config_path} 不存在")
            return []
        except Exception as e:
            self._log_error(f"加载配置文件失败: {str(e)}")
            return []
    
    def _create_tool(self, config: ToolConfig):
        """创建工具函数"""
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
        
        # 定义函数体
        async def dynamic_tool_func(*args, **kwargs):
            try:
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
                result = await self._call_api(config, params)
                
                self._log_info(f"工具 {config.name} 执行成功")
                
                # 处理静态响应，支持 control 参数
                if isinstance(result, dict) and "data" in result:
                    control = result.get("control", {})
                    return p.ToolResult(data=result["data"], control=control)
                else:
                    return p.ToolResult(data=result)
                
            except Exception as e:
                self._log_error(f"工具 {config.name} 执行失败: {str(e)}")
                return p.ToolResult(data={"error": str(e)})
        
        # 设置元数据
        dynamic_tool_func.__name__ = config.name
        dynamic_tool_func.__doc__ = config.description
        dynamic_tool_func.__signature__ = signature
        
        return p.tool(dynamic_tool_func)
    
    async def _call_api(self, config: ToolConfig, params: Dict[str, Any], max_retries: int = 2) -> Dict[str, Any]:
        """调用API，支持重试机制"""
        # # 过滤掉 None 值
        # params = {k: v for k, v in params.items() if v is not None}
        
        endpoint = config.endpoint
        
        # 检查是否为静态响应
        if endpoint.get("url", "").startswith("static://"):
            self._log_info(f"🔧 静态工具调用: {config.name}")
            static_response = endpoint.get("response", {})
            self._log_debug(f"📨 静态响应: {static_response}")
            return static_response
        
        # 替换占位符
        url = self._replace_placeholders(endpoint["url"], params)
        method = endpoint.get("method", "GET").upper()
        headers = self._replace_placeholders(endpoint.get("headers", {}), params)
        body = self._replace_placeholders(endpoint.get("body"), params) if endpoint.get("body") else None
        
        # 处理查询参数
        query_params = {}
        if method == "GET":
            # 收集已使用的参数
            used_params = set()
            for param_name in params:
                if f"{{{param_name}}}" in endpoint.get("url", ""):
                    used_params.add(param_name)
                if f"{{{param_name}}}" in str(endpoint.get("headers", {})):
                    used_params.add(param_name)
                if endpoint.get("body") and f"{{{param_name}}}" in str(endpoint.get("body")):
                    used_params.add(param_name)
            
            # 剩余参数作为查询参数
            query_params = {k: v for k, v in params.items() if k not in used_params}
        
        # 记录请求信息
        self._log_info(f"🚀 API调用: {method} {url}")
        if headers:
            self._log_debug(f"📋 请求头: {headers}")
        if query_params:
            self._log_debug(f"❓ Query参数: {query_params}")
        if body:
            self._log_debug(f"📦 请求体: {json.dumps(body, ensure_ascii=False, indent=2)}")
        
        # 重试机制
        last_exception = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                self._log_info(f"🔄 重试第 {attempt} 次...")
                await asyncio.sleep(1 * attempt)  # 递增延迟
            
            # 发送请求
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                try:
                    if method == "GET":
                        async with session.get(url, params=query_params, headers=headers) as response:
                            self._log_info(f"✅ 响应状态: {response.status}")
                            result = await response.json()
                            self._log_debug(f"📨 响应数据: {result}")
                            return result
                    else:
                        async with session.request(method, url, json=body, params=query_params, headers=headers) as response:
                            self._log_info(f"✅ 响应状态: {response.status}")
                            result = await response.json()
                            self._log_debug(f"📨 响应数据: {result}")
                            return result
                except aiohttp.ClientTimeout as e:
                    last_exception = e
                    self._log_error(f"❌ API调用超时 ({self.timeout}秒) - 尝试 {attempt + 1}/{max_retries + 1}: {str(e)}")
                    if attempt == max_retries:
                        raise Exception(f"API调用超时，已重试 {max_retries} 次，请检查网络连接")
                except aiohttp.ClientError as e:
                    last_exception = e
                    self._log_error(f"❌ API调用网络错误 - 尝试 {attempt + 1}/{max_retries + 1}: {str(e)}")
                    if attempt == max_retries:
                        raise Exception(f"网络连接错误，已重试 {max_retries} 次: {str(e)}")
                except Exception as e:
                    last_exception = e
                    error_msg = str(e) if str(e) else f"未知错误: {type(e).__name__}"
                    self._log_error(f"❌ API调用失败 - 尝试 {attempt + 1}/{max_retries + 1}: {error_msg}")
                    if attempt == max_retries:
                        raise Exception(f"API调用失败，已重试 {max_retries} 次: {error_msg}")
        
        # 如果所有重试都失败了
        if last_exception:
            if isinstance(last_exception, Exception):
                raise last_exception
            else:
                raise Exception(f"API调用失败: {str(last_exception)}")
    
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
