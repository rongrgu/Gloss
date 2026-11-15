<!-- 核对自测题的答案（DESIGN.md §13.5 第 4 步）。可以用 course tools 查材料；输出受 JSON schema 约束。可用变量：{{questions}} {{language}} -->
下面是根据课件出的自测题，每道题带标准答案和出处。请逐题核对**标准答案和课件内容是否一致**。

- 每道题都用工具看出处页（`get_slide`，需要时加 `image: true`），必要时用 `search_course` 查别处。
- `ok`：答案正确，并且能根据课件得出，就是 true。答案错误、选择题有不止一个正确选项、或者课件里找不到依据，就是 false。
- `reason`：`ok` 为 false 时，用{{language}}写一句话说明问题；为 true 时留空字符串。
- 只核对，不要改题。每道题都要给出结果，`n` 是题号。

## 题目

{{questions}}
