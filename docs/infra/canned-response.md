
# Canned Responses深度分析报告

## 一、设计理念

Canned Responses（预设响应）其设计理念源自真实世界的客服中心实践。它通过限制AI代理只能从预定义的响应集合中选择，实现了以下目标：

1. **完全消除幻觉风险**：确保AI永远不会生成未经批准的内容
2. **品牌一致性**：保持统一的语气、风格和品牌声音
3. **精确控制**：对每个可能的响应都有完全的掌控
4. **合规性保证**：满足高风险场景的严格要求

比喻：就像给AI代理一副"牌"，它只能从手中的牌中选择最合适的一张来"出牌"。

## 二、适用场景

### 1. 高风险业务场景
- **金融服务**：涉及交易、账户信息等敏感操作
- **医疗健康**：需要准确传达医疗信息
- **法律咨询**：必须避免误导性建议
- **合规要求严格的行业**：需要每个响应都经过审核

### 2. 品牌敏感场景
- **高端品牌客服**：需要保持特定的品牌调性
- **官方发言**：代表公司的正式回应
- **营销活动**：精确控制促销信息

### 3. 渐进式部署场景
- **AI试点项目**：从严格模式开始，逐步放松限制
- **新产品上线**：初期使用预设响应，收集数据后逐步开放
- **原型开发**：快速验证对话流程

## 三、核心数据结构

### 1. CannedResponse类
```python
@dataclass(frozen=True)
class CannedResponse:
    id: CannedResponseId
    creation_utc: datetime
    value: str  # 模板内容
    fields: Sequence[CannedResponseField]  # 字段定义
    signals: Sequence[str]  # 语义信号，帮助匹配
    tags: Sequence[TagId]  # 标签（如preamble）
```

### 2. CompositionMode枚举
```python
class CompositionMode(Enum):
    FLUID = "fluid"  # 不使用Canned Response，直接生成
    CANNED_FLUID = "canned_fluid"  # 优先使用模板，无匹配时生成
    CANNED_COMPOSITED = "canned_composited"  # 使用模板风格重组
    CANNED_STRICT = "canned_strict"  # 只能使用模板，无匹配返回默认消息
```

## 四、实现逻辑（4+1阶段）

### 阶段1：消息起草
- **LLM调用**：`CannedResponseDraftSchema`
- **任务**：基于当前上下文（guidelines、工具结果、对话历史）起草一个流畅的响应
- **核心代码**：`_build_draft_prompt()` 和 `_canrep_response_draft_generation()`

### 阶段2：模板检索
- **机制**：语义相似度匹配 + signals信号匹配
- **优化**：
  - 过滤掉引用不存在字段的模板
  - 基于Journey和Guidelines范围缩小候选集
- **核心代码**：`_get_relevant_canned_responses()`

### 阶段3：字段提取与渲染
- **字段类型**：
  - **标准字段**（`std.`）：客户名、代理名、上下文变量
  - **工具字段**：来自`ToolResult.canned_response_fields`
  - **生成字段**（`generative.`）：LLM推断值
- **渲染引擎**：Jinja2模板引擎
- **核心代码**：`_render_canned_response()` 和 `CannedResponseFieldExtractor`

### 阶段4：响应选择
- **LLM调用**：`CannedResponseSelectionSchema`
- **任务**：从渲染后的候选中选择最匹配草稿的响应
- **匹配质量**：high、partial、none
- **核心代码**：`_canrep_response_selection()`

### 阶段4+1：修订（可选）
- **触发条件**：COMPOSITED模式或部分匹配
- **LLM调用**：`CannedResponseRevisionSchema`
- **任务**：使用选中模板的风格重写草稿
- **核心代码**：`_canrep_response_revision()`

## 五、核心代码位置

### 主要文件
1. **核心生成器**：`/src/parlant/core/engines/alpha/canned_response_generator.py`
   - 包含完整的Canned Response生成流程
   - 约1800行代码，是最复杂的组件之一

2. **数据存储**：`/src/parlant/core/canned_responses.py`
   - CannedResponse数据结构定义
   - 存储接口和实现

3. **配置集成**：`/src/parlant/core/agents.py`
   - CompositionMode定义
   - Agent级别的配置

### 关键方法
- `CannedResponseGenerator.generate_response()` - 主入口
- `_canrep_process_responses()` - 核心处理流程
- `_render_canned_response()` - 模板渲染
- `NoMatchResponseProvider.get_response()` - 无匹配处理

## 六、高级特性

### 1. Signals（信号）
用于提高模板检索准确性：
```python
await agent.create_canned_response(
    template="Yes, we have it in stock!",
    signals=["We do have it", "It's available"]
)
```

### 2. Preamble（前导响应）
快速确认用户输入，提升体验：
```python
await agent.create_canned_response(
    template="Got it.",
    tags=[p.Tag.preamble()]
)
```

### 3. Journey范围限定
将响应限定在特定Journey中：
```python
await journey.create_canned_response(template=TEXT)
```

### 4. 动态字段防护
自动过滤引用不存在字段的模板，防止幻觉：
- 如果模板引用`{{transaction.id}}`
- 但当前上下文没有`transaction`字段
- 该模板不会被选为候选

## 七、设计优势

1. **渐进式控制**：从STRICT到FLUID，可根据信心逐步放松限制
2. **智能降级**：无匹配时的多种处理策略
3. **上下文感知**：基于实际可用数据过滤模板
4. **性能优化**：通过语义检索快速定位候选
5. **可扩展性**：支持自定义NoMatchProvider和字段提取器

## 八、最佳实践

1. **开始于STRICT模式**：新项目从最严格的控制开始
2. **善用Signals**：为语义差异大的模板添加信号
3. **合理使用生成字段**：在需要局部灵活性时使用`generative.`
4. **工具集成**：充分利用工具返回的动态字段
5. **持续优化**：监控无匹配情况，不断补充模板

## 九、总结

Canned Responses是Parlant框架中平衡"控制"与"智能"的关键设计。它不是简单的模板系统，而是一个智能的响应管理框架，通过多阶段的LLM协作，实现了在保持完全控制的同时，仍能提供自然、上下文相关的对话体验。这种设计特别适合那些对准确性和合规性有严格要求，但又希望利用AI能力提升用户体验的企业级应用场景。





## case

### 一、电商客服场景配置示例

#### 1. 基础配置 - STRICT模式

```python

import parlant as p

# 创建严格模式的客服代理
customer_service_agent = await server.create_agent(
    name="E-commerce Support",
    description="Customer service agent for online store",
    composition_mode=p.CompositionMode.CANNED_STRICT,
)

# 配置基础问候响应
await customer_service_agent.create_canned_response(
    template="Hi {{std.customer.name}}! Welcome to our store. How can I help you today?",
    tags=[p.Tag.preamble()],  # 标记为前导响应
)

# 配置产品查询响应
await customer_service_agent.create_canned_response(
    template="Yes, {{generative.product_name}} is currently in stock. Would you like me to add it to your cart?",
    signals=[
        "We have this item in stock",
        "This product is available",
        "Yes, it's in stock"
    ]
)

await customer_service_agent.create_canned_response(
    template="I'm sorry, {{generative.product_name}} is currently out of stock. Would you like to be notified when it's available?",
    signals=[
        "This item is out of stock",
        "We don't have this available",
        "Sorry, it's sold out"
    ]
)

# 配置价格查询响应
await customer_service_agent.create_canned_response(
    template="The price of {{product.name}} is {{product.price}}. {{product.discount_info}}",
    signals=["Here's the price", "The cost is", "It's priced at"]
)

# 配置订单状态查询响应
await customer_service_agent.create_canned_response(
    template="Your order #{{order.id}} is currently {{order.status}}. Expected delivery: {{order.delivery_date}}.",
    signals=["Your order status", "Order update", "Tracking information"]
)

# 配置无匹配响应
async def configure_no_match(c: p.Container) -> None:
    no_match_provider = c[p.BasicNoMatchResponseProvider]
    no_match_provider.template = "I apologize, but I can only help with product inquiries, order status, and general store information. Could you please rephrase your question?"

# 创建工具以提供动态数据
@p.tool
async def check_inventory(context: p.ToolContext, product_name: str) -> p.ToolResult:
    # 模拟库存检查
    inventory = {
        "laptop": {"stock": 15, "price": "$999", "discount": "10% off this week"},
        "phone": {"stock": 0, "price": "$699", "discount": None},
        "tablet": {"stock": 8, "price": "$499", "discount": "Free shipping"}
    }
    
    product = inventory.get(product_name.lower(), None)
    if product:
        return p.ToolResult(
            data={
                "product": product_name,
                "in_stock": product["stock"] > 0,
                "quantity": product["stock"]
            },
            canned_response_fields={
                "product.name": product_name,
                "product.price": product["price"],
                "product.discount_info": product["discount"] or "No current discounts.",
                "product.stock": product["stock"]
            }
        )
    else:
        return p.ToolResult(
            data={"error": "Product not found"},
            canned_responses=[
                f"I couldn't find {product_name} in our system. Please check the product name or browse our catalog."
            ]
        )

# 配置Journey特定响应
checkout_journey = await customer_service_agent.create_journey("Checkout Process")

await checkout_journey.create_canned_response(
    template="Great! I've added {{product.name}} to your cart. Your total is {{cart.total}}. Would you like to proceed to checkout?"
)

await checkout_journey.create_canned_response(
    template="Perfect! Let me guide you through checkout. First, please confirm your shipping address: {{std.variables.shipping_address}}"
)
```


#### 2. 退货流程的Journey配置

```python

# 创建退货Journey
return_journey = await customer_service_agent.create_journey("Product Return")

# 退货原因询问
await return_journey.create_canned_response(
    template="I'm sorry to hear you want to return {{generative.item}}. May I ask the reason for the return?",
    signals=["Why do you want to return", "What's the reason for return"]
)

# 退货政策说明
await return_journey.create_canned_response(
    template="""Our return policy allows returns within 30 days of purchase. 
    For {{generative.product_category}}, the item must be:
    - Unused and in original condition
    - With all original packaging and tags
    - Accompanied by the receipt
    
    Would you like to proceed with the return?""",
    signals=["Here's our return policy", "Return requirements"]
)

# 退货确认
await return_journey.create_canned_response(
    template="I've initiated return #{{return.id}} for your {{return.item}}. You'll receive a prepaid shipping label at {{std.variables.email}} within 24 hours.",
    signals=["Return confirmed", "Return processed"]
)

```



### 二、银行客服场景配置示例（高安全要求）


```python

# 银行客服 - 最严格的STRICT模式
bank_agent = await server.create_agent(
    name="SecureBank Assistant",
    description="Secure banking customer service agent",
    composition_mode=p.CompositionMode.CANNED_STRICT,
)

# 身份验证响应
await bank_agent.create_canned_response(
    template="For security purposes, I need to verify your identity. Please provide the last 4 digits of your account number.",
    tags=[p.Tag.preamble()],
    signals=["I need to verify", "Security check required"]
)

await bank_agent.create_canned_response(
    template="Thank you. Now, please provide your date of birth in MM/DD/YYYY format.",
    signals=["Next verification step", "Additional verification needed"]
)

# 账户余额查询 - 只能通过工具提供
await bank_agent.create_canned_response(
    template="Your {{account.type}} account (ending in {{account.last_four}}) has a current balance of {{account.balance}}.",
    signals=["Your balance is", "Account balance"]
)

# 交易查询响应
await bank_agent.create_canned_response(
    template="""Here are your recent transactions:
{% for transaction in transactions %}
- {{transaction.date}}: {{transaction.description}} - {{transaction.amount}}
{% endfor %}""",
    signals=["Recent transactions", "Transaction history"]
)

# 敏感操作拒绝响应
await bank_agent.create_canned_response(
    template="For security reasons, I cannot process {{generative.sensitive_action}} through this channel. Please visit your nearest branch or call our secure line at 1-800-SECURE-BANK.",
    signals=[
        "I cannot process transfers",
        "Cannot change account details",
        "Security policy prevents"
    ]
)

# 合规性响应
await bank_agent.create_canned_response(
    template="All banking activities are subject to federal regulations. {{generative.specific_regulation_info}}",
    signals=["Regulatory requirements", "Compliance information"]
)

# 银行特定工具
@p.tool
async def check_balance(
    context: p.ToolContext, 
    account_last_four: str,
    verification_passed: bool
) -> p.ToolResult:
    if not verification_passed:
        return p.ToolResult(
            data={"error": "Verification required"},
            canned_responses=[
                "I need to verify your identity before I can access account information."
            ]
        )
    
    # 模拟安全的余额查询
    return p.ToolResult(
        data={
            "balance": 5432.10,
            "account_type": "Checking"
        },
        canned_response_fields={
            "account.balance": "$5,432.10",
            "account.type": "Checking",
            "account.last_four": account_last_four
        }
    )

# 配置无匹配响应（银行场景更保守）
async def configure_bank_no_match(c: p.Container) -> None:
    no_match_provider = c[p.BasicNoMatchResponseProvider]
    no_match_provider.template = "I can only assist with balance inquiries, recent transactions, and general account information. For other banking needs, please contact our support line at 1-800-SECURE-BANK."
    
```