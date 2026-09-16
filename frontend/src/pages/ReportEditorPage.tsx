import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  LibraryDocument,
  LlmProfile,
  PipelineRun,
  ReportProject,
  ReportTheme,
  SavedQuery,
  UploadedFile,
} from "../api";
import { markdownToHtml } from "../markdownPreview";
import { applyMarkdownAction, MdAction } from "../markdownToolbar";

type ExampleOption = {
  key: string;
  kind: "file" | "document";
  id: number;
  label: string;
  meta: string;
};

type ThemeOption = {
  key: string;
  kind: "file" | "document";
  id: number;
  label: string;
  meta: string;
};

type BodyView = "split" | "edit" | "preview";

const EMPTY_THEME: ReportTheme = {
  heading_font: null,
  body_font: null,
  header_text: "",
  footer_text: "",
  header_logo: null,
  footer_logo: null,
  source_label: "",
};

function normalizeReport(r: ReportProject): ReportProject {
  return {
    ...r,
    document_ids: r.document_ids ?? [],
    example_file_ids: r.example_file_ids ?? [],
    use_all_examples: r.use_all_examples ?? true,
    theme: r.theme ?? EMPTY_THEME,
  };
}

function themeIsSet(theme: ReportTheme | undefined): boolean {
  if (!theme) return false;
  return Boolean(
    theme.source_label ||
      theme.heading_font ||
      theme.body_font ||
      theme.header_text ||
      theme.footer_text ||
      theme.header_logo ||
      theme.footer_logo,
  );
}

const FORMAT_ACTIONS: { action: MdAction; label: string; title: string }[] = [
  { action: "bold", label: "B", title: "Bold" },
  { action: "italic", label: "I", title: "Italic" },
  { action: "h1", label: "H1", title: "Heading 1" },
  { action: "h2", label: "H2", title: "Heading 2" },
  { action: "h3", label: "H3", title: "Heading 3" },
  { action: "ul", label: "• List", title: "Bullet list" },
  { action: "ol", label: "1. List", title: "Numbered list" },
  { action: "link", label: "Link", title: "Insert link" },
  { action: "code", label: "</>", title: "Inline code" },
];

export default function ReportEditorPage() {
  const { id } = useParams();
  const reportId = Number(id);
  const [report, setReport] = useState<ReportProject | null>(null);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);
  const [queries, setQueries] = useState<SavedQuery[]>([]);
  const [profiles, setProfiles] = useState<LlmProfile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastErrorRun, setLastErrorRun] = useState<PipelineRun | null>(null);
  const [tab, setTab] = useState<"body" | "outline" | "critique" | "style">("body");
  const [bodyView, setBodyView] = useState<BodyView>("split");
  const [previewOpen, setPreviewOpen] = useState(true);
  const [exampleSearch, setExampleSearch] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [themeSearch, setThemeSearch] = useState("");
  const [themePickerOpen, setThemePickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const themePickerRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  async function load() {
    const [r, f, d, q, p] = await Promise.all([
      api.getReport(reportId),
      api.listFiles(),
      api.listDocuments(),
      api.listQueries(),
      api.listProfiles(),
    ]);
    setReport(normalizeReport(r));
    setFiles(f);
    setDocuments(d);
    setQueries(q);
    setProfiles(p);
    if (r.status === "error") {
      await loadLastErrorRun(r.id);
    } else {
      setLastErrorRun(null);
    }
  }

  async function loadLastErrorRun(id: number) {
    try {
      const runs = await api.listReportRuns(id, { limit: 1, errorsOnly: true });
      setLastErrorRun(runs[0] ?? null);
    } catch {
      setLastErrorRun(null);
    }
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [reportId]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!pickerRef.current?.contains(e.target as Node)) setPickerOpen(false);
      if (!themePickerRef.current?.contains(e.target as Node)) setThemePickerOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  async function save(patch: Record<string, unknown>) {
    if (!report) return;
    const updated = await api.updateReport(report.id, patch);
    setReport(normalizeReport(updated));
  }

  function toggleId(list: number[], value: number): number[] {
    return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
  }

  const contextFiles = useMemo(
    () => files.filter((f) => f.role === "context" || f.role === "both"),
    [files],
  );
  const resultsQueries = useMemo(
    () => queries.filter((q) => q.purpose === "results" || q.purpose === "both"),
    [queries],
  );

  const exampleOptions: ExampleOption[] = useMemo(() => {
    const fromFiles: ExampleOption[] = files
      .filter((f) => f.role === "example" || f.role === "both")
      .map((f) => ({
        key: `file-${f.id}`,
        kind: "file" as const,
        id: f.id,
        label: f.original_name,
        meta: `upload · ${f.role}`,
      }));
    const fromDocs: ExampleOption[] = documents
      .filter((d) => d.role === "example" || d.role === "both")
      .map((d) => ({
        key: `doc-${d.id}`,
        kind: "document" as const,
        id: d.id,
        label: d.title,
        meta: d.filename ? `library · ${d.filename}` : "library",
      }));
    return [...fromDocs, ...fromFiles];
  }, [files, documents]);

  const themeOptions: ThemeOption[] = useMemo(() => {
    const fromFiles: ThemeOption[] = files
      .filter((f) => f.original_name.toLowerCase().endsWith(".docx"))
      .map((f) => ({
        key: `file-${f.id}`,
        kind: "file" as const,
        id: f.id,
        label: f.original_name,
        meta: `upload · ${f.role}`,
      }));
    const fromDocs: ThemeOption[] = documents
      .filter(
        (d) =>
          (d.format || "").toLowerCase() === "docx" ||
          (d.filename || "").toLowerCase().endsWith(".docx"),
      )
      .map((d) => ({
        key: `doc-${d.id}`,
        kind: "document" as const,
        id: d.id,
        label: d.title,
        meta: d.filename ? `library · ${d.filename}` : "library · docx",
      }));
    return [...fromFiles, ...fromDocs];
  }, [files, documents]);

  const filteredExamples = useMemo(() => {
    const q = exampleSearch.trim().toLowerCase();
    if (!q) return exampleOptions;
    return exampleOptions.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        o.meta.toLowerCase().includes(q),
    );
  }, [exampleOptions, exampleSearch]);

  const filteredThemes = useMemo(() => {
    const q = themeSearch.trim().toLowerCase();
    if (!q) return themeOptions;
    return themeOptions.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        o.meta.toLowerCase().includes(q),
    );
  }, [themeOptions, themeSearch]);

  const selectedExampleChips = useMemo(() => {
    if (!report) return [];
    const chips: ExampleOption[] = [];
    for (const id of report.example_file_ids) {
      const opt = exampleOptions.find((o) => o.kind === "file" && o.id === id);
      if (opt) chips.push(opt);
    }
    for (const id of report.document_ids) {
      const opt = exampleOptions.find((o) => o.kind === "document" && o.id === id);
      if (opt) chips.push(opt);
    }
    return chips;
  }, [report, exampleOptions]);

  const previewHtml = useMemo(
    () => markdownToHtml(report?.body_md || ""),
    [report?.body_md],
  );

  const theme = report?.theme ?? EMPTY_THEME;

  async function generate(stage: string) {
    if (!report) return;
    setBusy(true);
    setError(null);
    setLastErrorRun(null);
    try {
      await save({
        title: report.title,
        brief: report.brief,
        file_ids: report.file_ids,
        query_ids: report.query_ids,
        document_ids: report.document_ids,
        example_file_ids: report.example_file_ids,
        use_all_examples: report.use_all_examples,
        llm_profile_id: report.llm_profile_id,
        body_md: report.body_md,
        outline_md: report.outline_md,
      });
      const updated = await api.generate(report.id, stage, true);
      setReport(normalizeReport(updated));
      setLastErrorRun(null);
      if (stage === "outline") setTab("outline");
      else if (stage === "critique") setTab("critique");
      else if (stage === "style_notes") setTab("style");
      else setTab("body");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      try {
        const refreshed = await api.getReport(report.id);
        setReport(normalizeReport(refreshed));
        await loadLastErrorRun(report.id);
      } catch {
        /* keep request error message */
      }
    } finally {
      setBusy(false);
    }
  }

  async function markDone() {
    if (!report) return;
    setBusy(true);
    setError(null);
    try {
      await save({ body_md: report.body_md, title: report.title });
      const result = await api.markDone(report.id);
      setReport(normalizeReport(result.report));
      setDocuments(await api.listDocuments());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function importTheme(opt: ThemeOption) {
    if (!report) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.importTheme(report.id, opt.kind, opt.id);
      setReport(normalizeReport(updated));
      setThemeSearch("");
      setThemePickerOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function clearTheme() {
    if (!report) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.clearTheme(report.id);
      setReport(normalizeReport(updated));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function addExample(opt: ExampleOption) {
    if (!report) return;
    if (opt.kind === "file") {
      if (report.example_file_ids.includes(opt.id)) return;
      const example_file_ids = [...report.example_file_ids, opt.id];
      setReport({ ...report, example_file_ids });
      void save({ example_file_ids });
    } else {
      if (report.document_ids.includes(opt.id)) return;
      const document_ids = [...report.document_ids, opt.id];
      setReport({ ...report, document_ids });
      void save({ document_ids });
    }
    setExampleSearch("");
    setPickerOpen(false);
  }

  function removeExample(opt: ExampleOption) {
    if (!report) return;
    if (opt.kind === "file") {
      const example_file_ids = report.example_file_ids.filter((x) => x !== opt.id);
      setReport({ ...report, example_file_ids });
      void save({ example_file_ids });
    } else {
      const document_ids = report.document_ids.filter((x) => x !== opt.id);
      setReport({ ...report, document_ids });
      void save({ document_ids });
    }
  }

  function runFormat(action: MdAction) {
    if (!report) return;
    const el = bodyRef.current;
    const start = el?.selectionStart ?? report.body_md.length;
    const end = el?.selectionEnd ?? start;
    const next = applyMarkdownAction(report.body_md, start, end, action);
    setReport({ ...report, body_md: next.value });
    requestAnimationFrame(() => {
      const ta = bodyRef.current;
      if (!ta) return;
      ta.focus();
      ta.setSelectionRange(next.start, next.end);
    });
  }

  if (!report) {
    return <p className="empty">{error || "Loading…"}</p>;
  }

  const allExampleCount = exampleOptions.length;
  const showEditor = bodyView === "edit" || bodyView === "split";
  const showPreview = previewOpen && (bodyView === "preview" || bodyView === "split");
  const previewStyle: CSSProperties = {
    ...(theme.body_font ? { fontFamily: `"${theme.body_font}", var(--font-display)` } : {}),
  };
  const previewHeadingStyle: CSSProperties | undefined = theme.heading_font
    ? ({ ["--preview-heading-font" as string]: `"${theme.heading_font}", var(--font-ui)` } as CSSProperties)
    : undefined;

  return (
    <>
      <div className="page-head">
        <div>
          <p style={{ margin: 0 }}>
            <Link to="/">← Reports</Link>
          </p>
          <h1 style={{ marginTop: "0.4rem" }}>
            <input
              value={report.title}
              onChange={(e) => setReport({ ...report, title: e.target.value })}
              onBlur={() => void save({ title: report.title })}
              style={{
                font: "inherit",
                border: "none",
                background: "transparent",
                width: "100%",
                padding: 0,
              }}
            />
          </h1>
          <p className="status-line">
            Status: <span className={`status ${report.status}`}>{report.status}</span>
            {busy ? " · working…" : ""}
          </p>
        </div>
        <div className="row export-row">
          <button
            type="button"
            disabled={busy || report.status === "done"}
            onClick={() => void markDone()}
            title="Archives this draft into the document library as an example"
          >
            Mark done
          </button>
          <a className="btn secondary" href={`/api/reports/${report.id}/export.md`}>
            Export Markdown
          </a>
          <a className="btn secondary" href={`/api/reports/${report.id}/export.html`}>
            Export HTML
          </a>
          <a className="btn secondary" href={`/api/reports/${report.id}/export.docx`}>
            Export Word (.docx)
          </a>
        </div>
      </div>

      {(error || lastErrorRun || report.status === "error") && (
        <div className="error-banner" role="alert">
          <strong>Generation failed</strong>
          {lastErrorRun ? (
            <>
              <div className="error-banner-meta">
                Stage: <code>{lastErrorRun.stage}</code>
                {lastErrorRun.created_at
                  ? ` · ${new Date(lastErrorRun.created_at).toLocaleString()}`
                  : ""}
              </div>
              {lastErrorRun.log_text.trim() ? (
                <pre className="error-banner-log">{lastErrorRun.log_text.trim()}</pre>
              ) : error ? (
                <p className="error-banner-fallback">{error}</p>
              ) : (
                <p className="error-banner-fallback">
                  Pipeline stopped with status <code>error</code>, but no log was recorded.
                </p>
              )}
            </>
          ) : error ? (
            <p className="error-banner-fallback">{error}</p>
          ) : (
            <p className="error-banner-fallback">
              This report is in <code>error</code> status. Re-run generate after fixing the LLM
              endpoint or inputs.
            </p>
          )}
        </div>
      )}

      <div className="editor-layout">
        <div className="editor-controls">
          <div className="panel">
            <h2>Brief</h2>
            <p className="field-hint">What should this report cover? Which results matter?</p>
            <textarea
              value={report.brief}
              onChange={(e) => setReport({ ...report, brief: e.target.value })}
              onBlur={() => void save({ brief: report.brief })}
              placeholder="Audience, scope, and the questions the report must answer…"
            />
          </div>

          <div className="panel">
            <h2>LLM profile</h2>
            <p className="field-hint">Which local endpoint drafts this report.</p>
            <select
              value={report.llm_profile_id ?? ""}
              onChange={(e) => {
                const v = e.target.value ? Number(e.target.value) : null;
                setReport({ ...report, llm_profile_id: v });
                void save({ llm_profile_id: v });
              }}
            >
              <option value="">Default (.env endpoint)</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.model}
                </option>
              ))}
            </select>
          </div>

          <div className="panel">
            <h2>Visual theme</h2>
            <p className="field-hint">
              Import fonts, header/footer text, and logos from a <code>.docx</code> example for
              Word export (preview shows simplified chrome). Not a full Word clone.
            </p>
            <div className="example-picker" ref={themePickerRef}>
              <div className="search-select">
                <input
                  type="search"
                  placeholder="Import theme from…"
                  value={themeSearch}
                  onChange={(e) => {
                    setThemeSearch(e.target.value);
                    setThemePickerOpen(true);
                  }}
                  onFocus={() => setThemePickerOpen(true)}
                />
                {themePickerOpen && (
                  <ul className="search-select-menu">
                    {filteredThemes.length === 0 ? (
                      <li className="empty-option">
                        No .docx sources — upload an example under Sources
                      </li>
                    ) : (
                      filteredThemes.map((opt) => (
                        <li key={opt.key}>
                          <button type="button" disabled={busy} onClick={() => void importTheme(opt)}>
                            <span>{opt.label}</span>
                            <small>{opt.meta}</small>
                          </button>
                        </li>
                      ))
                    )}
                  </ul>
                )}
              </div>
            </div>
            {themeIsSet(theme) ? (
              <div className="theme-summary">
                <p className="field-hint" style={{ marginBottom: "0.4rem" }}>
                  Active: <strong>{theme.source_label || "custom"}</strong>
                  {theme.heading_font || theme.body_font
                    ? ` · ${[theme.heading_font, theme.body_font].filter(Boolean).join(" / ")}`
                    : ""}
                </p>
                {(theme.header_text || theme.footer_text) && (
                  <p className="empty" style={{ paddingTop: 0 }}>
                    {theme.header_text ? `Header: ${theme.header_text}` : ""}
                    {theme.header_text && theme.footer_text ? " · " : ""}
                    {theme.footer_text ? `Footer: ${theme.footer_text}` : ""}
                  </p>
                )}
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => void clearTheme()}
                >
                  Clear theme
                </button>
              </div>
            ) : (
              <p className="empty">No visual theme applied yet.</p>
            )}
          </div>

          <div className="panel">
            <h2>Examples</h2>
            <p className="field-hint">
              Style references only — the pipeline extracts recurring phrases, section naming,
              voice, terminology, and how metrics/closings are phrased, then prefers those
              formulations (without copying example-only facts).
            </p>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={report.use_all_examples}
                onChange={(e) => {
                  const use_all_examples = e.target.checked;
                  setReport({ ...report, use_all_examples });
                  void save({ use_all_examples });
                }}
              />
              <span>
                Use all example files &amp; library reports
                <small>
                  {allExampleCount === 0
                    ? " — none tagged as examples yet"
                    : ` — ${allExampleCount} available`}
                </small>
              </span>
            </label>

            {!report.use_all_examples && (
              <div className="example-picker" ref={pickerRef}>
                <p className="field-hint">Search and add specific reports or example files.</p>
                <div className="search-select">
                  <input
                    type="search"
                    placeholder="Search examples…"
                    value={exampleSearch}
                    onChange={(e) => {
                      setExampleSearch(e.target.value);
                      setPickerOpen(true);
                    }}
                    onFocus={() => setPickerOpen(true)}
                  />
                  {pickerOpen && (
                    <ul className="search-select-menu">
                      {filteredExamples.length === 0 ? (
                        <li className="empty-option">No matches</li>
                      ) : (
                        filteredExamples.map((opt) => {
                          const selected =
                            opt.kind === "file"
                              ? report.example_file_ids.includes(opt.id)
                              : report.document_ids.includes(opt.id);
                          return (
                            <li key={opt.key}>
                              <button
                                type="button"
                                disabled={selected}
                                onClick={() => addExample(opt)}
                              >
                                <span>{opt.label}</span>
                                <small>{selected ? "added" : opt.meta}</small>
                              </button>
                            </li>
                          );
                        })
                      )}
                    </ul>
                  )}
                </div>
                {selectedExampleChips.length > 0 ? (
                  <div className="chip-row">
                    {selectedExampleChips.map((opt) => (
                      <span className="chip removable" key={opt.key}>
                        {opt.label}
                        <button type="button" aria-label="Remove" onClick={() => removeExample(opt)}>
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="empty">No specific examples selected.</p>
                )}
              </div>
            )}
            {report.use_all_examples && allExampleCount === 0 && (
              <p className="empty">
                Tag uploads as example under <Link to="/sources">Sources</Link>, or mark a report
                done to add it to the library.
              </p>
            )}
          </div>

          <div className="panel">
            <h2>Context &amp; results</h2>
            <p className="field-hint">
              Data and material that should appear in this report (not style examples).
            </p>

            <h3 className="subhead">Context files</h3>
            {contextFiles.length === 0 ? (
              <p className="empty">
                No context uploads. Add them under <Link to="/sources">Sources</Link>.
              </p>
            ) : (
              <div className="check-list">
                {contextFiles.map((f) => (
                  <label key={f.id}>
                    <input
                      type="checkbox"
                      checked={report.file_ids.includes(f.id)}
                      onChange={() => {
                        const file_ids = toggleId(report.file_ids, f.id);
                        setReport({ ...report, file_ids });
                        void save({ file_ids });
                      }}
                    />
                    <span>
                      {f.original_name} <span className="chip">{f.role}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}

            <h3 className="subhead">Results queries</h3>
            {resultsQueries.length === 0 ? (
              <p className="empty">
                No results queries. Configure under <Link to="/sources">Sources</Link>.
              </p>
            ) : (
              <div className="check-list">
                {resultsQueries.map((q) => (
                  <label key={q.id}>
                    <input
                      type="checkbox"
                      checked={report.query_ids.includes(q.id)}
                      onChange={() => {
                        const query_ids = toggleId(report.query_ids, q.id);
                        setReport({ ...report, query_ids });
                        void save({ query_ids });
                      }}
                    />
                    <span>
                      {q.name} <span className="chip">{q.purpose}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="panel">
            <h2>Pipeline</h2>
            <p className="field-hint">Generate stages against your brief, examples, and context.</p>
            <div className="row">
              <button type="button" disabled={busy} onClick={() => void generate("full")}>
                Full generate
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void generate("style_notes")}
              >
                Style notes
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void generate("outline")}
              >
                Outline
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void generate("draft")}
              >
                Draft
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void generate("critique")}
              >
                Critique
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void generate("revise")}
              >
                Revise
              </button>
            </div>
            <p className="empty" style={{ marginBottom: 0 }}>
              Full = style notes → outline → draft → critique → revise. Style notes are cached per
              example set; “use all” ranks examples by brief similarity (no model fine-tuning).
              Needs a reachable local LLM with a pulled model.
            </p>
          </div>

          <div className="panel mark-done-help">
            <h2>Finish</h2>
            <p className="field-hint">
              <strong>Mark done</strong> sets status to <code>done</code> and copies the draft into
              the document library as an example other reports can reuse. Legacy Word{" "}
              <code>.doc</code> is not supported — export <code>.docx</code> instead.
            </p>
          </div>
        </div>

        <div className="panel draft-panel">
          <div className="draft-toolbar">
            <div className="row tab-row">
              <button
                type="button"
                className={tab === "body" ? "" : "secondary"}
                onClick={() => setTab("body")}
              >
                Draft
              </button>
              <button
                type="button"
                className={tab === "outline" ? "" : "secondary"}
                onClick={() => setTab("outline")}
              >
                Outline
              </button>
              <button
                type="button"
                className={tab === "style" ? "" : "secondary"}
                onClick={() => setTab("style")}
              >
                Style notes
              </button>
              <button
                type="button"
                className={tab === "critique" ? "" : "secondary"}
                onClick={() => setTab("critique")}
              >
                Critique
              </button>
            </div>
            {tab === "body" && (
              <div className="row view-row">
                <button
                  type="button"
                  className={bodyView === "split" ? "" : "secondary"}
                  onClick={() => {
                    setBodyView("split");
                    setPreviewOpen(true);
                  }}
                >
                  Split
                </button>
                <button
                  type="button"
                  className={bodyView === "edit" ? "" : "secondary"}
                  onClick={() => setBodyView("edit")}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className={bodyView === "preview" ? "" : "secondary"}
                  onClick={() => {
                    setBodyView("preview");
                    setPreviewOpen(true);
                  }}
                >
                  Preview
                </button>
                {bodyView === "split" && (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setPreviewOpen((v) => !v)}
                  >
                    {previewOpen ? "Collapse preview" : "Show preview"}
                  </button>
                )}
              </div>
            )}
          </div>

          {tab === "body" && showEditor && (
            <div className="md-format-bar" role="toolbar" aria-label="Markdown formatting">
              {FORMAT_ACTIONS.map((item) => (
                <button
                  key={item.action}
                  type="button"
                  className={`secondary format-btn format-${item.action}`}
                  title={item.title}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => runFormat(item.action)}
                >
                  {item.label}
                </button>
              ))}
              <span className="pane-hint format-hint">Inserts Markdown around the selection</span>
            </div>
          )}

          {tab === "body" && (
            <div
              className={[
                "draft-workspace",
                showPreview && showEditor ? "is-split" : "",
                showEditor && !showPreview ? "is-edit-only" : "",
                showPreview && !showEditor ? "is-preview-only" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              {showEditor && (
                <div className="draft-editor-pane">
                  <div className="pane-label-row">
                    <label className="pane-label" htmlFor="draft-body">
                      Editable Markdown
                    </label>
                    <span className="pane-hint">Drag the corner to resize width &amp; height</span>
                  </div>
                  <div
                    className="editor-panel resizable-pane"
                    title="Drag the bottom-right corner to resize"
                  >
                    <textarea
                      id="draft-body"
                      ref={bodyRef}
                      className="report-body"
                      value={report.body_md}
                      onChange={(e) => setReport({ ...report, body_md: e.target.value })}
                      onBlur={() => void save({ body_md: report.body_md })}
                      placeholder="Generated report appears here. Edit freely in Markdown."
                    />
                    <div className="resize-grip" aria-hidden="true" />
                  </div>
                </div>
              )}
              {showPreview && (
                <div className="preview-shell">
                  <div className="pane-label-row">
                    <span className="pane-label">Live preview</span>
                    <span className="pane-hint">Drag the corner to resize width &amp; height</span>
                  </div>
                  <div
                    className="preview-panel resizable-pane"
                    title="Drag the bottom-right corner to resize"
                    style={previewHeadingStyle}
                  >
                    {themeIsSet(theme) && (
                      <div className="preview-chrome preview-chrome-header">
                        {theme.header_logo && (
                          <img
                            src={`/api/reports/${report.id}/theme/assets/${theme.header_logo}`}
                            alt=""
                            className="preview-logo"
                          />
                        )}
                        {theme.header_text && <span>{theme.header_text}</span>}
                      </div>
                    )}
                    <div
                      className="md-preview"
                      style={previewStyle}
                      dangerouslySetInnerHTML={{ __html: previewHtml }}
                    />
                    {themeIsSet(theme) && (theme.footer_text || theme.footer_logo) && (
                      <div className="preview-chrome preview-chrome-footer">
                        {theme.footer_logo && (
                          <img
                            src={`/api/reports/${report.id}/theme/assets/${theme.footer_logo}`}
                            alt=""
                            className="preview-logo"
                          />
                        )}
                        {theme.footer_text && <span>{theme.footer_text}</span>}
                      </div>
                    )}
                    <div className="resize-grip" aria-hidden="true" />
                  </div>
                </div>
              )}
            </div>
          )}

          {tab === "outline" && (
            <>
              <p className="field-hint">Structure from the outline stage — editable.</p>
              <textarea
                className="report-body"
                value={report.outline_md}
                onChange={(e) => setReport({ ...report, outline_md: e.target.value })}
                onBlur={() => void save({ outline_md: report.outline_md })}
                placeholder="Outline stage output"
              />
            </>
          )}
          {tab === "style" && (
            <>
              <p className="field-hint">
                Formulation signals extracted from examples (cached per example set). Re-run Style
                notes to refresh after changing examples.
              </p>
              <textarea
                className="report-body"
                value={report.style_notes_md || ""}
                readOnly
                placeholder="No style notes yet — attach examples and run Style notes (needs a local LLM model)."
              />
            </>
          )}
          {tab === "critique" && (
            <>
              <p className="field-hint">Model critique — read-only reference.</p>
              <textarea
                className="report-body"
                value={report.critique_md}
                readOnly
                placeholder="Critique stage output"
              />
            </>
          )}
        </div>
      </div>
    </>
  );
}
