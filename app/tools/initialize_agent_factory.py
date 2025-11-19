import json
import os
import re
import time
from typing import Dict, Any, List, Optional
from parlant.core.agent_factory import AgentFactory
import parlant.sdk as p
from parlant.core.agents import AgentStore, AgentId
from parlant.core.sessions import SessionId
from app.tools import ToolManager
from app.tools.http_config import AgentConfigRequest, HttpConfigLoader
from app.tools.prompts_format import decode_markdown_links
from app.tools.retriver import create_knowledge_retriever
from parlant.core.services.journey_builder import JourneyBuilder
from parlant.core.services.indexing.journey_structure_analysis import JourneyGraph

class CustomAgentFactory(AgentFactory):
    def __init__(self, agent_store: AgentStore, logger, container):
        super().__init__(agent_store, logger)
        self.config_path = "app/case/journey-tool.json"
        self.container = container

    def _load_config(self) -> Dict[str, Any]:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            self._logger.error(f"配置文件不存在: {self.config_path}")
            raise
        except json.JSONDecodeError as e:
            self._logger.error(f"配置文件格式错误: {e}")
            raise
    
    def _get_server_from_container(self):
        if hasattr(self.container, '_server_ref') and self.container._server_ref:
            return self.container._server_ref

        return None
    
    async def create_agent_for_customer(self, config_request: AgentConfigRequest) -> p.Agent:
        """
        从HTTP配置请求创建个性化智能体
        
        该方法支持灵活的配置方式：
        - action_books 为可选配置，即使为空也能创建基础智能体
        - 基础智能体具备完整的知识库查询和通用问答能力
        - tools 和 retrievers 可独立配置，不依赖 action_books
        
        Args:
            config_request: HTTP配置请求参数，必须提供
            
        Returns:
            创建的Agent实例（包含知识库检索器、工具和 guidelines）
            
        Raises:
            RuntimeError: 当 Server 不可用或配置加载失败时
        """
        server = self._get_server_from_container()
        if not server:
            raise RuntimeError("Server 对象不可用，无法创建智能体")

        http_loader = HttpConfigLoader(self._logger)
        config = await http_loader.load_config_from_http(config_request)
        # config = self._load_config()

        basic_settings = config.get("basic_settings", {})

        metadata = {
            "k_language": basic_settings.get("language", "English"),
            "communication_style": basic_settings.get("communication_style", []),
            "tone": basic_settings.get("tone", "Friendly and professional"),
            "chatbot_id": config_request.chatbot_id,
            "tenant_id": config_request.tenant_id,
            "md5_checksum": config_request.md5_checksum,
            "session_id": config_request.session_id
        }

        session_id = SessionId(config_request.session_id) if config_request.session_id else None
        
        agent = await server.create_agent(
            id=AgentId(config_request.session_id) if config_request.session_id else None,
            name=basic_settings.get("name"),
            description=f"{basic_settings.get('description', '')} {basic_settings.get('background', '')}",
            max_engine_iterations=int(os.getenv("MAX_ENGINE_ITERATIONS", "3")),  # 默认3次迭代，支持多步骤工具调用
            metadata=metadata,
        )
        
        # 设置知识库检索器
        await self._setup_retriever(agent, config_request, basic_settings)
        
        # setup tools
        tools = await self._setup_tools(agent, config.get("tools", []))
        
        start_time = time.time()
        # get action_books and merge handover config
        action_books = config.get("action_books", [])
        handover_actionbook = self._convert_handover_to_actionbook(basic_settings)
        if handover_actionbook:
            action_books.append(handover_actionbook)
        
        # create guidelines
        guidelines = await self._create_guidelines(agent, action_books, tools)
        
        # process the evaluation queue of the agent (isolated by agent_id, write into session inspection)
        await server._process_evaluations(agent_id=agent.id, session_id=session_id)
        
        # after evaluation, process the journey conversion
        await self._process_journey_conversions(agent, guidelines, tools)

        await server._process_evaluations(agent_id=agent.id, session_id=session_id)


        end_time = time.time()
        self._logger.info(f"⏱️ create guidelines: {(end_time - start_time):.3f} seconds")
        # default guideline
        # await agent.create_guideline(
        #     condition="The customer's inquiry does not match any specific business guidelines or the customer asks about topics outside our expertise",
        #     action="Politely explain that you specialize in our business area and would be happy to help with related questions. Ask how you can assist them with our services.",
        #     metadata={"type": "default"},
        # )

        return agent
    
    async def _setup_retriever(
        self, 
        agent: p.Agent, 
        config_request: AgentConfigRequest,
        basic_settings: Dict[str, Any]
    ) -> None:
        """
        为Agent配置知识库检索器
        
        Args:
            agent: Agent实例
            config_request: HTTP配置请求，包含chatbot_id等信息
            basic_settings: 基础配置，包含retrieve_knowledge_url
        """
        try:
            # get the knowledge base information from the config
            chatbot_id = config_request.chatbot_id
            retrieve_url = basic_settings.get("retrieve_knowledge_url")
            
            # validate the necessary parameters
            if not chatbot_id:
                self._logger.warning("chatbot_id not found, skipping retriever setup")
                return
                
            if not retrieve_url:
                self._logger.warning("retrieve_knowledge_url not found, skipping retriever setup")
                return
            
            # create the knowledge base retriever instance
            knowledge_retriever = create_knowledge_retriever(
                chatbot_id=chatbot_id,
                retrieve_url=retrieve_url,
                logger=self._logger,
                timeout=int(os.getenv("RETRIEVER_TIMEOUT", "10"))
            )
            
            # register the retriever to the agent
            await agent.attach_retriever(
                knowledge_retriever.retrieve,
                # id="knowledge_retriever"
            )
            
        except Exception as e:
            self._logger.error(f"🔴 Failed to setup retriever: {type(e).__name__}: {str(e)}")
            # do not raise an exception, allow the agent to continue working without the retriever

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
    
    async def _create_guidelines(self, agent: p.Agent, action_books: List[Dict[str, Any]], available_tools: Dict[str, Any]) -> List[p.Guideline]:
        """
        create guidelines and return the created guideline list
        
        Args:
            agent: Agent instance
            action_books: actionbook config list, can be empty
            available_tools: available tools dictionary
            
        Returns:
            created guideline list, if action_books is empty, return an empty list
        """
        if not action_books:
            self._logger.info(
                "📖 action_books is empty, skip creating guidelines (Agent will run in basic mode, relying on knowledge base and general conversation abilities)"
            )
            return []
        
        created_guidelines = []
        
        for action_book in action_books:
            try:
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
                guideline = await agent.create_guideline(
                    condition=condition,
                    action=action,
                    tools=associated_tools
                )
                
                created_guidelines.append(guideline)
                
            except Exception as e:
                self._logger.error(f"create guideline failed: {e}")
                continue
        
        self._logger.info(
            f"📖 successfully created {len(created_guidelines)}/{len(action_books)} guidelines"
        )
        return created_guidelines

    def _convert_handover_to_actionbook(self, basic_settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        将handover配置转换为actionbook格式
        
        Args:
            basic_settings: 基础配置，可能包含handover配置
            
        Returns:
            actionbook格式的字典，如果没有配置则返回None
        """
        handover_config = basic_settings.get("handover")
        
        if not handover_config or not isinstance(handover_config, list) or len(handover_config) == 0:
            return None
        
        condition_parts = [rule.strip() for rule in handover_config if isinstance(rule, str) and rule.strip()]
        
        if not condition_parts:
            return None
        
        condition = "\n".join(condition_parts)
        
        action = (
            "Output ONLY in this exact format: ho000001:<your message>\n"
            "Your message should convey: 'Got it! Let me connect you with one of our team members who'll be happy to help you further.'"
        )
        
        self._logger.info(f"✋ Converted handover config to actionbook with {len(condition_parts)} rules")
        
        return {
            "condition": condition,
            "action": action,
            "tools": []
        }
        
    async def _process_journey_conversions(
        self, 
        agent: p.Agent, 
        guidelines: List[p.Guideline],
        available_tools: Dict[str, Any]
    ) -> None:
        """在评估完成后，处理 journey 转换逻辑"""
        from parlant.core.guidelines import GuidelineStore
        from parlant.core.tags import Tag
        
        guideline_store = self.container[GuidelineStore]
        journey_builder = JourneyBuilder(self._logger)
        
        # 环境变量配置
        enable_journey_conversion = os.getenv("ENABLE_JOURNEY_AUTO_CONVERSION", "true").lower() == "true"
        journey_confidence_threshold = float(os.getenv("JOURNEY_CONFIDENCE_THRESHOLD", "0.5"))
        
        if not enable_journey_conversion:
            self._logger.debug("Journey自动转换已禁用")
            return
        
        for guideline in guidelines:
            try:
                # 从数据库读取guideline，获取evaluation更新后的metadata
                guideline_with_metadata = await guideline_store.read_guideline(guideline.id)
                
                # 检查evaluation结果，判断是否需要转换为Journey
                if guideline_with_metadata.metadata.get("is_journey_candidate"):
                    confidence = guideline_with_metadata.metadata.get("journey_confidence", 0.0)
                    
                    if confidence >= journey_confidence_threshold:
                        journey_graph_data = guideline_with_metadata.metadata.get("journey_graph")
                        
                        if journey_graph_data:
                            self._logger.trace(
                                f"🔍 detect journey candidate (confidence: {confidence:.2f}): {guideline_with_metadata.content.condition[:50]}..."
                            )
                            
                            try:
                                condition = guideline.condition

                                self._logger.trace(f"guideline deleting: {guideline.id}")
                                await guideline_store.delete_guideline(
                                    guideline_id=guideline.id,
                                )

                                # 1. 解析Journey Graph
                                journey_graph = JourneyGraph.from_dict(journey_graph_data)
                                
                                self._logger.trace(f"✈️ create_journey: conditions: {condition}")
                                journey = await agent.create_journey(
                                    title=journey_graph.title,
                                    description=journey_graph.description,
                                    conditions=[condition],
                                    agent_id=agent.id,
                                )
                                
                                # 3. 构建Journey的状态和转换
                                await journey_builder.build_journey_from_graph(
                                    journey=journey,
                                    journey_graph=journey_graph,
                                    available_tools=available_tools,
                                )
                                
                                self._logger.info(
                                    f"🚕 create journey successfully: {journey_graph.title}"
                                )
                                
                            except Exception as e:
                                self._logger.error(f"create journey failed: {e}")
                                raise

                    else:
                        self._logger.trace(
                            f"Journey候选但置信度不足 (confidence: {confidence:.2f} < {journey_confidence_threshold}): {guideline_with_metadata.content.condition[:50]}..."
                        )
                        
            except Exception as e:
                self._logger.error(f"process journey conversion failed: {e}")
                continue


async def initialize_agent_factory(container: p.Container) -> None:
    logger = container[p.Logger]
    logger.info("start initializing CustomAgentFactory...")
    
    container[p.AgentFactory] = CustomAgentFactory(
        agent_store=container[AgentStore],
        container=container,
        logger=logger
    )
    
    logger.info("✅ CustomAgentFactory initialized successfully!")