"""Separate fast provider generation from the text people have actually heard."""
import asyncio
from contextlib import aclosing


async def paced_stream(source, settings, agent_id, initial_delay=0):
    queue = asyncio.Queue(maxsize=64)

    async def produce():
        received = 0
        try:
            async with aclosing(source):
                async for chunk in source:
                    chunk = chunk[:settings.max_speech_chars - received]
                    if chunk:
                        await queue.put(chunk)
                        received += len(chunk)
                    if received >= settings.max_speech_chars:
                        break
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await queue.put(error)
        else:
            await queue.put(None)

    producer = asyncio.create_task(produce(), name=f"speech-buffer:{agent_id}")
    rate = settings.speech_chars_per_second * {"naru": 1.05, "sol": 0.9, "mio": 1.0}.get(agent_id, 1)
    delay = initial_delay
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            if isinstance(chunk, Exception):
                raise chunk
            # Small units avoid provider-dependent bursts. Punctuation adds breaths.
            for index in range(0, len(chunk), 3):
                unit = chunk[index:index + 3]
                if delay:
                    await asyncio.sleep(delay)
                yield unit
                delay = len(unit) / rate if rate else 0
                if rate:
                    if any(char in unit for char in ",，;:"):
                        delay += 0.18
                    if any(char in unit for char in ".!?。？！…"):
                        delay += 0.45
                    if "\n" in unit:
                        delay += 0.3
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
