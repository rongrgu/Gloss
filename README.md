# Gloss

Ask questions right on your lecture slides. A local study app powered by your own Claude.

把 PDF 课件变成可以提问的学习空间：思维导图、逐页讲解、在 slide 上框选提问、笔记。全部在本地运行，用你自己的 Claude。

## What it does

Import a PDF of lecture slides and Gloss gives you:

- **A mind map** of the lecture: topics on the left, every slide as a thumbnail, with badges for slides you asked about or took notes on.
- **A workspace for each slide**: the slide, an explanation of it, and your notes for that page.
- **Questions anchored to the slide**: drag a box around anything (a formula, a diagram) and ask about it. The answer appears next to that spot. Minimize it and a marker stays on the slide; click it later to pick the conversation back up. You can also select a sentence in the explanation and ask about just that.
- **The lecturer's words, matched to each slide** (optional): upload a transcript (`.txt`, `.vtt`, `.srt`) and Claude works out which part of the talk goes with which slide. Explanations and answers then draw on what was actually said in class.
- **Answers that know the whole course**: when a question reaches beyond the current slide ("where was this symbol defined?", "how is this different from the method on slide 12?"), Claude searches the rest of the lecture and tells you which slide to look at.
- **One file per course.** Everything — the slides, explanations, every question and answer, your notes and layout — is saved in a single `.gloss` file as you go. There is no save button. Close it, reopen it, it's all there.

Feeding slides straight into a chat gives you one long linear transcript where follow-ups lose track of which part of which slide they were about. Gloss keeps each question attached to the place it came from.

## Requirements

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/).
- **[Claude Code](https://claude.com/claude-code)**, installed and logged in with your Claude account (run `claude` once and use `/login`). Gloss calls the `claude` command on your machine; it never sees your credentials.
- A Claude plan with Claude Code access. Explanations and answers count toward your plan's usage limits, so Pro users may want a lighter model (see [Configuration](#configuration)).

You do **not** need Node.js: the web interface ships pre-built.

## Quick start

```sh
git clone <this repo> gloss && cd gloss
uv run gloss doctor     # checks Python, claude, login, and the install
uv run gloss            # starts the app and opens it in your browser
```

Then drop a PDF onto the home page. The first time you open a course, Claude organizes it into a mind map (one call, about 10–20 seconds). Each slide's explanation is generated the first time you stay on that slide for a moment, and then saved.

Other commands:

```sh
uv run gloss --port 9000 --no-browser
uv run gloss doctor --live          # also sends a tiny test prompt (uses a little quota)
uv run gloss export course.gloss    # writes course.md
```

## Using it

- **Mind map** — drag to pan, scroll or use the buttons to zoom, click a topic to fold it. Click a thumbnail to open that slide. "重新生成导图" asks Claude to regroup the slides; your questions and notes are untouched.
- **Slide workspace** — `←` / `→` change pages. The 布局 (layout) menu switches between stacked and side-by-side, hides panels, and resets; drag the dividers to resize (double-click resets one).
- **Ask** — drag a rectangle on the slide, type your question, press Enter. Follow up in the same box. Questions on the same slide are answered one at a time, so a second question may briefly show as queued. To ask about part of the explanation, select the text and press 就这段提问; those questions show up as numbered chips under the explanation.
- **Transcript** — on the course page, 上传 transcript. Claude aligns it to the slides (one call, usually under 20 seconds). Each slide's explanation panel can show its excerpt ("transcript 片段"). Explanations generated before the upload aren't redone automatically — use 重新生成 on the slides you care about.
- **Notes** — plain text per slide, saved automatically. "摘到笔记" under an answer copies the question and the answer's first paragraph into the page's notes.
- **后台 session** — shows exactly what was sent to Claude for this slide (for the curious).
- **Export** — "导出 Markdown" on the course page (or `gloss export`) writes the map, explanations, questions and answers, and notes as one Markdown file.

A `.gloss` file is self-contained: send it to a classmate and they can read everything in Gloss, even without Claude installed (they just can't ask new questions).

## Your data, your Claude account

- **Everything stays on your computer.** Courses are saved as `.gloss` files (by default in `~/Gloss/`). The recent-files list lives in `~/.gloss/`. Gloss runs a small web server on your own machine (`127.0.0.1`) and has no cloud service, no account, no telemetry.
- **What goes to Anthropic:** when you ask for an explanation, a question, or a mind map, Gloss runs your `claude` command with the relevant slide text and images, so that content is processed by Anthropic under your account, exactly as if you had pasted it into Claude Code yourself.
- **It uses your Claude login and your plan's usage.** Gloss does not log you in, store tokens, or proxy anyone else's account. Each person runs their own copy with their own account.
- **About Anthropic's terms:** Anthropic does not allow third-party products to offer claude.ai login or to use people's subscription usage unless approved, and expects commercial products to use API keys. Gloss is a free, open-source tool that you run yourself, driving your own locally installed Claude Code. It is not affiliated with or endorsed by Anthropic. Whether Claude Code usage keeps counting toward subscription limits is up to Anthropic and may change; you can switch Gloss to an API key instead (see [Using an API key](#using-an-api-key-instead)).
- **`ANTHROPIC_API_KEY`:** if this is set in your environment, Claude Code bills that API key instead of your subscription. Gloss removes it when it calls `claude`, and `gloss doctor` warns you if it's set.
- **Locked down by default:** Claude is run with only read-only file tools (Read, plus Grep/Glob for searching the course), restricted to that course's own working folder (`--restricted`), so it can't run commands, edit files, or read anything else on your disk — not even your other courses. The local server only accepts requests from `localhost` and refuses requests from other websites.

## Course materials and copyright

Lecture slides, transcripts and recordings belong to their authors. Use Gloss with material you're allowed to use, and **don't commit course files or `.gloss` files to this repository** (they're in `.gitignore`). Tests generate their own PDFs.

## Configuration

Copy `config.example.toml` to `config.toml` (in the repo; or `~/.gloss/config.toml` if you installed Gloss with pip):

| key | default | meaning |
|---|---|---|
| `model` | `"sonnet"` | passed to `claude --model` — an alias (`haiku`, `sonnet`, `opus`) or a full model name |
| `language` | `"中文"` | language for explanations, answers and the mind map |
| `concurrency` | `2` | how many Claude calls may run at once (different slides) |
| `qa_retrieval` | `true` | let answers search the rest of the course when a question needs it |
| `provider` | `"claude_cli"` | `"claude_cli"` (your Claude Code login) or `"anthropic_api"` (see below) |
| `library` | `~/Gloss` | where imported courses are saved |
| `claude_bin` | found on `PATH` | path to the `claude` executable |

### Using an API key instead

Set `provider = "anthropic_api"` in `config.toml`, install the extra (`uv sync --extra api`, or `pip install 'gloss[api]'`), and export `ANTHROPIC_API_KEY`. Calls are then billed to that key at API rates instead of counting toward your Claude plan; `gloss doctor` checks the setup. Differences: answers can't search the rest of the course (no file tools over the API), and every follow-up resends the slide's earlier questions and answers (prompt caching keeps the repeated part cheap). On models that support it, Gloss turns on Anthropic's server-side refusal fallback, so a request one model declines is retried on another automatically.

**Changing how Claude explains things:** the prompts are plain Markdown in `claude_workspace/` (`CLAUDE.md` for the tutor's role, `prompts/*.md` for each task). Edit them to change the style, for example exam-focused or more concise. Changes apply to new explanations and to new question sessions.

## Troubleshooting

Start with `uv run gloss doctor`.

- **"claude not found"** — install Claude Code, or set `claude_bin` in `config.toml`.
- **"not logged in"** — run `claude` in a terminal and use `/login`.
- **An answer failed** — the question is kept; press 重试 (retry). Common causes are usage limits or a network problem; the error message comes from Claude Code.
- **Port in use** — Gloss picks the next free port automatically, or pass `--port`.

## Development

See [`docs/DESIGN.md`](docs/DESIGN.md) for the design and [`CLAUDE.md`](CLAUDE.md) for commands. In short:

```sh
uv run pytest                               # backend tests
cd frontend && npm install && npm test      # frontend tests
npm run dev                                 # dev server, proxies /api to `uv run gloss --port 8765`
npm run build                               # rebuilds backend/gloss/static (committed; a test checks it's current)
```

## License

MIT — see [LICENSE](LICENSE).
