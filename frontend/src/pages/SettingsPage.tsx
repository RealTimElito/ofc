import { FormEvent, useEffect, useState } from "react";
import { api, LlmProfile } from "../api";

export default function SettingsPage() {
  const [profiles, setProfiles] = useState<LlmProfile[]>([]);
  const [settings, setSettings] = useState<{
    llm_base_url: string;
    llm_model: string;
    data_dir: string;
  } | null>(null);
  const [pingMsg, setPingMsg] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "Local Ollama",
    base_url: "http://127.0.0.1:11434/v1",
    api_key: "ollama",
    model: "llama3.1",
    temperature: 0.3,
    max_tokens: 4096,
    is_default: true,
  });

  async function load() {
    const [p, s] = await Promise.all([api.listProfiles(), api.settings()]);
    setProfiles(p);
    setSettings(s);
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.createProfile(form);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>LLM &amp; settings</h1>
          <p>
            Point OFC at your air-gapped OpenAI-compatible endpoint. Credentials stay on this
            machine (encrypted at rest). A saved <strong>default profile</strong> overrides the{" "}
            <code>.env</code> base URL/model for generate; if none is marked default,{" "}
            <code>.env</code> is used.
          </p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="panel">
        <h2>Runtime defaults (.env)</h2>
        {settings ? (
          <table>
            <tbody>
              <tr>
                <th>Base URL</th>
                <td>
                  <code>{settings.llm_base_url}</code>
                </td>
              </tr>
              <tr>
                <th>Model</th>
                <td>
                  <code>{settings.llm_model}</code>
                </td>
              </tr>
              <tr>
                <th>Data dir</th>
                <td>
                  <code>{settings.data_dir}</code>
                </td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p className="empty">Loading…</p>
        )}
        <div className="row" style={{ marginTop: "0.75rem" }}>
          <button
            type="button"
            className="secondary"
            onClick={() =>
              void api
                .pingDefault()
                .then((r) => setPingMsg(`Default endpoint: HTTP ${r.status_code}`))
                .catch((err) => setPingMsg(String(err)))
            }
          >
            Ping default endpoint
          </button>
          {pingMsg && <span className="chip">{pingMsg}</span>}
        </div>
      </div>

      <div className="panel">
        <h2>Add LLM profile</h2>
        <form className="stack" onSubmit={onCreate}>
          <div className="grid-2">
            <label>
              Name
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                required
              />
            </label>
            <label>
              Model
              <input
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
                required
              />
            </label>
            <label>
              Base URL
              <input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                required
              />
            </label>
            <label>
              API key
              <input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder="Any string for local models"
              />
            </label>
            <label>
              Temperature
              <input
                type="number"
                step="0.1"
                min="0"
                max="2"
                value={form.temperature}
                onChange={(e) =>
                  setForm({ ...form, temperature: Number(e.target.value) })
                }
              />
            </label>
            <label>
              Max tokens
              <input
                type="number"
                value={form.max_tokens}
                onChange={(e) =>
                  setForm({ ...form, max_tokens: Number(e.target.value) })
                }
              />
            </label>
          </div>
          <label style={{ flexDirection: "row", alignItems: "center" }}>
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
            />
            Mark as default profile
          </label>
          <button type="submit">Save profile</button>
        </form>
      </div>

      <div className="panel">
        <h2>Saved profiles</h2>
        {profiles.length === 0 ? (
          <p className="empty">No profiles yet — .env defaults will be used.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Endpoint</th>
                <th>Model</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr key={p.id}>
                  <td>
                    {p.name} {p.is_default ? <span className="chip">default</span> : null}
                  </td>
                  <td>
                    <code>{p.base_url}</code>
                  </td>
                  <td>{p.model}</td>
                  <td className="row">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() =>
                        void api
                          .pingProfile(p.id)
                          .then((r) => setPingMsg(`${p.name}: HTTP ${r.status_code}`))
                          .catch((err) => setPingMsg(String(err)))
                      }
                    >
                      Ping
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => void api.deleteProfile(p.id).then(load)}
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
    </>
  );
}
