#!/usr/bin/env python3
"""
1000会话压力测试场景配置

提供多种预设测试场景，方便快速执行不同类型的压力测试
"""

import asyncio
import json
from typing import Dict, List
from dataclasses import dataclass


@dataclass
class TestScenario:
    """测试场景配置"""
    name: str
    description: str
    sessions: int
    duration: int
    ip_count: int
    expected_rate_limit_percentage: float
    expected_success_rate: float


# 预设测试场景
TEST_SCENARIOS = {
    "startup_load": TestScenario(
        name="启动负载测试",
        description="启动时负载测试，测试系统启动性能",
        sessions=20,
        duration=60,
        ip_count=5,
        expected_rate_limit_percentage=0.0,
        expected_success_rate=99.0
    ),
    "light_load": TestScenario(
        name="轻负载测试",
        description="适合开发环境，低并发测试",
        sessions=100,
        duration=600,  # 10分钟
        ip_count=5,
        expected_rate_limit_percentage=0.0,
        expected_success_rate=99.0
    ),
    
    "medium_load": TestScenario(
        name="中等负载测试",
        description="适合测试环境，中等并发测试",
        sessions=500,
        duration=1800,  # 30分钟
        ip_count=10,
        expected_rate_limit_percentage=5.0,
        expected_success_rate=95.0
    ),
    
    "heavy_load": TestScenario(
        name="重负载测试",
        description="适合生产环境，高并发测试",
        sessions=1000,
        duration=3600,  # 60分钟
        ip_count=20,
        expected_rate_limit_percentage=10.0,
        expected_success_rate=90.0
    ),
    
    "stress_test": TestScenario(
        name="压力测试",
        description="极限压力测试，测试系统上限",
        sessions=1000,
        duration=7200,  # 120分钟
        ip_count=50,
        expected_rate_limit_percentage=20.0,
        expected_success_rate=85.0
    ),
    
    "endurance_test": TestScenario(
        name="耐久性测试",
        description="长时间运行测试，检查内存泄漏",
        sessions=1000,
        duration=14400,  # 240分钟 (4小时)
        ip_count=15,
        expected_rate_limit_percentage=5.0,
        expected_success_rate=95.0
    ),
    
    "burst_test": TestScenario(
        name="突发测试",
        description="短时间高并发，测试系统恢复能力",
        sessions=1000,
        duration=300,  # 5分钟
        ip_count=100,
        expected_rate_limit_percentage=30.0,
        expected_success_rate=80.0
    )
}


class ScenarioRunner:
    """场景运行器"""
    
    def __init__(self, base_url: str = "http://localhost:8800"):
        self.base_url = base_url
    
    async def run_scenario(self, scenario_name: str, output_dir: str = "./test_results"):
        """运行指定场景"""
        if scenario_name not in TEST_SCENARIOS:
            print(f"❌ 未知场景: {scenario_name}")
            print(f"可用场景: {', '.join(TEST_SCENARIOS.keys())}")
            return
        
        scenario = TEST_SCENARIOS[scenario_name]
        
        print(f"🚀 开始运行场景: {scenario.name}")
        print(f"📝 描述: {scenario.description}")
        print(f"👥 会话数: {scenario.sessions}")
        print(f"⏱️  时长: {scenario.duration} 秒")
        print(f"🌐 IP数量: {scenario.ip_count}")
        print(f"🎯 预期限流率: {scenario.expected_rate_limit_percentage}%")
        print(f"🎯 预期成功率: {scenario.expected_success_rate}%")
        
        # 导入测试器
        from load_test_1000_sessions import Parlant1000SessionTester
        
        tester = Parlant1000SessionTester(
            base_url=self.base_url,
            max_sessions=scenario.sessions,
            ip_count=scenario.ip_count
        )
        
        # 运行测试
        results = await tester.run_1000_session_test(scenario.duration)
        
        if not results:
            print("❌ 测试失败，没有收集到结果")
            return
        
        # 生成报告
        report = tester.calculate_statistics(results)
        
        # 保存结果
        import os
        os.makedirs(output_dir, exist_ok=True)
        timestamp = report.test_start_time.replace(":", "-").replace("T", "_").split(".")[0]
        filename = f"{output_dir}/{scenario_name}_{timestamp}.json"
        
        tester.save_results(results, report, filename)
        
        # 分析结果
        self.analyze_scenario_results(scenario, report)
    
    def analyze_scenario_results(self, scenario: TestScenario, report):
        """分析场景结果"""
        print(f"\n📊 场景结果分析: {scenario.name}")
        print("="*60)
        
        # 成功率分析
        if report.success_rate >= scenario.expected_success_rate:
            print(f"✅ 成功率达标: {report.success_rate:.1f}% >= {scenario.expected_success_rate}%")
        else:
            print(f"❌ 成功率不达标: {report.success_rate:.1f}% < {scenario.expected_success_rate}%")
        
        # 限流率分析
        if report.rate_limit_percentage <= scenario.expected_rate_limit_percentage:
            print(f"✅ 限流率正常: {report.rate_limit_percentage:.1f}% <= {scenario.expected_rate_limit_percentage}%")
        else:
            print(f"⚠️  限流率偏高: {report.rate_limit_percentage:.1f}% > {scenario.expected_rate_limit_percentage}%")
        
        # 会话创建分析
        session_creation_rate = report.sessions_per_second
        if session_creation_rate > 0.5:
            print(f"✅ 会话创建速率良好: {session_creation_rate:.2f} sessions/s")
        else:
            print(f"⚠️  会话创建速率较慢: {session_creation_rate:.2f} sessions/s")
        
        # 响应时间分析
        if report.avg_response_time_ms < 2000:
            print(f"✅ 平均响应时间良好: {report.avg_response_time_ms:.0f}ms")
        elif report.avg_response_time_ms < 5000:
            print(f"⚠️  平均响应时间一般: {report.avg_response_time_ms:.0f}ms")
        else:
            print(f"❌ 平均响应时间较慢: {report.avg_response_time_ms:.0f}ms")
        
        # 建议
        print(f"\n💡 优化建议:")
        if report.rate_limit_percentage > 15:
            print("   - 增加IP地址池数量")
            print("   - 降低请求频率")
        if report.success_rate < 90:
            print("   - 检查服务器资源使用情况")
            print("   - 优化数据库连接池")
        if report.avg_response_time_ms > 3000:
            print("   - 检查网络延迟")
            print("   - 优化模型推理性能")
    
    def list_scenarios(self):
        """列出所有可用场景"""
        print("📋 可用测试场景:")
        print("="*60)
        for name, scenario in TEST_SCENARIOS.items():
            print(f"🔹 {name}")
            print(f"   名称: {scenario.name}")
            print(f"   描述: {scenario.description}")
            print(f"   会话数: {scenario.sessions}")
            print(f"   时长: {scenario.duration}秒")
            print(f"   IP数: {scenario.ip_count}")
            print()


async def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="1000会话压力测试场景运行器")
    parser.add_argument("--server", default="http://localhost:8800", 
                       help="Parlant 服务器地址")
    parser.add_argument("--scenario", help="要运行的场景名称")
    parser.add_argument("--list", action="store_true", help="列出所有可用场景")
    parser.add_argument("--output-dir", default="./test_results", 
                       help="结果输出目录")
    
    args = parser.parse_args()
    
    runner = ScenarioRunner(base_url=args.server)
    
    if args.list:
        runner.list_scenarios()
        return
    
    if not args.scenario:
        print("❌ 请指定要运行的场景名称")
        print("使用 --list 查看所有可用场景")
        return
    
    try:
        await runner.run_scenario(args.scenario, args.output_dir)
    except KeyboardInterrupt:
        print("\n⏹️  测试被用户中断")
    except Exception as e:
        print(f"\n❌ 运行场景时发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
