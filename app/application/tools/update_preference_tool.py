"""长期记忆修改：按原文原子替换，身份只取当前上下文。"""
from typing import Literal
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolChunk
from app.domain.buyer.preference import BuyerPreference
from app.infrastructure.context import ShoppingContext


def build_update_preference_tool(store, bus):
    async def update_preference_tool(previous_statement: str, kind: Literal["like","dislike"], statement: str) -> ToolChunk:
        """买家明确要求长期修改偏好时，原子替换旧偏好。本轮临时例外不要保存。

        Args:
            previous_statement (`str`): 当前 buyer-preferences 中旧偏好的完整原文。
            kind (`str`): 新偏好类别，like 为喜欢，dislike 为避免。
            statement (`str`): 新偏好原文，最多 500 字符。
        """
        snapshot=ShoppingContext.current()
        if snapshot is None:
            return ToolChunk(content=[TextBlock(type="text",text="[error] 缺少可信买家上下文")],state=ToolResultState.ERROR)
        session=snapshot.shopping_session_id
        bus.publish(session,"tool.invoke",{"tool":"update_preference_tool","args":{"previous_statement":previous_statement,"kind":kind,"statement":statement}})
        try:
            updated=await store.replace(snapshot.buyer_id,previous_statement,BuyerPreference(snapshot.buyer_id,kind,statement))
            if not updated:
                remaining=await store.list_by_buyer(snapshot.buyer_id)
                text="原偏好未找到，没有修改。请按当前原文重试："+ "\n".join(p.statement for p in remaining)
            else:
                text=f"已更新长期偏好：[{kind}] {statement}。旧条目已替换，下一轮按新偏好加载。"
            bus.publish(session,"tool.result",{"tool":"update_preference_tool","updated":updated})
            return ToolChunk(content=[TextBlock(type="text",text=text)],state=ToolResultState.SUCCESS)
        except Exception:
            bus.publish(session,"tool.result",{"tool":"update_preference_tool","error":"偏好修改失败"})
            return ToolChunk(content=[TextBlock(type="text",text="[error] 偏好修改失败，未确认保存，请重试")],state=ToolResultState.ERROR)
    return update_preference_tool
