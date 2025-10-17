#!/bin/bash

# 1000会话压力测试使用示例
# 演示如何运行不同类型的压力测试

set -e

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

echo "🚀 Parlant 1000会话压力测试示例"
echo "=================================="
echo ""

# 检查服务器是否运行
print_info "检查Parlant服务器状态..."
if curl -s --connect-timeout 5 "http://localhost:8800/health" > /dev/null 2>&1; then
    print_success "服务器运行正常"
else
    print_warning "服务器可能未运行，请确保Parlant服务已启动"
    echo "启动命令示例:"
    echo "  python -m parlant.sdk"
    echo ""
fi

echo "📋 可用的测试场景:"
echo ""

# 显示所有场景
./run_1000_sessions_test.sh --list

echo ""
echo "🎯 推荐测试流程:"
echo ""

echo "1️⃣ 轻负载测试 (适合开发环境)"
echo "   命令: ./run_1000_sessions_test.sh light_load"
echo "   时长: 10分钟, 会话数: 100"
echo ""

echo "2️⃣ 中等负载测试 (适合测试环境)"
echo "   命令: ./run_1000_sessions_test.sh medium_load"
echo "   时长: 30分钟, 会话数: 500"
echo ""

echo "3️⃣ 重负载测试 (适合生产环境)"
echo "   命令: ./run_1000_sessions_test.sh heavy_load"
echo "   时长: 60分钟, 会话数: 1000"
echo ""

echo "4️⃣ 压力测试 (测试系统极限)"
echo "   命令: ./run_1000_sessions_test.sh stress_test"
echo "   时长: 120分钟, 会话数: 1000"
echo ""

echo "5️⃣ 耐久性测试 (检查内存泄漏)"
echo "   命令: ./run_1000_sessions_test.sh endurance_test"
echo "   时长: 240分钟, 会话数: 1000"
echo ""

echo "6️⃣ 突发测试 (测试恢复能力)"
echo "   命令: ./run_1000_sessions_test.sh burst_test"
echo "   时长: 5分钟, 会话数: 1000"
echo ""

echo "🔧 自定义测试参数:"
echo ""
echo "指定服务器地址:"
echo "  ./run_1000_sessions_test.sh -s http://prod-server:8800 heavy_load"
echo ""
echo "指定输出目录:"
echo "  ./run_1000_sessions_test.sh -o /tmp/test_results stress_test"
echo ""
echo "直接使用Python脚本:"
echo "  python.py --sessions 1000 --duration 3600 --ip-count 20"
echo ""

echo "📊 测试结果分析:"
echo ""
echo "测试完成后，查看以下指标:"
echo "  ✅ 成功率: 应 ≥ 90% (重负载) 或 ≥ 95% (中等负载)"
echo "  🚫 限流率: 应 ≤ 20% (压力测试) 或 ≤ 10% (重负载)"
echo "  ⏱️  响应时间: P95 < 5s, P99 < 10s"
echo "  👥 会话创建速率: 应 ≥ 0.5 sessions/s"
echo ""

echo "💡 优化建议:"
echo ""
echo "如果限流率过高:"
echo "  - 增加IP地址池数量 (--ip-count 参数)"
echo "  - 降低请求频率"
echo "  - 检查服务器限流配置"
echo ""
echo "如果成功率过低:"
echo "  - 检查服务器资源使用情况"
echo "  - 优化数据库连接池"
echo "  - 检查内存使用情况"
echo ""
echo "如果响应时间过长:"
echo "  - 检查网络延迟"
echo "  - 优化模型推理性能"
echo "  - 检查数据库查询性能"
echo ""

echo "🚀 开始测试:"
echo ""
echo "选择一个场景开始测试，例如:"
echo "  ./run_1000_sessions_test.sh light_load"
echo ""
echo "或者查看帮助信息:"
echo "  ./run_1000_sessions_test.sh --help"
