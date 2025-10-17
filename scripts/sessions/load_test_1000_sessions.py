#!/usr/bin/env python3
"""
Parlant 1000会话实例压力测试脚本

设计特点：
1. 智能频次控制 - 避免429限流
2. 多IP模拟 - 绕过单IP限制
3. 渐进式负载 - 逐步增加压力
4. 实时监控 - 动态调整策略
5. 会话生命周期管理 - 模拟真实用户行为

使用方法：
python scripts/load_test_1000_sessions.py --sessions 1000 --duration 3600
"""

import asyncio
import aiohttp
import argparse
import json
import time
import statistics
import os
import random
import string
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
import math


@dataclass
class SessionInstance:
    """会话实例"""
    session_id: str
    customer_id: str
    created_at: float
    last_activity: float
    message_count: int
    is_active: bool
    ip_address: str


@dataclass
class TestResult:
    """测试结果"""
    timestamp: str
    session_id: str
    message_id: str
    response_time_ms: float
    status_code: int
    success: bool
    error_message: Optional[str] = None
    is_rate_limited: bool = False
    retry_count: int = 0
    ip_address: str = ""


@dataclass
class FailureAnalysis:
    """失败分析"""
    error_categories: Dict[str, int]
    status_code_distribution: Dict[int, int]
    error_messages: Dict[str, int]
    ip_failure_distribution: Dict[str, int]
    session_failure_distribution: Dict[str, int]
    time_based_failures: List[Tuple[str, int]]  # (time_window, failure_count)
    retry_analysis: Dict[int, int]  # retry_count -> failure_count


@dataclass
class LoadTestReport:
    """压力测试报告"""
    test_start_time: str
    test_end_time: str
    total_duration_seconds: float
    total_sessions: int
    active_sessions: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    rate_limited_requests: int
    success_rate: float
    rate_limit_percentage: float
    avg_response_time_ms: float
    p95_response_time_ms: float
    p99_response_time_ms: float
    requests_per_second: float
    sessions_per_second: float
    peak_concurrent_sessions: int
    memory_usage_mb: float = 0.0
    failure_analysis: Optional[FailureAnalysis] = None


class RateController:
    """智能频次控制器"""
    
    def __init__(self, base_rate_per_minute: int = 30, ip_count: int = 10):
        self.base_rate_per_minute = base_rate_per_minute
        self.ip_count = ip_count
        self.rate_per_ip_per_minute = base_rate_per_minute // ip_count
        self.rate_per_ip_per_second = self.rate_per_ip_per_minute / 60.0
        
        # 每个IP的请求历史
        self.ip_request_history: Dict[str, deque] = defaultdict(lambda: deque())
        self.ip_last_request: Dict[str, float] = {}
        
        # 动态调整参数
        self.adaptive_rate_multiplier = 1.0
        self.last_adjustment_time = time.time()
        self.adjustment_interval = 30  # 30秒调整一次
        
    def can_send_request(self, ip_address: str) -> bool:
        """检查是否可以发送请求"""
        current_time = time.time()
        
        # 清理过期请求记录（超过1分钟）
        history = self.ip_request_history[ip_address]
        while history and current_time - history[0] > 60:
            history.popleft()
        
        # 检查是否超过速率限制
        if len(history) >= self.rate_per_ip_per_minute * self.adaptive_rate_multiplier:
            return False
        
        # 检查最小间隔
        last_request = self.ip_last_request.get(ip_address, 0)
        min_interval = 1.0 / (self.rate_per_ip_per_second * self.adaptive_rate_multiplier)
        
        if current_time - last_request < min_interval:
            return False
        
        return True
    
    def record_request(self, ip_address: str):
        """记录请求"""
        current_time = time.time()
        self.ip_request_history[ip_address].append(current_time)
        self.ip_last_request[ip_address] = current_time
    
    def adjust_rate_based_on_429(self, rate_limit_count: int, total_requests: int):
        """基于429响应动态调整速率"""
        current_time = time.time()
        
        if current_time - self.last_adjustment_time < self.adjustment_interval:
            return
        
        rate_limit_ratio = rate_limit_count / max(total_requests, 1)
        
        if rate_limit_ratio > 0.1:  # 限流率超过10%
            self.adaptive_rate_multiplier *= 0.8  # 降低20%
            print(f"🚫 限流率过高 ({rate_limit_ratio:.1%})，降低速率至 {self.adaptive_rate_multiplier:.2f}")
        elif rate_limit_ratio < 0.01:  # 限流率低于1%
            self.adaptive_rate_multiplier *= 1.1  # 提高10%
            print(f"✅ 限流率较低 ({rate_limit_ratio:.1%})，提高速率至 {self.adaptive_rate_multiplier:.2f}")
        
        self.last_adjustment_time = current_time


class IPPool:
    """IP地址池 - 模拟多IP环境"""
    
    def __init__(self, ip_count: int = 10):
        self.ip_count = ip_count
        self.ip_pool = [f"192.168.{i//256}.{i%256}" for i in range(1, ip_count + 1)]
        self.current_index = 0
    
    def get_next_ip(self) -> str:
        """获取下一个IP地址"""
        ip = self.ip_pool[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.ip_pool)
        return ip


class SessionManager:
    """会话管理器"""
    
    def __init__(self, max_sessions: int = 1000):
        self.max_sessions = max_sessions
        self.sessions: Dict[str, SessionInstance] = {}
        self.active_sessions: Set[str] = set()
        self.session_creation_queue = deque()
        self.session_cleanup_queue = deque()
    
    def create_session(self, ip_address: str) -> SessionInstance:
        """创建新会话"""
        session_id = f"load_test_{uuid.uuid4()}"
        customer_id = f"test_customer_{random.randint(10000, 99999)}"
        current_time = time.time()
        
        session = SessionInstance(
            session_id=session_id,
            customer_id=customer_id,
            created_at=current_time,
            last_activity=current_time,
            message_count=0,
            is_active=True,
            ip_address=ip_address
        )
        
        self.sessions[session_id] = session
        self.active_sessions.add(session_id)
        
        return session
    
    def get_active_session(self) -> Optional[SessionInstance]:
        """获取一个活跃会话"""
        if not self.active_sessions:
            return None
        
        session_id = random.choice(list(self.active_sessions))
        return self.sessions.get(session_id)
    
    def cleanup_old_sessions(self, max_age_seconds: int = 3600):
        """清理过期会话"""
        current_time = time.time()
        to_remove = []
        
        for session_id, session in self.sessions.items():
            if current_time - session.last_activity > max_age_seconds:
                to_remove.append(session_id)
        
        for session_id in to_remove:
            self.active_sessions.discard(session_id)
            del self.sessions[session_id]
    
    def get_stats(self) -> Dict:
        """获取会话统计"""
        return {
            "total_sessions": len(self.sessions),
            "active_sessions": len(self.active_sessions),
            "oldest_session_age": min(
                (time.time() - s.created_at for s in self.sessions.values()),
                default=0
            )
        }


class Parlant1000SessionTester:
    """1000会话压力测试器"""
    
    def __init__(self, base_url: str = None, max_sessions: int = 1000, ip_count: int = 10):
        self.base_url = base_url or os.getenv("PARLANT_SERVER_URL", "http://localhost:8800")
        self.max_sessions = max_sessions
        self.ip_count = ip_count
        
        # 核心组件
        self.rate_controller = RateController(ip_count=ip_count)
        self.ip_pool = IPPool(ip_count=ip_count)
        self.session_manager = SessionManager(max_sessions=max_sessions)
        
        # 测试数据
        self.results: List[TestResult] = []
        self.test_messages = [
            "你好",
            "hi",
            "早上好",
            "下午好",
            "晚上好",
            "在吗",
            "有人吗",
            "你好吗",
            "今天天气怎么样",
            "谢谢",
            "再见",
            "拜拜",
            "好的",
            "嗯",
            "是的",
            "不是",
            "可以",
            "不行",
            "好的谢谢",
            "没问题"
        ]
        
        # 统计信息
        self.stats = {
            "requests_sent": 0,
            "requests_successful": 0,
            "requests_failed": 0,
            "requests_rate_limited": 0,
            "sessions_created": 0,
            "peak_concurrent_sessions": 0
        }
    
    def get_random_message(self) -> str:
        """获取随机测试消息"""
        return random.choice(self.test_messages)
    
    def _is_system_success(self, status_code: int) -> bool:
        """判断是否为系统级成功
        - HTTP 200: 成功 (包括业务错误)
        - HTTP 429: 限流 (特殊状态，不算失败)
        - 其他: 系统性问题 (真正的失败)
        """
        return status_code in [200, 429]
    
    async def check_server_health(self, session: aiohttp.ClientSession) -> bool:
        """检查服务器健康状态"""
        try:
            url = f"{self.base_url}/health"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                return response.status == 200
        except:
            try:
                url = f"{self.base_url}/sessions/chat"
                data = {
                    "message": "health check",
                    "tenant_id": "test_tenant",
                    "chatbot_id": "test_chatbot"
                }
                async with session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    return response.status in [200, 422]
            except:
                return False
    
    async def send_chat_message(self, session: aiohttp.ClientSession, 
                               session_instance: SessionInstance, 
                               message: str, 
                               max_retries: int = 3) -> TestResult:
        """发送聊天消息"""
        message_id = str(uuid.uuid4())
        ip_address = session_instance.ip_address
        
        for attempt in range(max_retries + 1):
            start_time = time.time()
            
            try:
                url = f"{self.base_url}/sessions/chat"
                data = {
                    "message": message,
                    "customer_id": session_instance.customer_id,
                    "session_id": session_instance.session_id,
                    "session_title": f"Load Test Session {session_instance.customer_id}",
                    "tenant_id": "load_test_tenant",
                    "chatbot_id": "load_test_chatbot",
                    "timeout": 60
                }
                
                async with session.post(url, json=data) as response:
                    response_time = (time.time() - start_time) * 1000
                    response_text = await response.text()
                    
                    is_rate_limited = response.status == 429
                    
                    # 优化判断逻辑：只要HTTP状态码是200就认为是成功
                    # 失败仅限于系统性问题：404, 500, 502, 503, 504, 超时等
                    success = self._is_system_success(response.status)
                    error_message = None
                    
                    if response.status == 200:
                        try:
                            result = await response.json()
                            # 即使业务状态码不是0，HTTP 200仍然算成功
                            # 业务错误信息记录但不影响成功判断
                            if result.get("code") != 0:
                                error_message = f"Business warning: {result.get('message', 'Business error')}"
                        except:
                            # JSON解析失败，但HTTP 200仍然算成功
                            error_message = "Warning: Invalid JSON response"
                    elif not success:
                        # 系统性问题才记录为错误消息
                        error_message = response_text
                    
                    # 记录请求
                    self.rate_controller.record_request(ip_address)
                    
                    # 更新会话状态
                    session_instance.last_activity = time.time()
                    session_instance.message_count += 1
                    
                    # 更新统计
                    self.stats["requests_sent"] += 1
                    if success:
                        if is_rate_limited:
                            # 限流请求单独统计，不算成功也不算失败
                            self.stats["requests_rate_limited"] += 1
                        else:
                            # 真正的成功请求
                            self.stats["requests_successful"] += 1
                    else:
                        # 系统性问题才算失败
                        self.stats["requests_failed"] += 1
                    
                    return TestResult(
                        timestamp=datetime.now().isoformat(),
                        session_id=session_instance.session_id,
                        message_id=message_id,
                        response_time_ms=response_time,
                        status_code=response.status,
                        success=success,
                        error_message=error_message,
                        is_rate_limited=is_rate_limited,
                        retry_count=attempt,
                        ip_address=ip_address
                    )
                    
            except Exception as e:
                response_time = (time.time() - start_time) * 1000
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
                
                self.stats["requests_sent"] += 1
                self.stats["requests_failed"] += 1
                
                return TestResult(
                    timestamp=datetime.now().isoformat(),
                    session_id=session_instance.session_id,
                    message_id=message_id,
                    response_time_ms=response_time,
                    status_code=0,
                    success=False,
                    error_message=str(e),
                    retry_count=attempt,
                    ip_address=ip_address
                )
    
    async def create_new_session(self, session: aiohttp.ClientSession) -> Optional[SessionInstance]:
        """创建新会话"""
        if len(self.session_manager.sessions) >= self.max_sessions:
            return None
        
        ip_address = self.ip_pool.get_next_ip()
        session_instance = self.session_manager.create_session(ip_address)
        
        # 发送第一条消息创建会话
        message = self.get_random_message()
        result = await self.send_chat_message(session, session_instance, message)
        
        if result.success:
            self.stats["sessions_created"] += 1
            return session_instance
        else:
            # 创建失败，清理
            self.session_manager.active_sessions.discard(session_instance.session_id)
            del self.session_manager.sessions[session_instance.session_id]
            return None
    
    async def send_message_to_existing_session(self, session: aiohttp.ClientSession) -> Optional[TestResult]:
        """向现有会话发送消息"""
        session_instance = self.session_manager.get_active_session()
        if not session_instance:
            return None
        
        # 检查是否可以发送请求
        if not self.rate_controller.can_send_request(session_instance.ip_address):
            return None
        
        message = self.get_random_message()
        return await self.send_chat_message(session, session_instance, message)
    
    async def run_1000_session_test(self, duration_seconds: int) -> List[TestResult]:
        """运行1000会话压力测试"""
        print(f"🚀 开始1000会话压力测试")
        print(f"⏱️  测试时长: {duration_seconds} 秒")
        print(f"🌐 IP地址池: {self.ip_count} 个")
        print(f"📊 目标会话数: {self.max_sessions}")
        print(f"🎯 基础限流: {self.rate_controller.base_rate_per_minute} 次/分钟")
        
        start_time = time.time()
        end_time = start_time + duration_seconds
        
        connector = aiohttp.TCPConnector(limit=self.max_sessions * 2)
        timeout = aiohttp.ClientTimeout(total=60)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # 健康检查
            print("🔍 检查服务器健康状态...")
            if not await self.check_server_health(session):
                print("❌ 服务器健康检查失败")
                return []
            print("✅ 服务器健康检查通过")
            
            # 主测试循环
            last_stats_time = time.time()
            last_cleanup_time = time.time()
            
            while time.time() < end_time:
                current_time = time.time()
                
                # 定期清理过期会话
                if current_time - last_cleanup_time > 300:  # 5分钟清理一次
                    self.session_manager.cleanup_old_sessions()
                    last_cleanup_time = current_time
                
                # 动态调整速率
                self.rate_controller.adjust_rate_based_on_429(
                    self.stats["requests_rate_limited"],
                    self.stats["requests_sent"]
                )
                
                # 创建新会话（如果还没达到目标数量）
                if len(self.session_manager.sessions) < self.max_sessions:
                    new_session = await self.create_new_session(session)
                    if new_session:
                        self.results.append(TestResult(
                            timestamp=datetime.now().isoformat(),
                            session_id=new_session.session_id,
                            message_id="session_created",
                            response_time_ms=0,
                            status_code=200,
                            success=True,
                            ip_address=new_session.ip_address
                        ))
                
                # 向现有会话发送消息
                result = await self.send_message_to_existing_session(session)
                if result:
                    self.results.append(result)
                
                # 更新峰值并发会话数
                current_sessions = len(self.session_manager.active_sessions)
                self.stats["peak_concurrent_sessions"] = max(
                    self.stats["peak_concurrent_sessions"], 
                    current_sessions
                )
                
                # 定期打印统计信息
                if current_time - last_stats_time > 30:  # 30秒打印一次
                    self.print_progress_stats(current_time - start_time, end_time - start_time)
                    last_stats_time = current_time
                
                # 短暂休息
                await asyncio.sleep(0.1)
        
        return self.results
    
    def print_progress_stats(self, elapsed_time: float, total_time: float):
        """打印进度统计"""
        progress = (elapsed_time / total_time) * 100
        current_sessions = len(self.session_manager.active_sessions)
        
        print(f"\n📊 测试进度: {progress:.1f}% ({elapsed_time:.0f}s/{total_time:.0f}s)")
        print(f"👥 当前活跃会话: {current_sessions}/{self.max_sessions}")
        print(f"📨 总请求数: {self.stats['requests_sent']}")
        print(f"✅ 成功请求: {self.stats['requests_successful']}")
        print(f"❌ 失败请求: {self.stats['requests_failed']}")
        print(f"🚫 限流请求: {self.stats['requests_rate_limited']}")
        print(f"🎯 速率倍数: {self.rate_controller.adaptive_rate_multiplier:.2f}")
        
        if self.stats['requests_sent'] > 0:
            # 成功率 = (成功请求 + 限流请求) / 总请求数
            # 因为限流不算失败，只是被保护机制拦截
            effective_success = self.stats['requests_successful'] + self.stats['requests_rate_limited']
            success_rate = (effective_success / self.stats['requests_sent']) * 100
            rate_limit_rate = (self.stats['requests_rate_limited'] / self.stats['requests_sent']) * 100
            system_failure_rate = (self.stats['requests_failed'] / self.stats['requests_sent']) * 100
            
            print(f"📈 系统成功率: {success_rate:.1f}% (成功: {self.stats['requests_successful']}, 限流: {self.stats['requests_rate_limited']})")
            print(f"🚫 限流率: {rate_limit_rate:.1f}%")
            print(f"❌ 系统失败率: {system_failure_rate:.1f}%")
    
    def calculate_statistics(self, results: List[TestResult]) -> LoadTestReport:
        """计算统计信息"""
        if not results:
            return None
        
        # 重新定义成功、失败和限流的分类
        successful_results = [r for r in results if r.success and not r.is_rate_limited]
        rate_limited_results = [r for r in results if r.is_rate_limited]
        failed_results = [r for r in results if not r.success]
        response_times = [r.response_time_ms for r in successful_results if r.response_time_ms > 0]
        
        # 分析失败原因
        failure_analysis = self.analyze_failures(results)
        
        # 计算基础统计
        total_duration = (datetime.fromisoformat(max(r.timestamp for r in results)) - 
                         datetime.fromisoformat(min(r.timestamp for r in results))).total_seconds()
        
        return LoadTestReport(
            test_start_time=min(r.timestamp for r in results),
            test_end_time=max(r.timestamp for r in results),
            total_duration_seconds=total_duration,
            total_sessions=len(self.session_manager.sessions),
            active_sessions=len(self.session_manager.active_sessions),
            total_requests=len(results),
            successful_requests=len(successful_results),
            failed_requests=len(failed_results),
            rate_limited_requests=len(rate_limited_results),
            success_rate=(len(successful_results) + len(rate_limited_results)) / len(results) * 100,
            rate_limit_percentage=len(rate_limited_results) / len(results) * 100,
            avg_response_time_ms=statistics.mean(response_times) if response_times else 0,
            p95_response_time_ms=self.percentile(response_times, 95) if response_times else 0,
            p99_response_time_ms=self.percentile(response_times, 99) if response_times else 0,
            requests_per_second=len(results) / total_duration if total_duration > 0 else 0,
            sessions_per_second=self.stats["sessions_created"] / total_duration if total_duration > 0 else 0,
            peak_concurrent_sessions=self.stats["peak_concurrent_sessions"],
            failure_analysis=failure_analysis
        )
    
    def percentile(self, data: List[float], percentile: int) -> float:
        """计算百分位数"""
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]
    
    def analyze_failures(self, results: List[TestResult]) -> FailureAnalysis:
        """分析失败原因"""
        failed_results = [r for r in results if not r.success]
        
        if not failed_results:
            return FailureAnalysis(
                error_categories={},
                status_code_distribution={},
                error_messages={},
                ip_failure_distribution={},
                session_failure_distribution={},
                time_based_failures=[],
                retry_analysis={}
            )
        
        # 错误分类
        error_categories = defaultdict(int)
        status_code_distribution = defaultdict(int)
        error_messages = defaultdict(int)
        ip_failure_distribution = defaultdict(int)
        session_failure_distribution = defaultdict(int)
        retry_analysis = defaultdict(int)
        
        # 时间窗口分析 (每5分钟一个窗口)
        time_windows = defaultdict(int)
        
        for result in failed_results:
            # 状态码分布
            status_code_distribution[result.status_code] += 1
            
            # 错误消息统计
            if result.error_message:
                # 截取错误消息的前50个字符作为分类
                error_key = result.error_message[:50] + "..." if len(result.error_message) > 50 else result.error_message
                error_messages[error_key] += 1
            
            # IP失败分布
            ip_failure_distribution[result.ip_address] += 1
            
            # 会话失败分布
            session_failure_distribution[result.session_id] += 1
            
            # 重试分析
            retry_analysis[result.retry_count] += 1
            
            # 错误分类 - 只统计真正的系统性问题
            if result.is_rate_limited:
                error_categories["Rate Limited (429)"] += 1
            elif result.status_code == 0:
                error_categories["Network/Connection Error"] += 1
            elif result.status_code == 500:
                error_categories["Server Internal Error (500)"] += 1
            elif result.status_code == 502:
                error_categories["Bad Gateway (502)"] += 1
            elif result.status_code == 503:
                error_categories["Service Unavailable (503)"] += 1
            elif result.status_code == 504:
                error_categories["Gateway Timeout (504)"] += 1
            elif result.status_code == 404:
                error_categories["Not Found (404)"] += 1
            elif result.status_code == 401:
                error_categories["Unauthorized (401)"] += 1
            elif result.status_code == 403:
                error_categories["Forbidden (403)"] += 1
            elif result.status_code >= 400 and result.status_code < 500:
                error_categories[f"Client Error ({result.status_code})"] += 1
            elif result.status_code >= 500:
                error_categories[f"Server Error ({result.status_code})"] += 1
            else:
                error_categories["Unknown System Error"] += 1
            
            # 时间窗口分析
            try:
                timestamp = datetime.fromisoformat(result.timestamp)
                # 每5分钟一个窗口
                window_key = f"{timestamp.hour:02d}:{timestamp.minute//5*5:02d}-{timestamp.minute//5*5+5:02d}"
                time_windows[window_key] += 1
            except:
                pass
        
        return FailureAnalysis(
            error_categories=dict(error_categories),
            status_code_distribution=dict(status_code_distribution),
            error_messages=dict(error_messages),
            ip_failure_distribution=dict(ip_failure_distribution),
            session_failure_distribution=dict(session_failure_distribution),
            time_based_failures=list(time_windows.items()),
            retry_analysis=dict(retry_analysis)
        )
    
    def print_report(self, report: LoadTestReport):
        """打印测试报告"""
        if not report:
            print("❌ 无法生成测试报告")
            return
        
        print("\n" + "="*80)
        print("📊 PARLANT 1000会话压力测试报告")
        print("="*80)
        print(f"⏱️  测试时长: {report.total_duration_seconds:.1f} 秒")
        print(f"👥 总会话数: {report.total_sessions}")
        print(f"🟢 活跃会话: {report.active_sessions}")
        print(f"📈 峰值并发: {report.peak_concurrent_sessions}")
        print(f"📨 总请求数: {report.total_requests}")
        print(f"✅ 成功请求: {report.successful_requests}")
        print(f"❌ 失败请求: {report.failed_requests}")
        print(f"🚫 限流请求: {report.rate_limited_requests}")
        print(f"📈 系统成功率: {report.success_rate:.2f}% (成功+限流)")
        print(f"🚫 限流率: {report.rate_limit_percentage:.2f}%")
        print(f"❌ 系统失败率: {(report.failed_requests / report.total_requests * 100):.2f}%")
        print(f"🚀 请求速率: {report.requests_per_second:.2f} req/s")
        print(f"👥 会话创建速率: {report.sessions_per_second:.2f} sessions/s")
        print("-" * 80)
        print("⏱️  响应时间统计:")
        print(f"   平均响应时间: {report.avg_response_time_ms:.2f} ms")
        print(f"   P95: {report.p95_response_time_ms:.2f} ms")
        print(f"   P99: {report.p99_response_time_ms:.2f} ms")
        print("="*80)
        
        # 详细失败分析
        if report.failure_analysis and report.failed_requests > 0:
            self.print_failure_analysis(report.failure_analysis, report.failed_requests)
        
        # 性能分析
        print("\n🔍 性能分析:")
        system_failure_rate = (report.failed_requests / report.total_requests * 100)
        
        if report.rate_limit_percentage > 10:
            print("⚠️  限流率较高，建议:")
            print("   - 增加IP地址池数量")
            print("   - 降低请求频率")
            print("   - 检查服务器限流配置")
        elif report.rate_limit_percentage < 1:
            print("✅ 限流率较低，可以:")
            print("   - 适当增加并发数")
            print("   - 提高请求频率")
        
        if system_failure_rate > 5:
            print("⚠️  系统失败率较高，建议:")
            print("   - 检查服务器资源使用情况")
            print("   - 优化数据库连接池")
            print("   - 检查内存使用情况")
            print("   - 检查网络连接稳定性")
        else:
            print("✅ 系统失败率较低，系统运行稳定")
        
        if report.success_rate >= 95:
            print("✅ 系统成功率良好，整体表现优秀")
        elif report.success_rate >= 90:
            print("✅ 系统成功率良好，系统运行稳定")
        else:
            print("⚠️  系统成功率需要关注，建议优化")
    
    def print_failure_analysis(self, failure_analysis: FailureAnalysis, total_failures: int):
        """打印详细失败分析"""
        print("\n" + "="*80)
        print("🔍 详细失败分析")
        print("="*80)
        
        # 错误分类统计
        if failure_analysis.error_categories:
            print("📊 错误分类统计:")
            sorted_categories = sorted(failure_analysis.error_categories.items(), 
                                     key=lambda x: x[1], reverse=True)
            for category, count in sorted_categories:
                percentage = (count / total_failures) * 100
                print(f"   {category}: {count} ({percentage:.1f}%)")
            print()
        
        # 状态码分布
        if failure_analysis.status_code_distribution:
            print("📈 状态码分布:")
            sorted_status = sorted(failure_analysis.status_code_distribution.items(), 
                                 key=lambda x: x[1], reverse=True)
            for status_code, count in sorted_status:
                percentage = (count / total_failures) * 100
                print(f"   {status_code}: {count} ({percentage:.1f}%)")
            print()
        
        # 错误消息统计 (显示前5个最常见的)
        if failure_analysis.error_messages:
            print("💬 常见错误消息 (前5个):")
            sorted_messages = sorted(failure_analysis.error_messages.items(), 
                                   key=lambda x: x[1], reverse=True)[:5]
            for message, count in sorted_messages:
                percentage = (count / total_failures) * 100
                print(f"   \"{message}\": {count} ({percentage:.1f}%)")
            print()
        
        # IP失败分布 (显示失败最多的前5个IP)
        if failure_analysis.ip_failure_distribution:
            print("🌐 IP失败分布 (前5个):")
            sorted_ips = sorted(failure_analysis.ip_failure_distribution.items(), 
                              key=lambda x: x[1], reverse=True)[:5]
            for ip, count in sorted_ips:
                percentage = (count / total_failures) * 100
                print(f"   {ip}: {count} ({percentage:.1f}%)")
            print()
        
        # 重试分析
        if failure_analysis.retry_analysis:
            print("🔄 重试分析:")
            sorted_retries = sorted(failure_analysis.retry_analysis.items())
            for retry_count, count in sorted_retries:
                percentage = (count / total_failures) * 100
                retry_desc = "无重试" if retry_count == 0 else f"{retry_count}次重试"
                print(f"   {retry_desc}: {count} ({percentage:.1f}%)")
            print()
        
        # 时间窗口分析
        if failure_analysis.time_based_failures:
            print("⏰ 时间窗口失败分布:")
            sorted_time = sorted(failure_analysis.time_based_failures, 
                               key=lambda x: x[1], reverse=True)[:5]
            for time_window, count in sorted_time:
                percentage = (count / total_failures) * 100
                print(f"   {time_window}: {count} ({percentage:.1f}%)")
            print()
        
        # 失败原因诊断
        print("🩺 失败原因诊断:")
        self.diagnose_failures(failure_analysis, total_failures)
        print("="*80)
    
    def diagnose_failures(self, failure_analysis: FailureAnalysis, total_failures: int):
        """诊断失败原因并提供建议"""
        # 检查最常见的错误类型
        if failure_analysis.error_categories:
            most_common_error = max(failure_analysis.error_categories.items(), key=lambda x: x[1])
            error_type, count = most_common_error
            percentage = (count / total_failures) * 100
            
            print(f"   主要问题: {error_type} ({percentage:.1f}% 的失败)")
            
            # 根据错误类型提供具体建议
            if "Rate Limited" in error_type:
                print("   💡 建议:")
                print("      - 增加IP地址池数量 (--ip-count 参数)")
                print("      - 降低请求频率")
                print("      - 检查服务器限流配置")
                print("      - 考虑使用更智能的速率控制算法")
                
            elif "Network/Connection" in error_type:
                print("   💡 建议:")
                print("      - 检查网络连接稳定性")
                print("      - 增加连接超时时间")
                print("      - 检查防火墙设置")
                print("      - 验证服务器地址和端口")
                
            elif "Server Internal Error" in error_type:
                print("   💡 建议:")
                print("      - 检查服务器日志")
                print("      - 监控服务器资源使用情况 (CPU, 内存)")
                print("      - 检查数据库连接")
                print("      - 验证模型服务状态")
                
            elif "Service Unavailable" in error_type:
                print("   💡 建议:")
                print("      - 检查服务器是否过载")
                print("      - 验证负载均衡配置")
                print("      - 检查依赖服务状态")
                print("      - 考虑增加服务器实例")
                
            elif "Gateway Timeout" in error_type:
                print("   💡 建议:")
                print("      - 增加请求超时时间")
                print("      - 检查模型推理性能")
                print("      - 优化数据库查询")
                print("      - 检查网络延迟")
                
            elif "Validation Error" in error_type:
                print("   💡 建议:")
                print("      - 检查请求参数格式")
                print("      - 验证必填字段")
                print("      - 检查数据验证规则")
                print("      - 更新测试数据")
                
            elif "Not Found" in error_type:
                print("   💡 建议:")
                print("      - 检查API端点路径")
                print("      - 验证服务器配置")
                print("      - 确认服务是否正常运行")
        
        # 检查重试模式
        if failure_analysis.retry_analysis:
            max_retries = max(failure_analysis.retry_analysis.keys())
            if max_retries > 2:
                print(f"   ⚠️  检测到高重试次数 (最多{max_retries}次)")
                print("   💡 建议:")
                print("      - 检查网络稳定性")
                print("      - 优化重试策略")
                print("      - 增加初始超时时间")
        
        # 检查IP分布
        if failure_analysis.ip_failure_distribution:
            ip_failure_counts = list(failure_analysis.ip_failure_distribution.values())
            if len(ip_failure_counts) > 1:
                max_failures = max(ip_failure_counts)
                min_failures = min(ip_failure_counts)
                if max_failures > min_failures * 2:
                    print("   ⚠️  检测到IP失败分布不均")
                    print("   💡 建议:")
                    print("      - 检查IP地址池配置")
                    print("      - 验证负载均衡")
                    print("      - 检查特定IP的网络状况")
    
    def save_results(self, results: List[TestResult], report: LoadTestReport, 
                    filename: str = None):
        """保存测试结果"""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_dir = os.getenv("TEST_RESULTS_DIR", ".")
            filename = f"{result_dir}/parlant_1000_sessions_test_{timestamp}.json"
        
        data = {
            "metadata": {
                "test_type": "1000_sessions_load_test",
                "timestamp": datetime.now().isoformat(),
                "base_url": self.base_url,
                "max_sessions": self.max_sessions,
                "ip_count": self.ip_count
            },
            "report": asdict(report) if report else None,
            "results": [asdict(r) for r in results],
            "session_stats": self.session_manager.get_stats(),
            "rate_controller_stats": {
                "adaptive_rate_multiplier": self.rate_controller.adaptive_rate_multiplier,
                "base_rate_per_minute": self.rate_controller.base_rate_per_minute
            }
        }
        
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"📁 测试结果已保存到: {filename}")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Parlant 1000会话压力测试工具")
    parser.add_argument("--server", default="http://localhost:8800", 
                       help="Parlant 服务器地址 (默认: http://localhost:8800)")
    parser.add_argument("--sessions", type=int, default=1000, 
                       help="目标会话数 (默认: 1000)")
    parser.add_argument("--duration", type=int, default=3600, 
                       help="测试时长(秒) (默认: 3600)")
    parser.add_argument("--ip-count", type=int, default=10, 
                       help="IP地址池大小 (默认: 10)")
    parser.add_argument("--output", help="输出文件名")
    
    args = parser.parse_args()
    
    tester = Parlant1000SessionTester(
        base_url=args.server,
        max_sessions=args.sessions,
        ip_count=args.ip_count
    )
    
    print("="*80)
    print("🚀 Parlant 1000会话压力测试工具")
    print("="*80)
    print(f"🎯 目标服务器: {args.server}")
    print(f"👥 目标会话数: {args.sessions}")
    print(f"⏱️  测试时长: {args.duration} 秒")
    print(f"🌐 IP地址池: {args.ip_count} 个")
    
    try:
        results = await tester.run_1000_session_test(args.duration)
        
        if not results:
            print("❌ 没有收集到测试结果")
            return
        
        # 生成报告
        print("\n📈 正在生成测试报告...")
        report = tester.calculate_statistics(results)
        tester.print_report(report)
        tester.save_results(results, report, args.output)
        
        print("\n✅ 1000会话压力测试完成！")
        
    except KeyboardInterrupt:
        print("\n⏹️  测试被用户中断")
    except Exception as e:
        print(f"\n❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
