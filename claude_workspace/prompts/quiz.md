<!-- 测验出题（DESIGN.md §13.5、§13.8）。可以用 course tools 查材料；输出受 JSON schema 约束。可用变量：{{scope}} {{task}} {{settings}} {{lectures}} {{profile}} {{past_exam}} {{exams_hint}} {{instructions}} {{language}} {{why_example}} {{feedback}}。past_exam / exams_hint / instructions 为空时整节消失。 -->
学生要准备考试。请根据「{{scope}}」里的课件，以及学生学习时的提问和笔记，{{task}}。

**输出语言：{{language}}。** 这份说明是中文写的，但输出里所有给学生看的文字（包括 `title`）都必须用 {{language}}。

## 范围内的讲次

{{lectures}}

## 学生卡住过的地方（按提问、追问、笔记加权排序）

{{profile}}
{{past_exam}}
{{exams_hint}}

## 出题要求

{{settings}}
- **题型的写法**：
  - `mcq`：单选题，2–5 个选项，`answer` 是正确选项的字母（A、B、C…）。干扰项要像样，是学生真会犯的错误，不要一眼就能排除。判断题也写成 `mcq`，两个选项（用输出语言写，如 True / False 或 对 / 错）。**`stem` 只写题干，不要把选项写进题干；`options` 每项只写选项内容，不要加 "A." 之类的前缀**（界面会自动编号）。
  - `short`：简答题（填空题也用它），`options` 为空数组，`answer` 是参考答案的要点（2–4 句）。
  - `derivation`：推导或计算题，`options` 为空数组，`answer` 写出关键步骤。
- **先计划再出题**：先定下每道题考哪个知识点，覆盖不同的讲次和主题，再逐题写。
- **每道题都要先用工具看过出处页**（`get_slide`；公式、图表多的页加 `image: true`），题目必须能根据课件回答，课件之外的内容不考。
- 学生问过的问题说明这里是难点，但**不要照抄学生问过的原话**，换个角度考同一个知识点（变一个条件、反过来问、和另一页的概念对比）。
- `sources`：出处，`lecture` 写讲次编号（如 `L2`），`page` 写课件页码。至少一个。出处只能是课件页，不能是试卷。
- `why`：一句话说为什么出这道题，直接对学生说，例如：{{why_example}}
- `explanation`：解析，说清为什么这是答案、常见的错误是什么。
- `topic`：知识点名称（不超过 15 个字）。`difficulty`：1 容易，2 中等，3 难。
- `title`：这份测验的标题。
- **所有给学生看的文字都用 {{language}}**：`title`、`stem`、`options`、`answer`、`explanation`、`why`、`topic`。专有名词保留原文。公式用 `$...$`。
- 题目之间不要重复。
{{instructions}}

{{feedback}}
