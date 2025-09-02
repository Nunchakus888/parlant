#!/usr/bin/env python3
"""
测试新的chat端点并查看详细日志
"""

import requests
import json
import time


def test_chat_endpoint():
    """测试新的chat端点"""
    
    print("🚀 测试新的chat端点...")
    
    # 测试数据
    chat_data = {
        "message": "你好，请介绍一下你自己",
        "customer_id": "test_customer_003",
        "timeout": 60  # 增加超时时间以便观察日志
    }
    
    print(f"📤 发送请求数据: {json.dumps(chat_data, indent=2, ensure_ascii=False)}")
    
    try:
        # 发送聊天请求
        start_time = time.time()
        response = requests.post(
            "http://localhost:8800/sessions/chat",
            json=chat_data,
            timeout=65  # 比API超时稍长
        )
        end_time = time.time()
        
        print(f"⏱️ 请求耗时: {end_time - start_time:.2f} 秒")
        print(f"📊 响应状态码: {response.status_code}")
        print(f"📄 响应头: {dict(response.headers)}")
        
        if response.status_code == 200:
            result = response.json()
            print("✅ 成功!")
            print(f"📋 响应数据: {json.dumps(result, indent=2, ensure_ascii=False)}")
            
            # 提取AI回复
            if 'data' in result and 'message' in result['data']:
                ai_message = result['data']['message']
                print(f"🤖 AI回复: {ai_message}")
            else:
                print("⚠️ 响应中没有找到AI消息")
                
        elif response.status_code == 504:
            print("⏰ 504 Gateway Timeout - 查看服务器日志了解详情")
            print("💡 可能的原因:")
            print("   1. AI引擎未启动或配置错误")
            print("   2. 代理没有配置指南(guidelines)")
            print("   3. NLP服务不可用")
            print("   4. 后台任务服务未启动")
            
        elif response.status_code == 422:
            print("❌ 422 Unprocessable Entity")
            print(f"📄 错误详情: {response.text}")
            
        else:
            print(f"❌ 其他错误: {response.status_code}")
            print(f"📄 错误详情: {response.text}")
            
    except requests.exceptions.Timeout:
        print("⏰ 请求超时")
    except requests.exceptions.ConnectionError:
        print("❌ 连接错误 - 请确保服务器正在运行")
    except Exception as e:
        print(f"❌ 请求异常: {e}")


def test_simple_message():
    """测试最简单的消息"""
    
    print("\n" + "="*50)
    print("🧪 测试最简单的消息...")
    
    chat_data = {
        "message": "Hi"
    }
    
    print(f"📤 发送简单消息: {chat_data}")
    
    try:
        response = requests.post(
            "http://localhost:8800/sessions/chat",
            json=chat_data,
            timeout=35
        )
        
        print(f"📊 响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("✅ 简单消息成功!")
            print(f"🤖 AI回复: {result['data']['message']}")
        else:
            print(f"❌ 简单消息失败: {response.text}")
            
    except Exception as e:
        print(f"❌ 简单消息测试异常: {e}")


if __name__ == "__main__":
    print("🔍 Chat端点调试测试")
    print("="*50)
    
    # 测试1: 完整参数
    test_chat_endpoint()
    
    # 测试2: 简单消息
    test_simple_message()
    
    print("\n" + "="*50)
    print("📋 测试完成!")
    print("💡 如果遇到504错误，请查看服务器日志了解详细执行流程")
    print("🔍 日志会显示每个步骤的执行状态，帮助定位问题")
