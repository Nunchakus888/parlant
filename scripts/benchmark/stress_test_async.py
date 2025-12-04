#!/usr/bin/env python3
"""
Chat Async API 压力测试 - 简洁高效

测试维度：
  - 同一会话并发: 测试会话级取消机制
  - 多会话并发: 测试跨会话并发能力
  - 混合场景: 测试复杂真实场景

完整流程：
  请求 → 立即响应 (200 + correlation_id) → 后台处理 → Webhook 回调

Webhook 配置：
  1. 本地模式 (WEBHOOK_MODE="local")
     - 启动本地 webhook 服务器监听回调
     - 适合本地开发测试
     - 回调地址: http://localhost:9999/webhook
  
  2. 远程模式 (WEBHOOK_MODE="remote")
     - 使用远程 webhook 服务接收回调
     - 适合生产环境测试
     - 回调地址: http://callback-dev.ycloud.com/api/callback/agent/receive
     - 提示: 确保远程服务能转发回调到本地或记录回调数据

使用方法：
  python scripts/benchmark/stress_test_async.py
"""
import asyncio
import httpx
import time
import random
from uuid import uuid4
from dataclasses import dataclass, field
from typing import List, Dict
from statistics import mean, median
from aiohttp import web


# ==================== 配置 ====================
BASE_URL = "http://localhost:8800"
WEBHOOK_PORT = 9999
TIMEOUT = 70.0

# Webhook 配置
WEBHOOK_MODE = "local"  # "local" 或 "remote"
REMOTE_WEBHOOK_URL = "http://callback-dev.ycloud.com/api/callback/agent/receive"

# 请求配置（与 ChatRequestDTO 一致）
TEST_TENANT_ID = "LT_async_chat_tenant"
TEST_CHATBOT_ID = "68d3a13c7158ef500f9f25a8"
TEST_TIMEOUT = 60

# 压力测试配置
AUTO_STOP_ON_LOW_SUCCESS = True  # 成功率过低时自动停止
LOW_SUCCESS_THRESHOLD = 70  # 低成功率阈值（%）

# 测试消息池 - 多语言多样化消息，包含百科知识和边界测试（80条）
MESSAGES = [
    # 基础对话 (10条)
    "Hello, how are you?",
    "What can you help me with?",
    "Tell me about your features",
    "Can you explain this in detail?",
    "Help me solve a problem",
    "Show me some examples",
    "What are your capabilities?",
    "I need assistance",
    "Thank you for your help",
    "Can you recommend something?",
    
    # 百科知识 - 科学 (10条)
    "What is quantum mechanics?",
    "Explain the theory of relativity",
    "How does photosynthesis work?",
    "What is DNA and how does it work?",
    "Explain the water cycle",
    "What causes earthquakes?",
    "How do black holes form?",
    "What is artificial intelligence?",
    "Explain climate change",
    "How does the human brain work?",
    
    # 百科知识 - 历史地理 (10条)
    "Who was Alexander the Great?",
    "What caused World War II?",
    "Tell me about the Renaissance",
    "Where is the Sahara Desert?",
    "What is the capital of Australia?",
    "Explain the Industrial Revolution",
    "Who invented the telephone?",
    "What is the Great Wall of China?",
    "Tell me about Ancient Egypt",
    "Where are the Himalayas?",
    
    # 百科知识 - 文化艺术 (10条)
    "Who wrote Romeo and Juliet?",
    "What is abstract art?",
    "Explain classical music",
    "Who painted the Mona Lisa?",
    "What is haiku poetry?",
    "Tell me about Greek mythology",
    "What is jazz music?",
    "Explain Renaissance art",
    "Who was Beethoven?",
    "What is origami?",
    
    # 边界测试 - 复杂问题 (10条)
    "What is the meaning of life?",
    "Can you solve unsolvable problems?",
    "Explain consciousness and self-awareness",
    "What happens after death?",
    "Is time travel possible?",
    "What is the nature of reality?",
    "Can machines truly think?",
    "What is infinity?",
    "Explain the paradox of free will",
    "What came before the Big Bang?",
    
    # 中文 (10条)
    "你好，我需要帮助",
    "请解释量子力学的基本原理",
    "中国的四大发明是什么？",
    "请介绍一下唐诗宋词",
    "人工智能的未来发展方向是什么？",
    "什么是区块链技术？",
    "请分析气候变化的影响",
    "如何理解相对论？",
    "请介绍丝绸之路的历史",
    "谢谢你的帮助",
    
    # 日文 (5条)
    "こんにちは、助けてください",
    "量子コンピューターとは何ですか？",
    "日本の歴史について教えてください",
    "人工知能の未来はどうなりますか？",
    "ありがとうございます",
    
    # 韩文 (5条)
    "안녕하세요, 도움이 필요합니다",
    "인공지능이란 무엇입니까?",
    "한국의 역사에 대해 알려주세요",
    "기후 변화에 대해 설명해 주세요",
    "감사합니다",
    
    # 法文 (5条)
    "Bonjour, j'ai besoin d'aide",
    "Qu'est-ce que l'intelligence artificielle?",
    "Expliquez la théorie de la relativité",
    "Parlez-moi de l'histoire de France",
    "Merci beaucoup",
    
    # 西班牙文 (5条)
    "Hola, necesito ayuda",
    "¿Qué es la inteligencia artificial?",
    "Explique la teoría de la evolución",
    "Hábleme de la cultura española",
    "Muchas gracias",
]

# ==================== 工具调用验证问题 ====================
# 针对 journey-tool.json 配置设计，用于验证 LLM 输出格式错误重试逻辑
# 这些问题会触发 SingleToolBatch / OverlappingToolsBatch / GuidelineMatching

TOOL_VALIDATION_MESSAGES = [
    # ===== 天气查询场景 (触发 city_geo_info + get_weather_by_geo 工具链) =====
    "What's the weather in Beijing?",
    "Tell me the weather in Shanghai today",
    "北京今天天气怎么样？",
    "上海明天会下雨吗？",
    "What's the temperature in Tokyo?",
    "How is the weather in New York right now?",
    "伦敦现在的天气情况如何？",
    "巴黎今天的气温是多少度？",
    
    # ===== 留资场景 (触发 save_customer_information 工具) =====
    "I want to schedule a demo, my email is test@example.com",
    "Can you tell me the pricing? I'm John, my phone is 13800138000",
    "I'd like a free trial, contact me at demo@test.com, my name is Alice",
    "我想了解详细的产品信息，我的邮箱是 user@company.com",
    "请联系我，电话 15912345678，地址是北京市朝阳区",
    "I need to speak with a salesperson. Email: sales@corp.io, Name: Bob Smith",
    "想要获取电子书，我是李明，邮箱 liming@test.cn，电话 13700000001",
    "请帮我预约演示，联系方式：王伟 wangwei@demo.com 手机 18600000002",
    
    # ===== 物流查询场景 (触发 tracking_inquiry 工具) =====
    "查询物流单号 YT1234567890123",
    "帮我查一下圆通快递 YT9876543210987",
    "Track my package: YT5555666677778",
    "我的包裹到哪了？单号是 YT1111222233334",
    
    # ===== 多工具混合场景 =====
    "I want to know the weather in Beijing and also schedule a demo. My email is mixed@test.com",
    "北京天气怎么样？另外我想咨询产品，电话 13900001111",
    
    # ===== 边界测试 (可能触发 LLM 输出格式错误) =====
    # 包含特殊 Unicode 字符
    "What's the weather in 北京市? 我的邮箱是 test@example.com",
    "天气查询：東京（Tokyo）",
    "请查询天气：São Paulo",
    "Weather in München please",
    
    # 超长输入（可能导致 LLM 输出不稳定）
    "I need help with weather information for the following cities: Beijing, Shanghai, Guangzhou, Shenzhen, Hangzhou, Nanjing, Chengdu, Wuhan, Xi'an, Suzhou. Please provide current temperature and conditions for each.",
    
    # 多语言混合（可能触发 Unicode 转义问题）
    "查询天气 for Paris, 我叫 François，邮箱 françois@example.com",
    "Погода в Москве? 莫斯科天气如何？",
    "東京の天気は？What about Tokyo weather?",
    
    # 模拟可能导致 JSON 解析问题的输入
    "My email has special chars: user+tag@example.com, name: O'Brien",
    "地址：北京市朝阳区「建国门外大街」1号",
    "Contact: test@test.com\nPhone: 123\nAddress: Line1\nLine2",
]

# 快速验证问题集（简化版，用于快速测试）
QUICK_VALIDATION_MESSAGES = [
    "北京天气怎么样？我的邮箱 user@test.cn",
    "What's the weather in Beijing?",
    "查询物流 YT1234567890123",
]


@dataclass
class Result:
    """测试结果 - 完整流程"""
    session_id: str
    correlation_id: str
    request_status: int  # 请求响应状态
    request_time: float  # 请求响应时间
    callback_status: str = ""  # 回调状态：SUCCESS/CANCELLED/TIMEOUT/ERROR
    callback_time: float = 0  # 从请求到回调的总时间
    total_time: float = 0  # 完整流程耗时
    success: bool = False  # 完整流程是否成功
    error: str = ""
    message: str = ""  # 测试消息内容
    
    @property
    def callback_received(self) -> bool:
        return bool(self.callback_status)
    
    def to_dict(self) -> dict:
        """转换为字典，便于 JSON 序列化"""
        return {
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "message": self.message,
            "request_status": self.request_status,
            "request_time": round(self.request_time, 3),
            "callback_status": self.callback_status,
            "callback_time": round(self.callback_time, 3),
            "total_time": round(self.total_time, 3),
            "success": self.success,
            "error": self.error,
        }


class WebhookServer:
    """Webhook 服务器 - 接收异步处理回调"""
    
    def __init__(self, port: int = WEBHOOK_PORT, mode: str = "remote"):
        self.port = port
        self.mode = mode
        self.callbacks: Dict[str, dict] = {}  # correlation_id -> callback_data
        self.app = web.Application()
        # 本地模式：监听 /webhook
        # 远程模式：也需要监听，因为远程可能会转发到这里
        self.app.router.add_post('/webhook', self.handle_webhook)
        # 兼容远程回调路径
        self.app.router.add_post('/api/callback/agent/receive', self.handle_webhook)
        self.runner = None
    
    async def handle_webhook(self, request: web.Request) -> web.Response:
        """处理 webhook 回调"""
        try:
            data = await request.json()
            correlation_id = data.get('correlation_id', '')
            
            # 记录回调数据
            self.callbacks[correlation_id] = {
                'status': data.get('message', ''),  # SUCCESS/CANCELLED/TIMEOUT_ERROR
                'code': data.get('code', 0),
                'data': data.get('data'),
                'received_at': time.time()
            }
            
            print(f"📩 收到回调: {correlation_id[:30]}... | 状态: {data.get('message', 'UNKNOWN')}")
            
            return web.Response(text='OK', status=200)
        except Exception as e:
            print(f"❌ Webhook 错误: {e}")
            return web.Response(text=f'Error: {e}', status=500)
    
    async def start(self):
        """启动 webhook 服务器"""
        if self.mode == "local":
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, '0.0.0.0', self.port)
            await site.start()
    
    async def stop(self):
        """停止 webhook 服务器"""
        if self.runner:
            await self.runner.cleanup()
    
    def get_callback(self, correlation_id: str) -> dict | None:
        """获取回调数据"""
        return self.callbacks.get(correlation_id)
    
    def get_webhook_url(self, remote_url: str = None) -> str:
        """获取 webhook URL"""
        if self.mode == "remote" and remote_url:
            return remote_url
        return f"http://localhost:{self.port}/webhook"


def save_results_to_json(results: List[Result], test_name: str, extra_info: dict = None) -> str:
    """
    保存测试结果到 JSON 文件
    
    Args:
        results: 测试结果列表
        test_name: 测试名称
        extra_info: 额外信息
    
    Returns:
        保存的文件路径
    """
    import os
    from datetime import datetime
    
    # 创建 logs 目录
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # 生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{test_name}_{timestamp}.json"
    filepath = os.path.join(log_dir, filename)
    
    # 统计信息
    success_count = sum(1 for r in results if r.success)
    callback_count = sum(1 for r in results if r.callback_received)
    request_times = [r.request_time for r in results if r.request_status == 200]
    total_times = [r.total_time for r in results if r.success]
    
    # 构建报告数据
    report = {
        "test_name": test_name,
        "timestamp": datetime.now().isoformat(),
        "config": {
            "base_url": BASE_URL,
            "tenant_id": TEST_TENANT_ID,
            "chatbot_id": TEST_CHATBOT_ID,
            "webhook_mode": WEBHOOK_MODE,
            "timeout": TEST_TIMEOUT,
        },
        "summary": {
            "total_requests": len(results),
            "successful": success_count,
            "callback_received": callback_count,
            "success_rate": round(success_count / len(results) * 100, 2) if results else 0,
            "callback_rate": round(callback_count / len(results) * 100, 2) if results else 0,
        },
        "timing": {
            "request_time_avg": round(mean(request_times), 3) if request_times else 0,
            "request_time_min": round(min(request_times), 3) if request_times else 0,
            "request_time_max": round(max(request_times), 3) if request_times else 0,
            "total_time_avg": round(mean(total_times), 3) if total_times else 0,
            "total_time_min": round(min(total_times), 3) if total_times else 0,
            "total_time_max": round(max(total_times), 3) if total_times else 0,
        },
        "results": [r.to_dict() for r in results],
    }
    
    # 添加额外信息
    if extra_info:
        report["extra"] = extra_info
    
    # 保存文件
    import json
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    return filepath


def random_message() -> str:
    """生成随机消息"""
    return f"{random.choice(MESSAGES)}"


def random_tool_validation_message() -> str:
    """生成随机工具验证消息 - 用于测试 LLM 输出格式错误重试逻辑"""
    return random.choice(TOOL_VALIDATION_MESSAGES)


def sequential_tool_validation_message(index: int) -> str:
    """按顺序获取工具验证消息 - 用于系统性测试"""
    return TOOL_VALIDATION_MESSAGES[index % len(TOOL_VALIDATION_MESSAGES)]


async def send_request(session_id: str, customer_id: str, webhook_url: str, webhook_server: WebhookServer) -> Result:
    """
    发送异步聊天请求 - 完整流程
    
    请求格式与 ChatRequestDTO 一致：
    - message: str (必填)
    - session_id: str (必填，同一会话使用相同 session_id）
    - customer_id: str (必填，同一会话同一客户使用相同 customer_id）
    - tenant_id: str (必填)
    - chatbot_id: str (必填)
    - callback_url: str (必填，异步回调地址)
    - timeout: int (可选，默认60秒)
    - source: str (可选，默认"development")
    """
    payload = {
        # 必填字段
        "message": random_message(),
        "session_id": session_id,
        "customer_id": customer_id,
        "tenant_id": TEST_TENANT_ID,
        "chatbot_id": TEST_CHATBOT_ID,
        "callback_url": webhook_url,
        
        # 可选字段
        "timeout": TEST_TIMEOUT,
        "md5_checksum": TEST_CHATBOT_ID,
    }
    
    start = time.time()
    correlation_id = ""
    
    try:
        # 发送异步请求到 chat_async 端点
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(f"{BASE_URL}/sessions/chat_async", json=payload)
        
        request_time = time.time() - start
        
        if resp.status_code != 200:
            return Result(
                session_id=session_id,
                correlation_id="",
                request_status=resp.status_code,
                request_time=request_time,
                error=f"Request failed: {resp.status_code}"
            )
        
        # 解析 correlation_id
        data = resp.json()
        correlation_id = data.get('correlation_id', '')
        
        # 等待 webhook 回调 - 最多等待5分钟
        callback_data = None
        wait_start = time.time()
        max_wait = 120
        
        while time.time() - wait_start < max_wait:
            callback_data = webhook_server.get_callback(correlation_id)
            if callback_data:
                break
            await asyncio.sleep(0.2)  # 每0.2秒检查一次
        
        total_time = time.time() - start
        
        if callback_data:
            # 收到回调
            callback_status = callback_data['status']
            return Result(
                session_id=session_id,
                correlation_id=correlation_id,
                request_status=resp.status_code,
                request_time=request_time,
                callback_status=callback_status,
                callback_time=callback_data['received_at'] - start,
                total_time=total_time,
                success=(callback_status == 'SUCCESS'),
            )
        else:
            # 5分钟内未收到回调
            return Result(
                session_id=session_id,
                correlation_id=correlation_id,
                request_status=resp.status_code,
                request_time=request_time,
                callback_status="NO_CALLBACK",
                total_time=total_time,
                error=f"Webhook callback timeout after {max_wait}s"
            )
            
    except Exception as e:
        return Result(
            session_id=session_id,
            correlation_id=correlation_id,
            request_status=0,
            request_time=time.time() - start,
            error=str(e)
        )


async def test_single_session(num_requests: int, concurrent: int, webhook_url: str, webhook_server: WebhookServer) -> List[Result]:
    """
    测试：同一会话的并发请求
    
    场景：同一个客户在同一个会话中发送多条消息
    - session_id: 固定（同一会话）
    - customer_id: 固定（同一客户）
    """
    session_id = f"LT_chat_async_{uuid4().hex[:8]}"
    customer_id = f"LT_customer_{uuid4().hex[:8]}"
    results = []
    
    for batch in range(0, num_requests, concurrent):
        batch_size = min(concurrent, num_requests - batch)
        batch_results = await asyncio.gather(*[
            send_request(session_id, customer_id, webhook_url, webhook_server) 
            for _ in range(batch_size)
        ])
        results.extend(batch_results)
        if batch + batch_size < num_requests:
            await asyncio.sleep(0.5)
    
    return results


async def test_multi_sessions(num_sessions: int, requests_per_session: int, webhook_url: str, webhook_server: WebhookServer) -> List[Result]:
    """
    测试：多个会话的并发请求
    
    场景：多个不同客户各自在自己的会话中对话
    - 每个会话: session_id 不同
    - 每个会话: customer_id 不同（模拟不同客户）
    - 会话内: session_id 和 customer_id 固定
    """
    tasks = []
    for _ in range(num_sessions):
        session_id = f"LT_chat_async_{uuid4().hex[:8]}"
        customer_id = f"LT_customer_{uuid4().hex[:8]}"
        for _ in range(requests_per_session):
            tasks.append(send_request(session_id, customer_id, webhook_url, webhook_server))
    
    results = []
    for batch in range(0, len(tasks), 10):  # 批量10个避免过载
        batch_results = await asyncio.gather(*tasks[batch:batch+10])
        results.extend(batch_results)
        await asyncio.sleep(0.3)
    
    return results


async def send_tool_validation_request(
    session_id: str, 
    customer_id: str, 
    message: str,
    webhook_url: str, 
    webhook_server: WebhookServer,
    wait_callback: bool = True
) -> Result:
    """
    发送工具验证请求 - 使用指定消息
    
    用于验证 LLM 输出格式错误的重试逻辑
    
    Args:
        wait_callback: 是否等待回调。remote webhook 模式下应设为 False
    """
    payload = {
        "message": message,
        "session_id": session_id,
        "customer_id": customer_id,
        "tenant_id": TEST_TENANT_ID,
        "chatbot_id": TEST_CHATBOT_ID,
        "callback_url": webhook_url,
        "timeout": TEST_TIMEOUT,
        "md5_checksum": TEST_CHATBOT_ID,
    }
    
    start = time.time()
    correlation_id = ""
    
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(f"{BASE_URL}/sessions/chat_async", json=payload)
        
        request_time = time.time() - start
        
        if resp.status_code != 200:
            return Result(
                session_id=session_id,
                correlation_id="",
                request_status=resp.status_code,
                request_time=request_time,
                error=f"Request failed: {resp.status_code}",
                message=message,
            )
        
        data = resp.json()
        correlation_id = data.get('correlation_id', '')
        
        # Remote webhook 模式：不等待回调，直接返回请求成功
        if not wait_callback:
            return Result(
                session_id=session_id,
                correlation_id=correlation_id,
                request_status=resp.status_code,
                request_time=request_time,
                callback_status="PENDING",  # 标记为等待远程回调
                total_time=request_time,
                success=True,  # 请求成功即视为成功
                message=message,
            )
        
        # Local webhook 模式：等待回调
        callback_data = None
        wait_start = time.time()
        max_wait = 120
        
        while time.time() - wait_start < max_wait:
            callback_data = webhook_server.get_callback(correlation_id)
            if callback_data:
                break
            await asyncio.sleep(0.2)
        
        total_time = time.time() - start
        
        if callback_data:
            callback_status = callback_data['status']
            return Result(
                session_id=session_id,
                correlation_id=correlation_id,
                request_status=resp.status_code,
                request_time=request_time,
                callback_status=callback_status,
                callback_time=callback_data['received_at'] - start,
                total_time=total_time,
                success=(callback_status == 'SUCCESS'),
                message=message,
            )
        else:
            return Result(
                session_id=session_id,
                correlation_id=correlation_id,
                request_status=resp.status_code,
                request_time=request_time,
                callback_status="NO_CALLBACK",
                total_time=total_time,
                error=f"Webhook callback timeout after {max_wait}s",
                message=message,
            )
            
    except Exception as e:
        return Result(
            session_id=session_id,
            correlation_id=correlation_id,
            request_status=0,
            request_time=time.time() - start,
            error=str(e),
            message=message,
        )


async def test_retry_validation(
    num_requests: int = 10, 
    sequential: bool = True,
    webhook_url: str = "", 
    webhook_server: WebhookServer = None,
    wait_callback: bool = True
) -> List[Result]:
    """
    测试：LLM 输出格式错误重试逻辑验证
    
    场景：使用工具调用相关的消息来触发 LLM 输出
    - 天气查询（触发 city_geo_info + get_weather_by_geo）
    - 留资场景（触发 save_customer_information）
    - 物流查询（触发 tracking_inquiry）
    - 边界测试（Unicode 字符、长输入等）
    
    这些场景可能触发以下 LLM 输出格式错误：
    - JSONDecodeError: Invalid Unicode escape
    - ValueError: No JSON object found
    - ValidationError: Field name typos in schema
    
    Args:
        num_requests: 请求数量
        sequential: True=按顺序遍历所有测试消息, False=随机选择
        webhook_url: Webhook URL
        webhook_server: Webhook 服务器实例
        wait_callback: 是否等待回调（remote 模式下为 False）
    """
    results = []
    
    for i in range(num_requests):
        session_id = f"LT_retry_test_{uuid4().hex[:8]}"
        customer_id = f"LT_customer_{uuid4().hex[:8]}"
        
        # 选择测试消息
        if sequential:
            message = sequential_tool_validation_message(i)
        else:
            message = random_tool_validation_message()
        
        print(f"  📤 [{i+1}/{num_requests}] {message[:50]}...")
        
        result = await send_tool_validation_request(
            session_id, customer_id, message, webhook_url, webhook_server,
            wait_callback=wait_callback
        )
        results.append(result)
        
        # 打印即时结果
        status = "✅" if result.success else "❌"
        callback = result.callback_status or "NO_CALLBACK"
        time_info = f"{result.total_time:.2f}s" if wait_callback else f"{result.request_time:.2f}s (req)"
        print(f"  {status} {callback} | {time_info}")
        
        # 短暂等待避免过载
        await asyncio.sleep(0.5 if wait_callback else 0.2)
    
    return results


async def test_retry_validation_quick(
    webhook_url: str, 
    webhook_server: WebhookServer,
    wait_callback: bool = True
) -> List[Result]:
    """快速验证测试 - 使用简化问题集"""
    results = []
    
    for i, message in enumerate(QUICK_VALIDATION_MESSAGES):
        session_id = f"LT_quick_test_{uuid4().hex[:8]}"
        customer_id = f"LT_customer_{uuid4().hex[:8]}"
        
        print(f"  📤 [{i+1}/{len(QUICK_VALIDATION_MESSAGES)}] {message[:50]}...")
        
        result = await send_tool_validation_request(
            session_id, customer_id, message, webhook_url, webhook_server,
            wait_callback=wait_callback
        )
        results.append(result)
        
        status = "✅" if result.success else "❌"
        callback = result.callback_status or "NO_CALLBACK"
        time_info = f"{result.total_time:.2f}s" if wait_callback else f"{result.request_time:.2f}s (req)"
        print(f"  {status} {callback} | {time_info}")
        
        await asyncio.sleep(0.3 if wait_callback else 0.1)
    
    return results


async def test_mixed(num_sessions: int, requests_per_session: int, concurrent: int, webhook_url: str, webhook_server: WebhookServer) -> List[Result]:
    """
    测试：混合场景 - 多会话 × 每会话并发
    
    场景：多个客户各自在自己的会话中进行多轮并发对话
    - 每个会话: session_id 和 customer_id 都不同
    - 会话内: 同一 session_id + 同一 customer_id（复用 test_single_session）
    """
    tasks = [
        test_single_session(requests_per_session, concurrent, webhook_url, webhook_server)
        for _ in range(num_sessions)
    ]
    all_results = await asyncio.gather(*tasks)
    return [r for sublist in all_results for r in sublist]


def print_results(name: str, results: List[Result], show_sessions: bool = False):
    """打印测试结果 - 完整流程分析"""
    success = sum(1 for r in results if r.success)
    callback_received = sum(1 for r in results if r.callback_received)
    
    print(f"\n{'='*70}")
    print(f"📊 {name}")
    print(f"{'='*70}")
    print(f"总请求: {len(results)} | 回调接收: {callback_received} | "
          f"完整成功: {success} | 成功率: {success/len(results)*100:.1f}%")
    
    # 请求响应时间
    request_times = [r.request_time for r in results if r.request_status == 200]
    if request_times:
        print(f"请求响应: 平均 {mean(request_times):.2f}s | "
              f"中位 {median(request_times):.2f}s | "
              f"范围 [{min(request_times):.2f}s, {max(request_times):.2f}s]")
    
    # 完整流程时间（包含 webhook 回调）
    total_times = [r.total_time for r in results if r.callback_received]
    if total_times:
        sorted_times = sorted(total_times)
        print(f"完整流程: 平均 {mean(total_times):.2f}s | 中位 {median(total_times):.2f}s | "
              f"范围 [{min(total_times):.2f}s, {max(total_times):.2f}s]")
        print(f"分位数: P90 {sorted_times[int(len(total_times)*0.9)]:.2f}s | "
              f"P95 {sorted_times[int(len(total_times)*0.95)]:.2f}s | "
              f"P99 {sorted_times[int(len(total_times)*0.99)]:.2f}s")
    
    # 回调状态统计
    callback_stats = {}
    for r in results:
        status = r.callback_status or "NO_CALLBACK"
        callback_stats[status] = callback_stats.get(status, 0) + 1
    
    if len(callback_stats) > 1 or list(callback_stats.keys())[0] != "SUCCESS":
        print(f"回调状态: {', '.join(f'{k}×{v}' for k, v in callback_stats.items())}")
    
    # 会话统计
    if show_sessions:
        sessions = {}
        for r in results:
            if r.session_id not in sessions:
                sessions[r.session_id] = []
            sessions[r.session_id].append(r)
        
        if len(sessions) > 1:
            print(f"\n会话统计 ({len(sessions)}个会话):")
            for sid, reqs in list(sessions.items())[:5]:
                succ = sum(1 for r in reqs if r.success)
                avg_time = mean([r.total_time for r in reqs if r.callback_received]) if succ else 0
                print(f"  {sid[:30]:30s} | 完整成功 {succ}/{len(reqs)} | 平均 {avg_time:.2f}s")
            if len(sessions) > 5:
                print(f"  ... 还有 {len(sessions)-5} 个会话")
    
    # 错误统计
    errors = [r for r in results if not r.success or not r.callback_received]
    if errors:
        error_types = {}
        for e in errors:
            if e.error:
                key = "Network Error"
            elif not e.callback_received:
                key = "Callback Timeout"
            elif e.callback_status in ["CANCELLED", "TIMEOUT_ERROR", "PROCESSING_ERROR"]:
                key = e.callback_status
            else:
                key = "Unknown"
            error_types[key] = error_types.get(key, 0) + 1
        print(f"\n错误: {', '.join(f'{k}×{v}' for k, v in error_types.items())}")


async def health_check() -> bool:
    """健康检查"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BASE_URL}/health")
        return resp.status_code == 200
    except:
        return False


async def main():
    print("="*70)
    print("Chat Async API 压力测试 (含 Webhook 回调)")
    print("="*70)
    
    # 健康检查
    print("\n检查服务状态...")
    if not await health_check():
        print("❌ 服务不可用")
        return
    print("✅ 服务正常")
    
    # 启动 webhook 服务器
    webhook_server = WebhookServer(port=WEBHOOK_PORT, mode=WEBHOOK_MODE)
    await webhook_server.start()
    webhook_url = webhook_server.get_webhook_url(REMOTE_WEBHOOK_URL)
    
    print(f"\n📡 Webhook 配置:")
    print(f"  模式: {WEBHOOK_MODE}")
    print(f"  URL: {webhook_url}")
    if WEBHOOK_MODE == "local":
        print(f"  监听端口: {WEBHOOK_PORT}")
    else:
        print(f"  提示: 确保远程服务能转发回调到本地")
    print(f"  回调路径: /webhook 或 /api/callback/agent/receive")
    
    try:
        # ==================== 多阶段压力测试 ====================
        # 阶段1: 预热测试 - 验证基本功能
        # 阶段2: 常规压力 - 模拟正常业务负载
        # 阶段3: 高压力 - 测试系统容量上限
        # 阶段4: 极限压力 - 探索系统极限（可选）
        
        test_stages = [
            {
                "name": "阶段4: 极限压力",
                "description": "探索系统极限",
                "wait_after": 5,
                "tests": [
                    ("多会话 10会话×1请求", lambda: test_multi_sessions(10, 1, webhook_url, webhook_server), True),
                    # ("混合 15会话×2请求 5并发", lambda: test_mixed(15, 2, 5, webhook_url, webhook_server), True),
                    ("混合 20会话×2请求 40并发", lambda: test_mixed(20, 2, 40, webhook_url, webhook_server), True),
                ]
            },
        ]
        
        # 异步执行所有阶段，不等待处理完成
        all_stage_tasks = []
        stage_infos = []
        
        for stage_idx, stage in enumerate(test_stages, 1):
            print(f"\n\n{'='*70}")
            print(f"{'='*70}")
            print(f"🚀 {stage['name']} ({stage_idx}/{len(test_stages)})")
            print(f"📝 {stage['description']}")
            print(f"{'='*70}")
            print(f"{'='*70}")
            
            stage_tests = stage['tests']
            stage_test_tasks = []
            
            for test_idx, (name, test_func, show_sessions) in enumerate(stage_tests, 1):
                print(f"\n{'#'*70}")
                print(f"测试 {test_idx}/{len(stage_tests)}: {name}")
                print(f"{'#'*70}")
                
                print(f"✅ 请求发送中...")
                # 启动测试，不等待完成
                task = asyncio.create_task(test_func())
                stage_test_tasks.append((name, task, show_sessions))
                
                # 测试间短暂间隔
                if test_idx < len(stage_tests):
                    await asyncio.sleep(1)
            
            all_stage_tasks.append({
                'name': stage['name'],
                'tasks': stage_test_tasks,
                'stage_idx': stage_idx
            })
            
            stage_infos.append(stage['name'])
            
            print(f"\n✅ {stage['name']} 所有请求已发送")
            
            # 阶段间等待（控制发送速率）
            if stage_idx < len(test_stages):
                wait_time = stage.get('wait_after', 3)
                print(f"⏸️  等待 {wait_time}s 后开始下一阶段...")
                await asyncio.sleep(wait_time)
        
        # 等待所有阶段的测试完成
        print(f"\n\n{'='*70}")
        print(f"📊 所有请求已发送，等待处理完成...")
        print(f"{'='*70}")
        
        all_results = []
        stage_summaries = []
        
        for stage_info in all_stage_tasks:
            print(f"\n等待 {stage_info['name']} 处理完成...")
            
            stage_results = []
            for name, task, show_sessions in stage_info['tasks']:
                try:
                    results = await task
                    stage_results.extend(results)
                    
                    # 简化输出
                    success = sum(1 for r in results if r.success)
                    success_rate = success / len(results) * 100 if results else 0
                    print(f"  ✅ {name}: {success}/{len(results)} ({success_rate:.1f}%)")
                except Exception as e:
                    print(f"  ❌ {name}: 失败 - {e}")
            
            all_results.extend(stage_results)
            
            # 阶段统计
            stage_success = sum(1 for r in stage_results if r.success)
            stage_success_rate = stage_success / len(stage_results) * 100 if stage_results else 0
            
            stage_summaries.append({
                'name': stage_info['name'],
                'total': len(stage_results),
                'success': stage_success,
                'success_rate': stage_success_rate
            })
            
            print(f"  📊 {stage_info['name']}: {stage_success}/{len(stage_results)} ({stage_success_rate:.1f}%)")
        
        # 总结
        print(f"\n\n{'='*70}")
        print(f"{'='*70}")
        print("🏁 压力测试总结")
        print(f"{'='*70}")
        print(f"{'='*70}")
        
        # 阶段汇总
        print(f"\n各阶段成功率:")
        for idx, summary in enumerate(stage_summaries, 1):
            emoji = "✅" if summary['success_rate'] >= 95 else "⚠️" if summary['success_rate'] >= 80 else "❌"
            print(f"  {emoji} {summary['name']:20s} | "
                  f"{summary['success']:3d}/{summary['total']:3d} ({summary['success_rate']:5.1f}%)")
        
        # 总体统计
        print(f"\n总体统计:")
        total_success = sum(1 for r in all_results if r.success)
        total_callback = sum(1 for r in all_results if r.callback_received)
        print(f"  总请求数:     {len(all_results)}")
        print(f"  回调接收:     {total_callback} ({total_callback/len(all_results)*100:.1f}%)")
        print(f"  完整成功:     {total_success} ({total_success/len(all_results)*100:.1f}%)")
        
        # 响应时间统计
        all_times = [r.total_time for r in all_results if r.success]
        if all_times:
            sorted_times = sorted(all_times)
            print(f"\n响应时间统计:")
            print(f"  平均:         {mean(all_times):.2f}s")
            print(f"  中位数:       {median(all_times):.2f}s")
            print(f"  最小/最大:    {min(all_times):.2f}s / {max(all_times):.2f}s")
            print(f"  P90/P95/P99:  {sorted_times[int(len(sorted_times)*0.9)]:.2f}s / "
                  f"{sorted_times[int(len(sorted_times)*0.95)]:.2f}s / "
                  f"{sorted_times[int(len(sorted_times)*0.99)]:.2f}s")
        
        # 回调状态分布
        callback_stats = {}
        for r in all_results:
            status = r.callback_status or "NO_CALLBACK"
            callback_stats[status] = callback_stats.get(status, 0) + 1
        
        print(f"\n回调状态分布:")
        for status, count in sorted(callback_stats.items()):
            percent = count / len(all_results) * 100
            print(f"  {status:20s} {count:3d} ({percent:5.1f}%)")
        
        # 建议
        print(f"\n{'='*70}")
        overall_success_rate = total_success / len(all_results) * 100
        if overall_success_rate >= 95:
            print("✅ 测试结果优秀！系统在各个压力阶段表现稳定")
        elif overall_success_rate >= 80:
            print("⚠️  测试结果良好，建议关注失败案例并优化")
        else:
            print("❌ 测试结果需要改进，请检查系统配置和资源")
        print(f"{'='*70}")
        
    finally:
        # 停止 webhook 服务器
        if WEBHOOK_MODE == "local":
            await webhook_server.stop()
            print("\n✅ Webhook 服务器已停止")
        else:
            print("\n✅ 测试完成（远程 Webhook 模式）")


async def main_retry_validation():
    """
    重试逻辑验证测试入口
    
    专门用于验证 LLM 输出格式错误的重试优化逻辑：
    - JSONDecodeError: Invalid Unicode escape
    - ValueError: No JSON object found  
    - ValidationError: Field name typos in schema
    
    使用方法:
        python scripts/benchmark/stress_test_async.py --validate-retry
        python scripts/benchmark/stress_test_async.py --validate-retry --quick
        python scripts/benchmark/stress_test_async.py --validate-retry --count 20
    """
    import argparse
    parser = argparse.ArgumentParser(description="LLM 重试逻辑验证测试")
    parser.add_argument("--validate-retry", action="store_true", help="运行重试验证测试")
    parser.add_argument("--quick", action="store_true", help="快速验证模式（3个问题）")
    parser.add_argument("--count", type=int, default=10, help="测试请求数量（默认10）")
    parser.add_argument("--random", action="store_true", help="随机选择问题而非顺序遍历")
    parser.add_argument("--no-save", action="store_true", help="不保存结果到 JSON 文件")
    args = parser.parse_args()
    
    # 判断是否等待回调（remote 模式下不等待）
    wait_callback = (WEBHOOK_MODE == "local")
    
    print("="*70)
    print("🔄 LLM 输出格式错误重试逻辑验证测试")
    print("="*70)
    print(f"""
测试目标：
  验证以下 LLM 输出格式错误的重试逻辑是否正常工作：
  1. JSONDecodeError - JSON 语法错误（如无效的 Unicode 转义）
  2. ValueError - jsonfinder 找不到有效 JSON
  3. ValidationError - Pydantic schema 验证失败（字段名拼写错误）
  
测试场景：
  - 天气查询（触发 city_geo_info + get_weather_by_geo 工具链）
  - 留资场景（触发 save_customer_information 工具）
  - 物流查询（触发 tracking_inquiry 工具）
  - 边界测试（Unicode 字符、多语言混合、长输入）

Webhook 模式: {WEBHOOK_MODE} {'(等待回调)' if wait_callback else '(不等待回调，仅验证请求)'}
""")
    
    # 健康检查
    print("检查服务状态...")
    if not await health_check():
        print("❌ 服务不可用")
        return
    print("✅ 服务正常")
    
    # 启动 webhook 服务器
    webhook_server = WebhookServer(port=WEBHOOK_PORT, mode=WEBHOOK_MODE)
    await webhook_server.start()
    webhook_url = webhook_server.get_webhook_url(REMOTE_WEBHOOK_URL)
    
    print(f"\n📡 Webhook: {webhook_url}")
    if not wait_callback:
        print("⚠️  Remote 模式：不等待回调，成功 = 请求被接受 (HTTP 200)")
    
    try:
        print(f"\n{'='*70}")
        
        test_name = "retry_validation"
        if args.quick:
            print("🚀 快速验证模式")
            print("="*70)
            test_name = "retry_validation_quick"
            results = await test_retry_validation_quick(
                webhook_url, webhook_server, wait_callback=wait_callback
            )
        else:
            mode = "随机" if args.random else "顺序"
            print(f"🚀 完整验证模式 | 数量: {args.count} | 模式: {mode}")
            print("="*70)
            results = await test_retry_validation(
                num_requests=args.count,
                sequential=not args.random,
                webhook_url=webhook_url,
                webhook_server=webhook_server,
                wait_callback=wait_callback
            )
        
        # 打印结果
        print_results("重试逻辑验证测试结果", results)
        
        # 保存结果到 JSON
        if not args.no_save:
            extra_info = {
                "test_mode": "quick" if args.quick else "full",
                "message_selection": "random" if args.random else "sequential",
                "wait_callback": wait_callback,
                "webhook_mode": WEBHOOK_MODE,
            }
            json_path = save_results_to_json(results, test_name, extra_info)
            print(f"\n💾 结果已保存: {json_path}")
        
        # 额外分析
        print(f"\n{'='*70}")
        print("📊 验证分析")
        print("="*70)
        
        success_count = sum(1 for r in results if r.success)
        total_count = len(results)
        success_rate = success_count / total_count * 100
        
        # 分析失败原因
        failures = [r for r in results if not r.success]
        if failures:
            print(f"\n❌ 失败案例分析 ({len(failures)} 个):")
            for r in failures[:5]:  # 最多显示5个
                msg_preview = r.message[:30] + "..." if len(r.message) > 30 else r.message
                print(f"  - [{r.callback_status}] {msg_preview}")
                if r.error:
                    print(f"    Error: {r.error[:60]}...")
        
        print(f"\n{'='*70}")
        if wait_callback:
            # 本地模式：基于完整流程判断
            if success_rate >= 90:
                print("✅ 重试逻辑验证通过！LLM 输出格式错误能够正确重试")
            elif success_rate >= 70:
                print("⚠️  部分请求失败，建议检查服务日志中的重试记录")
            else:
                print("❌ 重试逻辑可能存在问题，请检查以下错误类型的处理：")
                print("   - JSONDecodeError (is_llm_output_format_error)")
                print("   - ValueError 'No JSON object found'")
                print("   - ValidationError for LLM schema")
        else:
            # 远程模式：基于请求接受率判断
            if success_rate >= 95:
                print("✅ 请求全部被接受！请检查服务端日志验证重试逻辑")
            elif success_rate >= 80:
                print("⚠️  部分请求失败，请检查网络连接和服务状态")
            else:
                print("❌ 请求接受率过低，请检查服务配置")
            print("\n💡 提示: Remote 模式下请查看服务端日志确认重试逻辑是否生效")
        print("="*70)
        
    finally:
        if WEBHOOK_MODE == "local":
            await webhook_server.stop()
        print("\n✅ 测试完成")


if __name__ == "__main__":
    import sys
    
    if "--validate-retry" in sys.argv or len(sys.argv) > 1 and sys.argv[1] == "retry":
        asyncio.run(main_retry_validation())
    else:
        asyncio.run(main())

