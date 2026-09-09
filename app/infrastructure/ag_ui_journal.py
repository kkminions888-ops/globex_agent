# -*- coding: utf-8 -*-
"""AG-UI 持久事件日志：SQLite 事务内追加序号、更新投影并绑定买家归属。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import aiosqlite


class JournalConflict(ValueError):
    pass


class JournalForbidden(PermissionError):
    pass


class JournalNotFound(LookupError):
    pass


class JournalLeaseLost(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class AGUIJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self):
        async with self._init_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 初始化短等待；总体期限避免 busy_timeout × 重试次数形成长时间挂起。
            deadline = time.monotonic() + 8.0
            for attempt in range(20):
                try:
                    # 每次失败都关闭连接，不能带着上一轮未完成游标/事务重试。
                    async with aiosqlite.connect(self.path, timeout=0.5) as db:
                        async with db.execute("PRAGMA busy_timeout=500") as cursor:
                            await cursor.fetchall()
                        # PRAGMA 会返回结果行；显式耗尽并关闭，避免游标持锁阻碍后续 DDL。
                        async with db.execute("PRAGMA journal_mode=WAL") as cursor:
                            await cursor.fetchall()
                        await db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS agui_sessions (
                  session_id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL, title TEXT NOT NULL,
                  messages_json TEXT NOT NULL, state_json TEXT NOT NULL, last_run_id TEXT NOT NULL,
                  updated_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS agui_buyer_sessions ON agui_sessions(buyer_id,updated_at);
                CREATE TABLE IF NOT EXISTS agui_runs (
                  run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, buyer_id TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, input_json TEXT NOT NULL, projection_json TEXT NOT NULL,
                  status TEXT NOT NULL, owner TEXT NOT NULL, lease_until REAL NOT NULL,
                  last_seq INTEGER NOT NULL DEFAULT 0, stop_requested INTEGER NOT NULL DEFAULT 0,
                  created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS agui_run_session ON agui_runs(session_id,status);
                CREATE TABLE IF NOT EXISTS agui_events (
                  run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT NOT NULL,
                  PRIMARY KEY(run_id,seq));
                COMMIT;
                        """)
                    self._initialized = True
                    return
                except aiosqlite.OperationalError as err:
                    if (not any(marker in str(err).lower() for marker in ("locked", "busy"))
                            or attempt == 19 or time.monotonic() >= deadline):
                        raise
                    await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))

    @asynccontextmanager
    async def _db(self, write=False):
        await self.initialize()
        async with aiosqlite.connect(self.path, timeout=10) as db:
            db.row_factory = aiosqlite.Row
            if write:
                await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                if write:
                    await db.commit()
            except BaseException:
                if write:
                    await db.rollback()
                raise

    @staticmethod
    async def _one(db, sql, params=()):
        async with db.execute(sql, params) as cursor:
            return await cursor.fetchone()

    @staticmethod
    def _owned(row, buyer_id, session_id=None):
        if row is None:
            raise JournalNotFound("运行或会话不存在")
        if row["buyer_id"] != buyer_id or (session_id is not None and row["session_id"] != session_id):
            raise JournalForbidden("无权访问该买家的会话或运行")
        return row

    @staticmethod
    def _run(row):
        projection = json.loads(row["projection_json"])
        return {"runId": row["run_id"], "threadId": row["session_id"], "status": row["status"],
                "cursor": row["last_seq"], "stopRequested": bool(row["stop_requested"]),
                "input": json.loads(row["input_json"]), "messages": projection["messages"],
                "state": projection["state"], "updatedAt": int(row["updated_at"] * 1000)}

    async def reserve(self, body: dict, buyer_id: str, owner: str, lease_seconds=30) -> tuple[dict, bool]:
        now, run_id, session_id = time.time(), body["runId"], body["threadId"]
        user = body["messages"][-1]
        props = body.get("forwardedProps") or {}
        identity = {"threadId": session_id, "buyerId": buyer_id, "message": user,
                    "locale": props.get("locale", "zh-CN"), "currency": props.get("currency", "CNY")}
        # 未选择时保持旧运行指纹；有明确选择时版本/hash均属于本次请求身份。
        if "selectedSkill" in props:
            identity["selectedSkill"] = props["selectedSkill"]
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        async with self._db(True) as db:
            await self._recover(db, now)
            previous = await self._one(db, "SELECT * FROM agui_runs WHERE run_id=?", (run_id,))
            if previous:
                self._owned(previous, buyer_id, session_id)
                if previous["fingerprint"] != fingerprint:
                    raise JournalConflict("相同 runId 已用于不同请求")
                return self._run(previous), False
            session = await self._one(db, "SELECT * FROM agui_sessions WHERE session_id=?", (session_id,))
            if session:
                self._owned(session, buyer_id)
            running = await self._one(db, "SELECT run_id FROM agui_runs WHERE session_id=? AND status='running'", (session_id,))
            if running:
                raise JournalConflict("该会话仍有执行中的运行，请先恢复或明确停止它")
            messages = json.loads(session["messages_json"]) if session else []
            # 客户端历史/state 不是事实来源；只接受本轮 user，旧历史由日志恢复。
            messages = [*messages[-99:], {"id": user["id"], "role": "user", "content": user["content"]}]
            body = {**body, "messages": messages, "state": {}}
            projection = {"messages": messages, "state": {}, "openMessages": [], "openTools": []}
            await db.execute("INSERT INTO agui_runs(run_id,session_id,buyer_id,fingerprint,input_json,projection_json,status,owner,lease_until,created_at,updated_at) VALUES(?,?,?,?,?,?,'running',?,?,?,?)",
                (run_id, session_id, buyer_id, fingerprint, _json(body), _json(projection), owner, now + lease_seconds, now, now))
            title = session["title"] if session else str(user["content"])[:48]
            await db.execute("INSERT INTO agui_sessions VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET messages_json=excluded.messages_json,state_json=excluded.state_json,last_run_id=excluded.last_run_id,updated_at=excluded.updated_at",
                (session_id, buyer_id, title, _json(messages), "{}", run_id, now))
            return self._run(await self._one(db, "SELECT * FROM agui_runs WHERE run_id=?", (run_id,))), True

    @staticmethod
    def _project(projection, event):
        kind = event["type"]
        if kind == "MESSAGES_SNAPSHOT":
            projection["messages"] = [m for m in event["messages"] if m.get("role") in {"user", "assistant"}]
        elif kind == "STATE_SNAPSHOT":
            projection["state"] = event["snapshot"]
        elif kind == "TEXT_MESSAGE_START":
            message_id = event["messageId"]
            if not any(m["id"] == message_id for m in projection["messages"]):
                projection["messages"].append({"id": message_id, "role": "assistant", "content": ""})
            if message_id not in projection["openMessages"]:
                projection["openMessages"].append(message_id)
        elif kind == "TEXT_MESSAGE_CONTENT":
            message = next((m for m in projection["messages"] if m["id"] == event["messageId"]), None)
            if message is None:
                raise JournalConflict("消息增量必须在对应 START 之后")
            message["content"] += event["delta"]
        elif kind == "TEXT_MESSAGE_END":
            projection["openMessages"] = [i for i in projection["openMessages"] if i != event["messageId"]]
        elif kind == "TOOL_CALL_START":
            projection["openTools"].append(event["toolCallId"])
        elif kind == "TOOL_CALL_END":
            projection["openTools"] = [i for i in projection["openTools"] if i != event["toolCallId"]]

    async def _append(self, db, row, events):
        projection, seq, status = json.loads(row["projection_json"]), row["last_seq"], row["status"]
        for event in events:
            self._project(projection, event)
            seq += 1
            await db.execute("INSERT INTO agui_events VALUES(?,?,?)", (row["run_id"], seq, _json(event)))
            if event["type"] == "RUN_FINISHED":
                status = "completed"
            elif event["type"] == "RUN_ERROR":
                status = "stopped" if event.get("code") == "CANCELLED" else "interrupted" if event.get("code") == "SERVER_RESTART" else "error"
        now = time.time()
        await db.execute("UPDATE agui_runs SET projection_json=?,last_seq=?,status=?,updated_at=? WHERE run_id=?",
                         (_json(projection), seq, status, now, row["run_id"]))
        await db.execute("UPDATE agui_sessions SET messages_json=?,state_json=?,updated_at=? WHERE session_id=? AND last_run_id=?",
                         (_json(projection["messages"][-100:]), _json(projection["state"]), now, row["session_id"], row["run_id"]))

    async def append(self, run_id, owner, events):
        async with self._db(True) as db:
            row = await self._one(db, "SELECT * FROM agui_runs WHERE run_id=?", (run_id,))
            if not row or row["owner"] != owner or row["status"] != "running" or row["lease_until"] <= time.time():
                raise JournalLeaseLost("运行日志执行租约已失效")
            await self._append(db, row, events)

    async def renew(self, run_id, owner, lease_seconds):
        async with self._db(True) as db:
            cursor = await db.execute("UPDATE agui_runs SET lease_until=? WHERE run_id=? AND owner=? AND status='running' AND stop_requested=0 AND lease_until>?",
                (time.time() + lease_seconds, run_id, owner, time.time()))
            return cursor.rowcount == 1

    async def _recover(self, db, now):
        async with db.execute("SELECT * FROM agui_runs WHERE status='running' AND lease_until<=?", (now,)) as cursor:
            expired = await cursor.fetchall()
        for row in expired:
            projection = json.loads(row["projection_json"])
            stopped = bool(row["stop_requested"])
            events = [{"type": "TEXT_MESSAGE_END", "messageId": i} for i in projection["openMessages"]]
            if row["last_seq"] == 0:
                events.insert(0, {"type": "RUN_STARTED", "threadId": row["session_id"], "runId": row["run_id"]})
            events += [{"type": "TOOL_CALL_END", "toolCallId": i} for i in projection["openTools"]]
            events += [{"type": "STATE_SNAPSHOT", "snapshot": {**projection["state"], "status": "cancelled" if stopped else "error"}},
                       {"type": "RUN_ERROR", "message": "本轮已明确停止" if stopped else "服务重启或执行租约失效，本轮未完成；已恢复保存的内容，可重新提交需求。", "code": "CANCELLED" if stopped else "SERVER_RESTART"}]
            await self._append(db, row, events)

    async def recover_expired(self):
        async with self._db(True) as db:
            await self._recover(db, time.time())

    async def run(self, run_id, buyer_id, session_id=None):
        async with self._db(True) as db:
            await self._recover(db, time.time())
            return self._run(self._owned(await self._one(db, "SELECT * FROM agui_runs WHERE run_id=?", (run_id,)), buyer_id, session_id))

    async def events(self, run_id, buyer_id, after=0, limit=200):
        async with self._db(True) as db:
            await self._recover(db, time.time())
            row = self._owned(await self._one(db, "SELECT * FROM agui_runs WHERE run_id=?", (run_id,)), buyer_id)
            if after < 0 or after > row["last_seq"]:
                raise JournalConflict("事件游标超出该运行范围")
            async with db.execute("SELECT seq,event_json FROM agui_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", (run_id, after, limit)) as cursor:
                events = [{"seq": item["seq"], "event": json.loads(item["event_json"])} for item in await cursor.fetchall()]
            return events, row["status"], row["last_seq"]

    async def request_stop(self, run_id, buyer_id):
        async with self._db(True) as db:
            row = self._owned(await self._one(db, "SELECT * FROM agui_runs WHERE run_id=?", (run_id,)), buyer_id)
            if row["status"] == "running":
                await db.execute("UPDATE agui_runs SET stop_requested=1 WHERE run_id=?", (run_id,))
        return await self.run(run_id, buyer_id)

    async def end_owned(self, run_id, owner, *, stopped=False):
        """生产协程在首次调度前就被取消时也要收口；终态不覆盖，其他 owner 不可写。"""
        async with self._db(True) as db:
            row = await self._one(db, "SELECT * FROM agui_runs WHERE run_id=?", (run_id,))
            if not row or row["owner"] != owner or row["status"] != "running":
                return
            projection = json.loads(row["projection_json"])
            events = [{"type": "TEXT_MESSAGE_END", "messageId": i} for i in projection["openMessages"]]
            if row["last_seq"] == 0:
                events.insert(0, {"type": "RUN_STARTED", "threadId": row["session_id"], "runId": row["run_id"]})
            events += [{"type": "TOOL_CALL_END", "toolCallId": i} for i in projection["openTools"]]
            events += [{"type": "STATE_SNAPSHOT", "snapshot": {**projection["state"], "status": "cancelled" if stopped else "error"}},
                       {"type": "RUN_ERROR", "message": "本轮已明确停止" if stopped else "服务已关闭，本轮已保存为中断，可恢复查看已有内容。", "code": "CANCELLED" if stopped else "SERVER_RESTART"}]
            await self._append(db, row, events)

    async def sessions(self, buyer_id):
        async with self._db(True) as db:
            await self._recover(db, time.time())
            async with db.execute("SELECT session_id,title,updated_at,last_run_id FROM agui_sessions WHERE buyer_id=? ORDER BY updated_at DESC LIMIT 100", (buyer_id,)) as cursor:
                return [{"id": row["session_id"], "title": row["title"], "updatedAt": int(row["updated_at"] * 1000), "runId": row["last_run_id"], "source": "server"} for row in await cursor.fetchall()]

    async def session(self, session_id, buyer_id):
        async with self._db(True) as db:
            await self._recover(db, time.time())
            row = self._owned(await self._one(db, "SELECT * FROM agui_sessions WHERE session_id=?", (session_id,)), buyer_id)
            run = self._run(await self._one(db, "SELECT * FROM agui_runs WHERE run_id=?", (row["last_run_id"],)))
            return {"id": session_id, "title": row["title"], "messages": json.loads(row["messages_json"]),
                    "state": json.loads(row["state_json"]), "run": run, "updatedAt": int(row["updated_at"] * 1000)}
