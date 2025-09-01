
## 模板变量替换机制详解

### 一、变量替换流程

模板中的变量替换不是模型自动完成的，而是由 **CannedResponseGenerator** 中的 **字段提取器（Field Extractor）** 负责的。整个流程如下：

```
模板定义 → 字段识别 → 字段提取 → Jinja2渲染 → 最终响应
```

### 二、核心组件和代码

#### 1. 字段提取器架构

```python
# 主提取器，协调三种提取方法
class CannedResponseFieldExtractor:
    def __init__(self,
        standard: StandardFieldExtraction,
        tool_based: ToolBasedFieldExtraction,
        generative: GenerativeFieldExtraction,
    ):
        self.methods = [standard, tool_based, generative]
    
    async def extract(self, canned_response: str, field_name: str, context: CannedResponseContext):
        # 依次尝试三种提取方法
        for method in self.methods:
            success, extracted_value = await method.extract(
                canned_response, field_name, context
            )
            if success:
                return success, extracted_value
        return False, None
```

#### 2. 渲染流程（核心代码）

```python
async def _render_response(self, context: CannedResponseContext, response: CannedResponse):
    args = {}
    
    # 1. 提取模板中的所有字段
    for field_name in _get_response_template_fields(response.value):
        # 2. 调用字段提取器
        success, value = await self._field_extractor.extract(
            response.value, field_name, context
        )
        
        if success:
            args[field_name] = value
        else:
            raise KeyError(f"Missing field '{field_name}'")
    
    # 3. 使用Jinja2渲染模板
    result = jinja2.Template(response.value).render(**args)
    return result
```

### 三、三种字段提取方法

#### 1. 标准字段提取（StandardFieldExtraction）
Ran tool

```python
class StandardFieldExtraction:
    async def extract(self, canned_response: str, field_name: str, context: CannedResponseContext):
        if field_name != "std":
            return False, None
        
        # 返回标准字段的值
        return True, {
            "customer": {"name": context.customer.name},
            "agent": {"name": context.agent.name},
            "variables": {var.name: var.value for var, value in context.context_variables},
            "missing_params": [m.parameter for m in context.tool_insights.missing_data],
        }
```

#### 2. 工具字段提取（ToolBasedFieldExtraction）- **这是您问题的核心**

```python
class ToolBasedFieldExtraction:
    async def extract(self, canned_response: str, field_name: str, context: CannedResponseContext):
        # 从工具调用结果中提取字段
        tool_calls_in_order = []
        
        # 1. 从已执行的工具事件中收集
        for event in context.staged_tool_events:
            if event.kind == EventKind.TOOL:
                tool_calls = event.data["tool_calls"]
                tool_calls_in_order.extend(tool_calls)
        
        # 2. 查找字段值
        for tool_call in tool_calls_in_order:
            # 检查 canned_response_fields
            if field_name in tool_call["result"].get("canned_response_fields", {}):
                return True, tool_call["result"]["canned_response_fields"][field_name]
        
        return False, None
```

#### 3. 生成字段提取（GenerativeFieldExtraction）

```python
class GenerativeFieldExtraction:
    async def extract(self, canned_response: str, field_name: str, context: CannedResponseContext):
        if not field_name.startswith("generative."):
            return False, None
        
        # 使用LLM生成字段值
        prompt = self._build_prompt(canned_response, field_name, context)
        result = await self._generator.generate(prompt)
        
        return True, result.content.field_value
```

### 四、完整示例：银行余额查询

让我们通过完整的银行余额查询示例来说明整个过程：

#### 步骤1：定义模板

```python
# 模板定义，包含需要替换的变量
await bank_agent.create_canned_response(
    template="Your {{account.type}} account (ending in {{account.last_four}}) has a current balance of {{account.balance}}.",
    signals=["Your balance is", "Account balance"]
)
```

#### 步骤2：工具返回数据

```python
@p.tool
async def check_balance(context: p.ToolContext, account_last_four: str) -> p.ToolResult:
    # 查询余额...
    balance = 5432.10
    account_type = "Checking"
    
    return p.ToolResult(
        data={"balance": balance, "type": account_type},  # 给AI理解的数据
        canned_response_fields={  # 专门为模板替换准备的字段
            "account.balance": "$5,432.10",  # 格式化后的显示值
            "account.type": "Checking",
            "account.last_four": account_last_four
        }
    )
```

#### 步骤3：字段提取和渲染过程

```python
# 内部处理流程（框架自动完成）
async def _render_response(context, response):
    # 1. 识别模板中的字段：["account.type", "account.last_four", "account.balance"]
    fields = _get_response_template_fields(response.value)
    
    args = {}
    for field in fields:
        # 2. ToolBasedFieldExtraction 从工具结果中提取
        # 找到 check_balance 返回的 canned_response_fields
        if field == "account.type":
            args["account.type"] = "Checking"
        elif field == "account.last_four":
            args["account.last_four"] = "1234"
        elif field == "account.balance":
            args["account.balance"] = "$5,432.10"
    
    # 3. Jinja2渲染
    result = jinja2.Template(
        "Your {{account.type}} account (ending in {{account.last_four}}) has a current balance of {{account.balance}}."
    ).render(**args)
    
    # 结果："Your Checking account (ending in 1234) has a current balance of $5,432.10."
    return result
```

### 五、关键点总结

1. **不是模型自动匹配**：变量替换是由框架的字段提取器机制完成的，不是LLM自动完成
2. **工具必须提供字段**：工具通过`canned_response_fields`显式提供字段值
3. **三层提取机制**：标准字段 → 工具字段 → 生成字段，按顺序尝试
4. **Jinja2渲染**：最终使用Jinja2模板引擎完成渲染
5. **类型安全**：如果字段缺失，会抛出错误，确保不会产生不完整的响应

### 六、高级用法示例

#### 1. 带条件判断的模板

```python
await agent.create_canned_response(
    template="""Your account balance is {{account.balance}}.
{% if account.overdraft %}
WARNING: Your account is overdrawn by {{account.overdraft_amount}}.
{% endif %}""",
)
```

#### 2. 循环处理列表数据

```python
# 工具返回交易列表
return p.ToolResult(
    data={"transactions": transactions},
    canned_response_fields={
        "transactions": [
            {"date": "2024-01-15", "description": "Coffee Shop", "amount": "-$4.50"},
            {"date": "2024-01-14", "description": "Salary Deposit", "amount": "+$3,000.00"}
        ]
    }
)

# 模板使用循环
await agent.create_canned_response(
    template="""Recent transactions:
{% for t in transactions %}
- {{t.date}}: {{t.description}} {{t.amount}}
{% endfor %}"""
)
```

#### 3. 混合使用三种字段类型

```python
await agent.create_canned_response(
    template="""Hi {{std.customer.name}}, {{generative.greeting_phrase}}.
Your {{account.type}} balance is {{account.balance}}.
{{generative.closing_remark}}""",
)
# 结果示例：
# "Hi John, I hope you're having a great day.
# Your Checking balance is $5,432.10.
# Is there anything else I can help you with?"
```
