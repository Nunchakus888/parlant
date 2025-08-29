#!/bin/bash
# Parlant API 直接调用示例 - cURL
# 不依赖任何SDK，直接使用HTTP请求

BASE_URL="http://localhost:8000"

echo "🚀 开始Parlant API直接调用测试..."

# 1. 创建AI代理
echo "📝 创建AI代理..."
AGENT_RESPONSE=$(curl -s -X POST "$BASE_URL/agents" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "测试助手",
    "description": "一个简单的测试AI助手",
    "composition_mode": "fluid"
  }')

AGENT_ID=$(echo $AGENT_RESPONSE | jq -r '.id')
echo "✅ 创建代理成功: $AGENT_ID"

# 2. 创建会话
echo "💬 创建会话..."
SESSION_RESPONSE=$(curl -s -X POST "$BASE_URL/sessions" \
  -H "Content-Type: application/json" \
  -d "{
    \"agent_id\": \"$AGENT_ID\",
    \"title\": \"cURL API测试\"
  }")

SESSION_ID=$(echo $SESSION_RESPONSE | jq -r '.id')
echo "✅ 创建会话成功: $SESSION_ID"

# 3. 发送第一条消息
echo "👤 发送消息: 你好，请介绍一下你自己"
MESSAGE_RESPONSE=$(curl -s -X POST "$BASE_URL/sessions/$SESSION_ID/events" \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "message",
    "source": "customer",
    "message": "你好，请介绍一下你自己"
  }')

MESSAGE_OFFSET=$(echo $MESSAGE_RESPONSE | jq -r '.offset')
echo "✅ 消息发送成功，offset: $MESSAGE_OFFSET"

# 4. 等待AI回复
echo "⏳ 等待AI回复..."
AI_RESPONSE=$(curl -s -X GET "$BASE_URL/sessions/$SESSION_ID/events?min_offset=$MESSAGE_OFFSET&source=ai_agent&kinds=message&wait_for_data=30")

if [ $? -eq 0 ]; then
    AI_MESSAGE=$(echo $AI_RESPONSE | jq -r '.[0].data.message // "无回复"')
    echo "🤖 AI回复: $AI_MESSAGE"
else
    echo "❌ 获取AI回复失败"
fi

# 5. 发送第二条消息
echo "👤 发送消息: 你能帮我做什么？"
MESSAGE_RESPONSE2=$(curl -s -X POST "$BASE_URL/sessions/$SESSION_ID/events" \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "message",
    "source": "customer",
    "message": "你能帮我做什么？"
  }')

MESSAGE_OFFSET2=$(echo $MESSAGE_RESPONSE2 | jq -r '.offset')
echo "✅ 消息发送成功，offset: $MESSAGE_OFFSET2"

# 6. 等待AI回复
echo "⏳ 等待AI回复..."
AI_RESPONSE2=$(curl -s -X GET "$BASE_URL/sessions/$SESSION_ID/events?min_offset=$MESSAGE_OFFSET2&source=ai_agent&kinds=message&wait_for_data=30")

if [ $? -eq 0 ]; then
    AI_MESSAGE2=$(echo $AI_RESPONSE2 | jq -r '.[0].data.message // "无回复"')
    echo "🤖 AI回复: $AI_MESSAGE2"
else
    echo "❌ 获取AI回复失败"
fi

echo "✅ 测试完成！"
