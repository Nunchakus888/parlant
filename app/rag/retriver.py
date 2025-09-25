from parlant.core.capabilities import RetrieverContext, RetrieverResult
import parlant.sdk as p

async def answer_retriever(context: p.RetrieverContext) -> p.RetrieverResult:
    
    return p.RetrieverResult(None)

# await agent.attach_retriever(my_retriever, id="my_retriever")