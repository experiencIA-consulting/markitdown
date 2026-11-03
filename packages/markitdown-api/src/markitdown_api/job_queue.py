import json

from redis.asyncio import Redis

QUEUE_NAME = "markitdown:jobs"
PROCESSING_NAME = "markitdown:processing"
HEARTBEAT_KEY = "markitdown:worker:heartbeat"

_ENQUEUE_SCRIPT = """
local pending = redis.call('LLEN', KEYS[1])
if pending >= tonumber(ARGV[1]) then
  return 0
end
redis.call('LPUSH', KEYS[1], ARGV[2])
return 1
"""


async def enqueue(redis: Redis, job: dict, max_pending: int) -> bool:
    result = await redis.eval(
        _ENQUEUE_SCRIPT,
        1,
        QUEUE_NAME,
        max_pending,
        json.dumps(job, separators=(",", ":")),
    )
    return bool(result)
