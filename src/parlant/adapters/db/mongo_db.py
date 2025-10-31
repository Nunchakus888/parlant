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

from typing import Any, Awaitable, Callable, Optional, Sequence
from bson import CodecOptions
from typing_extensions import Self
from parlant.core.loggers import Logger
from parlant.core.persistence.common import Where
from parlant.core.persistence.document_database import (
    BaseDocument,
    DeleteResult,
    DocumentCollection,
    DocumentDatabase,
    InsertResult,
    TDocument,
    UpdateResult,
)
from pymongo import AsyncMongoClient, ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.asynchronous.collection import AsyncCollection


class MongoDocumentDatabase(DocumentDatabase):
    def __init__(
        self,
        mongo_client: AsyncMongoClient[Any],
        database_name: str,
        logger: Logger,
    ):
        self.mongo_client: AsyncMongoClient[Any] = mongo_client
        self.database_name = database_name

        self._logger = logger

        self._database: Optional[AsyncDatabase[Any]] = None
        self._collections: dict[str, MongoDocumentCollection[Any]] = {}
        
        # 索引配置
        self._index_config = self._get_index_config()
    
    def _get_index_config(self) -> dict[str, list[dict[str, Any]]]:
        """
        索引配置 - 遵循 MongoDB 最佳实践
        
        设计原则（ESR 规则）：
        1. Equality (等值查询) → Sort (排序) → Range (范围查询)
        2. 高选择性字段优先（id > session_id > deleted）
        3. 排序字段方向必须匹配查询（DESC → DESCENDING）
        
        参考文档: docs/database-index-optimization.md
        """
        return {
            # Events - 最高优先级（聊天核心，极高频查询）
            "events": [
                # 主查询：session + correlation_id 精确定位（覆盖 99% 场景）
                {
                    "keys": [
                        ("session_id", ASCENDING),      # E: 必需，高选择性
                        ("correlation_id", ASCENDING),  # E: 关联 ID，高选择性
                        ("deleted", ASCENDING),         # E: 软删除过滤，低选择性
                    ],
                    "name": "idx_session_correlation_deleted",
                    "background": True,
                },
                # 范围查询：session + offset 分页/增量查询
                {
                    "keys": [
                        ("session_id", ASCENDING),      # E: 等值查询
                        ("offset", ASCENDING),          # R: 范围查询（>= min_offset）
                    ],
                    "name": "idx_session_offset",
                    "background": True,
                },
                # 来源过滤：按 EventSource 过滤（如只看 AI 回复）
                {
                    "keys": [
                        ("session_id", ASCENDING),      # E: 高选择性
                        ("source", ASCENDING),          # E: 中等选择性（6种）
                        ("deleted", ASCENDING),         # E: 低选择性
                    ],
                    "name": "idx_session_source_deleted",
                    "background": True,
                },
                # 类型过滤：按 EventKind 过滤（如只看消息）
                {
                    "keys": [
                        ("session_id", ASCENDING),      # E: 高选择性
                        ("kind", ASCENDING),            # E: 中等选择性（4种）
                        ("deleted", ASCENDING),         # E: 低选择性
                    ],
                    "name": "idx_session_kind_deleted",
                    "background": True,
                },
            ],
            
            # Sessions - 高频（会话管理）
            "sessions": [
                # 主键：唯一索引，确保数据完整性
                {
                    "keys": [("id", ASCENDING)],
                    "name": "idx_id",
                    "unique": True,
                    "background": True,
                },
                # Agent 列表：查询 + 时间降序排序（最新在前）
                {
                    "keys": [
                        ("agent_id", ASCENDING),        # E: 过滤 Agent
                        ("updated_utc", DESCENDING),    # S: 降序排序（查询用 DESC）
                    ],
                    "name": "idx_agent_updated",
                    "background": True,
                },
                # Customer 列表：查询 + 时间降序排序
                {
                    "keys": [
                        ("customer_id", ASCENDING),     # E: 过滤 Customer
                        ("updated_utc", DESCENDING),    # S: 降序排序
                    ],
                    "name": "idx_customer_updated",
                    "background": True,
                },
                # 多租户：SaaS 场景的租户隔离
                {
                    "keys": [
                        ("tenant_id", ASCENDING),       # E: 租户隔离
                        ("chatbot_id", ASCENDING),      # E: 机器人过滤
                    ],
                    "name": "idx_tenant_chatbot",
                    "background": True,
                },
            ],
            
            # Customers - 中频（用户信息查询）
            "customers": [
                {
                    "keys": [("id", ASCENDING)],
                    "name": "idx_id",
                    "unique": True,
                    "background": True,
                },
            ],
            
            # Customer Tag Associations - 中频（标签查询）
            "customer_tag_associations": [
                # 双向查询：customer → tags
                {
                    "keys": [("customer_id", ASCENDING)],
                    "name": "idx_customer",
                    "background": True,
                },
                # 双向查询：tag → customers
                {
                    "keys": [("tag_id", ASCENDING)],
                    "name": "idx_tag",
                    "background": True,
                },
                # 唯一约束：防止重复关联
                {
                    "keys": [
                        ("customer_id", ASCENDING),
                        ("tag_id", ASCENDING),
                    ],
                    "name": "idx_customer_tag_unique",
                    "unique": True,
                    "background": True,
                },
            ],
            
            # Guidelines - 高频（AI 决策核心）
            # "guidelines": [
            #     {
            #         "keys": [("id", ASCENDING)],
            #         "name": "idx_id",
            #         "unique": True,
            #         "background": True,
            #     },
            #     # 启用状态：常用于过滤 enabled 的指南
            #     {
            #         "keys": [("enabled", ASCENDING)],
            #         "name": "idx_enabled",
            #         "background": True,
            #     },
            # ],
            
            # Guideline Tag Associations - 高频（指南匹配）
            # "guideline_tag_associations": [
            #     # 双向查询：guideline → tags
            #     {
            #         "keys": [("guideline_id", ASCENDING)],
            #         "name": "idx_guideline",
            #         "background": True,
            #     },
            #     # 双向查询：tag → guidelines
            #     {
            #         "keys": [("tag_id", ASCENDING)],
            #         "name": "idx_tag",
            #         "background": True,
            #     },
            #     # 唯一约束：防止重复关联
            #     {
            #         "keys": [
            #             ("guideline_id", ASCENDING),
            #             ("tag_id", ASCENDING),
            #         ],
            #         "name": "idx_guideline_tag_unique",
            #         "unique": True,
            #         "background": True,
            #     },
            # ],
            
            # Inspections - 中频（调试和监控）
            "inspections": [
                # 按会话查询
                {
                    "keys": [("session_id", ASCENDING)],
                    "name": "idx_session",
                    "background": True,
                },
                # 按关联 ID 查询（精确查找）
                {
                    "keys": [("correlation_id", ASCENDING)],
                    "name": "idx_correlation",
                    "background": True,
                },
                # 复合查询：会话 + 关联 ID
                {
                    "keys": [
                        ("session_id", ASCENDING),
                        ("correlation_id", ASCENDING),
                    ],
                    "name": "idx_session_correlation",
                    "background": True,
                },
            ],
        }
    
    async def _ensure_indexes(self, collection_name: str, collection: AsyncCollection[Any]) -> None:
        """
        确保集合索引已创建（幂等操作，可重复执行）
        
        - 使用 background=True 避免阻塞数据库
        - 索引已存在时会静默跳过
        - 创建失败不影响应用启动
        """
        if collection_name not in self._index_config:
            return
        
        for spec in self._index_config[collection_name]:
            try:
                await collection.create_index(
                    spec["keys"],
                    name=spec.get("name"),
                    unique=spec.get("unique", False),
                    background=spec.get("background", True),
                )
                self._logger.info(f"✅ Index '{spec.get('name')}' ready on '{collection_name}'")
                
            except Exception as e:
                # 索引已存在是正常情况，不记录警告
                error_msg = str(e).lower()
                if "already exists" in error_msg or "index with name" in error_msg:
                    self._logger.debug(f"Index '{spec.get('name')}' exists on '{collection_name}'")
                else:
                    # 其他错误（如权限、重复数据等）记录但不中断
                    self._logger.warning(
                        f"⚠️  Index '{spec.get('name')}' on '{collection_name}' failed: {e}"
                    )

    async def create_collection(
        self,
        name: str,
        schema: type[TDocument],
    ) -> DocumentCollection[TDocument]:
        if self._database is None:
            raise Exception("underlying database missing.")

        mongo_collection = await self._database.create_collection(
            name=name,
            codec_options=CodecOptions(document_class=schema),
        )
        
        # 确保索引
        await self._ensure_indexes(name, mongo_collection)
        
        self._collections[name] = MongoDocumentCollection(
            self,
            mongo_collection,
        )
        return self._collections[name]

    async def get_collection(
        self,
        name: str,
        schema: type[TDocument],
        document_loader: Callable[[BaseDocument], Awaitable[TDocument | None]],
    ) -> DocumentCollection[TDocument]:
        if self._database is None:
            raise Exception("underlying database missing.")

        result_collection = self._database.get_collection(
            name=name,
            codec_options=CodecOptions(document_class=schema),
        )

        failed_migrations_collection_name = f"{self.database_name}_{name}_failed_migrations"
        collection_existing_documents = result_collection.find({})
        if failed_migrations_collection_name in await self._database.list_collection_names():
            self._logger.info(f"deleting old `{failed_migrations_collection_name}` collection")
            await self.delete_collection(failed_migrations_collection_name)

        failed_migration_collection: Optional[DocumentCollection[TDocument]] = None
        for doc in await collection_existing_documents.to_list():
            try:
                if loaded_doc := await document_loader(doc):
                    await result_collection.replace_one(doc, loaded_doc)
                    continue

                if failed_migration_collection is None:
                    self._logger.warning(
                        f"creating: `{failed_migrations_collection_name}` collection to store failed migrations..."
                    )
                    failed_migration_collection = await self.create_collection(
                        failed_migrations_collection_name, schema
                    )

                self._logger.warning(f'failed to load document "{doc}"')
                await failed_migration_collection.insert_one(doc)
                await result_collection.delete_one(doc)
            except Exception as e:
                if failed_migration_collection is None:
                    self._logger.warning(
                        f"creating: `{failed_migrations_collection_name}` collection to store failed migrations..."
                    )
                    failed_migration_collection = await self.create_collection(
                        failed_migrations_collection_name, schema
                    )

                self._logger.error(
                    f"failed to load document '{doc}' with error: {e}. Added to `{failed_migrations_collection_name}` collection."
                )
                await failed_migration_collection.insert_one(doc)

        # 确保索引
        await self._ensure_indexes(name, result_collection)
        
        self._collections[name] = MongoDocumentCollection(self, result_collection)
        return self._collections[name]

    async def get_or_create_collection(
        self,
        name: str,
        schema: type[TDocument],
        document_loader: Callable[[BaseDocument], Awaitable[TDocument | None]],
    ) -> DocumentCollection[TDocument]:
        return await self.get_collection(name, schema, document_loader)

    async def delete_collection(self, name: str) -> None:
        if self._database is None:
            raise Exception("underlying database missing.")

        await self._database.drop_collection(name)

    async def __aenter__(self) -> Self:
        self._database = self.mongo_client[self.database_name]
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[object],
    ) -> bool:
        if self._database is not None:
            self._database = None

        return False


class MongoDocumentCollection(DocumentCollection[TDocument]):
    def __init__(
        self,
        mongo_document_database: MongoDocumentDatabase,
        mongo_collection: AsyncCollection[TDocument],
    ) -> None:
        self._database = mongo_document_database
        self._collection = mongo_collection

    async def find(
        self, 
        filters: Where,
        sort: Optional[list[tuple[str, int]]] = None,
    ) -> Sequence[TDocument]:
        mongo_cursor = self._collection.find(filters)
        
        # Apply sorting at database level if specified
        if sort:
            mongo_cursor = mongo_cursor.sort(sort)
        
        result = await mongo_cursor.to_list()
        await mongo_cursor.close()
        return result

    async def find_one(self, filters: Where) -> TDocument | None:
        result = await self._collection.find_one(filters)
        return result

    async def insert_one(self, document: TDocument) -> InsertResult:
        insert_result = await self._collection.insert_one(document)
        return InsertResult(acknowledged=insert_result.acknowledged)

    async def update_one(
        self,
        filters: Where,
        params: TDocument,
        upsert: bool = False,
    ) -> UpdateResult[TDocument]:
        update_result = await self._collection.update_one(filters, {"$set": params}, upsert)
        result_document = await self._collection.find_one(filters)
        return UpdateResult[TDocument](
            update_result.acknowledged,
            update_result.matched_count,
            update_result.modified_count,
            result_document,
        )

    async def delete_one(self, filters: Where) -> DeleteResult[TDocument]:
        result_document = await self._collection.find_one(filters)
        if result_document is None:
            return DeleteResult(True, 0, None)

        delete_result = await self._collection.delete_one(filters)
        return DeleteResult(
            delete_result.acknowledged,
            deleted_count=delete_result.deleted_count,
            deleted_document=result_document,
        )

    async def delete_one_from_memory_only(self, filters: Where) -> DeleteResult[TDocument]:
        """删除内存中的文档（对于 MongoDB，此操作不适用，因为数据在远程数据库）"""
        # 这个方法主要用于 JSONFile 存储的内存管理
        # 对于 MongoDB，我们简单返回一个空结果，表示不执行任何操作
        return DeleteResult(
            acknowledged=True,
            deleted_count=0,
            deleted_document=None,
        )
