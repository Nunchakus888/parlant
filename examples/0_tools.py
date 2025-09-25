# healthcare.py

import parlant.sdk as p
import asyncio
import json
import os
import aiohttp
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Annotated
from parlant.core.tools import ToolParameterOptions
from parlant.core.loggers import Logger, LogLevel, StdoutLogger
from parlant.core.contextual_correlator import ContextualCorrelator
from parlant.core.agents import AgentStore
from initialize_agent_factory import initialize_agent_factory

# load env
from dotenv import load_dotenv
load_dotenv()

logger = None


# 通用 API 调用配置加载和工具生成
def load_tools_config(config_path: str = "tools_config.json") -> Dict[str, Any]:
    """加载工具配置文件"""
    try:
        logger.info(f"加载工具配置文件: {config_path}")
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"成功加载 {len(config)} 个工具配置")
        return config
    except FileNotFoundError:
        logger.warning(f"配置文件 {config_path} 不存在")
        return []
    except Exception as e:
        logger.error(f"加载配置文件失败: {str(e)}")
        return []


def replace_placeholders(template: Any, params: Dict[str, Any]) -> Any:
    """递归替换模板中的占位符
    
    支持：
    - 字符串中的 {param_name} 占位符
    - 嵌套的字典和列表结构
    - 保持原始数据类型
    """
    if isinstance(template, str):
        # 替换字符串中的所有占位符
        result = template
        for param_name, param_value in params.items():
            placeholder = f"{{{param_name}}}"
            if placeholder in result:
                # 如果整个字符串就是一个占位符，直接返回原始值（保持类型）
                if result == placeholder:
                    return param_value
                # 否则转换为字符串进行替换
                result = result.replace(placeholder, str(param_value))
        return result
    elif isinstance(template, dict):
        # 递归处理字典
        return {key: replace_placeholders(value, params) for key, value in template.items()}
    elif isinstance(template, list):
        # 递归处理列表
        return [replace_placeholders(item, params) for item in template]
    else:
        # 其他类型直接返回
        return template


async def call_api(config: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """通用 API 调用函数，支持灵活的配置格式"""
    
    # 过滤掉 None 值
    params = {k: v for k, v in params.items() if v is not None}
    
    # 支持两种配置格式：api 和 endpoint
    if "endpoint" in config:
        # 新格式：更灵活的配置
        endpoint_config = config["endpoint"]
        
        # 替换 URL 中的占位符
        url = replace_placeholders(endpoint_config["url"], params)
        method = endpoint_config.get("method", "GET").upper()
        
        # 替换 headers 中的占位符
        headers = replace_placeholders(endpoint_config.get("headers", {}), params)
        
        # 替换 body 中的占位符（对于非 GET 请求）
        if method != "GET" and "body" in endpoint_config:
            body = replace_placeholders(endpoint_config["body"], params)
        else:
            body = None
        
        # GET 请求使用未在 URL/headers/body 中使用的参数作为 query 参数
        if method == "GET":
            # 收集已使用的参数
            used_params = set()
            for param_name in params:
                if f"{{{param_name}}}" in endpoint_config.get("url", ""):
                    used_params.add(param_name)
                # 检查 headers 中是否使用
                headers_str = str(endpoint_config.get("headers", {}))
                if f"{{{param_name}}}" in headers_str:
                    used_params.add(param_name)
            # 剩余参数作为 query 参数
            query_params = {k: v for k, v in params.items() if k not in used_params}
        else:
            query_params = {}
    
    # 记录API调用信息
    logger.info(f"🚀 API调用: {method} {url}")
    if headers:
        logger.debug(f"📋 请求头: {headers}")
    if query_params:
        logger.debug(f"❓ Query参数: {query_params}")
    if body:
        logger.debug(f"📦 请求体: {json.dumps(body, ensure_ascii=False, indent=2)}")
    
    # 发送请求
    async with aiohttp.ClientSession() as session:
        try:
            if method == "GET":
                async with session.get(url, params=query_params, headers=headers) as response:
                    logger.info(f"✅ 响应状态: {response.status}")
                    result = await response.json()
                    logger.debug(f"📨 响应数据: {result}")
                    return result
            else:
                async with session.request(method, url, json=body, params=query_params, headers=headers) as response:
                    logger.info(f"✅ 响应状态: {response.status}")
                    result = await response.json()
                    logger.debug(f"📨 响应数据: {result}")
                    return result
        except Exception as e:
            logger.error(f"❌ API调用失败: {str(e)}")
            raise


def create_dynamic_tool(tool_config: Dict[str, Any]):
    """根据配置动态创建工具函数，使用 Annotated 传递参数描述"""
    from inspect import Parameter, Signature
    
    tool_name = tool_config["name"]
    description = tool_config["description"]
    parameters = tool_config.get("parameters", {})
    properties = parameters.get("properties", {})
    required_params = parameters.get("required", [])
    
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
    
    # 处理所有参数（先必需参数，后可选参数）
    for param_name, param_config in properties.items():
        param_type = type_mapping.get(param_config.get("type", "string"), str)
        is_required = param_name in required_params
        default_value = param_config.get("default") if not is_required else None

        # 从配置中提取参数描述
        param_description = param_config.get("description", f"Parameter {param_name}")
        param_examples = param_config.get("examples", [])
        
        # 只传递必要的描述信息，避免冗余
        # 框架会自动将 ToolParameterOptions 中的信息复制到 ToolParameterDescriptor
        annotated_type = Annotated[param_type, ToolParameterOptions(
            description=param_description,
            examples=param_examples
        )]
        
        # 创建参数，根据是否必需设置默认值
        if is_required:
            sig_params.append(Parameter(param_name, Parameter.POSITIONAL_OR_KEYWORD, annotation=annotated_type))
        else:
            sig_params.append(Parameter(param_name, Parameter.POSITIONAL_OR_KEYWORD, annotation=annotated_type, default=default_value))
        
        call_params.append(param_name)
    
    # 创建函数签名
    signature = Signature(sig_params, return_annotation=p.ToolResult)
    
    # 定义函数体
    async def dynamic_tool_func(*args, **kwargs):
        try:
            bound_args = signature.bind(*args, **kwargs)
            bound_args.apply_defaults()
            
            # 直接使用参数作为 API 请求参数
            params = {}
            for param_name in call_params:
                if param_name in bound_args.arguments:
                    params[param_name] = bound_args.arguments[param_name]
            
            logger.info(f"调用动态工具: {tool_name}")
            logger.debug(f"工具参数: {params}")
            
            result = await call_api(tool_config, params)
            
            logger.info(f"工具 {tool_name} 执行成功")
            return p.ToolResult(data=result)
            
        except Exception as e:
            logger.error(f"工具 {tool_name} 执行失败: {str(e)}")
            return p.ToolResult(data={"error": str(e)})
    
    # 设置元数据
    dynamic_tool_func.__name__ = tool_name
    dynamic_tool_func.__doc__ = description
    dynamic_tool_func.__signature__ = signature

    return p.tool(dynamic_tool_func)


async def setup_agent_with_tools(agent) -> None:
    """为智能体初始化工具和设置指导原则
    
    这个函数整合了工具初始化和指导原则设置，避免了全局变量的使用。
    每次调用都会重新加载工具配置，确保工具配置的实时性。
    """
    logger.info("开始为智能体设置工具和指导原则...")
    
    # 加载工具配置
    tools_config = load_tools_config()
    if not tools_config:
        logger.warning("没有找到工具配置，跳过工具设置")
        return
    
    # 创建动态工具并设置指导原则
    dynamic_tools = {}
    successful_tools = 0
    
    for tool_config in tools_config:
        tool_name = tool_config.get("name")
        if not tool_name:
            logger.warning(f"跳过无效的工具配置: {tool_config}")
            continue
            
        try:
            # 创建动态工具
            tool_func = create_dynamic_tool(tool_config)
            dynamic_tools[tool_name] = tool_func
            
            # 设置指导原则
            # action = f"Use the {tool_name} tool: {tool_config['description']}"
            # await agent.create_guideline(
            #     condition=tool_config['description'],
            #     action=action,
            #     tools=[tool_func],
            # )
            
            successful_tools += 1
            logger.debug(f"✅ 成功设置工具: {tool_name}")
            
        except Exception as e:
            logger.error(f"❌ 设置工具 {tool_name} 失败: {str(e)}")


async def main() -> None:
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
        
        logger.info("启动 Parlant 服务器...")
        
        # 初始化工具
        # await initialize_tools()

        # 创建一个默认智能体
        # default_agent = await server_ref.create_agent(
        #     name="Default Agent",
        #     description="Default agent for testing",
        #     max_engine_iterations=3,
        # )
        # await setup_agent_guidelines(default_agent)
        
        # 注意：使用 agent_factory 后，不需要在这里创建智能体
        # 智能体会在第一次创建会话时自动创建
        # 这样的好处是：
        # 1. 可以根据不同客户创建个性化的智能体
        # 2. 延迟创建，节省资源
        # 3. 智能体配置与框架解耦，保持在用户代码中
        
        logger.info("服务器已启动，等待客户请求...")
        logger.info("当客户发起会话时，将自动创建个性化智能体")
        


if __name__ == "__main__":
    asyncio.run(main())
