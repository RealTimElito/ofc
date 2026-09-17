import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, DbConnection, LibraryDocument, SavedQuery, UploadedFile } from "../api";

export default function SourcesPage() {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);
  const [connections, setConnections] = useState<DbConnection[]>([]);
  const [queries, setQueries] = useState<SavedQuery[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string>("");
  const [stubsOnly, setStubsOnly] = useState(false);
  const [selectedDocIds, setSelectedDocIds] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);

  const [role, setRole] = useState("context");
  const [connForm, setConnForm] = useState({
    name: "",
    dialect: "sqlite",
    dsn: "",
    notes: "",
  });
  const [queryForm, setQueryForm] = useState({
    connection_id: "",
    name: "",
    purpose: "results",
    sql_text: "SELECT * FROM reports LIMIT 20",
    example_body_column: "",
  });

  async function load() {
    const [f, d, c, q] = await Promise.all([
      api.listFiles(),
      api.listDocuments(),
      api.listConnections(),
      api.listQueries(),
    ]);
    setFiles(f);
    setDocuments(d);
    setConnections(c);
    setQueries(q);
    setSelectedDocIds((prev) => {
      const alive = new Set(d.map((doc) => doc.id));
      return new Set([...prev].filter((id) => alive.has(id)));
    });
    if (!queryForm.connection_id && c[0]) {
      setQueryForm((prev) => ({ ...prev, connection_id: String(c[0].id) }));
    }
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const stubDocs = useMemo(() => documents.filter((d) => d.is_stub), [documents]);
  const visibleDocs = useMemo(
    () => (stubsOnly ? stubDocs : documents),
    [documents, stubDocs, stubsOnly],
  );

  function toggleDocSelected(id: number) {
    setSelectedDocIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectVisibleStubs() {
    const stubIds = visibleDocs.filter((d) => d.is_stub).map((d) => d.id);
    setSelectedDocIds((prev) => {
      const allSelected = stubIds.length > 0 && stubIds.every((id) => prev.has(id));
      const next = new Set(prev);
      if (allSelected) {
        for (const id of stubIds) next.delete(id);
      } else {
        for (const id of stubIds) next.add(id);
      }
      return next;
    });
  }

  async function onDeleteSelectedStubs() {
    const ids = [...selectedDocIds].filter((id) =>
      documents.some((d) => d.id === id && d.is_stub),
    );
    if (!ids.length) return;
    if (!window.confirm(`Delete ${ids.length} selected stub document(s)?`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.pruneDocumentStubs(ids);
      setSelectedDocIds(new Set());
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onRemoveAllStubs() {
    if (!stubDocs.length) return;
    if (
      !window.confirm(
        `Remove all ${stubDocs.length} library stub(s)? Smoke/short placeholders that pollute examples will be deleted.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.pruneDocumentStubs();
      setSelectedDocIds(new Set());
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onUpload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const input = e.currentTarget.elements.namedItem("file") as HTMLInputElement;
    if (!input.files?.length) return;
    setError(null);
    try {
      for (const file of Array.from(input.files)) {
        await api.uploadFile(file, role);
      }
      input.value = "";
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function onCreateConn(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.createConnection(connForm);
      setConnForm({ name: "", dialect: "sqlite", dsn: "", notes: "" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function onCreateQuery(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.createQuery({
        connection_id: Number(queryForm.connection_id),
        name: queryForm.name,
        purpose: queryForm.purpose,
        sql_text: queryForm.sql_text,
        example_body_column: queryForm.example_body_column || null,
      });
      setQueryForm((prev) => ({
        ...prev,
        name: "",
        sql_text: "SELECT * FROM reports LIMIT 20",
        example_body_column: "",
      }));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const selectedStubCount = [...selectedDocIds].filter((id) =>
    documents.some((d) => d.id === id && d.is_stub),
  ).length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Sources</h1>
          <p>
            Prepare material reports pick from: <strong>examples</strong> (house style) and{" "}
            <strong>context &amp; results</strong> (facts). Tag roles here; each report editor
            chooses which ones to attach.
          </p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="sources-guide" aria-label="Suggested order">
        <div className="sources-guide-step">
          <strong>1 · Upload or library</strong>
          <p>Add .docx / Markdown files and tag them example, context, or both.</p>
        </div>
        <div className="sources-guide-step">
          <strong>2 · Database (optional)</strong>
          <p>Save a connection, then a results query the report can attach.</p>
        </div>
        <div className="sources-guide-step">
          <strong>3 · Open a report</strong>
          <p>In the editor, attach examples + results, then Full generate or Check.</p>
        </div>
      </div>

      <h2 className="sources-section-title">Files &amp; library</h2>
      <p className="sources-section-lead">
        Uploads feed the report editor immediately; the library also keeps finished reports and
        extracted .docx text for examples and theme import.
      </p>

      <div className="panel">
        <h2>Upload files</h2>
        <p className="field-hint">
          Role maps to the report editor: <em>example</em> → Examples,{" "}
          <em>context</em> → Context &amp; results, <em>both</em> → either. Supports text,
          Markdown, CSV/JSON, and Word <code>.docx</code>. Legacy <code>.doc</code> is not
          supported.
        </p>
        <form className="row" onSubmit={onUpload}>
          <input
            name="file"
            type="file"
            multiple
            accept=".txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.xml,.html,.htm,.log,.rst,.docx"
          />
          <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Upload role">
            <option value="context">Context / results</option>
            <option value="example">Example report</option>
            <option value="both">Both</option>
          </select>
          <button type="submit">Upload</button>
        </form>
        {files.length === 0 ? (
          <p className="empty">No files uploaded yet — start here if you have local docs.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Size</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {files.map((f) => (
                  <tr key={f.id}>
                    <td>{f.original_name}</td>
                    <td>
                      <select
                        value={f.role}
                        onChange={(e) =>
                          void api.updateFileRole(f.id, e.target.value).then(load)
                        }
                      >
                        <option value="context">context</option>
                        <option value="example">example</option>
                        <option value="both">both</option>
                      </select>
                    </td>
                    <td>{Math.round(f.size_bytes / 1024)} KB</td>
                    <td>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void api.deleteFile(f.id).then(load)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <h2>Document library</h2>
        <p className="field-hint">
          Finished reports (Mark done) and extracted uploads. Usually tag as <em>example</em>.
          Short smoke stubs are flagged so you can prune them before they pollute style packs.
        </p>
        {stubDocs.length > 0 && (
          <div className="warn-banner" style={{ marginBottom: "0.75rem" }}>
            <strong>{stubDocs.length} likely stub{stubDocs.length === 1 ? "" : "s"}</strong>
            <span className="warn-banner-detail">
              {" "}
              — smoke tests or short placeholders the pipeline already skips when richer
              examples exist.
            </span>
          </div>
        )}
        <div className="row" style={{ marginBottom: "0.75rem", flexWrap: "wrap", gap: "0.5rem" }}>
          <label className="row" style={{ gap: "0.4rem", alignItems: "center" }}>
            <input
              type="checkbox"
              checked={stubsOnly}
              onChange={(e) => setStubsOnly(e.target.checked)}
            />
            Show stubs only
          </label>
          <button
            type="button"
            className="secondary"
            disabled={!stubDocs.length || busy}
            onClick={toggleSelectVisibleStubs}
          >
            Select visible stubs
          </button>
          <button
            type="button"
            className="danger"
            disabled={!selectedStubCount || busy}
            onClick={() => void onDeleteSelectedStubs()}
          >
            Delete selected ({selectedStubCount})
          </button>
          <button
            type="button"
            className="danger"
            disabled={!stubDocs.length || busy}
            onClick={() => void onRemoveAllStubs()}
          >
            Remove stubs
          </button>
        </div>
        {documents.length === 0 ? (
          <p className="empty">Library is empty. Mark a report done or upload a .docx / .md file.</p>
        ) : visibleDocs.length === 0 ? (
          <p className="empty">No stub documents match the current filter.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th aria-label="Select" />
                  <th>Title</th>
                  <th>Format</th>
                  <th>Role</th>
                  <th>Source</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visibleDocs.map((d) => (
                  <tr key={d.id} className={d.is_stub ? "row-stub" : undefined}>
                    <td>
                      {d.is_stub ? (
                        <input
                          type="checkbox"
                          checked={selectedDocIds.has(d.id)}
                          onChange={() => toggleDocSelected(d.id)}
                          aria-label={`Select stub ${d.title}`}
                        />
                      ) : null}
                    </td>
                    <td>
                      <div>{d.title}</div>
                      {d.filename && (
                        <code style={{ fontSize: "0.75rem" }}>{d.filename}</code>
                      )}
                      {d.is_stub && d.stub_reason && (
                        <div>
                          <span className="chip chip-stub" title={d.stub_reason}>
                            stub · {d.stub_reason}
                          </span>
                        </div>
                      )}
                    </td>
                    <td>
                      <span className="chip">{d.format}</span>
                    </td>
                    <td>
                      <select
                        value={d.role}
                        onChange={(e) =>
                          void api.updateDocumentRole(d.id, e.target.value).then(load)
                        }
                      >
                        <option value="context">context</option>
                        <option value="example">example</option>
                        <option value="both">both</option>
                      </select>
                    </td>
                    <td>
                      {d.source_report_id ? `report #${d.source_report_id}` : "upload"}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void api.deleteDocument(d.id).then(load)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <h2 className="sources-section-title">Database results</h2>
      <p className="sources-section-lead">
        Optional. Connect a local DB, save a SELECT query with purpose <em>results</em>, then
        attach it on the report editor under Context &amp; results.
      </p>

      <div className="grid-2">
        <div className="panel">
          <h2>Database connection</h2>
          <p className="field-hint">Credentials stay on this machine (encrypted at rest).</p>
          <form className="stack" onSubmit={onCreateConn}>
            <label>
              Name
              <input
                value={connForm.name}
                onChange={(e) => setConnForm({ ...connForm, name: e.target.value })}
                required
              />
            </label>
            <label>
              Dialect
              <select
                value={connForm.dialect}
                onChange={(e) => setConnForm({ ...connForm, dialect: e.target.value })}
              >
                <option value="sqlite">SQLite (path)</option>
                <option value="postgresql">PostgreSQL</option>
                <option value="mysql">MySQL</option>
              </select>
            </label>
            <label>
              DSN / path
              <input
                value={connForm.dsn}
                onChange={(e) => setConnForm({ ...connForm, dsn: e.target.value })}
                placeholder={
                  connForm.dialect === "sqlite"
                    ? "/path/to/archive.db"
                    : "postgresql://user:pass@host:5432/dbname"
                }
                required
              />
            </label>
            <label>
              Notes
              <input
                value={connForm.notes}
                onChange={(e) => setConnForm({ ...connForm, notes: e.target.value })}
              />
            </label>
            <button type="submit">Save connection</button>
          </form>

          {connections.length > 0 && (
            <div className="table-wrap">
              <table style={{ marginTop: "1rem" }}>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Dialect</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {connections.map((c) => (
                    <tr key={c.id}>
                      <td>{c.name}</td>
                      <td>{c.dialect}</td>
                      <td>
                        <button
                          type="button"
                          className="danger"
                          onClick={() => void api.deleteConnection(c.id).then(load)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="panel">
          <h2>Saved query</h2>
          <p className="field-hint">SELECT only. Purpose <em>results</em> feeds report metrics.</p>
          <form className="stack" onSubmit={onCreateQuery}>
            <label>
              Connection
              <select
                value={queryForm.connection_id}
                onChange={(e) =>
                  setQueryForm({ ...queryForm, connection_id: e.target.value })
                }
                required
              >
                <option value="" disabled>
                  Select…
                </option>
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Name
              <input
                value={queryForm.name}
                onChange={(e) => setQueryForm({ ...queryForm, name: e.target.value })}
                required
              />
            </label>
            <label>
              Purpose
              <select
                value={queryForm.purpose}
                onChange={(e) => setQueryForm({ ...queryForm, purpose: e.target.value })}
              >
                <option value="results">Results for the report</option>
                <option value="examples">Example / prior reports</option>
                <option value="both">Both</option>
              </select>
            </label>
            <label>
              SQL (SELECT only)
              <textarea
                value={queryForm.sql_text}
                onChange={(e) => setQueryForm({ ...queryForm, sql_text: e.target.value })}
                required
              />
            </label>
            <label>
              Example body column (if purpose includes examples)
              <input
                value={queryForm.example_body_column}
                onChange={(e) =>
                  setQueryForm({ ...queryForm, example_body_column: e.target.value })
                }
                placeholder="body_md"
              />
            </label>
            <button type="submit" disabled={!queryForm.connection_id}>
              Save query
            </button>
          </form>
        </div>
      </div>

      <div className="panel">
        <h2>Queries</h2>
        {queries.length === 0 ? (
          <p className="empty">No queries saved yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Purpose</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {queries.map((q) => (
                  <tr key={q.id}>
                    <td>
                      <div>{q.name}</div>
                      <code style={{ fontSize: "0.75rem" }}>{q.sql_text.slice(0, 120)}</code>
                    </td>
                    <td>
                      <span className="chip">{q.purpose}</span>
                    </td>
                    <td className="row">
                      <button
                        type="button"
                        className="secondary"
                        onClick={() =>
                          void api
                            .previewQuery(q.id)
                            .then((data) =>
                              setPreview(JSON.stringify(data, null, 2)),
                            )
                            .catch((err) =>
                              setError(err instanceof Error ? err.message : String(err)),
                            )
                        }
                      >
                        Preview
                      </button>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void api.deleteQuery(q.id).then(load)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {preview && (
          <pre
            style={{
              marginTop: "1rem",
              background: "#fffef9",
              border: "1px solid var(--line)",
              borderRadius: 8,
              padding: "0.75rem",
              overflow: "auto",
              maxHeight: 280,
              fontSize: "0.8rem",
            }}
          >
            {preview}
          </pre>
        )}
      </div>
    </>
  );
}
