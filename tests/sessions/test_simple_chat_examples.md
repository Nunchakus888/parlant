# Parlant 简化聊天接口使用示例

新的 `/sessions/chat` 接口让客户端调用变得极其简单。只需要发送消息，系统会自动处理会话管理。

## API 端点

```
POST /sessions/chat
```

## 请求格式

```json
{
  "message": "用户消息",          // 必需
  "agent_id": "ag_xxx",          // 可选，不传则使用默认代理
  "customer_id": "cust_xxx",     // 可选，不传则使用访客
  "session_title": "聊天标题",    // 可选，新会话的标题
  "timeout": 30                  // 可选，等待超时（秒）
}
```

## 使用示例

### 1. JavaScript (最简单)

```javascript
// 最简单的使用方式
async function chat(message) {
  const response = await fetch('http://localhost:8000/sessions/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  });
  
  const result = await response.json();
  return result.data.message;
}

// 使用
const reply = await chat("你好");
console.log(reply);
```

### 2. Python (使用 requests)

```python
import requests

def chat(message):
    response = requests.post(
        'http://localhost:8000/sessions/chat',
        json={'message': message}
    )
    if response.status_code == 200:
        return response.json()['data']['message']
    return "错误: " + response.text

# 使用
reply = chat("你好")
print(reply)
```

### 3. cURL (命令行)

```bash
# 最简单的调用
curl -X POST http://localhost:8000/sessions/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'

# 获取AI回复的消息内容
curl -s -X POST http://localhost:8000/sessions/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}' | jq -r '.data.message'
```

### 4. Java (使用 HttpClient)

```java
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.URI;

public class SimpleChat {
    private static final HttpClient client = HttpClient.newHttpClient();
    
    public static String chat(String message) throws Exception {
        String json = String.format("{\"message\": \"%s\"}", message);
        
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("http://localhost:8000/sessions/chat"))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();
        
        HttpResponse<String> response = client.send(request, 
            HttpResponse.BodyHandlers.ofString());
        
        // 简单解析JSON获取消息
        String body = response.body();
        int start = body.indexOf("\"message\":\"") + 11;
        int end = body.indexOf("\"", start);
        return body.substring(start, end);
    }
}
```

### 5. React 组件示例

```jsx
import { useState } from 'react';

function ChatComponent() {
  const [message, setMessage] = useState('');
  const [reply, setReply] = useState('');
  const [loading, setLoading] = useState(false);

  const sendMessage = async () => {
    setLoading(true);
    try {
      const response = await fetch('http://localhost:8000/sessions/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message })
      });
      
      const data = await response.json();
      setReply(data.data.message);
      setMessage('');
    } catch (error) {
      setReply('发送失败: ' + error.message);
    }
    setLoading(false);
  };

  return (
    <div>
      <input 
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        onKeyPress={(e) => e.key === 'Enter' && sendMessage()}
        placeholder="输入消息..."
      />
      <button onClick={sendMessage} disabled={loading}>
        {loading ? '发送中...' : '发送'}
      </button>
      {reply && <div>AI回复: {reply}</div>}
    </div>
  );
}
```

## 特性说明

1. **自动会话管理**: 系统会自动为每个客户创建和管理会话
2. **默认代理**: 如果没有指定代理，使用名为"Default"的代理或第一个可用代理
3. **访客支持**: 如果没有指定客户ID，自动使用访客身份
4. **超时处理**: 默认30秒超时，可自定义
5. **错误处理**: 
   - 200: 成功，返回AI消息
   - 504: 超时
   - 422: 参数错误（如没有可用代理）

## 与传统方式对比

### 传统方式（需要3个步骤）:
```javascript
// 1. 创建会话
const session = await createSession(agentId);
// 2. 发送消息  
await sendMessage(session.id, message);
// 3. 轮询获取回复
const reply = await waitForReply(session.id);
```

### 新方式（只需1个步骤）:
```javascript
// 一步完成！
const reply = await chat(message);
```

## 高级用法

### 指定代理对话
```python
response = requests.post('/sessions/chat', json={
    'message': '你好',
    'agent_id': 'ag_customer_service'
})
```

### 为特定客户保持会话
```python
response = requests.post('/sessions/chat', json={
    'message': '继续之前的对话',
    'customer_id': 'cust_12345'
})
```

### 自定义超时时间
```python
response = requests.post('/sessions/chat', json={
    'message': '帮我生成一篇长文章',
    'timeout': 120  # 2分钟超时
})
```

这个简化接口让 Parlant 的使用变得和调用普通 API 一样简单，非常适合快速集成和原型开发。
