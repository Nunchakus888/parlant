/**
 * Parlant API 直接调用示例 - JavaScript
 * 不依赖任何Parlant SDK，直接使用HTTP请求
 */

const BASE_URL = 'http://localhost:8000';

class ParlantDirectClient {
    constructor(baseUrl = BASE_URL) {
        this.baseUrl = baseUrl;
        this.sessionId = null;
        this.lastOffset = 0;
    }

    // 创建AI代理
    async createAgent(name, description) {
        const response = await fetch(`${this.baseUrl}/agents`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                name: name,
                description: description,
                composition_mode: 'fluid'
            })
        });

        if (!response.ok) {
            throw new Error(`创建代理失败: ${response.status}`);
        }

        const agent = await response.json();
        console.log(`✅ 创建代理: ${agent.name} (ID: ${agent.id})`);
        return agent;
    }

    // 创建会话
    async createSession(agentId, title = '测试对话') {
        const response = await fetch(`${this.baseUrl}/sessions`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                agent_id: agentId,
                title: title
            })
        });

        if (!response.ok) {
            throw new Error(`创建会话失败: ${response.status}`);
        }

        const session = await response.json();
        this.sessionId = session.id;
        console.log(`✅ 创建会话: ${session.id}`);
        return session;
    }

    // 发送消息
    async sendMessage(message) {
        if (!this.sessionId) {
            throw new Error('请先创建会话');
        }

        const response = await fetch(`${this.baseUrl}/sessions/${this.sessionId}/events`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                kind: 'message',
                source: 'customer',
                message: message
            })
        });

        if (!response.ok) {
            throw new Error(`发送消息失败: ${response.status}`);
        }

        const event = await response.json();
        console.log(`👤 用户: ${message}`);
        return event;
    }

    // 等待AI回复
    async waitForReply(timeout = 30) {
        if (!this.sessionId) {
            throw new Error('请先创建会话');
        }

        const response = await fetch(
            `${this.baseUrl}/sessions/${this.sessionId}/events?` + 
            `min_offset=${this.lastOffset}&` +
            `source=ai_agent&` +
            `kinds=message&` +
            `wait_for_data=${timeout}`,
            {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                }
            }
        );

        if (!response.ok) {
            if (response.status === 504) {
                console.log('⏰ 等待超时，未收到回复');
                return null;
            }
            throw new Error(`获取回复失败: ${response.status}`);
        }

        const events = await response.json();
        if (events.length > 0) {
            const lastEvent = events[events.length - 1];
            this.lastOffset = lastEvent.offset + 1;
            const aiMessage = lastEvent.data.message;
            console.log(`🤖 AI: ${aiMessage}`);
            return aiMessage;
        }

        return null;
    }

    // 完整的对话流程
    async chat(message) {
        await this.sendMessage(message);
        return await this.waitForReply();
    }
}

// 使用示例
async function testDirectAPI() {
    const client = new ParlantDirectClient();

    try {
        // 1. 创建代理
        const agent = await client.createAgent('测试助手', '一个简单的测试AI助手');

        // 2. 创建会话
        await client.createSession(agent.id, '直接API测试');

        // 3. 发送消息并等待回复
        await client.chat('你好，请介绍一下你自己');
        
        // 4. 继续对话
        await client.chat('你能帮我做什么？');

    } catch (error) {
        console.error('❌ 错误:', error.message);
    }
}

// 在Node.js环境中运行
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ParlantDirectClient, testDirectAPI };
}

// 在浏览器环境中运行
if (typeof window !== 'undefined') {
    window.ParlantDirectClient = ParlantDirectClient;
    window.testDirectAPI = testDirectAPI;
}
