# 税务稽查案件与复议流程

纯Python标准库实现的税务稽查案件与复议流程原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、补税、滞纳金、处罚计算、办理台判定（期限、逾期不受理、复核闸门）。
- `src/repository.py`：SQLite建表、事务和查询（记录保存）。
- `src/service.py`：用例编排、权限检查、乐观并发和审计。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：办理台演示页面（与后端分开维护）。
- `tests/`：完整流程、规则计算、办理台规则和失败场景测试。

## 启动

```bash
python3 app.py --db ./data.db --port 8326
```

默认端口为`8326`，默认数据库位于项目目录。服务启动时自动建表；办理台数据随记录保存在SQLite，服务重开后仍可查看。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：办理台页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/workbench`：办理台状态（期限、各项登记、听证状态、金额变更、程序是否办完、可否复核）。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：状态统计。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 办理台：陈述申辩、补交证据与听证

建议发出前的流程为`opened → investigating → proposed`。提出建议（`propose`，inspector）时必须写明：

- `defense_deadline_day`：陈述申辩/补交证据截止日（第几天）。
- `hearing_deadline_day`：听证申请期限（第几天）。

案件进入`proposed`后开放办理台动作（均为同状态登记，动作后仍停留在`proposed`，直至复核）：

- `defend`（taxpayer_rep）：陈述申辩，参数`defense_day`、`statement`。
- `submit_evidence`（taxpayer_rep）：补交证据，参数`evidence_day`、`items`（文本列表），可选`assessed_tax`；受理后证据份数累加，若核定税额改变则重算补税、滞纳金、处罚并标记`amount_changed`。
- `request_hearing`（taxpayer_rep）：申请听证，参数`request_day`、`reason`。
- `conclude_hearing`（inspector）：听证办结，参数`conclusion`，可选`assessed_tax`。

逾期规则：申请日超过对应期限时**只留记录**（写入payload登记列表和审计时间线），条目标记`accepted=false`并注明“不予受理”，不改变案件状态、证据份数或金额。

复核闸门（`review`，reviewer）：

- 听证处于受理未办结（`hearing_status=open`）时，原决定不能确认，返回冲突错误。
- 听证已办结或新证据改变过金额时，复核人员必须重新核对并填写`decision_basis`（决定依据），否则拒绝；决定依据写入记录并在办理台展示。

复核后流程不变：`reviewed → appealed → closed`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、办理台期限与逾期不受理、听证阻断复核、新证据改金额与决定依据、重开持久化、权限拒绝和版本冲突。
