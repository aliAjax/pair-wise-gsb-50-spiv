# 税务稽查案件与复议流程

纯Python标准库实现的税务稽查案件与复议流程原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、补税、滞纳金、处罚和证据完整性和冲突检查。
- `src/procedure_rules.py`：陈述申辩、补交证据、听证程序的纯函数判定（期限、逾期、办结条件）。
- `src/repository.py`：案件记录SQLite建表、事务和查询。
- `src/procedure_repository.py`：程序记录（陈述/证据/听证）独立建表与查询。
- `src/service.py`：案件用例编排、权限检查、乐观并发和审计，复核前检查程序办结条件。
- `src/procedure_service.py`：程序办理台用例编排（提交、听证办结、办理台视图）。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：最小演示页面。
- `static/desk.html`：程序办理台页面。
- `tests/`：完整流程、规则计算、程序办理台和失败场景测试。

## 启动

```bash
python3 app.py --db ./data.db --port 8326
```

默认端口为`8326`，默认数据库位于项目目录。服务启动时自动建表。

## 程序办理台

补税和处罚建议（`propose`）发出时必须写明`defense_deadline_day`（申辩截止日）和`hearing_request_deadline_day`（听证申请期限）。建议发出后、复核确认前：

- 纳税人代表可提交`statement`（陈述申辩）、`evidence`（补交证据，可带重新核定税额等字段，金额变化会重算并标记`needs_recheck`）、`hearing_request`（听证申请）。
- 逾期提交只留存记录，状态为`late_filed`并注明不予受理，不改变金额、不形成待办听证。
- 听证未办结或新证据改变金额时，`review`（复核确认）被拒绝；复核人员先执行`recheck`（重新核对）或由`hearing_conclusion`办结听证，复核时须写入`decision_basis`（决定依据）。
- 程序记录独立保存于`procedures`表，服务重启后办理台视图仍可查看。

## 主要接口

- `GET /health`：健康检查。
- `GET /`、`GET /desk.html`：演示页面与程序办理台页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/records/{id}/desk`：程序办理台视图（期限、程序记录、听证状态、办结条件）。
- `POST /api/records/{id}/procedures`：提交程序材料，请求体为`{"kind":"statement|evidence|hearing_request|hearing_conclusion","data":{...}}`。
- `GET /api/stats`：状态统计。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作（含`recheck`），请求体为`{"expected_version":1,"data":{...}}`。

除`/health`和页面外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、程序办理台（期限、逾期、听证阻断、重新核对、重启后查看）、重复引用、权限拒绝和版本冲突。
