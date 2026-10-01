"""Relays port 3000 to the converter, which sits on a network with no route out (ADR-0012).

Docker publishes no port from such a network, so a worker run on the host, and the tests, reach the
converter through this container, which is on both networks (docker-compose.yml). It runs in the
converter's own image, so it adds no image to trust, and it never opens a file: it copies bytes.
"""

import asyncio

CONVERTER = ("converter", 3000)


async def copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        reader, writer = await asyncio.open_connection(*CONVERTER)
    except OSError:
        client_writer.close()
        return
    await asyncio.gather(copy(client_reader, writer), copy(reader, client_writer))


async def main() -> None:
    server = await asyncio.start_server(relay, "0.0.0.0", 3000)
    async with server:
        await server.serve_forever()


asyncio.run(main())
