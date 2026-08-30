# Freshman entrance-exam question bank

Homepage: `https://fq.easy-qfnu.top/`

This skill calls only the public question-bank search API. It requires no login and never submits or modifies questions.

## API

```text
GET https://fq.easy-qfnu.top/api/questions
```

Query parameters:

| Parameter | Type | Description |
|---|---|---|
| `keyword` | string | Non-empty text matched against question or option content |
| `page` | integer | One-based page number |
| `pageSize` | integer | Items per page; CLI default 20, maximum 100 |

CLI examples:

```bash
easy-qfnu freshman search "校规"
easy-qfnu freshman search "数学" --page 2 --page-size 20
```

## Response fields

The upstream response contains `ok`, `total`, `matched`, `page`, `pageSize`, `totalPages`, and `items`.

Common item fields:

- `type`: question type, such as `单选`
- `question`: question text
- `optionA`, `optionB`, `optionC`, `optionD`: optional answer choices
- `optionAnswer`: multiple-choice answer, such as `A`
- `fillAnswerShow`, `fillAnswerType`, `fillAnswer`: fill-in-the-blank answer data
- `fillBlankCount`: number of blanks

The CLI converts `pageSize` to `page_size` and adds `source: "freshman"`, `count`, and the complete request `url`.

## Response rules

- Search by question or option keyword, then quote the question and answer from `items`.
- If no item matches, tell the user explicitly and do not guess an answer.
- If the API is unavailable, show `error` and `hint`; do not switch to an unverified data source.
- The bank is a third-party public source. For exam rules, remind the user that official university notices take precedence.
