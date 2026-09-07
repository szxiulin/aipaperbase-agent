# Tests

The test directory is organized by business module: `catalog/`, `analytics/`, `library/`, `rag/`, and `integrations/`.

`e2e/` verifies cross-module user closed loops. Tests should prefer real but controllable data slices; formal acceptance still requires running data-consistency and performance checks against the complete product scope.

---

## 中文

测试目录按业务模块组织：`catalog/`、`analytics/`、`library/`、`rag/` 和 `integrations/`。

`e2e/` 验证跨模块用户闭环。测试应优先使用真实但可控的数据切片；正式验收仍需对完整产品范围运行数据一致性和性能检查。

## 正确性回归

在项目根目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
node --test tests/frontend/collection_selection.test.cjs
node --check frontend/app.js
```

前端测试使用 Node 内置测试器执行实际 app.js 函数，DOM 和请求为模拟，不执行真实清理。
浏览器交互可使用 `.venv/bin/python -m tests.frontend.serve_fixture`：
`http://127.0.0.1:8766` 提供真实前端、只读目录以及 QA 集合 A/B；服务拒绝所有写请求，不访问实际用户集合或模型 API。


## Sprint-023 隔离写入验收

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.frontend.test_workflow_http -v
node --test tests/frontend/collection_selection.test.cjs
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m tests.frontend.serve_workflow_fixture --root /tmp/apex-qa-unique --port 8767
```

`test_workflow_http` 不在默认 unittest discover 的目录发现范围内，需单独运行。fixture 使用真实前端、聊天 Runner、工具分发、入库编排及 SQLite；只 stub 外部适配器并阻断真实网络。它允许写入临时数据，与旧 `serve_fixture` 只读展示用途不同。详情与截图见 Sprint-023。


## Sprint-025

新增 tests/research_tests/test_research.py（默认 discover 覆盖）：范围约束、只读记录、备份事务回滚和新数据库恢复。独立 HTTP 套件新增研究资料、PDF 上传、遗留任务状态、比较工具草稿确认测试。最终 322 Python + 7 HTTP + 20 Node，通过 349，失败/跳过 0；隔离浏览器证据见 Sprint-025。
