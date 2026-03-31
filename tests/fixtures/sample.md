# Redis Key Expiration Guide

Redis supports automatic key expiration through several commands. This guide covers the core expiration mechanisms, common patterns, and important gotchas that every developer should understand.

## Setting Expiration with EXPIRE

The EXPIRE command sets a timeout on a key. After the timeout has expired, the key will automatically be deleted. A key with an associated timeout is often said to be volatile in Redis terminology.

The timeout can only be set using the EXPIRE or PEXPIRE commands. The timeout is cleared only when the key is removed using the DEL command or overwritten using the SET command. This means that all operations that conceptually alter the value stored at the key without replacing it with a new one will leave the timeout untouched.

For example, incrementing the value of a key with INCR, pushing a new value into a list with LPUSH, or altering the field value of a hash with HSET will not modify the timeout.

```
EXPIRE mykey 60        # expires in 60 seconds
PEXPIRE mykey 60000    # expires in 60000 milliseconds
EXPIREAT mykey 1700000000  # expires at Unix timestamp
TTL mykey              # returns remaining seconds (-1 if no expiry, -2 if key doesn't exist)
PERSIST mykey          # removes the expiration
```

The EXPIRE command returns 1 if the timeout was set successfully and 0 if the key does not exist. The TTL command returns the remaining time to live of a key that has a timeout. It returns -1 if the key exists but has no associated expire, and -2 if the key does not exist.

## How Redis Expires Keys Internally

Redis uses two mechanisms to expire keys: passive expiration and active expiration. Understanding these mechanisms is important for predicting memory usage patterns.

### Passive Expiration

A key is passively expired when a client tries to access it and the key is found to be timed out. This means that at any given moment there may be keys already expired that are still in memory because no client has accessed them yet. This is important for memory planning.

### Active Expiration

Redis periodically tests a few keys at random among keys with an associated expire. All the keys that are already expired are deleted from the keyspace. Specifically, Redis does the following 10 times per second:

1. Test 20 random keys from the set of keys with an associated expire
2. Delete all the keys found expired
3. If more than 25% of keys were expired, start again from step 1

This is a probabilistic algorithm. The assumption is that the sample is representative of the whole key space, and Redis continues to expire until the percentage of likely expired keys is under 25%.

## Memory Management with Expiration

When using Redis as a cache, it is common to set a maxmemory directive and a maxmemory-policy. The maxmemory-policy determines what happens when the memory limit is reached. Common policies include:

- **allkeys-lru**: Evict the least recently used keys first, regardless of TTL
- **volatile-lru**: Evict the least recently used keys among those with TTL set
- **allkeys-random**: Evict random keys regardless of TTL
- **volatile-ttl**: Evict keys with the shortest TTL first
- **noeviction**: Return errors when memory limit is reached

For most cache use cases, `allkeys-lru` is recommended because it provides good hit rates without requiring every key to have an expiration set.

## Common Patterns

### Cache-Aside Pattern

The most common caching pattern involves checking the cache first, and on a miss, loading from the database and storing in the cache with a TTL:

```python
async def get_user(user_id: str) -> dict:
    cached = await redis.get(f"user:{user_id}")
    if cached:
        return json.loads(cached)

    user = await database.fetch_user(user_id)
    await redis.set(f"user:{user_id}", json.dumps(user), ex=3600)
    return user
```

### Rate Limiting with EXPIRE

Redis expiration is commonly used for rate limiting. The sliding window pattern uses sorted sets with timestamps:

```python
async def is_rate_limited(client_id: str, limit: int, window: int) -> bool:
    now = time.time()
    key = f"rate:{client_id}"
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - window)
    pipe.zadd(key, {str(now): now})
    pipe.zcard(key)
    pipe.expire(key, window)
    results = await pipe.execute()
    return results[2] > limit
```

## Important Gotchas

There are several common mistakes developers make when working with Redis expiration:

1. **SET overwrites TTL**: If you SET a key that has an expiration, the expiration is removed. Use SET with the EX option to preserve expiration behavior: `SET key value EX 60`.

2. **RENAME preserves TTL**: When you RENAME a key, the TTL is transferred to the new key name. If the destination key already existed with a TTL, that TTL is replaced.

3. **Expired keys and replication**: Expiration is handled in the primary and propagated to replicas via DEL commands. This means there can be a brief window where a replica still serves an expired key.

4. **Memory isn't freed immediately**: Due to the probabilistic active expiration, there can be a lag between a key expiring and its memory being freed. Under high load with many expiring keys, this lag can cause temporary memory spikes.

5. **Persistence and expiration**: When Redis saves an RDB snapshot or rewrites the AOF, expired keys are not included. However, keys that are about to expire are persisted. On restart, these keys will be checked and expired if their timeout has passed.
