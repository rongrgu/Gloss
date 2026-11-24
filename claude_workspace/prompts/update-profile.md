<!-- 更新整门课的学习档案（DESIGN.md §15.4.2 A）。输入都在这条消息里，不用工具；输出受 JSON schema 约束。可用变量：{{language}} {{current}} {{resolved}} {{findings}} {{pending}} {{limit}} -->
你在维护一个学生这门课的**学习档案**：按概念记录学生还没弄懂的地方（薄弱点）。下面是现在的档案，以及刚得到的新证据。请输出一组操作来更新档案。

**输出语言：{{language}}。** 新的或改过的 `concept`、`summary`、`reason` 都用 {{language}}。

所有内容都在这条消息里，不要使用任何工具。

## 现在的薄弱点

{{current}}

已经解决的（只列名称）。新证据如果又是这些概念，**用原来的编号**（attach，需要时用 status 改回 open），不要新建：

{{resolved}}

## 新证据：刚完成的一讲总结

（F 开头的编号。`weak` / `open` 说明学生还不会，`understood` 说明学生弄懂了。）

{{findings}}

## 新证据：还没归类的测验错题

（P 开头的编号。）

{{pending}}

## 可以用的操作

- `{"op": "add", "concept": "…", "summary": "…", "evidence": ["F1"]}`：新的薄弱点。只有新证据里出现了档案里**没有**的概念时才加。证据里至少要有一条说明学生不会的（`weak`、`open` 或测验错题）。编号由系统分配，不要自己写 `id`。
- `{"op": "attach", "id": "W3", "evidence": ["F2", "P1"]}`：新证据属于已有的薄弱点（同一个概念，哪怕在不同的讲次、说法不同）。
- `{"op": "status", "id": "W2", "to": "improving" | "resolved" | "open", "reason": "…", "evidence": ["F4"]}`：改状态。改成 `resolved` 必须有学生弄懂了的证据（`understood`）。
- `{"op": "merge", "from": "W5", "into": "W3", "reason": "…"}`：两条其实是同一个概念。
- `{"op": "edit", "id": "W3", "concept": "…", "summary": "…"}`：改名或者改写摘要，让它更准确。

## 要求

- **优先归到已有的薄弱点**（attach），不要为同一个概念再加一条。总结里写明"就是 Wn"的，一般就 attach 到那一条。
- 概念名要具体（"多头注意力里每个头的维度"），不要太宽（"注意力"）。`summary` 一句话说学生具体卡在哪里。
- 没有新证据支撑的操作不要做。没有需要做的就输出空的 `ops`。
- {{limit}}
