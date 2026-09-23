"""CONNECT proxy for the emulator's WebView: w.bench.test:443 -> the netns relay (through netem)."""
import asyncio, socket, sys
TARGET = ("10.78.0.1", 443)
async def pipe(r, w):
    try:
        while True:
            d = await r.read(1 << 18)
            if not d:
                break
            w.write(d)
            await w.drain()
    except Exception:
        pass
    try:
        w.close()
    except Exception:
        pass
async def handle(r, w):
    try:
        head = await r.readuntil(b"\r\n\r\n")
        line = head.split(b"\r\n", 1)[0].decode()
        method, target, _ = line.split(" ", 2)
        if method != "CONNECT" or target != "w.bench.test:443":
            w.write(b"HTTP/1.1 403 Forbidden\r\n\r\n"); await w.drain(); w.close(); return
        ur, uw = await asyncio.open_connection(*TARGET)
        for x in (w, uw):
            x.get_extra_info("socket").setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        w.write(b"HTTP/1.1 200 Connection established\r\n\r\n"); await w.drain()
        await asyncio.gather(pipe(r, uw), pipe(ur, w))
    except Exception:
        w.close()
async def main():
    s = await asyncio.start_server(handle, "127.0.0.1", int(sys.argv[1]))
    async with s:
        await s.serve_forever()
asyncio.run(main())
