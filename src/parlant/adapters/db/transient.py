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

from __future__ import annotations
import sys
from typing import Awaitable, Callable, Optional, Sequence, cast
from typing_extensions import override
from typing_extensions import get_type_hints

from parlant.core.persistence.common import matches_filters, Where, ObjectId, ensure_is_total
from parlant.core.persistence.document_database import (
    BaseDocument,
    DeleteResult,
    DocumentCollection,
    DocumentDatabase,
    InsertResult,
    TDocument,
    UpdateResult,
)


class TransientDocumentDatabase(DocumentDatabase):
    def __init__(self) -> None:
        self._collections: dict[str, TransientDocumentCollection[BaseDocument]] = {}

    @override
    async def create_collection(
        self,
        name: str,
        schema: type[TDocument],
    ) -> TransientDocumentCollection[TDocument]:
        annotations = get_type_hints(schema)
        assert "id" in annotations and annotations["id"] == ObjectId

        self._collections[name] = TransientDocumentCollection(
            name=name,
            schema=schema,
        )

        return cast(TransientDocumentCollection[TDocument], self._collections[name])

    @override
    async def get_collection(
        self,
        name: str,
        schema: type[TDocument],
        document_loader: Callable[[BaseDocument], Awaitable[Optional[TDocument]]],
    ) -> TransientDocumentCollection[TDocument]:
        if name in self._collections:
            return cast(TransientDocumentCollection[TDocument], self._collections[name])
        raise ValueError(f'Collection "{name}" does not exist')

    @override
    async def get_or_create_collection(
        self,
        name: str,
        schema: type[TDocument],
        document_loader: Callable[[BaseDocument], Awaitable[Optional[TDocument]]],
    ) -> TransientDocumentCollection[TDocument]:
        if collection := self._collections.get(name):
            return cast(TransientDocumentCollection[TDocument], collection)

        annotations = get_type_hints(schema)
        assert "id" in annotations and annotations["id"] == ObjectId

        return await self.create_collection(
            name=name,
            schema=schema,
        )

    @override
    async def delete_collection(
        self,
        name: str,
    ) -> None:
        if name in self._collections:
            del self._collections[name]
        else:
            raise ValueError(f'Collection "{name}" does not exist')


class TransientDocumentCollection(DocumentCollection[TDocument]):
    def __init__(
        self,
        name: str,
        schema: type[TDocument],
        data: Optional[Sequence[TDocument]] = None,
    ) -> None:
        self._name = name
        self._schema = schema
        self._documents = list(data) if data else []
    
    def get_memory_stats(self) -> dict:
        """获取内存使用统计"""
        try:
            doc_count = len(self._documents)
            if doc_count == 0:
                return {"count": 0, "estimated_size_kb": 0, "actual_size_kb": 0}
            
            # 估算内存使用
            estimated_size = doc_count * 2  # 假设每个文档平均2KB
            
            # 实际内存使用（更精确）
            actual_size = sys.getsizeof(self._documents)
            for doc in self._documents:
                actual_size += sys.getsizeof(doc)
                if hasattr(doc, '__dict__'):
                    actual_size += sys.getsizeof(doc.__dict__)
            
            return {
                "count": doc_count,
                "estimated_size_kb": estimated_size,
                "actual_size_kb": actual_size / 1024
            }
        except Exception:
            return {"count": 0, "estimated_size_kb": 0, "actual_size_kb": 0}

    @override
    async def find(
        self,
        filters: Where,
        sort: Optional[list[tuple[str, int]]] = None,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Sequence[TDocument]:
        result = []
        for doc in filter(
            lambda d: matches_filters(filters, d),
            self._documents,
        ):
            result.append(doc)

        # Apply sorting in memory if specified
        if sort:
            for field, direction in reversed(sort):
                result.sort(
                    key=lambda d: d.get(field, 0),
                    reverse=(direction == -1)
                )

        # Apply pagination in memory
        if skip is not None:
            result = result[skip:]
        if limit is not None:
            result = result[:limit]

        return result

    @override
    async def find_one(
        self,
        filters: Where,
    ) -> Optional[TDocument]:
        for doc in self._documents:
            if matches_filters(filters, doc):
                return doc

        return None

    @override
    async def insert_one(
        self,
        document: TDocument,
    ) -> InsertResult:
        ensure_is_total(document, self._schema)

        self._documents.append(document)

        return InsertResult(acknowledged=True)

    @override
    async def update_one(
        self,
        filters: Where,
        params: TDocument,
        upsert: bool = False,
    ) -> UpdateResult[TDocument]:
        for i, d in enumerate(self._documents):
            if matches_filters(filters, d):
                self._documents[i] = cast(TDocument, {**self._documents[i], **params})

                return UpdateResult(
                    acknowledged=True,
                    matched_count=1,
                    modified_count=1,
                    updated_document=self._documents[i],
                )

        if upsert:
            await self.insert_one(params)

            return UpdateResult(
                acknowledged=True,
                matched_count=0,
                modified_count=0,
                updated_document=params,
            )

        return UpdateResult(
            acknowledged=True,
            matched_count=0,
            modified_count=0,
            updated_document=None,
        )

    @override
    async def delete_one(
        self,
        filters: Where,
    ) -> DeleteResult[TDocument]:
        for i, d in enumerate(self._documents):
            if matches_filters(filters, d):
                document = self._documents.pop(i)

                return DeleteResult(deleted_count=1, acknowledged=True, deleted_document=document)

        return DeleteResult(
            acknowledged=True,
            deleted_count=0,
            deleted_document=None,
        )

    @override
    async def delete_one_from_memory_only(
        self,
        filters: Where,
    ) -> DeleteResult[TDocument]:
        """删除内存中的文档（对于 Transient 存储，等同于 delete_one）"""
        # Transient 存储本身就只在内存中，所以直接调用 delete_one
        return await self.delete_one(filters)

    @override
    async def count(
        self,
        filters: Where,
    ) -> int:
        """计数匹配的文档"""
        return sum(1 for d in self._documents if matches_filters(filters, d))
