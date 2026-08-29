# 新生入学考试题库

题库首页：`https://fq.easy-qfnu.top/`

本 skill 只调用题库公开搜索 API，不需要登录，也不会提交或修改题目。

## API

```text
GET https://fq.easy-qfnu.top/api/questions
```

查询参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `keyword` | string | 搜索题干或选项内容，不能为空 |
| `page` | integer | 页码，从 1 开始 |
| `pageSize` | integer | 每页数量，CLI 默认 20，最大 100 |

CLI 调用：

```bash
python3 scripts/qfnu freshman search "校规"
python3 scripts/qfnu freshman search "数学" --page 2 --page-size 20
```

## 返回字段

接口原始返回包含 `ok`、`total`、`matched`、`page`、`pageSize`、`totalPages` 和 `items`。

每道题常见字段：

- `type`：题型，例如「单选」
- `question`：题干
- `optionA`、`optionB`、`optionC`、`optionD`：选项，可能为空
- `optionAnswer`：选择题答案，例如 `A`
- `fillAnswerShow`、`fillAnswerType`、`fillAnswer`：填空题答案相关字段
- `fillBlankCount`：填空数量

CLI 会把 `pageSize` 转成 JSON 的 `page_size`，并补充 `source: "freshman"`、`count` 和完整请求 `url`。

## 回答规则

- 先按题干或选项关键词搜索，再引用 `items` 中的题目和答案。
- 搜索结果为空时，明确告诉用户没有匹配题目，不要猜答案。
- 题库接口不可访问时，展示 `error` 和 `hint`，不要改用其他未确认的数据源。
- 题库内容来自第三方公开站点，涉及考试规则时应提示用户以学校正式通知为准。
