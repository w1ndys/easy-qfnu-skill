# 多仓库开发协调

## 仓库定位

- 当前仓库 / 产品主线：`easy-qfnu-skill`
- 工程主仓库：`easy-qfnu-cli`
- 下游服务仓库：`easy-qfnu-hub`
- 独立预选课中转仓库：`easy-qfnu-precourse`
- 独立选课推荐仓库：`easy-qfnu-recommendation`
- 独立排名快照仓库：`easy-qfnu-ranking`
- 独立新生题库仓库：`easy-qfnu-freshman-exam`
- 独立空教室仓库：`easy-qfnu-kjs`
- 校园指南仓库：`easy-qfnu-guide`

`easy-qfnu-cli` 是跨仓库工程协调入口。各仓库分别开发、分别提交。

已归档、不再作为当前主线：`easy-qfnu-course-recommendations`（旧推荐审核后台，由 `easy-qfnu-recommendation` 替代）。

## 各仓库职责

### easy-qfnu-skill

- 定义用户能力、Agent 流程和公开使用说明；
- 维护产品需求和公开版本文档；
- 不在公开 README、SKILL.md 或 Release 文案中暴露 CLI、Hub、内部域名、部署结构或维护流程。

### easy-qfnu-cli

- 实现本地教务登录、会话、成绩、课表等能力；
- 实现固定域名 relay 和匿名统计客户端；
- 维护客户端 API 消费契约与兼容版本；公开 Release 由 `easy-qfnu-skill` 发布；
- 负责协调 Hub 与 Skill 的工程变更。

### easy-qfnu-hub

- 实现反馈、推荐提交、匿名使用统计和 Dashboard 等云端服务；
- 遵循 Skill 已确定的产品行为和 CLI 已确认的客户端契约；
- 不独立改变产品语义，不把 Hub 内部凭据、日志或数据边界暴露给客户端；
- 不直接修改推荐仓库或排名快照仓库。

### easy-qfnu-precourse

- 作为独立 Vercel 项目提供公开预选课只读中转；
- 同时提供静态网页，供用户在浏览器里按课程、教师、校区等条件查询缓存开课安排；
- 仅在服务端持有上游 API Key，过滤查询字段，不接收教务 Cookie 或账号凭据；不能选课。

### easy-qfnu-recommendation

- 作为独立 Vercel 项目提供公开选课推荐只读查询；
- 同时提供静态网页，供用户在浏览器里手动按课程名 / 教师名查询；
- 不接收教务 Cookie 或账号凭据；不能提交、编辑或删除推荐；
- 推荐提交仍走 Hub + 已登录 JWXT 中继，不在本仓库实现。

### easy-qfnu-ranking

- 作为独立 Vercel 项目提供排名只读快照查询；
- 快照数据与校验留在本仓库；Hub / CLI 不直接改快照文件。

### easy-qfnu-freshman-exam

- 作为独立 Vercel 项目提供新生入学考试题库网页与只读搜索 API；
- Skill / CLI 只消费公开搜索接口，不把完整题库打进客户端。

### easy-qfnu-kjs

- 作为独立 Vercel 项目提供空教室公开查询（采集快照 + Serverless API + 网页）；
- 教师账号只在本机采集器使用，不进入 Skill / CLI / Hub。

### easy-qfnu-guide

- 维护校园生活 / 学习指南内容；
- 不承载教务登录或查询中转。

## 跨仓库影响检查

- 新增、修改或删除功能时，必须检查上列在役仓库中其余仓库是否需要同步修改；不能只检查当前仓库。
- 检查范围至少包括产品行为、公开说明、Agent 流程、Hub 使用统计契约、各独立只读服务与网页、CLI 调用与兼容版本、发布清单和部署配置。
- 需要联动时，按各仓库职责分别修改和验证；确认无需修改时，在交付说明中明确记录已检查的仓库及无需修改的原因。
- 已归档仓库默认不改；只有迁移或安全问题时才动。

## 变更流程

1. 在 `easy-qfnu-skill` 的需求或 issue 中确定用户可见的产品行为；
2. 在 `easy-qfnu-cli` 中拆分工程任务，冻结客户端接口、统计事件和兼容要求；
3. 在对应的独立服务仓库实现只读接口 / 网页 / 快照（precourse、recommendation、ranking、freshman-exam、kjs、guide）；
4. 在 `easy-qfnu-hub` 中实现匿名使用统计契约（以及推荐提交中继，如涉及）；
5. 在 `easy-qfnu-cli` 中接入并完成客户端验证；
6. 在 `easy-qfnu-skill` 中只同步经过脱敏的公开使用说明。

API 变更顺序：外部只读接口与中转契约 → Hub 统计 / 提交契约 → CLI 客户端 → Skill 公开文档。

## 提交与发布

- 在役仓库必须分别提交，不跨仓库混合 commit；
- 每个逻辑改动先完成检查，再展示完整 diff，获得明确确认后才 commit/push；
- 发布顺序：独立只读服务 → Hub → Skill；
- 破坏性变更必须同时记录迁移要求、最低兼容版本和回滚方式；
- 不在提交、日志、测试输出或文档中写入 Cookie、Token、密码、Webhook 或其他密钥。

## 当前实施主线

```text
easy-qfnu-precourse 网页查询
→ 独立只读服务稳定
→ Hub
→ CLI
→ Skill 公开说明
```
