# Copyright 2025 Emcie Co Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from abc import ABC, abstractmethod
from parlant.core.agents import Agent, AgentStore
from parlant.core.customers import CustomerId
from parlant.core.loggers import Logger


class AgentFactory(ABC):
    """Agent工厂抽象基类，定义创建Agent的标准接口"""
    
    def __init__(self, agent_store: AgentStore, logger: Logger):
        self._agent_store = agent_store
        self._logger = logger
    
    @abstractmethod
    async def create_agent_for_customer(self, customer_id: CustomerId) -> Agent:
        """为指定客户创建Agent的抽象方法，子类必须实现"""
        pass
