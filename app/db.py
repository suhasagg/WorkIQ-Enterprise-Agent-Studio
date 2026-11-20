"""Async PostgreSQL access (psycopg 3 pool) shared by the API, graph runtime and demo MCP servers."""
from __future__ import annotations

import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import settings

J = Jsonb
_pool: AsyncConnectionPool | None = None
_cp_pool: AsyncConnectionPool | None = None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


async def init_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            settings.database_url, min_size=1, max_size=12, open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        await _pool.open(wait=True, timeout=30)
    return _pool


async def checkpoint_pool() -> AsyncConnectionPool:
    """Dedicated pool for the LangGraph checkpointer (needs autocommit + no prepared statements)."""
    global _cp_pool
    if _cp_pool is None:
        _cp_pool = AsyncConnectionPool(
            settings.database_url, min_size=1, max_size=6, open=False,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        )
        await _cp_pool.open(wait=True, timeout=30)
    return _cp_pool


async def close_pools() -> None:
    global _pool, _cp_pool
    for p in (_pool, _cp_pool):
        if p is not None:
            await p.close()
    _pool = _cp_pool = None


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("database pool not initialised")
    return _pool


async def fetch_all(sql: str, params: Any = None) -> list[dict]:
    async with pool().connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def fetch_one(sql: str, params: Any = None) -> dict | None:
    async with pool().connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchone()


async def fetch_val(sql: str, params: Any = None) -> Any:
    row = await fetch_one(sql, params)
    return None if row is None else next(iter(row.values()))


async def execute(sql: str, params: Any = None) -> int:
    async with pool().connection() as conn:
        cur = await conn.execute(sql, params)
        return cur.rowcount
