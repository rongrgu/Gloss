<!-- Cheatsheet 修改（DESIGN.md §16.4）。输出：一句回复、分隔行、改后的全文。可用变量：{{language}} {{lectures}} {{current}} {{budget}} {{request}} {{selection}} {{history}}。selection 为空时整节消失。 -->
学生在编辑一份 cheatsheet，请按学生的要求修改它。

**输出语言：{{language}}。** 回复和 cheatsheet 都用 {{language}}。

## 范围内的讲次

{{lectures}}

## 现在的 cheatsheet（Markdown 源码）

````markdown
{{current}}
````

## 之前的对话

{{history}}

## 学生这次的要求

{{request}}
{{selection}}

## 规则

- 只做学生要求的改动；没要求改的部分**逐字保持不变**（包括公式、标题、顺序）。
- 篇幅：{{budget}}
- 需要补充内容时，可以用 course tools 查课件（`get_slide`、`search_course`、`get_notes`、`get_questions`）；没有工具时只用你已有的信息，不确定就不要编。
- 公式用 `$...$` 和 `$$...$$`。不写客套话。

## 输出格式（严格）

第一行：一句话告诉学生改了什么（不超过 40 个字 / 25 个英文词）。
第二行：只有 `===CHEATSHEET===`
之后：改好的**完整** cheatsheet，从 `# 标题` 开始，不要用代码块包起来。
