import json
import os
import re
import time
from typing import Dict, Any, List, Optional
from parlant.core.agent_factory import AgentFactory
import parlant.sdk as p
from parlant.core.agents import AgentStore, AgentId
from app.tools import ToolManager
from app.tools.http_config import AgentConfigRequest, HttpConfigLoader
from app.tools.prompts_format import decode_markdown_links

class CustomAgentFactory(AgentFactory):
    def __init__(self, agent_store: AgentStore, logger, container):
        super().__init__(agent_store, logger)
        self.config_path = "app/lead-acquistion.json"
        self._config_cache = None
        self.container = container

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件，使用缓存避免重复读取"""
        if self._config_cache is None:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self._config_cache = json.load(f)
                self._logger.info(f"成功加载配置文件: {self.config_path}")
            except FileNotFoundError:
                self._logger.error(f"配置文件不存在: {self.config_path}")
                raise
            except json.JSONDecodeError as e:
                self._logger.error(f"配置文件格式错误: {e}")
                raise
        return self._config_cache
    
    def _get_server_from_container(self):
        if hasattr(self.container, '_server_ref') and self.container._server_ref:
            return self.container._server_ref

        return None
    
    async def create_agent_for_customer(self, config_request: AgentConfigRequest) -> p.Agent:
        """
        从HTTP配置请求创建个性化智能体
        
        Args:
            config_request: HTTP配置请求参数，必须提供
            
        Returns:
            创建的Agent实例
            
        Raises:
            RuntimeError: 当配置加载失败时
        """
        server = self._get_server_from_container()
        if not server:
            raise RuntimeError("Server 对象不可用，无法创建智能体")

        http_loader = HttpConfigLoader(self._logger)
        config = await http_loader.load_config_from_http(config_request)
        # config = self._load_config()

        self._logger.info(f"✅成功加载配置: {config}")

        basic_settings = config.get("basic_settings", {})

        action_books = config.get("action_books")
        if not action_books:
            self._logger.error("❌ 没有找到 action_books，无法创建智能体")
            # todo: handle this
            raise RuntimeError("没有找到 action_books，无法创建智能体")

        metadata = {
            "k_language": basic_settings.get("language", "English"),
            "tone": basic_settings.get("tone", "Friendly and professional"),
            "chatbot_id": basic_settings.get("chatbot_id"),
            "md5_checksum": config_request.md5_checksum
        }
        
        agent = await server.create_agent(
            id=AgentId(config_request.tenant_id) if config_request.tenant_id else None,
            name=basic_settings.get("name"),
            description=f"{basic_settings.get('description', '')} {basic_settings.get('background', '')}",
            max_engine_iterations=3,
            metadata=metadata,
        )
        
        # setup tools
        tools = await self._setup_tools(agent, config.get("tools", []))
        
        # create guidelines
        await self._create_guidelines(agent, config.get("action_books", []), tools)

        # default guideline
        await agent.create_guideline(
            condition="The customer's inquiry does not match any specific business guidelines or the customer asks about topics outside our expertise",
            action="Politely explain that you specialize in our business area and would be happy to help with related questions. Ask how you can assist them with our services.",
            metadata={"type": "default"},
        )

        # _process_evaluations
        self._logger.info("🔍 处理评估..._process_evaluations")
        start_time = time.time()
        await server._process_evaluations()
        end_time = time.time()
        elapsed_time = end_time - start_time
        self._logger.info(f"✅⏱️ _process_evaluations 耗时: {elapsed_time:.3f} 秒")

        return agent
    

    async def _setup_tools(self, agent: p.Agent, tools_config: List[Dict[str, Any]]) -> Dict[str, Any]:
        """setup tools and return the tool mapping"""
        if not tools_config:
            self._logger.warning("no tools config, skip setup")
            return {}
        
        tool_manager = ToolManager(
            raw_configs=tools_config,
            logger=self._logger,
            timeout=10
        )
        await tool_manager.setup_tools(agent)
        
        return tool_manager._tools
    
    async def _create_guidelines(self, agent: p.Agent, action_books: List[Dict[str, Any]], available_tools: Dict[str, Any]) -> None:
        if not action_books:
            self._logger.warning("no guidelines config, skip creating")
            return
        
        for action_book in action_books:
            try:
                # 检查是否为journey类型
                if action_book.get("type") == "journey":
                    # await self._process_journey(action_book, agent)
                    continue
                
                condition = action_book.get("condition", "")
                action = action_book.get("action", "")
                tool_names = action_book.get("tools", [])
                
                if not condition and not action:
                    self._logger.warning(f"skip invalid guideline: condition={condition}, action={action}")
                    continue
                
                action = decode_markdown_links(action, self._logger)

                # get associated tools
                associated_tools = []
                for tool_name in tool_names:
                    if tool_name in available_tools:
                        associated_tools.append(available_tools[tool_name])
                    else:
                        self._logger.warning(f"tool {tool_name} not found, skip associating")
                
                # create guideline
                await agent.create_guideline(
                    condition=condition,
                    action=action,
                    tools=associated_tools
                )
                
                # print tool names
                tool_names = [tool.tool.name for tool in associated_tools] if associated_tools else []
                self._logger.debug(f"create actionbooks: {condition} -> (associated {tool_names} tools)")
                
            except Exception as e:
                self._logger.error(f"create guideline failed: {e}")
                continue
        
        
        self._logger.info(f"✅successfully created {len(action_books)} actionbooks")

    async def _process_journey(self, journey_config: Dict[str, Any], agent: p.Agent) -> None:
        """
        处理journey类型的数据
        解析action中的缩进层级结构，创建journey和状态转换
        """
        try:
            title = journey_config.get("title", "Untitled Journey")
            description = journey_config.get("description", "")
            conditions = journey_config.get("conditions", [])
            action = journey_config.get("action", "")
            
            self._logger.info(f"Processing journey: {title}")
            
            # 创建journey
            journey = await agent.create_journey(
                title=title,
                description=description,
                conditions=conditions
            )
            
            # 解析action中的状态结构
            states = self._parse_journey_states(action)
            
            # 创建状态和转换
            await self._create_journey_states_and_transitions(journey, states)
            
            self._logger.info(f"Successfully created journey: {title} with {len(states)} states")
            
            # 记录解析的状态结构
            self._log_journey_structure(states)
            
        except Exception as e:
            self._logger.error(f"Error processing journey {journey_config.get('title', 'Unknown')}: {e}")
            raise

    def _log_journey_structure(self, states: List[Dict[str, Any]], indent: int = 0) -> None:
        """
        记录journey的结构层次
        """
        for state in states:
            indent_str = "  " * indent
            self._logger.debug(f"{indent_str}State {state['id']}: {state['name']} (level: {state['indent_level']})")
            if state.get('children'):
                self._log_journey_structure(state['children'], indent + 1)

    def _parse_journey_states(self, action: str) -> List[Dict[str, Any]]:
        """
        解析journey action中的状态结构
        根据缩进层级解析状态层级关系
        """
        states = []
        lines = action.split('\n')
        current_state = None
        state_stack = []  # 用于跟踪状态层级
        state_counter = 0
        
        for line_num, line in enumerate(lines, 1):
            original_line = line
            line = line.rstrip()  # 保留左侧空格用于缩进检测
            
            # 跳过空行和注释行
            if not line.strip() or line.strip().startswith('#'):
                continue
            
            # 计算缩进级别
            indent_level = self._calculate_indent_level(line)
            content = line.strip()
            
            # 检测状态行（包含数字编号或状态标识）
            if self._is_state_line(content):
                state_info = self._parse_state_line(content, indent_level, state_counter)
                if state_info:
                    state_counter += 1
                    current_state = state_info
                    
                    # 根据缩进级别确定父子关系
                    self._update_state_hierarchy(state_info, state_stack, indent_level, states)
                    
            elif content.startswith('- **') and current_state:
                # 解析状态属性
                self._parse_state_attribute(content, current_state)
                
        return states

    def _calculate_indent_level(self, line: str) -> int:
        """
        计算行的缩进级别
        使用空格或制表符作为缩进单位
        """
        if not line:
            return 0
            
        # 计算前导空格数
        leading_spaces = len(line) - len(line.lstrip())
        
        # 将空格数转换为缩进级别（每4个空格为一级）
        return leading_spaces // 4

    def _is_state_line(self, content: str) -> bool:
        """
        判断是否为状态行
        状态行通常包含：
        1. 数字编号（如 "1. Initial State", "3.1 Happy Path"）
        2. 状态标识词（如 "State", "Step", "Phase"）
        3. 特定格式（如 "### 3.1 Happy Path"）
        4. 动作描述（如 "Check Availability", "Present Options"）
        """
        # 检查是否包含数字编号
        if re.match(r'^\d+(\.\d+)*\.?\s+', content):
            return True
            
        # 检查是否包含状态标识词
        state_keywords = ['state', 'step', 'phase', 'stage', 'node', 'action', 'check', 'present', 'confirm', 'schedule']
        content_lower = content.lower()
        for keyword in state_keywords:
            if keyword in content_lower:
                return True
                
        # 检查是否以特定格式开头（Markdown标题）
        if re.match(r'^#{1,6}\s+\d+', content):
            return True
            
        # 检查是否以大写字母开头的动作描述
        if re.match(r'^[A-Z][a-zA-Z\s]+$', content.strip()):
            return True
            
        return False

    def _update_state_hierarchy(self, state_info: Dict[str, Any], state_stack: List[Dict[str, Any]], 
                               indent_level: int, states: List[Dict[str, Any]]) -> None:
        """
        根据缩进级别更新状态层级关系
        """
        # 调整状态栈以匹配当前缩进级别
        while len(state_stack) > indent_level:
            state_stack.pop()
            
        # 设置父状态
        if indent_level == 0:
            # 根级别状态
            state_info['parent'] = None
            state_info['children'] = []
            states.append(state_info)
            state_stack = [state_info]
        else:
            # 子状态
            if state_stack:
                parent = state_stack[-1]
                state_info['parent'] = parent
                state_info['children'] = []
                parent['children'].append(state_info)
                
                # 更新状态栈
                if len(state_stack) > indent_level:
                    state_stack = state_stack[:indent_level]
                state_stack.append(state_info)
            else:
                # 如果没有父状态，作为根状态处理
                state_info['parent'] = None
                state_info['children'] = []
                states.append(state_info)
                state_stack = [state_info]

    def _parse_state_line(self, content: str, indent_level: int, state_counter: int) -> Optional[Dict[str, Any]]:
        """
        解析状态行，提取状态信息
        """
        try:
            # 移除标题标记（如果存在）
            clean_content = re.sub(r'^#{1,6}\s+', '', content)
            
            # 解析状态编号和名称
            state_id = None
            state_name = clean_content
            
            # 尝试提取数字编号
            number_match = re.match(r'^(\d+(?:\.\d+)*)\.?\s*(.*)', clean_content)
            if number_match:
                state_id = number_match.group(1)
                state_name = number_match.group(2).strip() or clean_content
            else:
                # 如果没有数字编号，使用计数器
                state_id = str(state_counter + 1)
                
            # 清理状态名称
            if not state_name:
                state_name = f"State {state_id}"
                
            return {
                'id': state_id,
                'name': state_name,
                'indent_level': indent_level,
                'action': None,
                'type': None,
                'tool': None,
                'condition': None,
                'description': None,
                'end': False,
                'transition': None,
                'parent': None,
                'children': []
            }
        except Exception as e:
            self._logger.warning(f"Error parsing state line '{content}': {e}")
            return None

    def _parse_state_attribute(self, line: str, state: Dict[str, Any]) -> None:
        """
        解析状态属性行
        """
        try:
            # 移除开头的 - **
            content = line.replace('- **', '').replace('**', '')
            
            if ': ' in content:
                key, value = content.split(': ', 1)
                key = key.strip().lower()
                value = value.strip()
                
                if key == 'action':
                    state['action'] = value
                elif key == 'type':
                    state['type'] = value
                elif key == 'tool':
                    state['tool'] = value
                elif key == 'condition':
                    state['condition'] = value
                elif key == 'description':
                    state['description'] = value
                elif key == 'end':
                    state['end'] = value.lower() == 'journey complete'
                elif key == 'transition':
                    state['transition'] = value
                    
        except Exception as e:
            self._logger.warning(f"Error parsing state attribute '{line}': {e}")

    async def _create_journey_states_and_transitions(self, journey: p.Journey, states: List[Dict[str, Any]]) -> None:
        """
        创建journey的状态和转换
        """
        try:
            # 创建初始状态
            initial_state = await journey._create_state(
                p.ChatJourneyState,
                action="Welcome! How can I help you today?"
            )
            
            # 创建所有状态
            state_map = {}
            for state_info in states:
                state = await self._create_single_state(journey, state_info)
                state_map[state_info['id']] = state
                
            # 创建转换
            await self._create_transitions(journey, states, state_map, initial_state)
            
        except Exception as e:
            self._logger.error(f"Error creating journey states and transitions: {e}")
            raise

    async def _create_single_state(self, journey: p.Journey, state_info: Dict[str, Any]) -> p.JourneyState:
        """
        创建单个状态
        """
        try:
            action = state_info.get('action', state_info.get('name', ''))
            state_type = state_info.get('type', 'Chat State')
            tool_name = state_info.get('tool')
            
            if state_type == 'Tool State' and tool_name:
                # 创建工具状态
                tool_entry = p.ToolEntry(
                    tool=p.Tool(name=tool_name, description=f"Tool for {state_info['name']}"),
                    arguments={}
                )
                return await journey._create_state(
                    p.ToolJourneyState,
                    action=action,
                    tools=[tool_entry]
                )
            else:
                # 创建聊天状态
                return await journey._create_state(
                    p.ChatJourneyState,
                    action=action
                )
                
        except Exception as e:
            self._logger.error(f"Error creating state {state_info.get('name', 'Unknown')}: {e}")
            raise

    async def _create_transitions(self, journey: p.Journey, states: List[Dict[str, Any]], state_map: Dict[str, p.JourneyState], initial_state: p.JourneyState) -> None:
        """
        创建状态转换
        """
        try:
            # 从初始状态到第一个主状态
            if states:
                first_state = state_map[states[0]['id']]
                await journey.create_transition(
                    condition=None,
                    source=initial_state,
                    target=first_state
                )
                
            # 创建其他转换
            for state_info in states:
                await self._create_state_transitions(journey, state_info, state_map)
                
        except Exception as e:
            self._logger.error(f"Error creating transitions: {e}")
            raise

    async def _create_state_transitions(self, journey: p.Journey, state_info: Dict[str, Any], state_map: Dict[str, p.JourneyState]) -> None:
        """
        为单个状态创建转换
        """
        try:
            current_state = state_map[state_info['id']]
            
            # 处理子状态转换
            for child in state_info.get('children', []):
                child_state = state_map[child['id']]
                condition = child.get('condition')
                
                await journey.create_transition(
                    condition=condition,
                    source=current_state,
                    target=child_state
                )
                
                # 递归处理子状态
                await self._create_state_transitions(journey, child, state_map)
                
            # 处理转换到其他状态
            if state_info.get('transition'):
                transition_info = state_info['transition']
                # 解析转换目标（如 "Back to 3.1.1 (Final Confirmation)"）
                target_id = self._extract_transition_target(transition_info)
                if target_id and target_id in state_map:
                    target_state = state_map[target_id]
                    await journey.create_transition(
                        condition=state_info.get('condition'),
                        source=current_state,
                        target=target_state
                    )
                    
        except Exception as e:
            self._logger.error(f"Error creating transitions for state {state_info.get('name', 'Unknown')}: {e}")
            raise

    def _extract_transition_target(self, transition_text: str) -> Optional[str]:
        """
        从转换文本中提取目标状态ID
        """
        try:
            # 匹配 "Back to 3.1.1 (Final Confirmation)" 格式
            match = re.search(r'(\d+(?:\.\d+)*)', transition_text)
            if match:
                return match.group(1)
            return None
        except Exception as e:
            self._logger.warning(f"Error extracting transition target from '{transition_text}': {e}")
            return None

    def _create_sample_journey_config(self) -> Dict[str, Any]:
        """
        创建示例journey配置，展示基于缩进的语法
        """
        return {
            "type": "journey",
            "title": "Customer Onboarding",
            "description": "Guide new customers through the onboarding process",
            "conditions": ["Customer is new to the platform"],
            "action": """
1. Welcome Customer
    - **Action**: Greet the customer and explain the onboarding process
    - **Type**: Chat State
    - **Description**: Initial welcome message

2. Collect Basic Information
    - **Action**: Ask for customer's basic details
    - **Type**: Chat State
    - **Description**: Gather name, email, and preferences
    
    2.1 Validate Information
        - **Action**: Verify the provided information
        - **Type**: Tool State
        - **Tool**: validate_customer_info
        - **Condition**: Customer provides information
        
        2.1.1 Information Valid
            - **Action**: Confirm information and proceed
            - **Type**: Chat State
            - **Transition**: Continue to step 3
            
        2.1.2 Information Invalid
            - **Action**: Ask customer to correct the information
            - **Type**: Chat State
            - **Transition**: Back to step 2

3. Setup Account
    - **Action**: Create customer account
    - **Type**: Tool State
    - **Tool**: create_customer_account
    - **Description**: Set up the customer's account

4. Send Welcome Email
    - **Action**: Send welcome email with next steps
    - **Type**: Tool State
    - **Tool**: send_welcome_email
    - **Description**: Notify customer of successful onboarding

5. Complete Onboarding
    - **Action**: Thank customer and provide next steps
    - **Type**: Chat State
    - **End**: Journey Complete
"""
        }
    




async def initialize_agent_factory(container: p.Container) -> None:
    logger = container[p.Logger]
    logger.info("🚀 开始初始化 CustomAgentFactory...")
    
    container[p.AgentFactory] = CustomAgentFactory(
        agent_store=container[AgentStore],
        container=container,
        logger=logger
    )
    
    logger.info("✅ CustomAgentFactory 初始化完成！")