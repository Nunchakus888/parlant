#!/usr/bin/env python3
"""
阶段2：小规模并发测试 - 验证LLM并发控制是否有效
优化版本：使用唯一值避免并发冲突，加强测试梯度
"""
import asyncio
import httpx
import time
import uuid
import random
import psutil
import json
from datetime import datetime
from statistics import mean
import sys

from parlant.core.common import md5_checksum

BASE_URL = "http://localhost:8800"

def get_app_metrics():
    """获取应用资源指标 - 通过启动命令识别进程"""
    # 获取系统整体资源作为参考
    system_cpu = psutil.cpu_percent(interval=1)
    system_memory = psutil.virtual_memory()
    
    # 通过启动命令查找目标进程
    app_processes = []
    total_threads = 0
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_info', 'memory_percent']):
        try:
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''
                # 检查启动命令特征
                if any(keyword in cmdline.lower() for keyword in [
                    'app/agent.py', 'agent.py'
                ]):
                    app_processes.append(proc)
                    # 获取进程的线程数
                    try:
                        thread_count = proc.num_threads()
                        total_threads += thread_count
                    except:
                        pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    # 计算应用资源使用
    app_cpu = sum(proc.info['cpu_percent'] for proc in app_processes if proc.info['cpu_percent'])
    app_memory_mb = sum(proc.info['memory_info'].rss for proc in app_processes) / (1024**2)
    app_memory_percent = sum(proc.info['memory_percent'] for proc in app_processes if proc.info['memory_percent'])
    
    # 获取进程详细信息
    process_info = []
    for proc in app_processes:
        try:
            cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else 'N/A'
            thread_count = proc.num_threads() if hasattr(proc, 'num_threads') else 'N/A'
            process_info.append({
                'pid': proc.info['pid'],
                'name': proc.info['name'],
                'cmdline': cmdline[:100] + '...' if len(cmdline) > 100 else cmdline,
                'threads': thread_count
            })
        except:
            continue
    
    return {
        "app_cpu_percent": app_cpu,
        "app_memory_mb": app_memory_mb,
        "app_memory_percent": app_memory_percent,
        "app_process_count": len(app_processes),
        "app_thread_count": total_threads,
        "app_process_info": process_info,
        "system_cpu_percent": system_cpu,
        "system_memory_percent": system_memory.percent,
        "system_memory_total_gb": system_memory.total / (1024**3),
        "timestamp": datetime.now().isoformat()
    }

async def concurrent_test(concurrency: int, total_requests: int):
    """并发测试"""
    url = f"{BASE_URL}/sessions/chat"
    
    # 获取测试开始前的应用指标
    start_metrics = get_app_metrics()
    
    print(f"\n{'='*60}")
    print(f"测试配置: 并发数={concurrency}, 总请求={total_requests}")
    print(f"应用状态: CPU {start_metrics['app_cpu_percent']:.1f}%, 内存 {start_metrics['app_memory_mb']:.0f}MB ({start_metrics['app_memory_percent']:.1f}%)")
    print(f"系统参考: CPU {start_metrics['system_cpu_percent']:.1f}%, 内存 {start_metrics['system_memory_percent']:.1f}% (进程数: {start_metrics['app_process_count']}, 线程数: {start_metrics['app_thread_count']})")
    
    # 显示找到的进程信息
    if start_metrics['app_process_info']:
        print(f"检测到的应用进程:")
        for i, proc_info in enumerate(start_metrics['app_process_info'], 1):
            print(f"  {i}. PID {proc_info['pid']} - {proc_info['name']} (线程数: {proc_info['threads']})")
            print(f"     命令: {proc_info['cmdline']}")
    else:
        print("⚠️  未检测到8800端口相关进程，将监控所有Python进程")
    
    print(f"{'='*60}\n")
    
    async def single_request(req_id: int):
        """单个请求 - 使用唯一值避免并发冲突"""
        # 生成唯一标识符，避免并发冲突
        unique_suffix = str(uuid.uuid4())[:8]
        timestamp = int(time.time() * 1000)  # 毫秒时间戳
        
        payload = {
            "message": "hello",
            "customer_id": f"customer_{unique_suffix}",
            "session_id": f"session_load_test_{req_id % 30}",
            "tenant_id": f"test_tenant_concurrency",
            "chatbot_id": f"test_bot_load_test",
            "md5_checksum": "test_load_test",
            "timeout": 57
        }
        
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=67.0) as client:
                response = await client.post(url, json=payload)
            elapsed = time.time() - start
            
            return {
                "id": req_id,
                "status": response.status_code,
                "time": elapsed,
                "success": response.status_code == 200,
                "start_time": start
            }
        except Exception as e:
            elapsed = time.time() - start
            return {
                "id": req_id,
                "status": "error",
                "time": elapsed,
                "success": False,
                "error": str(e),
                "start_time": start
            }
    
    # 分批执行，避免一次性压垮服务
    all_results = []
    batch_count = (total_requests + concurrency - 1) // concurrency
    
    for batch_num in range(batch_count):
        batch_start = batch_num * concurrency
        batch_size = min(concurrency, total_requests - batch_start)
        
        print(f"批次 {batch_num + 1}/{batch_count}: "
              f"发送 {batch_size} 个并发请求...")
        
        batch_start_time = time.time()
        
        # 并发发送请求
        tasks = [
            single_request(batch_start + i)
            for i in range(batch_size)
        ]
        batch_results = await asyncio.gather(*tasks)
        all_results.extend(batch_results)
        
        batch_elapsed = time.time() - batch_start_time
        
        # 统计批次结果
        batch_success = sum(1 for r in batch_results if r["success"])
        batch_times = [r["time"] for r in batch_results if r["success"]]
        
        # 获取批次完成后的应用指标
        batch_metrics = get_app_metrics()
        
        # 计算进度
        completed_requests = batch_start + batch_size
        progress_percent = (completed_requests / total_requests) * 100
        
        print(f"  完成: {batch_elapsed:.1f}s (进度: {progress_percent:.1f}%)")
        if batch_times:
            print(f"  平均响应时间: {mean(batch_times):.2f}s")
        print(f"  应用状态: CPU {batch_metrics['app_cpu_percent']:.1f}%, 内存 {batch_metrics['app_memory_mb']:.0f}MB")
        
        # 批次间暂停，让系统缓一缓 - 根据批次大小调整
        if batch_num < batch_count - 1:
            pause = min(8, 3 + batch_size // 10)  # 根据批次大小动态调整，最多8秒
            print(f"  等待 {pause}s 后继续...\n")
            await asyncio.sleep(pause)
    
    # 获取测试结束后的应用指标
    end_metrics = get_app_metrics()
    
    # 总体统计
    print(f"\n{'='*60}")
    print(f"并发测试结果 (并发={concurrency})")
    print(f"{'='*60}")
    
    # 应用资源变化
    app_cpu_change = end_metrics['app_cpu_percent'] - start_metrics['app_cpu_percent']
    app_memory_change = end_metrics['app_memory_mb'] - start_metrics['app_memory_mb']
    app_memory_percent_change = end_metrics['app_memory_percent'] - start_metrics['app_memory_percent']
    
    print(f"应用资源变化:")
    print(f"  CPU: {start_metrics['app_cpu_percent']:.1f}% → {end_metrics['app_cpu_percent']:.1f}% ({app_cpu_change:+.1f}%)")
    print(f"  内存: {start_metrics['app_memory_mb']:.0f}MB → {end_metrics['app_memory_mb']:.0f}MB ({app_memory_change:+.0f}MB)")
    print(f"  内存占比: {start_metrics['app_memory_percent']:.1f}% → {end_metrics['app_memory_percent']:.1f}% ({app_memory_percent_change:+.1f}%)")
    print()
    
    success_count = sum(1 for r in all_results if r["success"])
    success_rate = success_count / len(all_results) * 100
    times = [r["time"] for r in all_results if r["success"]]
    
    print(f"总请求数:     {len(all_results)}")
    print(f"成功率:       {success_rate:.1f}% ({success_count}/{len(all_results)})")
    
    if times:
        sorted_times = sorted(times)
        print(f"平均响应时间: {mean(times):.2f}s")
        print(f"最小响应时间: {min(times):.2f}s")
        print(f"最大响应时间: {max(times):.2f}s")
        print(f"P50:          {sorted_times[len(times)//2]:.2f}s")
        print(f"P90:          {sorted_times[int(len(times)*0.9)]:.2f}s")
        print(f"P95:          {sorted_times[int(len(times)*0.95)]:.2f}s")
    
    # 错误分析
    errors = [r for r in all_results if not r["success"]]
    if errors:
        print(f"\n错误统计:")
        error_types = {}
        for e in errors:
            status = e.get("status", "unknown")
            error_types[status] = error_types.get(status, 0) + 1
        
        for error_type, count in sorted(error_types.items()):
            print(f"  {error_type}: {count}")
        
        # 简化错误详情
        if len(errors) <= 3:
            print(f"\n错误详情:")
            for i, error in enumerate(errors):
                print(f"  {i+1}. 状态={error.get('status', 'unknown')}, 时间={error['time']:.2f}s")
    
    # 简化性能分析
    if times:
        slow_responses = sum(1 for t in times if t > 30)
        if slow_responses > 0:
            print(f"\n⚠️  慢响应 (>30s): {slow_responses} 个")
    
    return {
        "concurrency": concurrency,
        "success_rate": success_rate,
        "results": all_results,
        "app_metrics": {
            "start": start_metrics,
            "end": end_metrics,
            "app_cpu_change": app_cpu_change,
            "app_memory_change": app_memory_change,
            "app_memory_percent_change": app_memory_percent_change
        }
    }


async def health_check():
    """健康检查"""
    url = f"{BASE_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
        return response.status_code == 200
    except:
        return False

def test_process_detection():
    """测试进程检测功能"""
    print("🔍 测试进程检测功能...")
    
    # 测试应用指标获取
    metrics = get_app_metrics()
    print(f"应用指标获取结果:")
    print(f"  - 检测到进程数: {metrics['app_process_count']}")
    print(f"  - 应用CPU: {metrics['app_cpu_percent']:.1f}%")
    print(f"  - 应用内存: {metrics['app_memory_mb']:.0f}MB")
    
    if metrics['app_process_info']:
        print(f"  - 进程详情:")
        for proc_info in metrics['app_process_info']:
            print(f"    * PID {proc_info['pid']}: {proc_info['name']}")
            print(f"      命令: {proc_info['cmdline']}")
        print(f"✅ 通过启动命令成功识别进程")
    else:
        print(f"❌ 未能识别到目标进程")
    
    return metrics['app_process_count'] > 0

def list_all_python_processes():
    """列出所有Python进程，用于调试"""
    print("🐍 所有Python进程列表:")
    python_processes = []
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else 'N/A'
                python_processes.append({
                    'pid': proc.info['pid'],
                    'name': proc.info['name'],
                    'cmdline': cmdline
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    if python_processes:
        for i, proc in enumerate(python_processes, 1):
            print(f"  {i}. PID {proc['pid']} - {proc['name']}")
            print(f"     命令: {proc['cmdline'][:150]}{'...' if len(proc['cmdline']) > 150 else ''}")
    else:
        print("  未找到Python进程")
    
    return python_processes

def generate_test_report(results):
    """生成测试报告"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = f"concurrent_test_report_{timestamp}.json"
    
    # 计算总体统计
    total_requests = sum(len(r["results"]) for r in results)
    total_success = sum(len([req for req in r["results"] if req["success"]]) for r in results)
    overall_success_rate = (total_success / total_requests * 100) if total_requests > 0 else 0
    
    # 计算平均响应时间
    all_times = []
    for r in results:
        all_times.extend([req["time"] for req in r["results"] if req["success"]])
    avg_response_time = mean(all_times) if all_times else 0
    
    # 计算应用资源峰值
    max_app_cpu = max(r["app_metrics"]["end"]["app_cpu_percent"] for r in results)
    max_app_memory = max(r["app_metrics"]["end"]["app_memory_mb"] for r in results)
    max_system_cpu = max(r["app_metrics"]["end"]["system_cpu_percent"] for r in results)
    max_system_memory = max(r["app_metrics"]["end"]["system_memory_percent"] for r in results)
    
    report = {
        "test_info": {
            "timestamp": datetime.now().isoformat(),
            "test_type": "高强度并发测试",
            "total_requests": total_requests,
            "overall_success_rate": round(overall_success_rate, 2),
            "avg_response_time": round(avg_response_time, 2)
        },
        "app_metrics": {
            "max_app_cpu_usage": round(max_app_cpu, 1),
            "max_app_memory_usage_mb": round(max_app_memory, 0),
            "max_system_cpu_usage": round(max_system_cpu, 1),
            "max_system_memory_usage": round(max_system_memory, 1),
            "system_info": {
                "total_memory_gb": round(results[0]["app_metrics"]["start"]["system_memory_total_gb"], 1),
                "cpu_count": psutil.cpu_count()
            }
        },
        "concurrency_results": []
    }
    
    # 添加每个并发级别的结果
    for r in results:
        success_count = len([req for req in r["results"] if req["success"]])
        times = [req["time"] for req in r["results"] if req["success"]]
        
        concurrency_result = {
            "concurrency": r["concurrency"],
            "total_requests": len(r["results"]),
            "success_count": success_count,
            "success_rate": round(r["success_rate"], 2),
            "avg_response_time": round(mean(times), 2) if times else 0,
            "min_response_time": round(min(times), 2) if times else 0,
            "max_response_time": round(max(times), 2) if times else 0,
            "app_impact": {
                "app_cpu_change": round(r["app_metrics"]["app_cpu_change"], 1),
                "app_memory_change_mb": round(r["app_metrics"]["app_memory_change"], 0),
                "app_memory_percent_change": round(r["app_metrics"]["app_memory_percent_change"], 1),
                "final_app_cpu": round(r["app_metrics"]["end"]["app_cpu_percent"], 1),
                "final_app_memory_mb": round(r["app_metrics"]["end"]["app_memory_mb"], 0),
                "final_system_cpu": round(r["app_metrics"]["end"]["system_cpu_percent"], 1),
                "final_system_memory": round(r["app_metrics"]["end"]["system_memory_percent"], 1)
            }
        }
        report["concurrency_results"].append(concurrency_result)
    
    # 生成建议
    excellent_results = [r for r in results if r["success_rate"] >= 95]
    if excellent_results:
        max_concurrency = max(r["concurrency"] for r in excellent_results)
        report["recommendations"] = {
            "recommended_concurrency": max_concurrency,
            "status": "excellent",
            "message": f"系统可以稳定支持 {max_concurrency} 并发"
        }
    else:
        good_results = [r for r in results if r["success_rate"] >= 80]
        if good_results:
            max_concurrency = max(r["concurrency"] for r in good_results)
            report["recommendations"] = {
                "recommended_concurrency": max_concurrency - 5,
                "status": "good",
                "message": f"系统可以支持 {max_concurrency} 并发，建议生产环境使用 {max_concurrency - 5}"
            }
        else:
            report["recommendations"] = {
                "recommended_concurrency": 0,
                "status": "poor",
                "message": "系统并发性能需要优化"
            }
    
    # 保存报告
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    return report_file, report


async def main():
    print("=" * 60)
    print("阶段2：高强度并发测试")
    print("=" * 60)
    print("目标: 从20并发基础上继续测试更高并发")
    print("优化: 全部使用新建会话的方式进行测试")
    print("策略: 经济高效的动态测试策略")
    print("⚠️  注意: 系统有速率限制(30请求/分钟/IP)，避免过高并发")
    print()
    
    # 预估测试时间 - 降低并发避免瓶颈
    test_levels = [100]  # 降低并发级别，避免速率限制
    total_requests = sum(level * 1.2 for level in test_levels)
    
    # 基于历史数据：平均响应时间约18秒，考虑并发因素
    avg_response_time = 15  # 秒
    # 并发测试中，实际耗时 = 响应时间 + 批次间隔 + 等待时间
    estimated_time_per_request = avg_response_time + 5  # 加上批次间隔
    estimated_time = ((total_requests / max(test_levels)) * estimated_time_per_request / 60) + len(test_levels) * 0.5  # 分钟
    
    print(f"📊 预估测试规模: 总请求约{int(total_requests)}个，预计耗时约{estimated_time:.0f}分钟")
    print(f"📊 测试配置: 并发级别{test_levels}，每级别{int(total_requests/len(test_levels))}个请求")
    print()
    
    # 健康检查
    print("检查服务健康状态...")
    if not await health_check():
        print("❌ 服务不可用")
        sys.exit(1)
    print("✅ 服务健康")
    
    # 测试进程检测
    process_detected = test_process_detection()
    if not process_detected:
        print("⚠️  未检测到目标进程，显示所有Python进程用于调试:")
        list_all_python_processes()
        print("⚠️  将使用备用检测方法继续测试")
    print()
    
    # 从之前测试过的梯度基础上继续 - 简化测试
    # 根据之前测试结果，从20并发开始继续测试更高并发
    results = []
    
    for i, concurrency in enumerate(test_levels):
        # 经济高效的测试策略 - 根据并发数动态调整请求数量
        if concurrency <= 30:
            total_requests = concurrency * 3  # 低并发：3倍请求数，充分测试
        elif concurrency <= 60:
            total_requests = concurrency * 2  # 中并发：2倍请求数，平衡测试
        else:
            total_requests = concurrency * 1.5  # 高并发：1.5倍请求数，重点测试并发能力
        
        print(f"\n📊 测试级别 {i+1}/{len(test_levels)}: 并发={concurrency}, 总请求={int(total_requests)}")
        print(f"   测试策略: {'充分测试' if total_requests >= concurrency * 3 else '平衡测试' if total_requests >= concurrency * 2 else '重点测试'}")
        print(f"   预估耗时: 约{int(total_requests * 23 / 60)}分钟")
        
        result = await concurrent_test(
            concurrency=concurrency,
            total_requests=int(total_requests)
        )
        results.append(result)
        
        # 动态停止策略
        if result["success_rate"] < 80:
            print(f"\n⚠️  并发数 {concurrency} 时成功率过低，停止测试")
            print(f"💡 可能原因: 速率限制(30请求/分钟)或LLM API限制")
            break
        elif result["success_rate"] < 90 and concurrency > 20:
            print(f"\n⚠️  并发数 {concurrency} 时成功率下降，谨慎继续")
            print(f"💡 建议: 检查速率限制和资源使用情况")
        
        # 智能等待策略 - 根据并发数和成功率调整等待时间
        if concurrency != test_levels[-1]:
            if result["success_rate"] >= 95:
                wait_time = 10  # 成功率很高时，短暂等待
            elif result["success_rate"] >= 80:
                wait_time = 20  # 成功率一般时，中等等待
            else:
                wait_time = 30  # 成功率较低时，较长等待
            
            print(f"\n等待{wait_time}秒让系统恢复...")
            await asyncio.sleep(wait_time)
    
    # 总结
    print(f"\n{'='*60}")
    print("测试总结")
    print(f"{'='*60}")
    
    for r in results:
        emoji = "✅" if r["success_rate"] >= 95 else "⚠️" if r["success_rate"] >= 80 else "❌"
        print(f"{emoji} 并发 {r['concurrency']:2d}: 成功率 {r['success_rate']:5.1f}%")
    
    # 建议
    print(f"\n{'='*60}")
    print("建议")
    print(f"{'='*60}")
    
    excellent_results = [r for r in results if r["success_rate"] >= 95]
    good_results = [r for r in results if r["success_rate"] >= 80]
    
    if excellent_results:
        max_excellent_concurrency = max(r["concurrency"] for r in excellent_results)
        print(f"✅ 系统可以稳定支持 {max_excellent_concurrency} 并发 (成功率≥95%)")
        
        if good_results:
            max_good_concurrency = max(r["concurrency"] for r in good_results)
            if max_good_concurrency > max_excellent_concurrency:
                print(f"⚠️  系统可以支持 {max_good_concurrency} 并发 (成功率≥80%)")
        
        print(f"   建议生产环境并发数: {max_excellent_concurrency}")
        print(f"   系统已通过高强度并发测试")
    elif good_results:
        max_good_concurrency = max(r["concurrency"] for r in good_results)
        print(f"⚠️  系统可以支持 {max_good_concurrency} 并发 (成功率≥80%)")
        print(f"   建议生产环境并发数: {max_good_concurrency - 5}")
        print("   建议优化系统性能后再进行大规模测试")
    else:
        print("❌ 系统在并发测试下表现不佳，建议:")
        print("   1. 检查LLM并发限制器是否生效")
        print("   2. 增加超时时间")
        print("   3. 减少worker数量")
        print("   4. 检查系统资源使用情况")
    
    # 生成测试报告
    print(f"\n{'='*60}")
    print("生成测试报告")
    print(f"{'='*60}")
    
    report_file, report = generate_test_report(results)
    
    print(f"📊 测试报告已生成: {report_file}")
    print(f"📈 总体成功率: {report['test_info']['overall_success_rate']}%")
    print(f"⏱️  平均响应时间: {report['test_info']['avg_response_time']}s")
    print(f"💻 应用最大CPU: {report['app_metrics']['max_app_cpu_usage']}% (系统: {report['app_metrics']['max_system_cpu_usage']}%)")
    print(f"🧠 应用最大内存: {report['app_metrics']['max_app_memory_usage_mb']}MB (系统: {report['app_metrics']['max_system_memory_usage']}%)")
    print(f"💡 建议: {report['recommendations']['message']}")
    
    # 生成简化的文本报告
    txt_report_file = report_file.replace('.json', '.txt')
    with open(txt_report_file, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("高强度并发测试报告\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"测试时间: {report['test_info']['timestamp']}\n")
        f.write(f"总请求数: {report['test_info']['total_requests']}\n")
        f.write(f"总体成功率: {report['test_info']['overall_success_rate']}%\n")
        f.write(f"平均响应时间: {report['test_info']['avg_response_time']}s\n\n")
        
        f.write("应用资源使用情况:\n")
        f.write(f"  应用最大CPU: {report['app_metrics']['max_app_cpu_usage']}%\n")
        f.write(f"  应用最大内存: {report['app_metrics']['max_app_memory_usage_mb']}MB\n")
        f.write(f"  系统最大CPU: {report['app_metrics']['max_system_cpu_usage']}%\n")
        f.write(f"  系统最大内存: {report['app_metrics']['max_system_memory_usage']}%\n")
        f.write(f"  系统总内存: {report['app_metrics']['system_info']['total_memory_gb']}GB\n")
        f.write(f"  CPU核心数: {report['app_metrics']['system_info']['cpu_count']}\n\n")
        
        f.write("各并发级别测试结果:\n")
        for result in report['concurrency_results']:
            f.write(f"  并发{result['concurrency']}: 成功率{result['success_rate']}%, "
                   f"平均响应{result['avg_response_time']}s, "
                   f"应用CPU变化{result['app_impact']['app_cpu_change']:+.1f}%, "
                   f"应用内存变化{result['app_impact']['app_memory_change_mb']:+.0f}MB\n")
        
        f.write(f"\n建议: {report['recommendations']['message']}\n")
    
    print(f"📄 简化报告已生成: {txt_report_file}")


if __name__ == "__main__":
    asyncio.run(main())

