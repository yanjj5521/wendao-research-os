# v2.0 规模化联机扩展接口

## 这次做了什么

v2.0 继续只保留“可替换边界”，不建设也不启用面向成百上千人的云端服务。

- `services/sync_backend.py` 定义统一后端协议；
- `disabled` 是默认后端，不排队、不上传、不拉取；
- `legacy_hub` 只是兼容已有小团队的适配器；
- `cloud_v2` 是不可启用的预留槽位；
- `/api/sync/capabilities` 返回机器可读能力；
- `scalable-sync-api.openapi.yaml` 固定未来云端的 HTTP 契约草案。

这意味着未来替换后端时，每日任务、知识库、工作区、洞府和境界代码不需要直接依赖某个服务器。

## 客户端事件契约

每个事件预留以下稳定字段：

| 字段 | 作用 |
|---|---|
| `event_uuid` | 全局唯一编号，用于幂等去重 |
| `event_type` | 稳定事件类型 |
| `schema_version` | 单个事件的数据版本 |
| `aggregate_type` / `aggregate_id` | 事件所属对象 |
| `sequence_no` | 同一对象内的顺序 |
| `occurred_at` | 客户端发生时间 |
| `payload` | 版本化业务数据 |

当前轻量同行会会忽略它不认识的扩展字段，因此旧接口仍兼容。

## 真正扩容时必须补齐

1. 采用 OAuth 2.0 / OIDC 登录，不复用当前手工 Token 方案；
2. 服务端使用 PostgreSQL，并按用户或组织启用行级安全；
3. 所有写入带租户标识、幂等键、审计记录和服务端时间；
4. 事件批处理支持游标、重试退避、死信队列和限流；
5. 用户资料与公共状态进入服务端数据库，科研原文件默认仍保持本地；
6. 只有用户明确选择云文件功能后，附件才进入独立对象存储；
7. 上线前完成负载测试、恢复演练、数据导出与账号删除流程。

当前 SQLite 同行会不得直接宣传为“千人后端”。它适合本机或小团队，保留它只是为了兼容。

## 依据

- [OpenAPI Specification](https://spec.openapis.org/oas/latest.html)：用语言无关的接口描述固定客户端与服务端边界；
- [OAuth 2.0 Security Best Current Practice](https://www.rfc-editor.org/rfc/rfc9700.html)：未来标准登录与令牌安全基线；
- [PostgreSQL Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)：按用户或租户限制可见、可写数据；
- [HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)：保持标准 HTTP 行为与可替换性。
