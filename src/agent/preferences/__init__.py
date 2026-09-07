"""跨会话偏好 RAG。

按采纳清单实施：**pgvector L2 KNN + chars-aware decay 评分**
(``1 / (1 + distance) * (1 + log(rc)) * exp(-age/half_life)``) +
轻量 LLM 抽取（结构化 JSON，4 类：budget/category/logistics/platform）。

SQLite 路径已删（pg_store.knn_preferences 是唯一
检索后端），偏好向量与元数据都在 PG（preference_embeddings.embedding +
preferences 表）。实施时无需再考虑 sqlite-vec。
"""
