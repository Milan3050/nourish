"""Per-user documents: run history, saved plans, calendar entries and goals.

One table of JSON documents keyed by (user_id, kind) keeps the MVP small; every query is scoped to the
authenticated user. A production schema would normalise these (see docs/deployment.md).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg

ITEMS_SQL = """CREATE TABLE IF NOT EXISTS user_items (
  id text PRIMARY KEY, user_id text NOT NULL, kind text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), data jsonb NOT NULL);
CREATE INDEX IF NOT EXISTS user_items_user_kind ON user_items (user_id, kind, created_at DESC)"""

KINDS = {"run", "plan", "calendar", "goal"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """Postgres when a connection is given, otherwise in memory (tests, no-DB dev)."""

    def __init__(self, db: psycopg.AsyncConnection | None = None):
        self.db = db
        self.mem: dict[str, dict] = {}

    async def setup(self) -> None:
        if self.db:
            for stmt in ITEMS_SQL.split(";"):
                await self.db.execute(stmt)

    async def add(self, user: str, kind: str, data: dict[str, Any]) -> dict:
        assert kind in KINDS
        doc = {**data, "id": data.get("id") or uuid.uuid4().hex[:12], "created_at": data.get("created_at") or _now()}
        if self.db:
            await self.db.execute("INSERT INTO user_items (id, user_id, kind, data) VALUES (%s, %s, %s, %s)",
                                  (doc["id"], user, kind, json.dumps(doc, default=str)))
        else:
            self.mem[doc["id"]] = {"user": user, "kind": kind, "data": doc}
        return doc

    async def list(self, user: str, kind: str, limit: int = 200) -> list[dict]:
        if self.db:
            rows = await (await self.db.execute(
                "SELECT data FROM user_items WHERE user_id=%s AND kind=%s ORDER BY created_at DESC LIMIT %s",
                (user, kind, limit))).fetchall()
            return [r[0] for r in rows]
        docs = [v["data"] for v in self.mem.values() if v["user"] == user and v["kind"] == kind]
        return sorted(docs, key=lambda d: d["created_at"], reverse=True)[:limit]

    async def get(self, user: str, kind: str, item_id: str) -> dict | None:
        if self.db:
            row = await (await self.db.execute(
                "SELECT data FROM user_items WHERE user_id=%s AND kind=%s AND id=%s", (user, kind, item_id))).fetchone()
            return row[0] if row else None
        v = self.mem.get(item_id)
        return v["data"] if v and v["user"] == user and v["kind"] == kind else None

    async def update(self, user: str, kind: str, item_id: str, changes: dict) -> dict | None:
        doc = await self.get(user, kind, item_id)
        if doc is None:
            return None
        doc = {**doc, **changes, "id": item_id}
        if self.db:
            await self.db.execute("UPDATE user_items SET data=%s WHERE user_id=%s AND kind=%s AND id=%s",
                                  (json.dumps(doc, default=str), user, kind, item_id))
        else:
            self.mem[item_id]["data"] = doc
        return doc

    async def delete(self, user: str, kind: str, item_id: str) -> bool:
        if self.db:
            cur = await self.db.execute("DELETE FROM user_items WHERE user_id=%s AND kind=%s AND id=%s",
                                        (user, kind, item_id))
            return cur.rowcount > 0
        v = self.mem.get(item_id)
        if v and v["user"] == user and v["kind"] == kind:
            del self.mem[item_id]
            return True
        return False
