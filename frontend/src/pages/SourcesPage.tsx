import { FormEvent, useEffect, useState } from "react";
import { api, DbConnection, LibraryDocument, SavedQuery, UploadedFile } from "../api";

export default function SourcesPage() {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);
  const [connections, setConnections] = useState<DbConnection[]>([]);
  const [queries, setQueries] = useState<SavedQuery[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string>("");

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
    if (!queryForm.connection_id && c[0]) {
      setQueryForm((prev) => ({ ...prev, connection_id: String(c[0].id) }));
    }
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

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

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Sources</h1>
          <p>
            Upload files as context or style examples, browse the document library, and/or connect
            a database for results and prior reports. Both can be mixed on a single report.
          </p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="panel">
        <h2>Upload files</h2>
        <p className="field-hint">
          Supports text, Markdown, CSV/JSON, and Word <code>.docx</code> (text is extracted). Legacy{" "}
          <code>.doc</code> is not supported — convert to <code>.docx</code> first.
        </p>
        <form className="row" onSubmit={onUpload}>
          <input
            name="file"
            type="file"
            multiple
            accept=".txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.xml,.html,.htm,.log,.rst,.docx"
          />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="context">Context / results</option>
            <option value="example">Example report</option>
            <option value="both">Both</option>
          </select>
          <button type="submit">Upload</button>
        </form>
        {files.length === 0 ? (
          <p className="empty">No files uploaded.</p>
        ) : (
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
        )}
      </div>

      <div className="panel">
        <h2>Document library</h2>
        <p className="field-hint">
          Finished reports (via Mark done) and extracted <code>.docx</code> / Markdown uploads live
          here. Use them as examples when composing new reports.
        </p>
        {documents.length === 0 ? (
          <p className="empty">Library is empty. Mark a report done or upload a .docx / .md file.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Format</th>
                <th>Role</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {documents.map((d) => (
                <tr key={d.id}>
                  <td>
                    <div>{d.title}</div>
                    {d.filename && (
                      <code style={{ fontSize: "0.75rem" }}>{d.filename}</code>
                    )}
                  </td>
                  <td>
                    <span className="chip">{d.format}</span>
                  </td>
                  <td>
                    <span className="chip">{d.role}</span>
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
        )}
      </div>

      <div className="grid-2">
        <div className="panel">
          <h2>Database connection</h2>
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
          )}
        </div>

        <div className="panel">
          <h2>Saved query</h2>
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
          <p className="empty">No queries saved.</p>
        ) : (
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
