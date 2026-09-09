# -*- coding: utf-8 -*-
"""订单工具集：create_order_tool / query_order_tool / cancel_order_tool

MainAgent 单干与 TradeAgent 派发两条路径共用。写工具只准备确认凭证，不执行用户决议。

注意：本模块不能用 `from __future__ import annotations`（AgentScope schema 生成依赖运行时注解）。
"""
import json

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolChunk

from app.application.usecases.order_usecases import (
    CancelOrderUseCase,
    OrderItemInput,
    PlaceOrderUseCase,
    QueryOrderUseCase,
)
from app.domain.order.address import Address
from app.infrastructure.context import ShoppingContext
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.budget import remember_verified_result


def _ok(payload: dict) -> ToolChunk:
    if payload.get("confirmation_required"):
        remember_verified_result("confirmation", {"pending": True})
    elif payload.get("order_id"):
        remember_verified_result("trade", {"orders": [payload]})
    return ToolChunk(
        content=[TextBlock(type="text", text=json.dumps(payload, ensure_ascii=False))],
        state=ToolResultState.SUCCESS,
    )


def _fail(message: str) -> ToolChunk:
    return ToolChunk(
        content=[TextBlock(type="text", text=f"[error] {message}")],
        state=ToolResultState.ERROR,
    )


def _identity():
    snapshot = ShoppingContext.current()
    if snapshot is None or not snapshot.buyer_id or not snapshot.shopping_session_id:
        raise ValueError("订单操作缺少已绑定的买家与会话身份")
    return snapshot.buyer_id, snapshot.shopping_session_id


def build_create_order_tool(usecase: PlaceOrderUseCase, bus: TradeEventBus):
    async def create_order_tool(
        items: list[dict],
        shipping_address: dict,
    ) -> ToolChunk:
        """准备下单意向的权威确认卡，返回 confirmation_required，不创建订单或扣库存。

        即使买家在对话中说“同意”，也必须等待其点击页面确认卡；模型不能代为确认。
        金额仅含所选商品，不含运费和税费，不代表付款。买家身份由系统会话上下文注入。

        Args:
            items (`list[dict]`):
                订单行列表，每项形如 {"product_id": "P1001", "sku_id": "P1001-S1", "quantity": 1}。
            shipping_address (`dict`):
                收货地址，形如 {"recipient_name": "...", "country": "CN", "state": "...",
                "city": "...", "address_line": "...", "postal_code": "...", "phone": "..."}。
        """
        try:
            buyer_id, session_id = _identity()
        except ValueError as err:
            return _fail(str(err))
        bus.publish(session_id, "tool.invoke", {"tool": "create_order_tool", "args": {"buyer_id": buyer_id, "items": items}})
        try:
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("items 必须是订单行对象列表")
            if not isinstance(shipping_address, dict):
                raise ValueError("shipping_address 必须是地址对象")
            order_items = [
                OrderItemInput(
                    product_id=item["product_id"],
                    sku_id=item["sku_id"],
                    quantity=item.get("quantity", 1),
                )
                for item in items
            ]
            address = Address(
                recipient_name=shipping_address.get("recipient_name", ""),
                country=shipping_address.get("country", ""),
                state=shipping_address.get("state", ""),
                city=shipping_address.get("city", ""),
                address_line=shipping_address.get("address_line", ""),
                postal_code=shipping_address.get("postal_code", ""),
                phone=shipping_address.get("phone", ""),
            )
            result = await usecase.execute(
                buyer_id=buyer_id, session_id=session_id, items=order_items, shipping_address=address,
            )
        except (ValueError, KeyError, TypeError) as err:
            bus.publish(session_id, "tool.result", {"tool": "create_order_tool", "error": str(err)})
            return _fail(str(err))
        bus.publish(session_id, "tool.result", {"tool": "create_order_tool", **result})
        return _ok(result)

    return create_order_tool


def build_query_order_tool(usecase: QueryOrderUseCase, bus: TradeEventBus):
    async def query_order_tool(order_id: str) -> ToolChunk:
        """查询当前买家的订单详情；订单号本身不构成读取权限。

        Args:
            order_id (`str`):
                订单号，如 "GBX-000001"。
        """
        try:
            buyer_id, session_id = _identity()
        except ValueError as err:
            return _fail(str(err))
        bus.publish(session_id, "tool.invoke", {"tool": "query_order_tool", "args": {"order_id": order_id}})
        try:
            snapshot = await usecase.execute(order_id, buyer_id=buyer_id)
        except ValueError as err:
            bus.publish(session_id, "tool.result", {"tool": "query_order_tool", "error": str(err)})
            return _fail(str(err))
        bus.publish(session_id, "tool.result", {"tool": "query_order_tool", "order": snapshot})
        return _ok(snapshot)

    return query_order_tool


def build_cancel_order_tool(usecase: CancelOrderUseCase, bus: TradeEventBus):
    async def cancel_order_tool(order_id: str, reason: str) -> ToolChunk:
        """准备当前买家的订单取消确认卡，不立即取消或回补库存。

        用户必须点击页面确认卡才能执行。自然语言同意不能代替该用户动作。

        Args:
            order_id (`str`):
                订单号，如 "GBX-000001"。
            reason (`str`):
                取消原因，必填。
        """
        try:
            buyer_id, session_id = _identity()
        except ValueError as err:
            return _fail(str(err))
        bus.publish(session_id, "tool.invoke", {"tool": "cancel_order_tool", "args": {"order_id": order_id, "reason": reason}})
        try:
            result = await usecase.execute(order_id, reason, buyer_id=buyer_id, session_id=session_id)
        except ValueError as err:
            bus.publish(session_id, "tool.result", {"tool": "cancel_order_tool", "error": str(err)})
            return _fail(str(err))
        bus.publish(session_id, "tool.result", {"tool": "cancel_order_tool", **result})
        return _ok(result)

    return cancel_order_tool
