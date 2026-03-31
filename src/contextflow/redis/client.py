"""Redis async client.

Create a single async connection pool at startup.
All modules receive this client as a dependency — none create their own connections.
"""

import redis.asyncio as aioredis

from contextflow.config import Settings


def get_redis_client(settings: Settings) -> aioredis.Redis:
    """Create an async Redis client with a connection pool.

    The client is created from the URL in settings, with the pool size
    matching settings.redis.max_connections. This function does NOT open
    a connection — that happens lazily on first use.
    """
    return aioredis.from_url(
        settings.redis.url,
        max_connections=settings.redis.max_connections,
        decode_responses=False,
    )


async def close_redis_client(client: aioredis.Redis) -> None:
    """Drain and close the connection pool.

    Call this during application shutdown to release all connections cleanly.
    """
    await client.aclose()
