"""按会话与证据引用回查，不把冷历史重新全部灌入模型。"""
import json
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolChunk
from app.infrastructure.context import ShoppingContext
from app.infrastructure.persistence.context_evidence import product_decision_view


def build_conversation_fact_lookup(store):
    async def conversation_fact_lookup(result_ref: str = "", query: str = "", position: int = 0) -> ToolChunk:
        """查询本会话历史证据；未给引用时查最新候选或按关键词回查历史。

        Args:
            result_ref (`str`): 工具返回的 ctx_ 引用，可为空。
            query (`str`): 原文关键词，可为空；不执行模糊事实推断。
            position (`int`): 最新候选中的序号，从1开始；0表示返回候选投影。
        """
        ctx = ShoppingContext.current()
        if ctx is None:
            return ToolChunk(content=[TextBlock(text="[error] 缺少会话身份")], state=ToolResultState.ERROR)
        if position < 0:
            return ToolChunk(content=[TextBlock(text="[error] position 不得小于0")], state=ToolResultState.ERROR)
        if result_ref:
            item = await store.get(ctx.buyer_id, ctx.shopping_session_id, result_ref)
            records = [item] if item else []
        else:
            records = await store.search(ctx.buyer_id, ctx.shopping_session_id, kind="products" if not query else "", query=query, limit=1 if not query else 3)
        for record in records:
            if record["kind"] == "products":
                record["data"] = product_decision_view(record["data"])
                if position:
                    hits = record["data"]["hits"]
                    record["data"]["hits"] = hits[position-1:position]
            elif record["kind"] == "conversation":
                record["data"] = {k: str(v)[:3000] for k, v in record["data"].items()}
            elif record["kind"] == "tool_archive":
                # 任意工具原文只做定点文本片段，不把巨型结果再次注入。
                raw = record["data"].get("text", "")
                start = max(0, raw.find(query) - 200) if query else 0
                record["data"] = {"excerpt": raw[start:start+2400], "truncated": len(raw) > 2400}
        return ToolChunk(content=[TextBlock(text=json.dumps({"records": records, "source": "session_evidence", "historical": True}, ensure_ascii=False))], state=ToolResultState.SUCCESS)
    return conversation_fact_lookup
