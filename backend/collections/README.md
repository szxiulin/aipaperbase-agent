# Paper collections

User-writable paper collections, stored in the separate `data/user/collections/collections.sqlite`, kept apart from the rebuildable catalog database.

- `database.py`: collections schema, connection, and initialization;
- `service.py`: collection CRUD, membership management, entity resolution (current / alias migration / invalid), and export-row generation;
- `export.py`: four export formats — CSV / JSON / BibTeX / Markdown.

Collection members are keyed by the unified paper entity `entity_id`, resolved back to the current catalog on open or export; old IDs caused by a catalog rebuild are migrated via `entity_aliases`, and members that cannot be resolved are marked invalid rather than silently deleted.

---

## 中文

用户可写的论文集合，保存在独立的 `data/user/collections/collections.sqlite`，与可重建的目录数据库分离。

- `database.py`：集合库 schema、连接与初始化；
- `service.py`：集合增删改查、成员管理、实体解析（当前 / 别名迁移 / 失效）与导出行生成；
- `export.py`：CSV / JSON / BibTeX / Markdown 四种导出。

集合成员以统一论文实体 `entity_id` 为单位，打开或导出时解析回当前目录；目录重建导致的旧 ID 通过 `entity_aliases` 迁移，无法解析的成员被标注为失效而不静默删除。
