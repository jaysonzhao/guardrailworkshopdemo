"""Authenticated single-participant Showroom. Only the OAuth sidecar can reach this listener over the network."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, WSMsgType, web

@dataclass(frozen=True)
class Settings:
    owner: str
    namespace: str
    origin: str
    console: str
    embedded_console: str = ""
    api: str = "https://kubernetes.default.svc"
    ca: str = "/run/api-ca/ca.crt"
    sessions: Path = Path("/run/workshop")
    www: Path = Path("/opt/showroom/www")
    ttyd: str = "http://127.0.0.1:7681"
    agent: str = "http://grlab-agent:8080"

HTTP = web.AppKey("http", ClientSession)
HERE = Path(__file__).resolve().parent
DENIED = "Sign in with the OpenShift account assigned to this workshop."

def make_app(cfg):
    cache = {}
    api_tls = ssl.create_default_context(cafile=cfg.ca) if cfg.api.startswith("https:") else None

    async def clients(app):
        for path in cfg.sessions.glob("*.json"):
            if re.fullmatch(r"[a-f0-9]{24}[.]json", path.name):
                path.unlink(missing_ok=True)
        async with ClientSession(timeout=ClientTimeout(total=15), trust_env=False, cookie_jar=DummyCookieJar()) as session:
            app[HTTP] = session
            yield

    async def authorize(request, fresh=False):
        token = request.headers.get("X-Forwarded-Access-Token", "")
        if not token:
            raise web.HTTPForbidden(text=DENIED)
        key = hashlib.sha256(token.encode()).digest()
        now = time.monotonic()
        if not fresh and cache.get(key, 0) > now:
            return token
        try:
            async with request.app[HTTP].get(cfg.api + "/apis/user.openshift.io/v1/users/~", ssl=api_tls,
                    headers={"Authorization": "Bearer " + token}, allow_redirects=False) as response:
                if response.status in (401, 403):
                    cache.pop(key, None)
                    raise web.HTTPForbidden(text=DENIED)
                if response.status != 200:
                    raise web.HTTPServiceUnavailable(text="OpenShift user lookup is unavailable.")
                if (await response.json()).get("metadata", {}).get("name") != cfg.owner:
                    raise web.HTTPForbidden(text=DENIED)
        except (ClientError, TimeoutError, ValueError):
            raise web.HTTPServiceUnavailable(text="OpenShift user lookup is unavailable.") from None
        if len(cache) >= 64:
            cache.clear()
        cache[key] = now + 30
        return token

    @web.middleware
    async def access(request, handler):
        await authorize(request)
        if request.method == "POST" or request.path == "/terminal/ws":
            if request.headers.get("Origin") != cfg.origin:
                raise web.HTTPForbidden(text="Open the workshop at its assigned HTTPS address.")
        try:
            return await handler(request)
        except (ClientError, TimeoutError):
            raise web.HTTPBadGateway(text="Service unavailable. Check the workshop pod or complete Pre-1 for the playground.") from None

    async def headers(request,response):
        response.headers.update({"Cache-Control":"no-store","Referrer-Policy":"same-origin","X-Content-Type-Options":"nosniff",
                                "X-Frame-Options":"SAMEORIGIN","Content-Security-Policy":"frame-ancestors 'self'; object-src 'none'; base-uri 'self'"})

    async def info(request):
        return web.json_response({"owner":cfg.owner, "namespace":cfg.namespace, "console":cfg.console, "embeddedConsole":cfg.embedded_console})

    async def home(request):
        return web.FileResponse(HERE / "index.html")

    async def asset(request):
        return web.FileResponse(HERE / request.match_info["asset"])

    @asynccontextmanager
    async def credential(token):
        key = secrets.token_hex(12)
        config = {"apiVersion":"v1","kind":"Config","current-context":"lab",
                  "clusters":[{"name":"ocp","cluster":{"server":cfg.api,"certificate-authority":cfg.ca}}],
                  "contexts":[{"name":"lab","context":{"cluster":"ocp","user":"participant","namespace":cfg.namespace}}],
                  "users":[{"name":"participant","user":{"token":token}}]}
        path = cfg.sessions / (key + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(config,out)
            yield key
        finally:
            path.unlink(missing_ok=True)

    async def relay(source, target):
        async for msg in source:
            if msg.type == WSMsgType.BINARY:
                await target.send_bytes(msg.data)
            elif msg.type == WSMsgType.TEXT:
                await target.send_str(msg.data)
            else:
                break

    async def recheck(request):
        for _ in range(60):
            await asyncio.sleep(60)
            await authorize(request, fresh=True)

    async def terminal_ws(request):
        token = await authorize(request, fresh=True)
        public = web.WebSocketResponse(protocols=("tty",), heartbeat=30, max_msg_size=1024*1024)
        if public.can_prepare(request).protocol != "tty":
            raise web.HTTPBadRequest(text="A tty WebSocket is required.")
        async with credential(token) as key:
            async with request.app[HTTP].ws_connect(cfg.ttyd + "/terminal/ws", headers={"X-Workshop-Session":key},
                                                    protocols=("tty",), max_msg_size=1024*1024) as upstream:
                if upstream.protocol != "tty":
                    raise web.HTTPBadGateway(text="Terminal protocol unavailable.")
                await public.prepare(request)
                jobs = [asyncio.create_task(coro) for coro in (relay(public, upstream), relay(upstream, public), recheck(request))]
                try:
                    await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for job in jobs:
                        job.cancel()
                    await asyncio.gather(*jobs, return_exceptions=True)
                    await public.close()
        return public

    async def terminal_http(request):
        async with request.app[HTTP].get(cfg.ttyd + request.path, headers={"X-Workshop-Session":"page"}, allow_redirects=False) as upstream:
            return web.Response(body=await upstream.read(), status=upstream.status, headers={"Content-Type":upstream.headers.get("Content-Type","text/html")})

    async def playground(request):
        path = request.path.removeprefix("/playground")
        async with request.app[HTTP].request(request.method, cfg.agent+path, data=await request.read(), headers={"Content-Type":"application/json"},
                                             timeout=ClientTimeout(total=240,connect=10), allow_redirects=False) as upstream:
            response = web.StreamResponse(status=upstream.status, headers={"Content-Type":upstream.headers.get("Content-Type","application/octet-stream")})
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
            await response.write_eof()
            return response

    app = web.Application(middlewares=[access], client_max_size=1024*1024)
    app.cleanup_ctx.append(clients)
    app.on_response_prepare.append(headers)
    app.router.add_get("/", home)
    app.router.add_get("/session", info)
    app.router.add_get("/portal/{asset:portal[.]css|portal[.]js}", asset)
    app.router.add_get("/terminal/ws", terminal_ws)
    for path in ("/terminal/","/terminal/token"):
        app.router.add_get(path,terminal_http)
    for path in ("/playground/","/playground/health","/playground/images/rh_logo.svg"):
        app.router.add_get(path,playground)
    app.router.add_post("/playground/chat/completions",playground)
    for folder in ("_", "modules"):
        app.router.add_static("/"+folder,cfg.www/folder,show_index=False,follow_symlinks=False)
    return app

if __name__ == "__main__":
    config = Settings(os.environ["SHOWROOM_USER"],os.environ["LAB_NAMESPACE"],os.environ["SHOWROOM_ORIGIN"],
                      os.environ["SHOWROOM_CONSOLE_URL"],os.getenv("SHOWROOM_CONSOLE_EMBED_URL",""))
    web.run_app(make_app(config), host="127.0.0.1", port=8080, access_log=None)
