"""Check the rendered guide, authenticated terminal and rule edits on loopback in Chromium.

Run after building the guide. Supply paths to Chromium and ttyd 1.7.7.
Requires Python packages aiohttp, playwright and PyYAML.
"""
import argparse
import asyncio
from contextlib import AsyncExitStack
import os
from pathlib import Path
import socket
import sys
import tempfile

import yaml
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"showroom/portal"))
from seed import seed
from server import make_app, Settings


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


async def eventually(check):
    for _ in range(100):
        if check():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("Expected terminal state did not appear")


async def check(args):
    async with AsyncExitStack() as stack:
        tmp=Path(stack.enter_context(tempfile.TemporaryDirectory()))
        sessions=tmp/"sessions"
        sessions.mkdir()
        www=ROOT/"showroom/www"
        seed(www/"modules/en/_attachments/lab-files.zip",tmp)
        assert os.access(tmp/"lab/grlab",os.X_OK)
        draft=tmp/"lab/policies/selection.yaml"
        def choices():
            return yaml.safe_load(draft.read_text())

        async def whoami(req):
            account="student" if req.headers.get("Authorization")=="Bearer demo-session" else "other"
            return web.json_response({"metadata":{"name":account}})

        identity=web.Application()
        identity.router.add_get("/apis/user.openshift.io/v1/users/~",whoami)
        api=await stack.enter_async_context(TestServer(identity))
        port, ttyport=free_port(),free_port()
        origin="http://127.0.0.1:"+str(port)
        config=Settings("student","lab-student",origin,"https://console.example.test",api=str(api.make_url("/")).rstrip("/"),
                        sessions=sessions,www=www,ttyd="http://127.0.0.1:"+str(ttyport))
        tty=await asyncio.create_subprocess_exec(str(args.ttyd),"-i","lo","-p",str(ttyport),"-b","/terminal","-W",
                   "-H","X-Workshop-Session","-t","titleFixed=Guardrail terminal","-t","disableLeaveAlert=true",str(ROOT/"showroom/portal/start-shell"),
                   env=dict(os.environ,WORKSPACE=str(tmp),SESSION_DIR=str(sessions),LAB_NAMESPACE=config.namespace),
                   stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        async def stop():
            if tty.returncode is None:
                tty.terminate()
                await tty.wait()
        stack.push_async_callback(stop)

        # Stand-in for the OAuth sidecar on this loopback-only test server.
        @web.middleware
        async def demo_login(req, handler):
            if req.cookies.get("workshop")=="student":
                req=req.clone(headers={**req.headers,"X-Forwarded-Access-Token":"demo-session"})
            return await handler(req)

        portal=make_app(config)
        portal.middlewares.insert(0,demo_login)
        await stack.enter_async_context(TestServer(portal,port=port))
        http=await stack.enter_async_context(ClientSession())
        for path in ("/","/terminal/","/playground/","/modules/en/_attachments/lab-files.zip"):
            async with http.get(origin+path) as response:
                assert response.status==403,path
        async with http.get(origin+"/session",headers={"X-Forwarded-Access-Token":"another-user"}) as response:
            assert response.status==403
        async with http.post(origin+"/playground/chat/completions",headers={"X-Forwarded-Access-Token":"demo-session","Origin":"https://other.example.test"}) as response:
            assert response.status==403

        pw=await stack.enter_async_context(async_playwright())
        browser=await pw.chromium.launch(executable_path=str(args.chromium),args=["--no-sandbox","--disable-dev-shm-usage"])
        stack.push_async_callback(browser.close)
        context=await browser.new_context(viewport={"width":1440,"height":900})
        await context.add_cookies([{"name":"workshop","value":"student","url":origin}])
        page=await context.new_page()
        await page.goto(origin)
        await page.wait_for_function("document.querySelector('#identity').textContent==='student · lab-student'")
        assert await page.locator("#consoleTab").get_attribute("href")==config.console
        assert await page.locator("#consoleTab").get_attribute("target")=="_blank"
        await page.wait_for_function("document.querySelector('#terminal').contentWindow.term && document.querySelector('#terminal').contentDocument.title.startsWith('Guardrail terminal')")
        guide=page.frame_locator("#guide")
        await guide.locator("article a[href='appendix-rules.html']").first.click()
        await guide.locator("#rule-category").select_option("customer-care")
        assert await guide.locator(".rule-card:visible").count()==3
        await guide.locator("#rule-id").select_option("marketing-consent")
        card=guide.locator(".rule-card:visible")
        assert await card.count()==1 and await card.get_attribute("data-rule")=="marketing-consent"
        assert "How does a customer change marketing preferences?" in await card.inner_text()
        await card.locator(".rule-command").filter(has_text="Stage this rule").get_by_role("button",name="Run in terminal").click()
        await eventually(lambda: choices()["enable"]==["marketing-consent"])
        assert len(list(sessions.glob("*.json")))==1 and next(sessions.glob("*.json")).stat().st_mode & 0o777 == 0o600
        await card.locator(".rule-command").filter(has_text="Stage removal").get_by_role("button",name="Run in terminal").click()
        await eventually(lambda: choices()=={"enable":[],"disable":["marketing-consent"]})
        await guide.locator("#rule-reset").click()
        assert await guide.locator(".rule-card:visible").count()==53
        await guide.locator("article a[href='module-5.html']").last.click()
        assert "./grlab cleanup --dry-run" in await guide.locator("article").inner_text()
        await page.close()
        await eventually(lambda: not list(sessions.glob("*.json")))
        print("PASS owner-only portal, console link, terminal credential lifecycle, 53 rule cards, Run → real shell add/remove, cleanup navigation")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("ttyd","chromium"):
        parser.add_argument("--"+name,type=lambda p:Path(p).resolve(),required=True)
    asyncio.run(check(parser.parse_args()))
