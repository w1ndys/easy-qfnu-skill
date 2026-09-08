# Public course and teacher recommendations

Public, read-only reviews of courses and teachers. The query does not require a JWXT session and it cannot submit, edit, or delete a recommendation.

## CLI

```bash
easy-qfnu recommendation search --course "高等数学"
easy-qfnu recommendation search --teacher "张" --top 20
easy-qfnu recommendation search --course "高等数学" --teacher "张"
```

| CLI option | Meaning |
|---|---|
| `--course` | Course-name fuzzy match |
| `--teacher` | Teacher-name fuzzy match |
| `--top` | Maximum items; default 20, maximum 100 |

At least one of `--course` or `--teacher` must be non-empty. When both are set, they are combined with AND.

## Response

The CLI returns JSON with `ok`, `source: "recommendation"`, `count`, and `items`. Each item is one review. Typical fields:

- `course_name`
- `teacher_name`
- `year`
- `reason`
- `nickname` (public when present; otherwise `null`)
- `submitted_at`

There are no scores or tags. The same course, teacher, and year may have several different reviews.

If `count` is 0 or `items` is empty, tell the user there is no public recommendation. Never invent a review, score, or teacher comment.

## Safety

- Never send a JWXT Cookie or account credential with this query.
- Do not present the results as official university ratings.
- Users cannot delete a recommendation through this skill.
- Submitting a new recommendation is a separate, confirmation-gated JWXT command; see `references/jwxt.md`.
