"""Multi-stage report generation pipeline."""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LlmProfile, PipelineRun, ReportProject
from app.services.context import build_context_pack, has_real_examples
from app.services.llm import LlmClient, config_from_profile, config_from_settings
from app.services.style_cache import (
    fingerprint_examples,
    load_style_notes,
    save_style_notes,
)


STYLE_NOTES_PROMPT = """You analyze example reports to extract reusable *writing style* signals.
Do not summarize facts or outcomes from the examples. Extract only how they are written.

## Example reports
{examples}

---
Produce concise Markdown style notes under these headings (skip a heading only if
nothing useful appears in the examples):

### Section naming
Typical heading wording and order (quote the names used).

### Voice & person
Formality, tense, and person (e.g. third-person past, impersonal passive).

### Recurring phrases
Stock openers, transitions, and closings — quote short phrases to reuse.

### Terminology
Preferred terms / glossary-like wording (and what to avoid if contrast is clear).

### Metrics & findings
How numbers, comparisons, and results are stated (units, precision, hedging).

### Hedging & certainty
How claims are qualified (e.g. "indicates", "suggests", "was observed").

### Closing / outlook
How summaries, recommendations, or next-steps sections are phrased.

End with a short **Formulation preferences** bullet list the draft stage can follow.
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
and results. Do not write the full report yet."""


DRAFT_PROMPT = """Title: {title}

## Report brief
{brief}

## Results / data to include
{results_context}

## Style notes from examples (prefer these formulations for similar content)
{style_notes}

## Example reports (match language and structure; never copy facts not in results)
{examples}

## Approved outline
{outline}

---
Write the full report in Markdown. Follow the outline.
Use only facts from the results/context. If examples conflict with results, prefer results.

Language consistency (required when style notes are present):
- Prefer the same type of wording, section names, tense/person, terminology, metric
  phrasing, hedging, and closing patterns listed in the style notes.
- Reuse recurring phrases from the notes where they fit; do not invent new house style.
- Do not copy example-only facts, names, or figures.

Be precise. Output only the report."""


CRITIQUE_PROMPT = """You are reviewing a draft report written for an air-gapped organization.

## Brief
{brief}

## Results / data that should be reflected
{results_context}

## Style notes the draft should follow
{style_notes}

## Draft
{draft}

---
List concrete issues in these categories:
1. Missing data or invented claims (vs results)
2. Structure mismatches with the brief
3. Language / formulation drift from the style notes (wrong section naming, voice,
   terminology, metric phrasing, hedging, or closing patterns)

Be terse. End with a short "revised priority fixes" list."""


REVISE_PROMPT = """Title: {title}

## Brief
{brief}

## Results
{results_context}

## Style notes (restore consistency with example language)
{style_notes}

## Critique
{critique}

## Draft
{draft}

---
Produce a revised Markdown report that addresses the critique while staying faithful
to the results. Prefer formulations from the style notes for similar content; do not
introduce facts that are only in examples. Output only the revised report."""


NO_STYLE_NOTES = (
    "_(No example reports attached — use clear, professional report language "
    "consistent with the brief.)_"
)


class ReportPipeline:
    def __init__(self, db: Session, project: ReportProject):
        self.db = db
        self.project = project
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

    def _apply_style_notes(self, notes: str, key: str, *, from_cache: bool) -> str:
        self.project.style_notes_md = notes
        self.project.style_notes_key = key
        self.db.commit()
        source = "cache" if from_cache else "llm"
        self._log_run("style_notes", "ok", f"[{source}] {notes[:1900]}")
        return notes

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

        self.project.status = "style_notes"
        self.db.commit()
        prompt = STYLE_NOTES_PROMPT.format(examples=self.pack["examples"])
        try:
            notes = await self.client.chat(prompt, temperature=0.2)
            save_style_notes(settings.style_cache_dir, key, notes)
            return self._apply_style_notes(notes, key, from_cache=False)
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
        await self.ensure_style_notes()
        self.project.status = "outlining"
        self.db.commit()
        prompt = OUTLINE_PROMPT.format(
            **self.pack,
            style_notes=self._style_notes_for_prompt(),
        )
        try:
            outline = await self.client.chat(prompt)
            self.project.outline_md = outline
            self.project.status = "outlined"
            self.db.commit()
            self._log_run("outline", "ok", outline[:2000])
            return outline
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("outline", "error", str(exc))
            raise

    async def run_draft(self) -> str:
        if not self.project.outline_md.strip():
            await self.run_outline()
        else:
            await self.ensure_style_notes()
        self.project.status = "drafting"
        self.db.commit()
        prompt = DRAFT_PROMPT.format(
            **self.pack,
            outline=self.project.outline_md,
            style_notes=self._style_notes_for_prompt(),
        )
        try:
            body = await self.client.chat(prompt)
            self.project.body_md = body
            self.project.status = "drafted"
            self.db.commit()
            self._log_run("draft", "ok", body[:2000])
            return body
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("draft", "error", str(exc))
            raise

    async def run_critique(self) -> str:
        if not self.project.body_md.strip():
            await self.run_draft()
        else:
            await self.ensure_style_notes()
        self.project.status = "critiquing"
        self.db.commit()
        prompt = CRITIQUE_PROMPT.format(
            brief=self.pack["brief"],
            results_context=self.pack["results_context"],
            style_notes=self._style_notes_for_prompt(),
            draft=self.project.body_md,
        )
        try:
            critique = await self.client.chat(prompt, temperature=0.2)
            self.project.critique_md = critique
            self.project.status = "critiqued"
            self.db.commit()
            self._log_run("critique", "ok", critique[:2000])
            return critique
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("critique", "error", str(exc))
            raise

    async def run_revise(self) -> str:
        if not self.project.critique_md.strip():
            await self.run_critique()
        else:
            await self.ensure_style_notes()
        self.project.status = "revising"
        self.db.commit()
        prompt = REVISE_PROMPT.format(
            title=self.pack["title"],
            brief=self.pack["brief"],
            results_context=self.pack["results_context"],
            style_notes=self._style_notes_for_prompt(),
            critique=self.project.critique_md,
            draft=self.project.body_md,
        )
        try:
            revised = await self.client.chat(prompt)
            self.project.body_md = revised
            self.project.status = "ready"
            self.db.commit()
            self._log_run("revise", "ok", revised[:2000])
            return revised
        except Exception as exc:  # noqa: BLE001
            self.project.status = "error"
            self.db.commit()
            self._log_run("revise", "error", str(exc))
            raise

    async def run_full(self, with_critique: bool = True) -> ReportProject:
        await self.run_style_notes(force=False)
        await self.run_outline()
        await self.run_draft()
        if with_critique:
            await self.run_critique()
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
