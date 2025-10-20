#!/usr/bin/env python3
"""
阶段1：基准测试 - 测量单请求的基准响应时间
"""
import asyncio
import httpx
import time
from statistics import mean, stdev
import sys

BASE_URL = "http://localhost:8800"

async def single_request_test(num_requests: int = 10):
    """单请求基准测试"""
    url = f"{BASE_URL}/sessions/chat"
    
    print("=" * 60)
    print("阶段1：基准测试（单并发）")
    print("=" * 60)
    print(f"将执行 {num_requests} 次单独请求，测量基准性能\n")
    
    times = []
    successes = 0
    
    for i in range(num_requests):
        payload = {
            "message": f"基准测试消息 {i+1}",
            "customer_id": f"baseline_user_{i}",
            "session_id": f"baseline_session_{i}",
            "tenant_id": "baseline_tenant",
            "chatbot_id": "baseline_bot",
            "timeout": 120
        }
        
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=130.0) as client:
                response = await client.post(url, json=payload)
            elapsed = time.time() - start
            
            if response.status_code == 200:
                successes += 1
                status_emoji = "✅"
            elif response.status_code == 504:
                status_emoji = "⏰"
            else:
                status_emoji = "❌"
            
            times.append(elapsed)
            print(f"{status_emoji} 请求 {i+1:2d}/{num_requests}: "
                  f"{elapsed:6.2f}s - Status: {response.status_code}")
            
        except Exception as e:
            elapsed = time.time() - start
            times.append(elapsed)
            print(f"❌ 请求 {i+1:2d}/{num_requests}: "
                  f"{elapsed:6.2f}s - Error: {str(e)[:50]}")
        
        # 请求间暂停，避免缓存影响
        if i < num_requests - 1:
            await asyncio.sleep(2)
    
    # 统计结果
    print("\n" + "=" * 60)
    print("基准测试结果")
    print("=" * 60)
    
    success_rate = successes / num_requests * 100
    print(f"成功率:       {success_rate:.1f}% ({successes}/{num_requests})")
    
    if times:
        avg_time = mean(times)
        print(f"平均响应时间: {avg_time:.2f}s")
        
        if len(times) > 1:
            print(f"标准差:       {stdev(times):.2f}s")
        
        print(f"最小响应时间: {min(times):.2f}s")
        print(f"最大响应时间: {max(times):.2f}s")
        
        # 计算推荐并发数
        print("\n" + "-" * 60)
        print("并发能力估算")
        print("-" * 60)
        
        # 基于60秒周期估算
        if avg_time > 0:
            max_throughput_60s = 60 / avg_time
            print(f"理论最大吞吐量:   {max_throughput_60s:.1f} req/min (单worker)")
            print(f"推荐并发数 (保守): {int(max_throughput_60s * 0.5)}")
            print(f"推荐并发数 (激进): {int(max_throughput_60s * 0.8)}")
            
            # 考虑多worker
            workers = 2
            print(f"\n使用 {workers} workers:")
            print(f"  理论吞吐量: {max_throughput_60s * workers:.1f} req/min")
            print(f"  推荐并发数: {int(max_throughput_60s * workers * 0.6)}")
    
    return {
        "success_rate": success_rate,
        "avg_time": mean(times) if times else 0,
        "times": times
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


async def main():
    print("检查服务健康状态...")
    if not await health_check():
        print("❌ 服务不可用，请确保服务正在运行在", BASE_URL)
        sys.exit(1)
    print("✅ 服务健康\n")
    
    # 执行基准测试
    result = await single_request_test(num_requests=10)
    
    # 建议下一步
    print("\n" + "=" * 60)
    print("下一步建议")
    print("=" * 60)
    
    if result["success_rate"] >= 80:
        print("✅ 基准测试通过，可以进入下一阶段")
        print("   运行: python scripts/benchmark/stage2_small_concurrent.py")
    else:
        print("⚠️  成功率较低，请检查:")
        print("   1. 服务配置是否正确")
        print("   2. LLM API是否可用")
        print("   3. 超时时间是否足够")


if __name__ == "__main__":
    asyncio.run(main())
