import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.URI;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONObject;
import org.json.JSONArray;

/**
 * Parlant API 直接调用示例 - Java
 * 不依赖任何Parlant SDK，直接使用HTTP请求
 */
public class ParlantDirectClient {
    private static final String BASE_URL = "http://localhost:8000";
    private final HttpClient httpClient;
    private String sessionId;
    private final AtomicInteger lastOffset = new AtomicInteger(0);

    public ParlantDirectClient() {
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .build();
    }

    /**
     * 创建AI代理
     */
    public CompletableFuture<JSONObject> createAgent(String name, String description) {
        JSONObject requestBody = new JSONObject();
        requestBody.put("name", name);
        requestBody.put("description", description);
        requestBody.put("composition_mode", "fluid");

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(BASE_URL + "/agents"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(requestBody.toString()))
                .build();

        return httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .thenApply(response -> {
                    if (response.statusCode() != 200) {
                        throw new RuntimeException("创建代理失败: " + response.statusCode());
                    }
                    JSONObject agent = new JSONObject(response.body());
                    System.out.println("✅ 创建代理: " + agent.getString("name") + " (ID: " + agent.getString("id") + ")");
                    return agent;
                });
    }

    /**
     * 创建会话
     */
    public CompletableFuture<JSONObject> createSession(String agentId, String title) {
        JSONObject requestBody = new JSONObject();
        requestBody.put("agent_id", agentId);
        requestBody.put("title", title);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(BASE_URL + "/sessions"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(requestBody.toString()))
                .build();

        return httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .thenApply(response -> {
                    if (response.statusCode() != 200) {
                        throw new RuntimeException("创建会话失败: " + response.statusCode());
                    }
                    JSONObject session = new JSONObject(response.body());
                    this.sessionId = session.getString("id");
                    System.out.println("✅ 创建会话: " + sessionId);
                    return session;
                });
    }

    /**
     * 发送消息
     */
    public CompletableFuture<JSONObject> sendMessage(String message) {
        if (sessionId == null) {
            throw new RuntimeException("请先创建会话");
        }

        JSONObject requestBody = new JSONObject();
        requestBody.put("kind", "message");
        requestBody.put("source", "customer");
        requestBody.put("message", message);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(BASE_URL + "/sessions/" + sessionId + "/events"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(requestBody.toString()))
                .build();

        return httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .thenApply(response -> {
                    if (response.statusCode() != 200) {
                        throw new RuntimeException("发送消息失败: " + response.statusCode());
                    }
                    System.out.println("👤 用户: " + message);
                    return new JSONObject(response.body());
                });
    }

    /**
     * 等待AI回复
     */
    public CompletableFuture<String> waitForReply(int timeout) {
        if (sessionId == null) {
            throw new RuntimeException("请先创建会话");
        }

        String url = String.format("%s/sessions/%s/events?min_offset=%d&source=ai_agent&kinds=message&wait_for_data=%d",
                BASE_URL, sessionId, lastOffset.get(), timeout);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("Content-Type", "application/json")
                .GET()
                .build();

        return httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .thenApply(response -> {
                    if (response.statusCode() == 504) {
                        System.out.println("⏰ 等待超时，未收到回复");
                        return null;
                    }
                    if (response.statusCode() != 200) {
                        throw new RuntimeException("获取回复失败: " + response.statusCode());
                    }

                    JSONArray events = new JSONArray(response.body());
                    if (events.length() > 0) {
                        JSONObject lastEvent = events.getJSONObject(events.length() - 1);
                        lastOffset.set(lastEvent.getInt("offset") + 1);
                        String aiMessage = lastEvent.getJSONObject("data").getString("message");
                        System.out.println("🤖 AI: " + aiMessage);
                        return aiMessage;
                    }
                    return null;
                });
    }

    /**
     * 完整的对话流程
     */
    public CompletableFuture<String> chat(String message) {
        return sendMessage(message)
                .thenCompose(event -> waitForReply(30));
    }

    /**
     * 测试方法
     */
    public static void main(String[] args) {
        ParlantDirectClient client = new ParlantDirectClient();

        // 1. 创建代理
        client.createAgent("测试助手", "一个简单的测试AI助手")
                .thenCompose(agent -> {
                    // 2. 创建会话
                    return client.createSession(agent.getString("id"), "Java API测试");
                })
                .thenCompose(session -> {
                    // 3. 发送消息并等待回复
                    return client.chat("你好，请介绍一下你自己");
                })
                .thenCompose(reply -> {
                    // 4. 继续对话
                    return client.chat("你能帮我做什么？");
                })
                .exceptionally(throwable -> {
                    System.err.println("❌ 错误: " + throwable.getMessage());
                    return null;
                })
                .join(); // 等待所有操作完成
    }
}
