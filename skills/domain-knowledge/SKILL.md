---
name: domain-knowledge
description: 域知识方法论：11 域识别 + 四级找表（域索引→memory→资产→同类工单）+ 口径抽取
---

# domain-knowledge

为 SDS 提供知识底座。先把需求归类到域（用户/商品/交易/流量/财务/供应链/渠道/营销/
内容/风控/公共…11 域），再按四级找表：

1. 知识库域索引（`knowledge.uri_prefix`，默认 `viking://resources/…`，只允许该前缀）；
2. memory / 既有 mapping；
3. 资产搜索（只读）；
4. 同类工单与历史 SDS。

每级都要能给出证据（URI/文件），找不到就返回空并如实说，禁止编造 URI 或“大概在 XX 表”。
域口径（主键、分区、状态字典）是你输出 `knowledge_source_uri` 与字段结论的依据。
