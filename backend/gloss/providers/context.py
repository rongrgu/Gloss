"""The context a call carries, as ordered parts (DESIGN.md §6, §7).

One implementation for two uses:
- CLI providers: rebuilding a lost or foreign session (§7.4); they get the parts flattened into one
  prompt plus image paths, exactly what they had before.
- API providers: their normal path (they have no sessions), sent as content blocks.

The order is a stable prefix then a changing tail, so prompt caching works:

    page image -> opening (page text, transcript, explanation, weak points) -> earlier Q&A (by message id)
    -> the new message (and its crop)

`cache=True` marks the end of a stable stretch (Anthropic puts a cache breakpoint there; OpenAI caches
any byte-identical prefix on its own). Nothing in the prefix may depend on time or random order.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Part:
    text: str | None = None
    image: Path | None = None
    cache: bool = False  # a cache breakpoint after this part


def flatten(parts: list[Part]) -> tuple[str, list[Path]]:
    """For CLI providers: one prompt (texts joined by blank lines) and the images in order."""
    return "\n\n".join(p.text for p in parts if p.text), [p.image for p in parts if p.image is not None]


def text_only(prompt: str, images: list[Path] = ()) -> list[Part]:
    """A one-off call (explanation, tree, quiz, summary): images first, then the prompt."""
    return [*(Part(image=Path(i)) for i in images), Part(text=prompt)]


def qa_session(page_image: Path, opening: str, history: tuple[str, list[str], str] | None, current: str,
               current_images: list[Path]) -> list[Part]:
    """A page's Q&A from scratch: the API path every time, the CLI path when a session is (re)started.

    `history` is (heading, one entry per earlier message, closing line). Each message is its own part so
    that the next question's context starts with exactly the same parts (caches reuse whole blocks);
    the closing line ("## 现在的消息") goes with the new message for the same reason.
    """
    parts = [Part(image=page_image), Part(text=opening, cache=True)]
    if history:
        heading, entries, closing = history
        parts.append(Part(text=heading))
        parts += [Part(text=e, cache=i == len(entries) - 1) for i, e in enumerate(entries)]
        current = f"{closing}\n\n{current}" if closing else current
    parts.append(Part(text=current))
    parts += [Part(image=Path(i)) for i in current_images]
    return parts


def qa_resume(current: str, current_images: list[Path]) -> list[Part]:
    """A follow-up in a live CLI session: only the new message."""
    return [Part(text=current), *(Part(image=Path(i)) for i in current_images)]
