"""Generate during the reading gap, then deliver one complete chat bubble."""
import asyncio
import time
from contextlib import aclosing


async def paced_stream(source, settings, agent_id, initial_delay=0):
    async def produce():
        text = ""
        async with aclosing(source):
            async for chunk in source:
                text += chunk[:settings.max_speech_chars - len(text)]
                if len(text) >= settings.max_speech_chars:
                    break
        return text

    ready_at = time.monotonic() + initial_delay
    producer = asyncio.create_task(produce(), name=f"speech-buffer:{agent_id}")
    try:
        text = await producer
        if text:
            # Network time counts toward the gap; never add a second delay after it.
            remaining = ready_at - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            yield text
    finally:
        if not producer.done():
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
