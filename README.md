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
- **[@Claude Code](https://code.claude.com)**, installed and logged in with your Claude account (run `claude` once and use `/login`). Gloss calls the `claude` command on your machine; it never sees your credentials.
- A Claude plan with Claude Code access. Explanations and answers count toward your plan's usage limits, so Pro users may want a lighter model (see [Configuration](#configuration)).

You do **not** need Node.js: the web interface ships pre-built.

## Quick start

Install from PyPI (the package is called `glossr`; the command is `gloss`):

```sh
uv tool install glossr  # or: pipx install glossr
gloss doctor            # checks Python, claude, login, and the install
gloss                   # starts the app and opens it in your browser
```

To try it without installing: `uvx glossr`. To update later: `uv tool upgrade glossr`.

Running from a clone instead (for development, or the latest unreleased code):

```sh
git clone https://github.com/rongrgu/gloss && cd gloss
uv run gloss doctor
uv run gloss
```

Then drop a PDF onto the home page. The first time you open a course, Claude organizes it into a mind map (one call, about 10–20 seconds). Each slide's explanation is generated the first time you stay on that slide for a moment, and then saved.

Other commands (prefix with `uv run` when running from a clone):

```sh
gloss --port 9000 --no-browser
gloss doctor --live          # also sends a tiny test prompt (uses a little quota)
gloss export course.gloss    # writes course.md
```

## Using it

- **Language** — the interface is in English by default; the 中文 / EN button in the top bar switches it (and the language Claude explains and answers in, unless `language` is set in `config.toml`). Content already generated keeps its language; use Regenerate to redo it.
- **Mind map** — drag to pan, scroll or use the buttons to zoom, click a topic to fold it. Click a thumbnail to open that slide. "Regenerate map" asks Claude to regroup the slides; your questions and notes are untouched.
- **Slide workspace** — `←` / `→` change pages. The Layout menu switches between stacked and side-by-side, hides panels, and resets; drag the dividers to resize (double-click resets one).
- **Ask** — drag a rectangle on the slide, type your question, press Enter. Follow up in the same box. Questions on the same slide are answered one at a time, so a second question may briefly show as queued. To ask about part of the explanation, select the text and press "Ask about this"; those questions show up as numbered chips under the explanation.
- **Transcript** — on the course page, "Upload transcript". Claude aligns it to the slides (one call, usually under 20 seconds). Each slide's explanation panel can show its excerpt ("Transcript passage"). Explanations generated before the upload aren't redone automatically — use "Regenerate" on the slides you care about.
- **Notes** — plain text per slide, saved automatically. "Clip to notes" under an answer copies the question and the answer's first paragraph into the page's notes.
- **Session log** — shows exactly what was sent to Claude for this slide (for the curious).
- **Library** — the home page is your library folder (`~/Gloss` by default). Organize it however you like: folders for school, year, term, course… A folder that holds lectures is a course. Lectures are listed in the order you imported them; drag to reorder, or drag onto a folder (or a breadcrumb) to move. "Import PDF" puts the new lecture in the folder you are looking at. Folders on disk are the source of truth, so organizing in Finder works too.
- **Got it / still confused** — under each answer, mark whether you understand now. Unresolved questions turn orange on the slide and on the mind map, and pre-quizzes and summaries take your marks into account.
- **Finish lecture** — on the mind map, "Finish lecture" has Claude write a study summary from your questions, notes and quiz answers: what the lecture covered, what you asked (grouped by concept), what is still unclear, what you now understand, open questions, and slides worth reviewing. Every point links back to the question it came from. When you ask more later, "Update summary" refreshes it.
- **Study profile** — each course keeps a list of what you still find unclear, by concept and with evidence (your questions, marks and quiz answers). Finishing a lecture updates it; quiz answers and marks move items between "still unclear", "improving" and "resolved" on their own. You can resolve, reopen, rename, merge or delete items, and undo the last automatic change. New explanations and question sessions mention a weak point when, and only when, the slide is related to it (turn this off with `use_profile = false`).
- **Pre-quiz** — on any course (or any folder above it), "New pre-quiz" has Claude write practice questions for exam prep. It reads the slides and, above all, what you asked and noted while studying, so it targets where you got stuck, and it covers topics you never asked about too. Each question cites the slide it comes from, says why it was chosen, and has an answer and explanation; a second pass checks every answer key against the slides. Multiple choice is marked automatically; for written answers you compare with the reference answer and mark yourself. Generation takes about a minute and uses noticeably more quota than a question.
- **Export** — "Export Markdown" on the course page (or `gloss export`) writes the map, explanations, questions and answers, and notes as one Markdown file.

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
| `model` | `"sonnet"` | the model for everything — for `claude` an alias (`haiku`, `sonnet`, `opus`) or a full name. `codex` ignores Claude aliases and uses its own default; `openai_api` then uses `gpt-5.5` |
| `language` | (unset) | language for explanations, answers and the mind map. Unset, it follows the interface language (English by default; switch with the 中文 / EN button in the top bar). Set it to pin one, e.g. `"中文"` |
| `concurrency` | `2` | how many Claude calls may run at once (different slides) |
| `qa_retrieval` | `true` | let answers search the rest of the course when a question needs it |
| `quiz_model` | (unset) | model for pre-quiz generation; unset uses `model` (newer form: `[llm.models] quiz`) |
| `quiz_verify` | `true` | check every pre-quiz answer key against the slides with a second call |
| `digest_model` | (unset) | model for lecture summaries and study-profile updates; unset uses `model` (newer form: `[llm.models] digest`) |
| `use_profile` | `true` | mention relevant weak points from the study profile in new explanations and question sessions |
| `[llm] provider` | (detected) | which LLM answers: `claude`, `codex`, `anthropic_api` or `openai_api` (see below). Unset, Gloss uses the installed CLI, or asks on the home page when both are. The older top-level `provider = "claude_cli"` still works |
| `[llm.models]` | (empty) | a model per use: `outline` (mind map, transcript alignment), `explain`, `qa`, `digest`, `quiz`; empty uses `model` |
| `library` | `~/Gloss` | where imported courses are saved |
| `claude_bin` / `codex_bin` | found on `PATH` | path to the `claude` / `codex` executable |

### Choosing the LLM: your CLI login, or an API key

**You usually don't need to configure anything.** Gloss looks for Claude Code (`claude`) and Codex (`codex`) when it starts: if only one is installed it uses that one; if both are, the home page asks which to use (and lets you switch later); if neither is, the home page and `gloss doctor` tell you. `gloss doctor` shows which one is in use (`--live` sends a tiny test prompt).

To always use a particular backend, including the API ones, set `provider` under `[llm]` in `config.toml`:

| `provider` | what it uses | who pays |
|---|---|---|
| `claude` (default) | your logged-in Claude Code (`claude`) | your Claude plan |
| `codex` | your logged-in OpenAI Codex CLI (`codex`; run `codex login` first) | your ChatGPT / Codex plan |
| `anthropic_api` | the Anthropic API (`pip install 'glossr[anthropic]'`) | per token, the key in `ANTHROPIC_API_KEY` |
| `openai_api` | the OpenAI API (`pip install 'glossr[openai]'`) | per token, the key in `OPENAI_API_KEY` |

- **CLIs use your own account and plan.** Gloss runs the CLI on your machine; it never sees your login. If `ANTHROPIC_API_KEY` (for `claude`) or `OPENAI_API_KEY` / `CODEX_API_KEY` (for `codex`) is set, Gloss removes it when calling the CLI so your plan is used, not the key (for codex, `codex_auth = "api_key"` under `[llm]` opts into the key).
- **API keys are read from the environment only** — never from `config.toml`, never written to logs, `.gloss` files or exports.
- **Both CLIs run read-only.** The model can read the course's own working folder and nothing else is ever written by it; Gloss writes everything itself after checking it.
- **The `codex` provider has not yet been tested against a real Codex CLI**; its flags follow Codex's documentation and source (DESIGN.md §6 lists what still needs checking).
- **API providers differ in two ways:** they can't search the rest of the course or make pre-quizzes (both need tools), and every question resends the slide and its earlier Q&A. Gloss orders that context so the repeated part is served from the providers' prompt cache. With `anthropic_api`, models that support it get Anthropic's server-side refusal fallback. With `openai_api`, the default model is `gpt-5.5`; set `model` (or `[llm.models]`) to choose another.
- **Switching provider** keeps everything: a page's questions continue with the new provider, which is given the earlier Q&A on that page.

**Changing how Claude explains things:** the prompts are plain Markdown in `claude_workspace/` (`CLAUDE.md` for the tutor's role, `prompts/*.md` for each task). Edit them to change the style, for example exam-focused or more concise. Changes apply to new explanations and to new question sessions.

## Troubleshooting

Start with `uv run gloss doctor`.

- **"claude not found"** / **"codex not found"** — install the CLI, or set `claude_bin` / `codex_bin` in `config.toml`.
- **"not logged in"** — run `claude` in a terminal and use `/login`.
- **An answer failed** — the question is kept; press 重试 (retry). Common causes are usage limits or a network problem; the error message comes from Claude Code.
- **Port in use** — Gloss picks the next free port automatically, or pass `--port`.

## Development

Common commands:

```sh
uv run pytest                               # backend tests
cd frontend && npm install && npm test      # frontend tests
npm run dev                                 # dev server, proxies /api to `uv run gloss --port 8765`
npm run build                               # rebuilds backend/gloss/static (committed; a test checks it's current)
```

## License

MIT — see [LICENSE](LICENSE).
