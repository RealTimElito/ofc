"""Multi-stage report generation pipeline."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LlmProfile, PipelineRun, ReportProject
from app.services.context import build_context_pack, has_real_examples
from app.services.llm import (
    LlmCancelled,
    LlmClient,
    config_from_profile,
    config_from_settings,
)
from app.services.style_cache import (
    fingerprint_examples,
    load_style_notes,
    save_style_notes,
)


class GenerationCancelled(Exception):
    """Raised when a background generate is cancelled (between or mid LLM call)."""


STYLE_NOTES_PROMPT = """You analyze example reports to extract reusable *writing style* signals.
Do not summarize facts or outcomes from the examples. Extract only how they are written.
Ignore stubs, placeholders, or tiny fragments (e.g. smoke tests) when richer examples exist.
When examples disagree, prefer section naming and voice from the longer, fuller reports.

## Example reports
{examples}

---
Produce concise Markdown style notes under these headings (skip a heading only if
nothing useful appears in the examples):

### Section naming
Typical heading wording and order (quote the names used).

### Voice & person
Formality, tense, and person (e.g. third-person past, impersonal passive).
Describe patterns only — do not quote full outcome sentences from examples.

### Recurring phrases
Short *templates* for openers, transitions, and closings (≤ ~6 words), with
placeholders like [METRIC] / [FINDING] when a slot would hold a claim.
Never quote full factual sentences, metric outcomes, or outlook recommendations
from the examples (those are period-specific, not style).

### Terminology
Preferred label wording for metrics/sections (and what to avoid if contrast is clear).
Do not treat example-only metric names as required vocabulary for new reports.

### Metrics & findings
How numbers, comparisons, and results are stated (units, precision, hedging).
Show the *pattern* (e.g. "Tickets closed: [N]") — not the example numbers.
Do not list stock outcome phrases (e.g. "within planned bands") — those are claims.

### Hedging & certainty
How claims are qualified (e.g. "indicates", "suggests", "was observed").

### Closing / outlook
Describe structure only (e.g. "1–3 imperative bullets about next period grounded
in notes"). Do not copy or paraphrase example outlook bullets or recommendations.

End with a short **Formulation preferences** bullet list the draft stage can follow.
Those bullets must be style rules only — no transplanted facts or outlook items.
Output only the style notes."""


OUTLINE_PROMPT = """Title: {title}

## Report brief
{brief}

## Results / data to include
{results_context}

## Style notes from examples (prefer these formulations; do not invent facts)
{style_notes}

## Example reports (style & structure reference — do not copy facts unless also in results)
{examples}

---
Produce a structured Markdown outline for the report. Prefer section names and
ordering that match the style notes / examples when they fit the brief and results.
Include bullet points of what each section must cover, grounded only in the brief
and results. Outlook / next-steps bullets may only restate notes or constraints
from results_context or the brief — never from examples. Do not reuse example
openers, staffing/backlog/"planned bands" themes, or prior-period recommendations.
If results/brief give no outlook content, omit Outlook or leave it empty.
Do not write the full report yet.
Output only the Markdown outline — no preamble, no closing notes, and no
commentary about what you omitted or alternative outlines."""


DRAFT_PROMPT = """Title: {title}

## Report brief
{brief}

## Results / data to include
{results_context}

## Style notes from examples (HOW to write — not WHAT to claim)
{style_notes}

## Example reports (match structure & voice only; never copy claims not in results)
{examples}

## Approved outline
{outline}

---
Write the full report in Markdown. Follow the outline.
Every factual claim, figure, incident, and outlook/next-step bullet must be
supported by results_context or the brief. If examples conflict with results,
prefer results. Style notes and examples supply voice/structure only.

Language consistency (required when style notes are present):
- Prefer the same type of wording, section names, tense/person, terminology, metric
  phrasing, hedging, and closing *patterns* listed in the style notes.
- Do not paste example sentences, openers, or outlook lines even if they appear
  in style notes as quoted phrases.
- Do not invent performance narratives (e.g. "within planned bands") unless that
  wording or fact appears in results_context or the brief.
- Do not copy example-only facts, names, figures, or recommendations.
- Do not introduce metric names that do not appear in results.
- Prefer section names from the richest prior-report examples when they fit the brief;
  do not invent extra sections or duplicate headings.
- If results/brief give no outlook content, keep Outlook empty or omit it — do not
  borrow prior-period recommendations.
- Do not invent staffing, backlog-aging, capacity, or "planned bands" themes unless
  those words appear in results_context or the brief.
- Mentions of incidents or sweeps must stay faithful to result notes (no new causes).

Be precise. Output only the report."""


CRITIQUE_PROMPT = """You are reviewing a draft report written for an air-gapped organization.

## Brief
{brief}

## Results / data that should be reflected
{results_context}

## Style notes (voice/structure only — not a source of facts)
{style_notes}

## Draft
{draft}
{bleed_hints}
---
List concrete issues in these categories:
1. Missing data or invented claims (vs results/brief)
2. Example-bleed: sentences, openers, or outlook/next-step bullets that match
   prior-report examples or style-note quotations but are not supported by
   results_context or the brief (flag even if stylistically fluent)
3. Structure mismatches with the brief
4. Language / formulation drift from the style notes (wrong section naming, voice,
   terminology, metric phrasing, hedging, or closing *patterns* — not missing
   example-only facts)

Be terse. End with a short "revised priority fixes" list."""


REVISE_PROMPT = """Title: {title}

## Brief
{brief}

## Results
{results_context}

## Style notes (restore voice/structure only — not facts)
{style_notes}

## Critique
{critique}

## Draft
{draft}
{bleed_hints}
---
Produce a revised Markdown report that addresses the critique while staying faithful
to the results and brief. Keep house style (section names, tense, metric phrasing
patterns) from the style notes, but remove or rewrite any claim, opener, or
outlook/next-step bullet that is not grounded in results_context or the brief.
Do not reintroduce example-only wording. If outlook has no support in results/brief,
omit Outlook entirely. Output only the revised report."""


NO_STYLE_NOTES = (
    "_(No example reports attached — use clear, professional report language "
    "consistent with the brief.)_"
)

_SPAN_SPLIT = re.compile(r"(?<=[.!;?])\s+|\n+")
_OMIT_CLAIM = "[example-specific claim omitted]"
_TRAIL_PUNCT = re.compile(r"[\s.!;:,\"']+$")


def _norm_overlap_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _norm_claim_key(text: str) -> str:
    return _TRAIL_PUNCT.sub("", _norm_overlap_text(text))


_BLEED_STOPWORDS = frozenset(
    """
    a an the and or but if to of in on for with from by as at is was were be been
    being this that these those it its their there here than then also into over
    under about after before during without within due any all may can will would
    should could none null n/a
    """.split()
)

# Month names → common abbreviations so "August 12" soft-matches "12 Aug".
_MONTH_TO_ABBR = {
    "january": "jan",
    "february": "feb",
    "march": "mar",
    "april": "apr",
    "may": "may",
    "june": "jun",
    "july": "jul",
    "august": "aug",
    "september": "sep",
    "october": "oct",
    "november": "nov",
    "december": "dec",
}


def _norm_allowed_text(text: str) -> str:
    """Normalize allowed fact text for bleed comparison (month aliases)."""
    base = _norm_overlap_text(text)
    parts: list[str] = []
    for token in base.split():
        parts.append(token)
        abbr = _MONTH_TO_ABBR.get(token)
        if abbr and abbr != token:
            parts.append(abbr)
    return " ".join(parts)


def _soft_subphrase_keys(
    key: str, *, min_words: int = 3, min_len: int = 18
) -> list[str]:
    """Contiguous word n-grams from a claim — catch paraphrase soft-bleed."""
    words = [w for w in (key or "").split() if w]
    if not words:
        return []
    out: list[str] = []
    seen: set[str] = set()
    # Longer first so scrubbing prefers the most specific overlapping span.
    for n in range(len(words), min_words - 1, -1):
        for i in range(0, len(words) - n + 1):
            chunk = " ".join(words[i : i + n])
            if len(chunk) < min_len or chunk in seen:
                continue
            seen.add(chunk)
            out.append(chunk)
    return out


def _is_metric_span(raw: str) -> bool:
    """True for 'Label: 12.3' style bullets — soft n-grams would over-match labels."""
    return bool(re.search(r":\s*\d", raw or ""))


def _supported_by_allowed(cand: str, allowed_cmp: str, *, min_ratio: float = 0.65) -> bool:
    """True when a draft span is grounded in results/brief despite wording drift."""
    if not cand:
        return False
    if cand in allowed_cmp:
        return True
    # Metric-style: every numeric token must appear in allowed.
    nums = re.findall(r"\d+(?:\.\d+)?", cand)
    if nums and _is_metric_span(cand) and all(n in allowed_cmp for n in nums):
        label = re.sub(r":\s*\d+(?:\.\d+)?.*$", "", cand).strip()
        label_toks = [t for t in label.split() if t not in _BLEED_STOPWORDS]
        if not label_toks or any(t in allowed_cmp for t in label_toks):
            return True
    toks = [
        w
        for w in cand.split()
        if w and w not in _BLEED_STOPWORDS
    ]
    if len(toks) < 3:
        return cand in allowed_cmp
    hits = 0
    for tok in toks:
        if tok in allowed_cmp:
            hits += 1
            continue
        abbr = _MONTH_TO_ABBR.get(tok)
        if abbr and abbr in allowed_cmp:
            hits += 1
    return (hits / len(toks)) >= min_ratio


def _example_claim_spans(examples: str, *, min_len: int = 20) -> list[str]:
    """Extract candidate factual spans from example report text."""
    spans: list[str] = []
    seen: set[str] = set()
    for part in _SPAN_SPLIT.split(examples or ""):
        raw = " ".join(part.strip().lstrip("-*• ").split())
        raw = _TRAIL_PUNCT.sub("", raw).strip()
        if len(raw) < min_len:
            continue
        if raw.startswith("#") or raw.startswith("|") or raw.startswith("---"):
            continue
        # Drop heading-only leftovers
        if raw.lower().startswith(
            ("executive overview", "key results", "outlook", "summary", "key metrics")
        ) and len(raw) < 40:
            continue
        key = _norm_claim_key(raw)
        if len(key) < min_len or key in seen:
            continue
        seen.add(key)
        spans.append(raw)
    return spans


def example_bleed_phrases(
    draft: str,
    examples: str,
    *,
    allowed: str,
    min_len: int = 20,
) -> list[str]:
    """Return example spans (or soft subphrases) in draft but not in allowed sources.

    Keeps at most one match per example span (longest soft hit) so the phrase
    budget is not flooded by n-grams of a single claim — that previously let
    other bleeds (e.g. outlook fluff) slip past scrubbing.
    """
    if not has_real_examples(examples) or not (draft or "").strip():
        return []
    draft_cmp = _norm_overlap_text(draft)
    allowed_cmp = _norm_allowed_text(allowed)
    found: list[str] = []
    found_keys: set[str] = set()
    soft_min = min(min_len, 18)
    for raw in _example_claim_spans(examples, min_len=min_len):
        key = _norm_claim_key(raw)
        if len(key) < soft_min:
            continue
        # Metric bullets: exact span only (avoid stripping shared labels like
        # "Mean time to resolve" when the figure differs).
        candidates = (
            [key]
            if _is_metric_span(raw)
            else _soft_subphrase_keys(key, min_words=3, min_len=soft_min)
        )
        matched: str | None = None
        for cand in candidates:  # longer first
            if cand not in draft_cmp:
                continue
            # Longest in-draft hit decides for this span — do not fall through
            # to shorter fragments of an already-grounded claim.
            if not _supported_by_allowed(cand, allowed_cmp):
                matched = cand
            break
        if not matched or matched in found_keys:
            continue
        # Skip if a longer phrase already covers this match.
        if any(matched != prev and matched in prev for prev in found_keys):
            continue
        # Replace shorter phrases covered by this longer match.
        drop = {prev for prev in found_keys if prev != matched and prev in matched}
        if drop:
            found = [p for p in found if p not in drop]
            found_keys -= drop
        found.append(matched)
        found_keys.add(matched)
    return found[:32]


def format_bleed_hints(phrases: list[str]) -> str:
    if not phrases:
        return ""
    bullets = "\n".join(f"- {p}" for p in phrases)
    return (
        "\n## Machine-detected example-bleed candidates\n"
        "These spans appear in the draft and in examples, but not in results/brief. "
        "Treat as invented/copied unless you can cite results/brief support:\n"
        f"{bullets}\n"
    )


def sanitize_style_notes(notes: str, examples: str) -> str:
    """Strip transplanted example claims from style notes text."""
    text = notes or ""
    if not text.strip() or not has_real_examples(examples):
        return text
    for raw in sorted(_example_claim_spans(examples, min_len=20), key=len, reverse=True):
        key = _norm_claim_key(raw)
        if len(key) < 20:
            continue
        # Replace case-insensitively even when trailing punctuation differs.
        pattern = re.compile(re.escape(key), re.IGNORECASE)
        text = pattern.sub(_OMIT_CLAIM, text)
        pattern_raw = re.compile(re.escape(raw), re.IGNORECASE)
        text = pattern_raw.sub(_OMIT_CLAIM, text)
        for soft in _soft_subphrase_keys(key, min_words=3, min_len=18):
            if soft == key:
                continue
            text = re.compile(re.escape(soft), re.IGNORECASE).sub(_OMIT_CLAIM, text)
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        core = stripped.lstrip("-* ").strip(" \"'")
        if _OMIT_CLAIM in core and len(re.sub(re.escape(_OMIT_CLAIM), "", core).strip(" .;,\"'")) < 8:
            continue
        cleaned.append(line)
    out = "\n".join(cleaned).strip()
    return out + ("\n" if (notes or "").endswith("\n") else "")


def _line_without_bleed(line: str, phrase_keys: list[str]) -> str:
    """Drop sentences/bullets that contain a bleed phrase; keep headings."""
    stripped = line.strip()
    if not stripped:
        return line.rstrip()
    if stripped.startswith("#"):
        return line.rstrip()
    bullet = ""
    match = re.match(r"^([ \t]*[-*•]\s+)", line)
    content = line
    if match:
        bullet = match.group(1)
        content = line[match.end() :]
    kept: list[str] = []
    for part in _SPAN_SPLIT.split(content):
        piece = " ".join(part.split()).strip()
        if not piece:
            continue
        piece_cmp = _norm_overlap_text(piece)
        if any(pk in piece_cmp for pk in phrase_keys):
            continue
        kept.append(piece)
    if not kept:
        return ""
    return f"{bullet}{' '.join(kept)}".rstrip()


def strip_example_bleed(body: str, examples: str, *, allowed: str) -> str:
    """Remove example-only spans (including soft subphrases) from a draft body."""
    text = body or ""
    phrases = example_bleed_phrases(text, examples, allowed=allowed)
    if not phrases:
        return text
    phrase_keys = [_norm_claim_key(p) for p in phrases if _norm_claim_key(p)]
    lines: list[str] = []
    for line in text.splitlines():
        scrubbed = _line_without_bleed(line, phrase_keys)
        stripped = scrubbed.strip()
        if not stripped:
            continue
        if stripped in {"-", "*", "- *", "* -"}:
            continue
        if re.fullmatch(r"[-*•]\s*\.?", stripped):
            continue
        lines.append(scrubbed)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out + ("\n" if (body or "").endswith("\n") else "")


_HEADING_LINE = re.compile(r"^(#{1,6})\s+(\S.*)$")
_NULLISH_BULLET = re.compile(
    r"^([ \t]*[-*•]\s+)?(none|null|n/?a|n\.a\.?)\s*$",
    re.IGNORECASE,
)
_META_COMMENTARY = re.compile(
    r"^(note\s*:|here is |here'?s |i('ve| have| will|'ll) |if you('d| would) |"
    r"however\s*,|as requested|let me |the outline (would|above)|"
    r"i omitted|i'?ve omitted|alternatively\s*:)",
    re.IGNORECASE,
)


def _is_meta_commentary(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("|"):
        return False
    if stripped.startswith(("-", "*", "•")):
        return False
    return bool(_META_COMMENTARY.match(stripped))


def sanitize_generated_markdown(text: str) -> str:
    """Drop LLM chat preamble, meta notes, duplicate restarts, and nullish bullets.

    Small local models often wrap outlines/drafts with 'Here is…' / 'Note:…' and
    re-emit a second outline. That noise then pollutes later pipeline stages.
    """
    raw = text or ""
    if not raw.strip():
        return raw
    lines = raw.replace("\r\n", "\n").split("\n")

    start = 0
    found_heading = False
    for i, line in enumerate(lines):
        if _HEADING_LINE.match(line.strip()):
            start = i
            found_heading = True
            break
    lines = lines[start:]
    if not found_heading:
        while lines and (
            not lines[0].strip() or _is_meta_commentary(lines[0].strip())
        ):
            lines = lines[1:]

    first_h1: str | None = None
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        heading = _HEADING_LINE.match(stripped)
        if heading and heading.group(1) == "#":
            title_key = heading.group(2).strip().lower()
            if first_h1 is None:
                first_h1 = title_key
            elif title_key == first_h1:
                break
        if kept and _is_meta_commentary(stripped):
            break
        if _NULLISH_BULLET.match(stripped):
            continue
        if re.fullmatch(r"[-*•]\s*", stripped):
            continue
        kept.append(line)

    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    if not out:
        return out
    return out + ("\n" if raw.endswith("\n") else "")


class ReportPipeline:
    def __init__(
        self,
        db: Session,
        project: ReportProject,
        *,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        self.db = db
        self.project = project
        self._cancel_check = cancel_check or (lambda: False)
        self.client = self._make_client()
        self.pack = build_context_pack(
            db,
            file_ids_json=project.file_ids_json,
            query_ids_json=project.query_ids_json,
            document_ids_json=getattr(project, "document_ids_json", None) or "[]",
            example_file_ids_json=getattr(project, "example_file_ids_json", None) or "[]",
            use_all_examples=bool(getattr(project, "use_all_examples", True)),
            brief=project.brief,
            title=project.title,
        )

    def _raise_if_cancelled(self, stage: str) -> None:
        if not self._cancel_check():
            return
        self.project.status = "cancelled"
        self.db.commit()
        self._log_run(stage, "cancelled", "cancelled by user")
        raise GenerationCancelled(stage)

    async def _chat(
        self,
        prompt: str,
        *,
        stage: str,
        temperature: Optional[float] = None,
    ) -> str:
        """LLM chat that aborts in-flight HTTP when cancel is requested."""
        try:
            if temperature is None:
                return await self.client.chat(
                    prompt, cancel_check=self._cancel_check
                )
            return await self.client.chat(
                prompt,
                temperature=temperature,
                cancel_check=self._cancel_check,
            )
        except LlmCancelled as exc:
            self.project.status = "cancelled"
            self.db.commit()
            self._log_run(stage, "cancelled", "cancelled by user during LLM call")
            raise GenerationCancelled(stage) from exc

    def _make_client(self) -> LlmClient:
        if self.project.llm_profile_id:
            profile = self.db.get(LlmProfile, self.project.llm_profile_id)
            if profile:
                return LlmClient(config_from_profile(profile))
        return LlmClient(config_from_settings(get_settings()))

    def _style_notes_for_prompt(self) -> str:
        notes = (getattr(self.project, "style_notes_md", None) or "").strip()
        return notes or NO_STYLE_NOTES

    def _examples_fingerprint(self) -> str:
        return fingerprint_examples(self.pack.get("examples") or "")

    def _allowed_fact_text(self) -> str:
        return "\n".join(
            (
                self.pack.get("brief") or "",
                self.pack.get("results_context") or "",
            )
        )

    def _bleed_hints_for_draft(self, draft: str) -> str:
        phrases = example_bleed_phrases(
            draft,
            self.pack.get("examples") or "",
            allowed=self._allowed_fact_text(),
        )
        return format_bleed_hints(phrases)

    def _scrub_body(self, body: str) -> str:
        cleaned = sanitize_generated_markdown(body)
        cleaned = strip_example_bleed(
            cleaned,
            self.pack.get("examples") or "",
            allowed=self._allowed_fact_text(),
        )
        return sanitize_generated_markdown(cleaned)

    def scrub_current_draft(self) -> tuple[str, list[str]]:
        """Re-apply example-bleed scrub to body_md (and outline) without regenerating."""
        before = self.project.body_md or ""
        phrases = example_bleed_phrases(
            before,
            self.pack.get("examples") or "",
            allowed=self._allowed_fact_text(),
        )
        scrubbed = self._scrub_body(before)
        self.project.body_md = scrubbed
        outline = self.project.outline_md or ""
        if outline.strip():
            self.project.outline_md = self._scrub_body(outline)
        self.db.commit()
        removed = max(0, len(phrases))
        self._log_run(
            "scrub_bleed",
            "ok",
            f"removed_candidates={removed}; phrases={phrases[:12]!r}",
        )
        return scrubbed, phrases

    def _apply_style_notes(self, notes: str, key: str, *, from_cache: bool) -> str:
        cleaned = sanitize_style_notes(notes, self.pack.get("examples") or "")
        self.project.style_notes_md = cleaned
        self.project.style_notes_key = key
        self.db.commit()
        source = "cache" if from_cache else "llm"
        self._log_run("style_notes", "ok", f"[{source}] {cleaned[:1900]}")
        return cleaned

    def _log_run(self, stage: str, status: str, log_text: str = "") -> PipelineRun:
        run = PipelineRun(
            project_id=self.project.id,
            stage=stage,
            status=status,
            log_text=log_text[:20_000],
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    async def run_style_notes(self, *, force: bool = False) -> str:
        """Extract reusable formulation signals from attached examples."""
        self._raise_if_cancelled("style_notes")
        if not has_real_examples(self.pack["examples"]):
            self.project.style_notes_md = ""
            self.project.style_notes_key = ""
            self.db.commit()
            self._log_run("style_notes", "skipped", "no examples attached")
            return ""

        key = self._examples_fingerprint()
        settings = get_settings()
        settings.ensure_dirs()

        if not force:
            existing = (getattr(self.project, "style_notes_md", None) or "").strip()
            existing_key = getattr(self.project, "style_notes_key", None) or ""
            if existing and existing_key == key:
                return existing
            cached = load_style_notes(settings.style_cache_dir, key)
            if cached:
                return self._apply_style_notes(cached, key, from_cache=True)

        prior_status = self.project.status or "draft"
        self.project.status = "style_notes"
        self.db.commit()
        prompt = STYLE_NOTES_PROMPT.format(examples=self.pack["examples"])
        try:
            notes = await self._chat(prompt, stage="style_notes", temperature=0.2)
            cleaned = sanitize_style_notes(notes, self.pack["examples"])
            save_style_notes(settings.style_cache_dir, key, cleaned)
            result = self._apply_style_notes(cleaned, key, from_cache=False)
            # Standalone style_notes should not leave the sticky in-progress label.
            in_flight = {
                "queued",
                "style_notes",
                "outlining",
                "drafting",
                "critiquing",
                "revising",
            }
            if self.project.status == "style_notes":
                if prior_status not in in_flight:
                    self.project.status = prior_status
                    self.db.commit()
                elif force and prior_status == "queued":
                    self.project.status = (
                        "ready" if (self.project.body_md or "").strip() else "draft"
                    )
                    self.db.commit()
            return result
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("style_notes", "error", str(exc))
            raise

    async def ensure_style_notes(self) -> str:
        if not has_real_examples(self.pack["examples"]):
            if getattr(self.project, "style_notes_md", None) or getattr(
                self.project, "style_notes_key", None
            ):
                self.project.style_notes_md = ""
                self.project.style_notes_key = ""
                self.db.commit()
            return ""
        return await self.run_style_notes(force=False)

    async def run_outline(self) -> str:
        self._raise_if_cancelled("outline")
        await self.ensure_style_notes()
        self._raise_if_cancelled("outline")
        self.project.status = "outlining"
        self.db.commit()
        prompt = OUTLINE_PROMPT.format(
            **self.pack,
            style_notes=self._style_notes_for_prompt(),
        )
        try:
            outline = self._scrub_body(await self._chat(prompt, stage="outline"))
            self.project.outline_md = outline
            self.project.status = "outlined"
            self.db.commit()
            self._log_run("outline", "ok", outline[:2000])
            return outline
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("outline", "error", str(exc))
            raise

    async def run_draft(self) -> str:
        self._raise_if_cancelled("draft")
        if not self.project.outline_md.strip():
            await self.run_outline()
        else:
            await self.ensure_style_notes()
        self._raise_if_cancelled("draft")
        self.project.status = "drafting"
        self.db.commit()
        prompt = DRAFT_PROMPT.format(
            **self.pack,
            outline=self.project.outline_md,
            style_notes=self._style_notes_for_prompt(),
        )
        try:
            body = self._scrub_body(await self._chat(prompt, stage="draft"))
            self.project.body_md = body
            self.project.status = "drafted"
            self.db.commit()
            self._log_run("draft", "ok", body[:2000])
            return body
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("draft", "error", str(exc))
            raise

    async def run_critique(self) -> str:
        self._raise_if_cancelled("critique")
        if not self.project.body_md.strip():
            await self.run_draft()
        else:
            await self.ensure_style_notes()
        self._raise_if_cancelled("critique")
        self.project.status = "critiquing"
        self.db.commit()
        prompt = CRITIQUE_PROMPT.format(
            brief=self.pack["brief"],
            results_context=self.pack["results_context"],
            style_notes=self._style_notes_for_prompt(),
            draft=self.project.body_md,
            bleed_hints=self._bleed_hints_for_draft(self.project.body_md),
        )
        try:
            critique = await self._chat(prompt, stage="critique", temperature=0.2)
            self.project.critique_md = critique
            self.project.status = "critiqued"
            self.db.commit()
            self._log_run("critique", "ok", critique[:2000])
            return critique
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("critique", "error", str(exc))
            raise

    async def run_revise(self) -> str:
        self._raise_if_cancelled("revise")
        if not self.project.critique_md.strip():
            await self.run_critique()
        else:
            await self.ensure_style_notes()
        self._raise_if_cancelled("revise")
        self.project.status = "revising"
        self.db.commit()
        prompt = REVISE_PROMPT.format(
            title=self.pack["title"],
            brief=self.pack["brief"],
            results_context=self.pack["results_context"],
            style_notes=self._style_notes_for_prompt(),
            critique=self.project.critique_md,
            draft=self.project.body_md,
            bleed_hints=self._bleed_hints_for_draft(self.project.body_md),
        )
        try:
            revised = self._scrub_body(await self._chat(prompt, stage="revise"))
            self.project.body_md = revised
            self.project.status = "ready"
            self.db.commit()
            self._log_run("revise", "ok", revised[:2000])
            return revised
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("revise", "error", str(exc))
            raise

    async def run_full(self, with_critique: bool = True) -> ReportProject:
        self._raise_if_cancelled("full")
        await self.run_style_notes(force=False)
        self._raise_if_cancelled("full")
        await self.run_outline()
        self._raise_if_cancelled("full")
        await self.run_draft()
        if with_critique:
            self._raise_if_cancelled("full")
            await self.run_critique()
            self._raise_if_cancelled("full")
            await self.run_revise()
        else:
            self.project.status = "ready"
            self.db.commit()
        return self.project


def parse_id_list(raw: Optional[str | list[int]]) -> str:
    if raw is None:
        return "[]"
    if isinstance(raw, list):
        return json.dumps(raw)
    return raw
