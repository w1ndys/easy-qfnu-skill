---
name: human-coding-contract
description: Use plain syntax and layered architecture for coding tasks with commit-based progress reports; 中文触发词包括人话编码契约、土法编码和分层交付；仅在用户明确启用本 skill 时使用。
---

# Human Coding Contract

This is an opt-in, project-specific coding workflow. Apply it only when the user explicitly invokes `$human-coding-contract` or asks for 人话编码契约、土法编码、分层交付, or the plain coding contract. Do not apply it as a global rule.

Communicate with the user in Chinese unless the user asks for another language. Keep the commit handoff in Chinese.

## Collaboration cadence

1. At the start of a new goal, list the files, each responsibility, and the planned commit scope for visibility. Then implement the agreed goal without waiting for a separate confirmation. Do not repeat the same inventory for later commits in that goal.
2. Design in this order: entity → data → business → entry. A continuous work segment may cover several related layers and files, but each proposed commit must represent one clear logical goal and must not bundle many independent features.
3. Complete the code for the current logical goal and run the required checks, but do not commit or push. Then report the files, a short change summary, the check results, and show the complete diff for the user to review.
4. Wait for explicit approval after the user has reviewed the diff. Only then create the commit containing exactly the reviewed changes. If the user requests revisions, update the code, rerun checks, and show a new diff without committing.
5. After every approved commit, pause and report with this exact structure. Use a short bullet list: more than one sentence, not a changelog.

   ```
   【停，等指令】

   本次提交：[commit 信息]（共 X 个文件）

   本次完成：
   - [文件或模块]：[改了什么、解决了什么]
   - [文件或模块]：[改了什么、解决了什么]

   检查：
   - [检查项]：[通过 / 失败及必要说明]

   下一步：
   - [下一个逻辑目标或待确认事项]（尚未开始编码）

   请确认，无误后回复“继续”。
   ```

   “本次完成” must list the changed files or modules, one short line each, covering what changed and why. Do not collapse the work into a single sentence. Do not write a long change document. “检查” must name the commands or quality gates and whether they passed. “下一步” is required; if the overall goal is done, say so and list remaining confirmation or wrap-up items.

6. Do not implement the next independent feature while waiting. The next scope belongs only in the “下一步” list; do not write its code early.

## Coding principles

- Prefer ordinary classes, explicit branches, direct dependencies, and traceable control flow.
- Do not use decorator factories, reflection, metaclasses, dynamic code generation, pattern matching, nested comprehensions, the walrus operator, or type gymnastics.
- Classic patterns are allowed when implemented with ordinary classes, interfaces, or map/dictionary dispatch. Do not use dynamic proxies or auto-discovery.
- Keep dependencies one-way: entry → business → data. Do not cross layers or create circular dependencies.
- Inject dependencies through constructor arguments or direct imports. Do not use DI containers, service auto-discovery, or annotation wiring.
- Keep each function at or below 50 lines, excluding blank lines and comments. Split longer functions.
- Do not hide business logic in vague `utils`, `common`, or `base` packages.

## Comments and generics

- Document parameters, return values, exceptions, and side effects for public interfaces or complex functions according to project conventions. Do not add repetitive comments to trivial functions.
- Explain complex decisions, security boundaries, and important side effects in plain language. Do not narrate obvious code.
- Add the reason and consequence beside each important condition.
- Only basic generics are allowed, such as `List[T]`, `Ref[T]`, or `[T any]`. Before writing a generic function, ask the user:

  `这处泛型是为了复用 [逻辑]，写法是 [片段]，你能看懂吗？`

  If the user does not understand it, immediately use a concrete non-generic function.

## Quality gates

- Go: run `golangci-lint run` and handle `if err != nil` explicitly.
- Python: run `ruff check .`, never use a bare `except: pass`, and split long functions.
- Vue/TypeScript: run `eslint` and `tsc --noEmit`; do not use `any` and keep types explicit.
- Treat every lint error as a bug and fix it before committing.

## Pre-commit checklist

1. The business logic still works when generics are replaced with concrete types.
2. A junior developer can find where data is queried and where rules are written.
3. Project-root quality checks are green before showing the diff.
4. Important functions and conditions are documented without jargon overload.
5. The proposed commit contains only the previously reported files and logical goal.
6. Secrets, tokens, and passwords do not appear in code, comments, logs, diff output, or commit messages.
7. The exact diff has been shown to the user, and no commit or push occurs before explicit approval.
