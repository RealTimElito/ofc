import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ReportProject } from "../api";

export default function ReportsPage() {
  const [reports, setReports] = useState<ReportProject[]>([]);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function load() {
    try {
      setReports(await api.listReports());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    try {
      const r = await api.createReport({ title: title.trim(), brief: "" });
      navigate(`/reports/${r.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function onDelete(id: number) {
    if (!confirm("Delete this report project?")) return;
    await api.deleteReport(id);
    await load();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Reports</h1>
          <p>Compose reports from uploaded context, database results, and prior examples.</p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="panel">
        <h2>New report</h2>
        <form className="row" onSubmit={onCreate}>
          <input
            style={{ flex: 1, minWidth: 220 }}
            placeholder="Report title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button type="submit">Create</button>
        </form>
      </div>

      <div className="panel">
        <h2>Projects</h2>
        {reports.length === 0 ? (
          <p className="empty">No reports yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={`/reports/${r.id}`}>{r.title}</Link>
                  </td>
                  <td>
                    <span className={`status ${r.status}`}>{r.status}</span>
                  </td>
                  <td>{new Date(r.updated_at).toLocaleString()}</td>
                  <td>
                    <button type="button" className="danger" onClick={() => void onDelete(r.id)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
