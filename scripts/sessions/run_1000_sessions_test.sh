#!/bin/bash

# 1000会话压力测试执行脚本
# 提供便捷的命令行接口来运行各种测试场景

set -e

# 默认配置
DEFAULT_SERVER="http://localhost:8800"
DEFAULT_OUTPUT_DIR="./test_results"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# 显示帮助信息
show_help() {
    echo "🚀 Parlant 1000会话压力测试工具"
    echo "=================================="
    echo ""
    echo "用法: $0 [选项] [场景名称]"
    echo ""
    echo "选项:"
    echo "  -s, --server URL        服务器地址 (默认: $DEFAULT_SERVER)"
    echo "  -o, --output DIR        结果输出目录 (默认: $DEFAULT_OUTPUT_DIR)"
    echo "  -l, --list              列出所有可用场景"
    echo "  -h, --help              显示此帮助信息"
    echo ""
    echo "场景名称:"
    echo "  light_load              轻负载测试 (100会话, 10分钟)"
    echo "  medium_load             中等负载测试 (500会话, 30分钟)"
    echo "  heavy_load              重负载测试 (1000会话, 60分钟)"
    echo "  stress_test             压力测试 (1000会话, 120分钟)"
    echo "  endurance_test          耐久性测试 (1000会话, 240分钟)"
    echo "  burst_test              突发测试 (1000会话, 5分钟)"
    echo ""
    echo "示例:"
    echo "  $0 --list                                    # 列出所有场景"
    echo "  $0 light_load                               # 运行轻负载测试"
    echo "  $0 -s http://prod-server:8800 heavy_load   # 在生产服务器运行重负载测试"
    echo "  $0 -o /tmp/results stress_test              # 运行压力测试并保存到指定目录"
}

# 检查依赖
check_dependencies() {
    print_info "检查依赖..."
    
    if ! command -v python3 &> /dev/null; then
        print_error "Python3 未安装"
        exit 1
    fi
    
    if ! python3 -c "import aiohttp" &> /dev/null; then
        print_error "aiohttp 未安装，请运行: pip install aiohttp"
        exit 1
    fi
    
    print_success "依赖检查通过"
}

# 检查服务器连接
check_server() {
    local server_url="$1"
    print_info "检查服务器连接: $server_url"
    
    if curl -s --connect-timeout 5 "$server_url/health" > /dev/null 2>&1; then
        print_success "服务器连接正常"
    else
        print_warning "无法连接到服务器，但测试将继续进行"
    fi
}

# 运行测试场景
run_scenario() {
    local scenario="$1"
    local server="$2"
    local output_dir="$3"
    
    print_info "开始运行场景: $scenario"
    print_info "服务器: $server"
    print_info "输出目录: $output_dir"
    
    # 创建输出目录
    mkdir -p "$output_dir"
    
    # 运行测试
    cd "$SCRIPT_DIR"
    python3 test_scenarios_1000_sessions.py \
        --server "$server" \
        --scenario "$scenario" \
        --output-dir "$output_dir"
    
    if [ $? -eq 0 ]; then
        print_success "场景 $scenario 运行完成"
    else
        print_error "场景 $scenario 运行失败"
        exit 1
    fi
}

# 列出所有场景
list_scenarios() {
    print_info "获取可用场景列表..."
    cd "$SCRIPT_DIR"
    python3 test_scenarios_1000_sessions.py --list
}

# 主函数
main() {
    local server="$DEFAULT_SERVER"
    local output_dir="$DEFAULT_OUTPUT_DIR"
    local scenario=""
    local list_only=false
    
    # 解析命令行参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            -s|--server)
                server="$2"
                shift 2
                ;;
            -o|--output)
                output_dir="$2"
                shift 2
                ;;
            -l|--list)
                list_only=true
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            -*)
                print_error "未知选项: $1"
                show_help
                exit 1
                ;;
            *)
                if [ -z "$scenario" ]; then
                    scenario="$1"
                else
                    print_error "只能指定一个场景"
                    exit 1
                fi
                shift
                ;;
        esac
    done
    
    # 显示标题
    echo "🚀 Parlant 1000会话压力测试工具"
    echo "=================================="
    echo ""
    
    # 检查依赖
    check_dependencies
    
    # 如果只是列出场景
    if [ "$list_only" = true ]; then
        list_scenarios
        exit 0
    fi
    
    # 检查是否指定了场景
    if [ -z "$scenario" ]; then
        print_error "请指定要运行的场景名称"
        echo ""
        show_help
        exit 1
    fi
    
    # 检查服务器连接
    check_server "$server"
    
    # 运行测试
    run_scenario "$scenario" "$server" "$output_dir"
    
    print_success "测试完成！结果保存在: $output_dir"
}

# 捕获中断信号
trap 'print_warning "测试被用户中断"; exit 130' INT

# 运行主函数
main "$@"
