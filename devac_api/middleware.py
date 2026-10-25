import time


# asgi puro; BaseHTTPMiddleware mete overhead a la medicion
class ProcessTimeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()

        async def send_with_timing(message):
            if message["type"] == "http.response.start":
                elapsed_us = (time.perf_counter() - start) * 1e6
                headers = list(message.get("headers", []))
                # tiempo dentro del servidor, sin red
                headers.append((b"x-process-time-us", f"{elapsed_us:.1f}".encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_timing)
