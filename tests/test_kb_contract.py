"""跨插件契约（**消费端**）：数开插件怎么读知识库插件写下的 mock 索引。

生产端的对应断言在 knowledge-base-plugin 的
`tests/test_engine.py::ConsumerContractTests`。两边各自对着同一份 schema 断言——
这样两个单仓 CI 都能钉住这条跨仓库的缝，不需要同时 checkout 两个仓库。

契约（当前实现，两侧必须一起改）：

    <data-dev 项目根>/.data-dev/mock/kb/index.json
    {"documents": [
       {"uri": "viking://resources/domains/<域>/tables/<表>.md",
        "domain": "<域>",
        "layer": "<表名首段>",
        "tables": ["<表>"],
        "abstract": "…"}
    ]}

消费侧只强制 `uri` 以 `knowledge.uri_prefix`（默认 `viking://resources/`）开头；
路径中间那一段的写法两侧目前并不一致（消费侧的种子数据用 `domain/`，生产侧写
`domains/`），所以这里**不**断言路径形状，只断言前缀与字段。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
ENGINE = ROOT_REPO / "engine"
sys.path.insert(0, str(ENGINE))

from core import clients, context  # noqa: E402

# 知识库插件发布后，消费侧索引里应当出现的那条记录
PUBLISHED_DOC = {
    "uri": "viking://resources/domains/user/tables/ads_user_act_di.md",
    "domain": "user",
    "layer": "ads",
    "tables": ["ads_user_act_di"],
    "abstract": "活跃口径：当日有任意行为即活跃；主键 user_id 唯一。",
}


class KnowledgeConsumerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")
        self.kb_dir = self.root / context.APP_DIR_NAME / "mock" / "kb"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _publish(self, *docs: dict) -> None:
        """模拟 /kb-publish 同步过来的索引内容（覆盖，避免被种子数据干扰）。"""
        context.write_json(self.kb_dir / "index.json", {"documents": [dict(d) for d in docs]})

    def test_search_finds_a_table_published_by_the_knowledge_plugin(self) -> None:
        self._publish(PUBLISHED_DOC)
        client = clients.MockKnowledgeClient(self.root)
        hits = client.search("ads_user_act_di")
        self.assertTrue(hits, "按表名搜索应当命中知识库插件发布的文档")
        self.assertEqual(hits[0]["uri"], PUBLISHED_DOC["uri"])
        self.assertEqual(hits[0]["tables"], ["ads_user_act_di"])

    def test_read_returns_the_abstract_for_a_published_uri(self) -> None:
        self._publish(PUBLISHED_DOC)
        client = clients.MockKnowledgeClient(self.root)
        self.assertEqual(client.read(PUBLISHED_DOC["uri"]), PUBLISHED_DOC["abstract"])

    def test_read_rejects_a_uri_outside_the_prefix(self) -> None:
        """知识源铁律：不在 uri_prefix 下的知识源一律拒绝。"""
        self._publish(PUBLISHED_DOC)
        client = clients.MockKnowledgeClient(self.root)
        for bad in ("file:///etc/passwd", "viking://resources-evil/x.md", "domains/user/tables/x.md"):
            with self.assertRaises(clients.ClientError, msg=bad):
                client.read(bad)

    def test_every_published_field_the_consumer_reads_is_present(self) -> None:
        """消费侧真正读的字段：uri / domain / abstract / tables。

        改了生产侧的字段名，这条会红——它是两侧 schema 的对齐点。
        """
        self._publish(PUBLISHED_DOC)
        client = clients.MockKnowledgeClient(self.root)
        hits = client.search("user")
        self.assertTrue(hits)
        for key in ("uri", "domain", "abstract", "tables"):
            self.assertIn(key, hits[0], f"消费侧要读 {key}，生产侧必须写")


if __name__ == "__main__":
    unittest.main()
