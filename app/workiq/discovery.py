from app.context import RunCtx
from app.workiq.client import workiq
from app.workiq.schema_cache import schema_cache


class SchemaDiscovery:
    """Introspection over enumeration: discover paths, then fetch (and cache) the schema for one path."""

    async def paths(self, query: str, ctx: RunCtx) -> dict:
        return await workiq.call("search_paths", {"filter": query}, ctx)

    async def schema(self, path: str, operation: str, ctx: RunCtx) -> dict:
        key = f"{ctx.workspace_id}:{operation}:{path}"
        cached = schema_cache.get(key)
        if cached:
            return {**cached, "cached": True}
        v = await workiq.call("get_schema", {"path": path, "operationType": operation, "format": "jsonschema"}, ctx)
        if v.get("status") == "ok":
            schema_cache.put(key, v)
        return v


discovery = SchemaDiscovery()
