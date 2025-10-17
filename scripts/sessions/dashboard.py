#!/usr/bin/env python3
"""
Parlant 实时监控仪表板

功能：
1. 实时显示系统性能指标
2. 可视化内存和CPU使用情况
3. 显示应用健康状态
4. 提供交互式监控界面
5. 支持历史数据查看

使用方法：
python scripts/dashboard.py --port 8080
"""

import asyncio
import json
import time
import psutil
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import threading
from pathlib import Path
import argparse

try:
    from flask import Flask, render_template, jsonify, request
    from flask_socketio import SocketIO, emit
    import plotly.graph_objs as go
    import plotly.utils
except ImportError:
    print("❌ 缺少依赖包，请安装: pip install flask flask-socketio plotly")
    exit(1)


class RealTimeMonitor:
    """实时监控器"""
    
    def __init__(self, server_url: str = "http://localhost:8800"):
        self.server_url = server_url
        self.metrics_history: List[Dict] = []
        self.max_history = 1000  # 保留最近1000个数据点
        self.running = False
        self.parlant_process = None
        
    def find_parlant_process(self) -> Optional[psutil.Process]:
        """查找 Parlant 进程"""
        print("🔍 正在查找 omni-agent-server 进程...")
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['cmdline']:
                    cmdline = ' '.join(proc.info['cmdline'])
                    # 匹配正确的启动命令：python app/agent.py
                    if 'app/agent.py' in cmdline or ('agent.py' in cmdline and 'python' in cmdline):
                        print(f"✅ 找到 Parlant 进程:")
                        print(f"   PID: {proc.info['pid']}")
                        print(f"   进程名: {proc.info['name']}")
                        print(f"   启动命令: {cmdline}")
                        
                        # 尝试获取端口信息
                        try:
                            process = psutil.Process(proc.info['pid'])
                            connections = process.connections()
                            listening_ports = [conn.laddr.port for conn in connections if conn.laddr and conn.status == 'LISTEN']
                            if listening_ports:
                                print(f"   监听端口: {', '.join(map(str, sorted(set(listening_ports))))}")
                            else:
                                print("   ⚠️  未检测到监听端口（可能正在启动中）")
                                print("   💡 Parlant 默认端口: 8800 (主服务), 8818 (工具服务)")
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            print("   ⚠️  无法获取端口信息")
                        
                        return psutil.Process(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        print("❌ 未找到 Parlant 进程")
        print("💡 请确保使用 'python app/agent.py' 启动服务器")
        return None
    
    def analyze_connections(self, connections) -> Dict:
        """分析连接详细信息"""
        connection_types = {}
        listening_ports = []
        connection_details = []
        
        for conn in connections:
            status = conn.status
            if status not in connection_types:
                connection_types[status] = 0
            connection_types[status] += 1
            
            # 收集监听端口
            if status == 'LISTEN' and conn.laddr:
                listening_ports.append(conn.laddr.port)
            
            # 收集连接详情
            detail = {
                'status': status,
                'local_addr': f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "N/A",
                'remote_addr': f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A",
                'family': 'IPv4' if conn.family == 2 else 'IPv6' if conn.family == 10 else 'Unix',
                'type': 'TCP' if conn.type == 1 else 'UDP' if conn.type == 2 else 'Unix'
            }
            connection_details.append(detail)
        
        return {
            'total': len(connections),
            'listening': connection_types.get('LISTEN', 0),
            'established': connection_types.get('ESTABLISHED', 0),
            'time_wait': connection_types.get('TIME_WAIT', 0),
            'close_wait': connection_types.get('CLOSE_WAIT', 0),
            'listening_ports': sorted(set(listening_ports)),
            'details': connection_details[:10]  # 只显示前10个连接详情
        }
    
    def analyze_threads(self, process) -> Dict:
        """分析线程详细信息"""
        try:
            # 首先获取线程数量
            thread_count = process.num_threads()
            
            # 尝试获取详细线程信息
            try:
                threads = process.threads()
                thread_analysis = {
                    'total': thread_count,
                    'details': []
                }
                
                for thread in threads:
                    thread_info = {
                        'id': thread.id,
                        'user_time': thread.user_time,
                        'system_time': thread.system_time,
                        'status': 'active' if thread.user_time > 0 or thread.system_time > 0 else 'idle'
                    }
                    thread_analysis['details'].append(thread_info)
                
                return thread_analysis
            except (psutil.AccessDenied, AttributeError, OSError) as e:
                # 如果无法获取详细线程信息，至少返回线程数量
                return {
                    'total': thread_count,
                    'details': [],
                    'note': f'无法获取详细线程信息: {str(e)}'
                }
        except (psutil.AccessDenied, AttributeError, OSError) as e:
            return {'total': 0, 'details': [], 'note': f'无法获取线程信息: {str(e)}'}
    
    def calculate_tps_metrics(self, current_time: float) -> Dict:
        """计算TPS和负载分析指标"""
        if len(self.metrics_history) < 2:
            return {
                "tps": 0,
                "avg_response_time": 0,
                "throughput_mb_per_sec": 0,
                "load_factor": 0,
                "concurrency": 0
            }
        
        # 获取最近的数据点
        recent_metrics = self.metrics_history[-10:]  # 最近10个数据点
        
        # 计算TPS (基于健康检查请求)
        time_span = 0
        successful_requests = 0
        total_response_time = 0
        
        for i in range(1, len(recent_metrics)):
            prev_metric = recent_metrics[i-1]
            curr_metric = recent_metrics[i]
            
            time_diff = (datetime.fromisoformat(curr_metric['timestamp']) - 
                        datetime.fromisoformat(prev_metric['timestamp'])).total_seconds()
            time_span += time_diff
            
            if curr_metric['health']['status'] == 'healthy':
                successful_requests += 1
                total_response_time += curr_metric['health']['response_time_ms']
        
        tps = successful_requests / time_span if time_span > 0 else 0
        avg_response_time = total_response_time / successful_requests if successful_requests > 0 else 0
        
        # 计算吞吐量 (基于网络I/O)
        if len(recent_metrics) >= 2:
            first_metric = recent_metrics[0]
            last_metric = recent_metrics[-1]
            
            network_diff = (last_metric['system']['network_sent_mb'] + last_metric['system']['network_recv_mb'] - 
                          first_metric['system']['network_sent_mb'] - first_metric['system']['network_recv_mb'])
            
            time_diff = (datetime.fromisoformat(last_metric['timestamp']) - 
                        datetime.fromisoformat(first_metric['timestamp'])).total_seconds()
            
            throughput_mb_per_sec = network_diff / time_diff if time_diff > 0 else 0
        else:
            throughput_mb_per_sec = 0
        
        # 计算负载因子 (CPU + 内存 + 连接数的综合指标)
        latest_metric = recent_metrics[-1]
        cpu_load = latest_metric['system']['cpu_percent'] / 100
        memory_load = latest_metric['system']['memory_percent'] / 100
        connection_load = min(latest_metric['application']['connections'] / 100, 1)  # 假设100个连接为满负载
        
        load_factor = (cpu_load + memory_load + connection_load) / 3
        
        # 并发度 (基于线程数和连接数)
        concurrency = latest_metric['application']['threads'] + latest_metric['application']['connections']
        
        return {
            "tps": round(tps, 2),
            "avg_response_time": round(avg_response_time, 2),
            "throughput_mb_per_sec": round(throughput_mb_per_sec, 2),
            "load_factor": round(load_factor, 3),
            "concurrency": concurrency
        }
    
    def collect_metrics(self) -> Dict:
        """收集当前指标"""
        # 系统指标
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        network = psutil.net_io_counters()
        load_avg = psutil.getloadavg() if hasattr(psutil, 'getloadavg') else [0, 0, 0]
        
        # 应用指标
        app_memory = 0
        app_cpu = 0
        app_threads = 0
        app_connections = 0
        app_listening_ports = []
        app_connection_details = []
        app_file_descriptors = 0
        app_io_counters = None
        app_create_time = 0
        app_num_fds = 0
        
        if not self.parlant_process:
            self.parlant_process = self.find_parlant_process()
        
        if self.parlant_process:
            try:
                memory_info = self.parlant_process.memory_info()
                app_memory = memory_info.rss / 1024 / 1024  # MB
                app_cpu = self.parlant_process.cpu_percent()
                app_threads = self.parlant_process.num_threads()
                
                # 线程详细信息
                thread_analysis = self.analyze_threads(self.parlant_process)
                
                # 连接详细信息
                connections = self.parlant_process.connections()
                app_connections = len(connections)
                
                # 分析连接类型和详细信息
                connection_analysis = self.analyze_connections(connections)
                app_listening_ports = connection_analysis['listening_ports']
                
                # 连接详情（用于分析）
                app_connection_details = {
                    'listening': connection_analysis['listening'],
                    'established': connection_analysis['established'],
                    'time_wait': connection_analysis['time_wait'],
                    'close_wait': connection_analysis['close_wait'],
                    'total': app_connections,
                    'listening_ports': app_listening_ports,
                    'connection_details': connection_analysis['details']
                }
                
                # 文件描述符数量
                try:
                    app_num_fds = self.parlant_process.num_fds()
                except (AttributeError, psutil.AccessDenied):
                    app_num_fds = 0
                
                # I/O 统计
                try:
                    io_counters = self.parlant_process.io_counters()
                    app_io_counters = {
                        'read_count': io_counters.read_count,
                        'write_count': io_counters.write_count,
                        'read_bytes': io_counters.read_bytes / 1024,  # KB
                        'write_bytes': io_counters.write_bytes / 1024,  # KB
                        'read_chars': io_counters.read_chars,
                        'write_chars': io_counters.write_chars
                    }
                except (psutil.AccessDenied, AttributeError):
                    app_io_counters = None
                
                # 进程创建时间
                try:
                    app_create_time = self.parlant_process.create_time()
                except (psutil.AccessDenied, AttributeError):
                    app_create_time = 0
                    
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self.parlant_process = None
        
        # 健康检查
        health_status = "unknown"
        response_time = 0
        try:
            start_time = time.time()
            response = requests.get(f"{self.server_url}/health", timeout=5)
            response_time = (time.time() - start_time) * 1000
            health_status = "healthy" if response.status_code == 200 else "unhealthy"
        except requests.exceptions.RequestException:
            health_status = "unreachable"
        
        # 计算TPS和负载分析
        current_time = time.time()
        tps_metrics = self.calculate_tps_metrics(current_time)
        
        return {
            "timestamp": datetime.now().isoformat(),
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_used_mb": memory.used / 1024 / 1024,
                "memory_available_mb": memory.available / 1024 / 1024,
                "disk_usage_percent": disk.percent,
                "network_sent_mb": network.bytes_sent / 1024 / 1024,
                "network_recv_mb": network.bytes_recv / 1024 / 1024,
                "load_avg_1m": load_avg[0],
                "load_avg_5m": load_avg[1],
                "load_avg_15m": load_avg[2]
            },
            "application": {
                "memory_mb": app_memory,
                "cpu_percent": app_cpu,
                "threads": app_threads,
                "thread_analysis": thread_analysis if 'thread_analysis' in locals() else {'total': 0, 'details': []},
                "connections": app_connections,
                "connection_details": app_connection_details,
                "listening_ports": app_listening_ports,
                "file_descriptors": app_num_fds,
                "io_counters": app_io_counters,
                "uptime_seconds": current_time - app_create_time if app_create_time > 0 else 0,
                "process_found": self.parlant_process is not None
            },
            "health": {
                "status": health_status,
                "response_time_ms": response_time
            },
            "performance": tps_metrics
        }
    
    def start_monitoring(self):
        """开始监控"""
        self.running = True
        while self.running:
            metrics = self.collect_metrics()
            self.metrics_history.append(metrics)
            
            # 保持历史数据在限制范围内
            if len(self.metrics_history) > self.max_history:
                self.metrics_history = self.metrics_history[-self.max_history:]
            
            time.sleep(2)  # 每2秒收集一次数据
    
    def stop_monitoring(self):
        """停止监控"""
        self.running = False
    
    def get_latest_metrics(self) -> Optional[Dict]:
        """获取最新指标"""
        return self.metrics_history[-1] if self.metrics_history else None
    
    def get_metrics_history(self, minutes: int = 10) -> List[Dict]:
        """获取历史指标"""
        cutoff_time = datetime.now() - timedelta(minutes=minutes)
        return [
            m for m in self.metrics_history
            if datetime.fromisoformat(m['timestamp']) > cutoff_time
        ]


# 全局监控器实例
monitor = RealTimeMonitor()

# Flask 应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'parlant-monitor-secret'
socketio = SocketIO(app, cors_allowed_origins="*")


@app.route('/')
def index():
    """主页"""
    return render_template('dashboard.html')


@app.route('/api/metrics/latest')
def get_latest_metrics():
    """获取最新指标"""
    metrics = monitor.get_latest_metrics()
    if metrics:
        return jsonify(metrics)
    else:
        return jsonify({"error": "No metrics available"}), 404


@app.route('/api/metrics/history')
def get_metrics_history():
    """获取历史指标"""
    minutes = request.args.get('minutes', 10, type=int)
    history = monitor.get_metrics_history(minutes)
    return jsonify(history)


@app.route('/api/health')
def health_check():
    """健康检查"""
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@socketio.on('connect')
def handle_connect():
    """客户端连接"""
    print(f"📱 客户端连接: {request.sid}")
    emit('status', {'message': 'Connected to Parlant Monitor'})


@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开连接"""
    print(f"📱 客户端断开: {request.sid}")


def emit_metrics():
    """发送指标数据"""
    while True:
        if monitor.metrics_history:
            latest = monitor.get_latest_metrics()
            if latest:
                socketio.emit('metrics_update', latest)
        time.sleep(2)


@app.route('/api/charts/cpu')
def get_cpu_chart():
    """获取 CPU 图表数据"""
    history = monitor.get_metrics_history(30)  # 最近30分钟
    if not history:
        return jsonify({"error": "No data available"})
    
    timestamps = [m['timestamp'] for m in history]
    cpu_values = [m['system']['cpu_percent'] for m in history]
    app_cpu_values = [m['application']['cpu_percent'] for m in history]
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=cpu_values,
        mode='lines',
        name='系统 CPU',
        line=dict(color='blue', width=2)
    ))
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=app_cpu_values,
        mode='lines',
        name='应用 CPU',
        line=dict(color='red', width=2)
    ))
    
    fig.update_layout(
        title='CPU 使用率',
        xaxis_title='时间',
        yaxis_title='CPU 使用率 (%)',
        hovermode='x unified'
    )
    
    return jsonify(fig.to_dict())


@app.route('/api/charts/memory')
def get_memory_chart():
    """获取内存图表数据"""
    history = monitor.get_metrics_history(30)
    if not history:
        return jsonify({"error": "No data available"})
    
    timestamps = [m['timestamp'] for m in history]
    memory_values = [m['system']['memory_percent'] for m in history]
    app_memory_values = [m['application']['memory_mb'] for m in history]
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=memory_values,
        mode='lines',
        name='系统内存使用率',
        line=dict(color='green', width=2),
        yaxis='y'
    ))
    
    # 添加应用内存（使用右侧Y轴）
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=app_memory_values,
        mode='lines',
        name='应用内存 (MB)',
        line=dict(color='orange', width=2),
        yaxis='y2'
    ))
    
    fig.update_layout(
        title='内存使用情况',
        xaxis_title='时间',
        yaxis=dict(title='系统内存使用率 (%)', side='left'),
        yaxis2=dict(title='应用内存 (MB)', side='right', overlaying='y'),
        hovermode='x unified'
    )
    
    return jsonify(fig.to_dict())


@app.route('/api/charts/response_time')
def get_response_time_chart():
    """获取响应时间图表数据"""
    history = monitor.get_metrics_history(30)
    if not history:
        return jsonify({"error": "No data available"})
    
    timestamps = [m['timestamp'] for m in history]
    response_times = [m['health']['response_time_ms'] for m in history]
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=response_times,
        mode='lines+markers',
        name='响应时间',
        line=dict(color='purple', width=2),
        marker=dict(size=4)
    ))
    
    fig.update_layout(
        title='服务响应时间',
        xaxis_title='时间',
        yaxis_title='响应时间 (ms)',
        hovermode='x unified'
    )
    
    return jsonify(fig.to_dict())


@app.route('/api/charts/performance')
def get_performance_chart():
    """获取性能指标图表数据"""
    history = monitor.get_metrics_history(30)
    if not history:
        return jsonify({"error": "No data available"})
    
    timestamps = [m['timestamp'] for m in history]
    tps_values = [m.get('performance', {}).get('tps', 0) for m in history]
    load_factor_values = [m.get('performance', {}).get('load_factor', 0) * 100 for m in history]
    concurrency_values = [m.get('performance', {}).get('concurrency', 0) for m in history]
    
    fig = go.Figure()
    
    # TPS
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=tps_values,
        mode='lines',
        name='TPS',
        line=dict(color='blue', width=2),
        yaxis='y'
    ))
    
    # 负载因子
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=load_factor_values,
        mode='lines',
        name='负载因子 (%)',
        line=dict(color='red', width=2),
        yaxis='y2'
    ))
    
    # 并发度
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=concurrency_values,
        mode='lines',
        name='并发度',
        line=dict(color='green', width=2),
        yaxis='y3'
    ))
    
    fig.update_layout(
        title='性能指标分析',
        xaxis_title='时间',
        yaxis=dict(title='TPS', side='left'),
        yaxis2=dict(title='负载因子 (%)', side='right', overlaying='y'),
        yaxis3=dict(title='并发度', side='right', overlaying='y', position=0.85),
        hovermode='x unified'
    )
    
    return jsonify(fig.to_dict())


@app.route('/api/threads')
def get_threads_info():
    """获取线程详细信息"""
    if not monitor.parlant_process:
        monitor.parlant_process = monitor.find_parlant_process()
    
    if not monitor.parlant_process:
        return jsonify({"error": "Process not found"})
    
    try:
        thread_analysis = monitor.analyze_threads(monitor.parlant_process)
        return jsonify(thread_analysis)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/api/connections')
def get_connections_info():
    """获取连接详细信息"""
    if not monitor.parlant_process:
        monitor.parlant_process = monitor.find_parlant_process()
    
    if not monitor.parlant_process:
        return jsonify({"error": "Process not found"})
    
    try:
        connections = monitor.parlant_process.connections()
        connection_analysis = monitor.analyze_connections(connections)
        return jsonify(connection_analysis)
    except Exception as e:
        return jsonify({"error": str(e)})


def create_html_template():
    """创建 HTML 模板"""
    template_dir = Path(__file__).parent / "templates"
    template_dir.mkdir(exist_ok=True)
    
    html_content = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Parlant 实时监控仪表板</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            text-align: center;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .metric-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            text-align: center;
        }
        .metric-value {
            font-size: 2em;
            font-weight: bold;
            margin: 10px 0;
        }
        .metric-label {
            color: #666;
            font-size: 0.9em;
        }
        .status-healthy { color: #28a745; }
        .status-unhealthy { color: #dc3545; }
        .status-unknown { color: #ffc107; }
        .charts-container {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        .chart-container {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .details-container {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-top: 20px;
        }
        .details-panel {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .details-panel h3 {
            margin: 0 0 15px 0;
            color: #333;
            border-bottom: 2px solid #007bff;
            padding-bottom: 10px;
        }
        .details-content {
            max-height: 300px;
            overflow-y: auto;
        }
        .thread-item, .connection-item {
            background: #f8f9fa;
            margin: 5px 0;
            padding: 10px;
            border-radius: 5px;
            border-left: 4px solid #007bff;
        }
        .thread-item.active {
            border-left-color: #28a745;
        }
        .thread-item.idle {
            border-left-color: #6c757d;
        }
        .connection-item.listening {
            border-left-color: #17a2b8;
        }
        .connection-item.established {
            border-left-color: #28a745;
        }
        .connection-item.time_wait {
            border-left-color: #ffc107;
        }
        .metric-label-small {
            font-size: 0.8em;
            color: #666;
            margin-right: 10px;
        }
        .full-width {
            grid-column: 1 / -1;
        }
        .connection-status {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 10px 15px;
            border-radius: 5px;
            color: white;
            font-weight: bold;
        }
        .connected { background-color: #28a745; }
        .disconnected { background-color: #dc3545; }
    </style>
</head>
<body>
    <div class="connection-status" id="connectionStatus">连接中...</div>
    <div class="metrics-grid">
        <div class="metric-card">
            <div class="metric-label">系统 CPU 使用率</div>
            <div class="metric-value" id="systemCpu">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">系统内存使用率</div>
            <div class="metric-value" id="systemMemory">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">应用内存</div>
            <div class="metric-value" id="appMemory">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">服务状态</div>
            <div class="metric-value" id="serviceStatus">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">响应时间</div>
            <div class="metric-value" id="responseTime">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">活跃连接</div>
            <div class="metric-value" id="connections">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">线程数</div>
            <div class="metric-value" id="threads">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">文件描述符</div>
            <div class="metric-value" id="fileDescriptors">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">TPS</div>
            <div class="metric-value" id="tps">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">负载因子</div>
            <div class="metric-value" id="loadFactor">--</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">并发度</div>
            <div class="metric-value" id="concurrency">--</div>
        </div>
    </div>
    
    <div class="charts-container">
        <div class="chart-container">
            <div id="cpuChart"></div>
        </div>
        <div class="chart-container">
            <div id="memoryChart"></div>
        </div>
        <div class="chart-container full-width">
            <div id="responseTimeChart"></div>
        </div>
        <div class="chart-container full-width">
            <div id="performanceChart"></div>
        </div>
    </div>
    
    <!-- 详细信息面板 -->
    <div class="details-container">
        <div class="details-panel">
            <h3>📊 线程详细信息</h3>
            <div id="threadsDetails" class="details-content">
                <p>正在加载线程信息...</p>
            </div>
        </div>
        
        <div class="details-panel">
            <h3>🔗 连接详细信息</h3>
            <div id="connectionsDetails" class="details-content">
                <p>正在加载连接信息...</p>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        let charts = {};
        
        // 连接状态管理
        socket.on('connect', function() {
            document.getElementById('connectionStatus').textContent = '已连接';
            document.getElementById('connectionStatus').className = 'connection-status connected';
        });
        
        socket.on('disconnect', function() {
            document.getElementById('connectionStatus').textContent = '连接断开';
            document.getElementById('connectionStatus').className = 'connection-status disconnected';
        });
        
        // 更新指标显示
        socket.on('metrics_update', function(data) {
            updateMetrics(data);
        });
        
        function updateMetrics(data) {
            document.getElementById('systemCpu').textContent = data.system.cpu_percent.toFixed(1) + '%';
            document.getElementById('systemMemory').textContent = data.system.memory_percent.toFixed(1) + '%';
            document.getElementById('appMemory').textContent = data.application.memory_mb.toFixed(1) + ' MB';
            document.getElementById('responseTime').textContent = data.health.response_time_ms.toFixed(0) + ' ms';
            document.getElementById('connections').textContent = data.application.connections;
            document.getElementById('threads').textContent = data.application.threads;
            document.getElementById('fileDescriptors').textContent = data.application.file_descriptors;
            document.getElementById('tps').textContent = data.performance.tps;
            document.getElementById('loadFactor').textContent = (data.performance.load_factor * 100).toFixed(1) + '%';
            document.getElementById('concurrency').textContent = data.performance.concurrency;
            
            const statusElement = document.getElementById('serviceStatus');
            statusElement.textContent = data.health.status;
            statusElement.className = 'metric-value status-' + data.health.status;
        }
        
        // 初始化图表
        function initCharts() {
            // CPU 图表
            fetch('/api/charts/cpu')
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        console.error('CPU chart error:', data.error);
                        return;
                    }
                    Plotly.newPlot('cpuChart', data.data, data.layout, {responsive: true});
                });
            
            // 内存图表
            fetch('/api/charts/memory')
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        console.error('Memory chart error:', data.error);
                        return;
                    }
                    Plotly.newPlot('memoryChart', data.data, data.layout, {responsive: true});
                });
            
            // 响应时间图表
            fetch('/api/charts/response_time')
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        console.error('Response time chart error:', data.error);
                        return;
                    }
                    Plotly.newPlot('responseTimeChart', data.data, data.layout, {responsive: true});
                });
            
            // 性能指标图表
            fetch('/api/charts/performance')
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        console.error('Performance chart error:', data.error);
                        return;
                    }
                    Plotly.newPlot('performanceChart', data.data, data.layout, {responsive: true});
                });
        }
        
        // 加载详细信息
        function loadDetails() {
            // 加载线程信息
            fetch('/api/threads')
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        document.getElementById('threadsDetails').innerHTML = '<p>❌ 无法加载线程信息: ' + data.error + '</p>';
                        return;
                    }
                    displayThreads(data);
                })
                .catch(error => {
                    document.getElementById('threadsDetails').innerHTML = '<p>❌ 加载线程信息失败</p>';
                });
            
            // 加载连接信息
            fetch('/api/connections')
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        document.getElementById('connectionsDetails').innerHTML = '<p>❌ 无法加载连接信息: ' + data.error + '</p>';
                        return;
                    }
                    displayConnections(data);
                })
                .catch(error => {
                    document.getElementById('connectionsDetails').innerHTML = '<p>❌ 加载连接信息失败</p>';
                });
        }
        
        function displayThreads(data) {
            console.log('Thread data:', data); // 调试信息
            
            let html = '<div class="metric-card"><div class="metric-label">总线程数</div><div class="metric-value">' + (data.total || 0) + '</div></div>';
            
            if (data.note) {
                html += '<p style="color: #666; font-size: 0.9em;">' + data.note + '</p>';
            }
            
            if (data.details && data.details.length > 0) {
                html += '<h4>线程详情:</h4>';
                data.details.forEach(thread => {
                    const statusClass = thread.status === 'active' ? 'active' : 'idle';
                    html += '<div class="thread-item ' + statusClass + '">';
                    html += '<span class="metric-label-small">ID:</span>' + thread.id + ' ';
                    html += '<span class="metric-label-small">状态:</span>' + thread.status + ' ';
                    html += '<span class="metric-label-small">用户时间:</span>' + thread.user_time.toFixed(2) + 's ';
                    html += '<span class="metric-label-small">系统时间:</span>' + thread.system_time.toFixed(2) + 's';
                    html += '</div>';
                });
            } else if (data.total > 0) {
                html += '<p style="color: #666; font-size: 0.9em;">线程详情不可用，但检测到 ' + data.total + ' 个线程</p>';
            }
            
            document.getElementById('threadsDetails').innerHTML = html;
        }
        
        function displayConnections(data) {
            console.log('Connection data:', data); // 调试信息
            
            let html = '<div class="metric-card"><div class="metric-label">总连接数</div><div class="metric-value">' + (data.total || 0) + '</div></div>';
            
            // 连接类型统计
            html += '<div class="metric-card"><div class="metric-label">监听</div><div class="metric-value">' + (data.listening || 0) + '</div></div>';
            html += '<div class="metric-card"><div class="metric-label">已建立</div><div class="metric-value">' + (data.established || 0) + '</div></div>';
            html += '<div class="metric-card"><div class="metric-label">等待关闭</div><div class="metric-value">' + (data.time_wait || 0) + '</div></div>';
            
            if (data.listening_ports && data.listening_ports.length > 0) {
                html += '<h4>监听端口:</h4><p>' + data.listening_ports.join(', ') + '</p>';
            }
            
            if (data.details && data.details.length > 0) {
                html += '<h4>连接详情:</h4>';
                data.details.forEach(conn => {
                    const statusClass = conn.status.toLowerCase().replace('_', '');
                    html += '<div class="connection-item ' + statusClass + '">';
                    html += '<span class="metric-label-small">状态:</span>' + conn.status + ' ';
                    html += '<span class="metric-label-small">本地:</span>' + conn.local_addr + ' ';
                    html += '<span class="metric-label-small">远程:</span>' + conn.remote_addr + ' ';
                    html += '<span class="metric-label-small">类型:</span>' + conn.type + '/' + conn.family;
                    html += '</div>';
                });
            }
            
            document.getElementById('connectionsDetails').innerHTML = html;
        }
        
        // 定期更新图表和详细信息
        setInterval(initCharts, 30000); // 每30秒更新一次
        setInterval(loadDetails, 10000); // 每10秒更新详细信息
        
        // 页面加载完成后初始化
        document.addEventListener('DOMContentLoaded', function() {
            initCharts();
            loadDetails();
        });
    </script>
</body>
</html>
    """
    
    with open(template_dir / "dashboard.html", "w", encoding="utf-8") as f:
        f.write(html_content)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Parlant 实时监控仪表板")
    parser.add_argument("--port", type=int, default=8080, help="仪表板端口 (默认: 8080)")
    parser.add_argument("--server", default="http://localhost:8800", help="Parlant 服务器地址")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址 (默认: 0.0.0.0)")
    
    args = parser.parse_args()
    
    # 更新监控器配置
    monitor.server_url = args.server
    
    # 创建 HTML 模板
    create_html_template()
    
    print(f"🚀 启动 Parlant 监控仪表板...")
    print(f"📡 监控目标: {args.server}")
    print(f"🌐 仪表板地址: http://{args.host}:{args.port}")
    
    # 启动监控线程
    monitor_thread = threading.Thread(target=monitor.start_monitoring, daemon=True)
    monitor_thread.start()
    
    # 启动指标发送线程
    emit_thread = threading.Thread(target=emit_metrics, daemon=True)
    emit_thread.start()
    
    try:
        # 启动 Flask 应用
        socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\n⏹️  正在停止监控仪表板...")
        monitor.stop_monitoring()


if __name__ == "__main__":
    main()
