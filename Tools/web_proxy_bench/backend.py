"""Bench backend standing in for MTProxy+Telegram: pipelined RPCs.

Request:  u32 reqLen | u32 respLen | u32 tag | u32 delayMs | reqLen bytes
Response: u32 respLen | u32 tag | respLen bytes, in request order, sent no
earlier than delayMs after the request was fully read (models the relay ->
Telegram DC round trip and server time).
"""
import asyncio, struct, sys, time
ZERO = bytes(1 << 20)
RX = [0]
TX = [0]

async def meter():
    # Bytes per 250 ms into rates.log: the relay-side view of throughput.
    last_rx = last_tx = 0
    with open("rates.log", "a", buffering=1) as log:
        while True:
            await asyncio.sleep(0.25)
            rx, tx = RX[0], TX[0]
            if rx != last_rx or tx != last_tx:
                log.write("%.3f %d %d\n" % (time.time(), rx - last_rx, tx - last_tx))
            last_rx, last_tx = rx, tx

async def handle(r, w):
    q = asyncio.Queue()
    async def writer():
        while True:
            item = await q.get()
            if item is None:
                break
            due, resp, tag = item
            wait = due - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            w.write(struct.pack(">II", resp, tag))
            left = resp
            while left:
                k = min(left, len(ZERO)); w.write(ZERO[:k] if k < len(ZERO) else ZERO); left -= k; TX[0] += k
            await w.drain()
    task = asyncio.create_task(writer())
    try:
        while True:
            head = await r.readexactly(16)
            req, resp, tag, delay = struct.unpack(">IIII", head)
            left = req
            while left:
                d = await r.read(min(left, 1 << 20))
                if not d:
                    raise asyncio.IncompleteReadError(b"", left)
                left -= len(d)
                RX[0] += len(d)
            q.put_nowait((time.monotonic() + delay / 1000.0, resp, tag))
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    q.put_nowait(None)
    try:
        await task
    except Exception:
        pass
    w.close()

async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 2398
    asyncio.create_task(meter())
    s = await asyncio.start_server(handle, "127.0.0.1", port, limit=1 << 22)
    async with s:
        await s.serve_forever()
asyncio.run(main())
