#!/usr/bin/env python3
"""Check the signed-in portal against local identity, terminal and playground fixtures."""
import asyncio
import json
import os
import subprocess
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from unittest.mock import patch

from aiohttp import web, WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "showroom/portal"))
from server import Settings, make_app


class PortalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        for folder in ("modules", "_", "sessions"):
            (self.dir / folder).mkdir()
        (self.dir / "modules/test.txt").write_text("signed-in guide")
        self.stale = self.dir / "sessions" / ("a" * 24 + ".json")
        self.stale.write_text("expired kubeconfig fixture")
        self.owner = "student@example.test"
        self.origin = "https://workshop.example.test"
        self.token = "fixture-only"
        self.headers = {"X-Forwarded-Access-Token": self.token, "Origin": self.origin}
        self.upstream_headers = {}
        self.active_id = None
        self.identities = {self.token: self.owner, "other-fixture": "other-user"}

        async def user(req):
            name = self.identities.get(req.headers.get("Authorization", "").removeprefix("Bearer "))
            if not name:
                raise web.HTTPUnauthorized()
            return web.json_response({"metadata": {"name": name}})

        async def tty(req):
            self.upstream_headers = dict(req.headers)
            self.active_id = req.headers["X-Workshop-Session"]
            ws = web.WebSocketResponse(protocols=("tty",))
            await ws.prepare(req)
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    await ws.send_bytes(message.data)
            return ws

        async def chat(req):
            self.upstream_headers = dict(req.headers)
            return web.Response(text='data: [DONE]\n\n', content_type="text/event-stream")

        fixtures = web.Application()
        fixtures.router.add_get("/apis/user.openshift.io/v1/users/~", user)
        fixtures.router.add_get("/terminal/ws", tty)
        fixtures.router.add_post("/chat/completions", chat)
        upstream = TestServer(fixtures)
        await upstream.start_server()
        self.addAsyncCleanup(upstream.close)
        base = str(upstream.make_url("/")).rstrip("/")
        self.portal = TestClient(TestServer(make_app(Settings(
            self.owner, "personal-lab", self.origin, "https://console.example.test",
            api=base, www=self.dir, sessions=self.dir / "sessions", ttyd=base, agent=base))))
        await self.portal.start_server()
        self.addAsyncCleanup(self.portal.close)

    async def test_all_participant_content_requires_owner(self):
        self.assertFalse(self.stale.exists())
        for endpoint in ("/", "/session", "/terminal/", "/terminal/ws", "/modules/test.txt", "/playground/"):
            self.assertEqual((await self.portal.get(endpoint)).status, 403)
            response = await self.portal.get(endpoint, headers={"X-Forwarded-User": self.owner,
                                            "X-Forwarded-Access-Token": "other-fixture"})
            self.assertEqual(response.status, 403)
        session = await (await self.portal.get("/session", headers=self.headers)).json()
        self.assertEqual(session["owner"], self.owner)
        self.assertNotIn(self.token, json.dumps(session))
        response = await self.portal.get("/modules/test.txt", headers=self.headers)
        self.assertEqual(await response.text(), "signed-in guide")

    async def test_terminal_connection_issues_private_scoped_kubeconfig(self):
        for origin in ("", "https://other.example.test"):
            with self.assertRaises(WSServerHandshakeError) as err:
                await self.portal.ws_connect("/terminal/ws", protocols=("tty",),
                                             headers={**self.headers, "Origin": origin})
            self.assertEqual(err.exception.status, 403)
        async with self.portal.ws_connect("/terminal/ws", protocols=("tty",), headers={
                    **self.headers, "Cookie": "private=fixture", "X-Workshop-Session": "forged"}) as ws:
            await ws.send_bytes(b"0oc whoami\r")
            self.assertEqual((await ws.receive()).data, b"0oc whoami\r")
            self.assertRegex(self.active_id, r"^[a-f0-9]{24}$")
            cfg = self.dir / "sessions" / (self.active_id + ".json")
            self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
            contents = json.loads(cfg.read_text())
            self.assertEqual(contents["users"][0]["user"]["token"], self.token)
            self.assertEqual(contents["contexts"][0]["context"]["namespace"], "personal-lab")
            for key in ("Cookie", "Authorization", "X-Forwarded-Access-Token"):
                self.assertNotIn(key, self.upstream_headers)
        for _ in range(50):
            if not cfg.exists():
                break
            await asyncio.sleep(0.02)
        self.assertFalse(cfg.exists())

    async def test_playground_stream_and_origin(self):
        path = "/playground/chat/completions"
        self.assertEqual((await self.portal.post(path, headers={**self.headers, "Origin": "null"})).status, 403)
        response = await self.portal.post(path, data=b"{}", headers={**self.headers, "Cookie": "private=fixture"})
        self.assertIn("text/event-stream", response.headers["Content-Type"])
        self.assertEqual(await response.text(), "data: [DONE]\n\n")
        for key in ("Cookie", "Authorization", "X-Forwarded-Access-Token"):
            self.assertNotIn(key, self.upstream_headers)
        self.assertEqual((await self.portal.get("/playground/docs", headers=self.headers)).status, 404)


class ProvisioningTests(unittest.TestCase):
    def test_stage_publishes_only_the_oauth_port(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import lab
        import showroom
        applied = {}
        owner_claims = []
        commands = []
        def oc(*args, **kwargs):
            commands.append(args)
            if args == ("whoami",):
                return "instructor"
            if args == ("whoami", "--show-console"):
                return "https://console.example.test"
            if "--raw" in args:
                return '{"authorization_endpoint":"https://oauth.example.test/oauth/authorize"}'
            if args[:3] == ("create", "-f", "-"):
                owner_claims.append(json.loads(kwargs["data"]))
            if args[:2] == ("get", "route/grlab-showroom"):
                return "guide.example.test"
            return ""
        settings = {"SHOWROOM_USER": "participant@example.test", "SHOWROOM_CLI_IMAGE": "cli-fixture",
                    "SHOWROOM_OAUTH_IMAGE": "oauth-fixture"}
        with patch.dict(os.environ, settings, clear=True), patch.object(lab, "NS", "my-lab"), \
                patch.object(lab, "get", return_value=None), patch.object(lab, "oc", side_effect=oc), \
                patch.object(lab, "build", return_value="site-fixture"), patch.object(lab, "rollout"), \
                patch.object(lab, "apply", side_effect=lambda d: applied.update({(d["kind"], d["metadata"]["name"]): d})), \
                patch.object(showroom, "check_route") as verify:
            showroom.deploy(lab)
        self.assertTrue(owner_claims[0]["immutable"])
        self.assertEqual(owner_claims[0]["data"]["user"], settings["SHOWROOM_USER"])
        pod = applied["Deployment", "grlab-showroom"]["spec"]["template"]["spec"]
        images = {c["name"]: c["image"] for c in pod["initContainers"] + pod["containers"]}
        self.assertEqual(images, {"oauth":"oauth-fixture", "portal":"site-fixture", "terminal":"site-fixture",
                                  "install-oc":"cli-fixture", "seed-workspace":"site-fixture"})
        self.assertFalse(pod["automountServiceAccountToken"])
        mounts = {c["name"]: {v["name"] for v in c.get("volumeMounts", [])} for c in pod["containers"]}
        for private in ("tls", "cookie", "oauth-client"):
            self.assertEqual([c for c, v in mounts.items() if private in v], ["oauth"])
        route = applied["Route", "grlab-showroom"]["spec"]
        self.assertEqual((route["tls"]["termination"], route["port"]["targetPort"]), ("reencrypt", "https"))
        ports = applied["Service", "grlab-showroom"]["spec"]["ports"]
        self.assertEqual([(p["port"],p["targetPort"]) for p in ports], [(443,"oauth")])
        verify.assert_called_once_with("https://guide.example.test", "my-lab", "https://oauth.example.test/oauth/authorize")

    def test_concurrent_owner_claim_protects_the_first_participant(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import lab
        import showroom
        winner = {"data": {"user": "first"}}
        with patch.object(lab, "oc", side_effect=subprocess.CalledProcessError(1, "create")), \
                patch.object(lab, "get", return_value=winner):
            with self.assertRaisesRegex(RuntimeError, "another user"):
                showroom.claim_owner(lab, "second", None)
            showroom.claim_owner(lab, "first", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
