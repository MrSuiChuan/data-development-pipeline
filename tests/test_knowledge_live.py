"""live 知识检索：HTTP 契约、CLI 适配、前缀越界拦截（不发真实网络）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_REPO = Path(__file__).resolve().parent.parent
ENGINE = ROOT_REPO / "engine"
sys.path.insert(0, str(ENGINE))

from core import clients, context, ov_knowledge  # noqa: E402


def _live_cfg(kind: str = "auto", url: str = "http://127.0.0.1:1933") -> dict:
    cfg = context.default_config()
    cfg["mode"] = "live"
    cfg["knowledge"] = {
        "uri_prefix": "viking://resources/",
        "kind": kind,
        "limit": 10,
        "level": 2,
        "ov": {
            "url": url,
            "api_key_env": "OV_TEST_KEY_FAKE",
            "api_key_file": "",
            "timeout": 20,
        },
    }
    return cfg


class NormalizeTests(unittest.TestCase):
    def test_find_rest_payload_normalized(self) -> None:
        payload = {
            "status": "ok",
            "result": {
                "memories": [],
                "resources": [
                    {
                        "uri": "viking://resources/domain/dwd-user/dwd_user_readme/dwd_user_readme.md",
                        "level": 2,
                        "score": 0.64,
                        "abstract": "用户域明细层知识",
                        "overview": None,
                    },
                    {
                        "uri": "viking://resources/domain/dim-date/dim_date_readme/dim_date_readme.md",
                        "level": 2,
                        "score": 0.2,
                        "abstract": "日期维表",
                    },
                ],
                "total": 2,
            },
        }
        hits = ov_knowledge.normalize_find_result(payload)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["uri"], "viking://resources/domain/dwd-user/dwd_user_readme/dwd_user_readme.md")
        self.assertEqual(hits[0]["domain"], "dwd-user")
        self.assertEqual(hits[0]["layer"], "dwd")
        self.assertEqual(hits[0]["abstract"], "用户域明细层知识")
        self.assertGreater(hits[0]["score"], hits[1]["score"])

    def test_find_cli_envelope_normalized(self) -> None:
        payload = {"ok": True, "result": {"memories": [], "resources": [], "total": 0}}
        self.assertEqual(ov_knowledge.normalize_find_result(payload), [])

    def test_read_result_text(self) -> None:
        payload = {"status": "ok", "result": "# 用户域：明细层知识\n\n口径：活跃=当日登录"}
        self.assertEqual(ov_knowledge.normalize_read_result(payload), "# 用户域：明细层知识\n\n口径：活跃=当日登录")

    def test_read_result_missing_content_rejected(self) -> None:
        with self.assertRaises(ov_knowledge.OpenVikingError):
            ov_knowledge.normalize_read_result({"status": "ok", "result": None})


class ResolveSettingsTests(unittest.TestCase):
    def test_missing_key_rejected(self) -> None:
        cfg = _live_cfg()
        with mock.patch.dict(os.environ, {"OV_TEST_KEY_FAKE": ""}, clear=False):
            with self.assertRaises(ov_knowledge.OpenVikingError):
                ov_knowledge.resolve_settings(cfg)

    def test_env_key_accepted(self) -> None:
        cfg = _live_cfg()
        with mock.patch.dict(os.environ, {"OV_TEST_KEY_FAKE": "secret-key-123"}, clear=False):
            settings = ov_knowledge.resolve_settings(cfg)
        self.assertEqual(settings["api_key"], "secret-key-123")

    def test_missing_url_rejected(self) -> None:
        cfg = _live_cfg(url="")
        with self.assertRaises(ov_knowledge.OpenVikingError):
            ov_knowledge.resolve_settings(cfg)


class HttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = _live_cfg()

    def test_find_posts_search_find(self) -> None:
        payload = {
            "status": "ok",
            "result": {
                "resources": [
                    {"uri": "viking://resources/domain/dwd-user/readme.md", "score": 0.5, "abstract": "x"}
                ]
            },
        }
        with mock.patch.dict(os.environ, {"OV_TEST_KEY_FAKE": "secret-key-123"}, clear=False):
            with mock.patch.object(ov_knowledge, "_request_json", return_value=payload) as req:
                hits = ov_knowledge.find(self.cfg, "活跃口径")
        self.assertEqual(len(hits), 1)
        method, path = req.call_args.args[1], req.call_args.args[2]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/api/v1/search/find")
        body = req.call_args.kwargs["body"]
        self.assertEqual(body["query"], "活跃口径")
        self.assertEqual(body["target_uri"], "viking://resources")

    def test_read_gets_content(self) -> None:
        payload = {"status": "ok", "result": "知识内容"}
        with mock.patch.dict(os.environ, {"OV_TEST_KEY_FAKE": "secret-key-123"}, clear=False):
            with mock.patch.object(ov_knowledge, "_request_json", return_value=payload) as req:
                text = ov_knowledge.read(self.cfg, "viking://resources/domain/dwd-user/readme.md")
        self.assertEqual(text, "知识内容")
        self.assertEqual(req.call_args.args[1], "GET")
        self.assertEqual(req.call_args.args[2], "/api/v1/content/read")


class LiveClientTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="live")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_http_auto_when_url_configured(self) -> None:
        cfg = _live_cfg()
        client = clients.LiveKnowledgeClient(cfg, self.root)
        self.assertEqual(client._transport(), "http")

    def test_ov_cli_fallback_when_no_url(self) -> None:
        cfg = _live_cfg(url="")
        client = clients.LiveKnowledgeClient(cfg, self.root)
        self.assertEqual(client._transport(), "ov-cli")

    def test_ov_cli_search_normalized(self) -> None:
        cfg = _live_cfg(url="")
        client = clients.LiveKnowledgeClient(cfg, self.root)
        envelope = {
            "ok": True,
            "result": {
                "resources": [
                    {
                        "uri": "viking://resources/domain/dwd-user/readme.md",
                        "score": 0.8,
                        "abstract": "用户域明细",
                    }
                ]
            },
        }
        with mock.patch.object(clients, "_run_cli", return_value=envelope) as run:
            hits = client.search("活跃")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["abstract"], "用户域明细")
        args = run.call_args.args
        self.assertIn("find", args)
        self.assertIn("活跃", args)

    def test_ov_cli_read_normalized(self) -> None:
        cfg = _live_cfg(url="")
        client = clients.LiveKnowledgeClient(cfg, self.root)
        with mock.patch.object(clients, "_run_cli", return_value={"ok": True, "result": "内容"}) as run:
            text = client.read("viking://resources/domain/dwd-user/readme.md")
        self.assertEqual(text, "内容")
        self.assertIn("read", run.call_args.args)

    def test_custom_cli_legacy_contract_kept(self) -> None:
        cfg = _live_cfg(kind="custom-cli", url="")
        client = clients.LiveKnowledgeClient(cfg, self.root)
        with mock.patch.object(clients, "_run_cli", return_value=[{"uri": "u1"}]) as run:
            hits = client.search("q")
        self.assertEqual(hits, [{"uri": "u1"}])
        args = run.call_args.args
        self.assertIn("search", args)
        self.assertIn("--query", args)
        with mock.patch.object(clients, "_run_cli", return_value={"content": "旧包装内容"}):
            self.assertEqual(client.read("viking://resources/domain/dwd-user/readme.md"), "旧包装内容")

    def test_read_outside_prefix_blocked(self) -> None:
        cfg = _live_cfg(url="")
        client = clients.LiveKnowledgeClient(cfg, self.root)
        with self.assertRaises(clients.ClientError):
            client.read("viking://projects/other/secret.md")

    def test_mock_knowledge_respects_custom_prefix(self) -> None:
        cfg = context.default_config()
        cfg["knowledge"] = {"uri_prefix": "viking://projects/kb/"}
        mock_client = clients.MockKnowledgeClient(self.root, clients.kb_prefix(cfg))
        self.assertTrue(mock_client.read("viking://projects/kb/domain/dwd-user/readme.md"))
        with self.assertRaises(clients.ClientError):
            mock_client.read("viking://resources/domain/dwd-user/readme.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
