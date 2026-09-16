import { Link } from "react-router-dom";

export default function AboutPage() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>About OFC</h1>
          <p>
            Offline Report Composer — write structured reports with a local LLM, using files
            and/or databases as context and style examples.
          </p>
        </div>
      </div>

      <div className="panel about-section">
        <h2>What this is for</h2>
        <p>
          OFC is built for air-gapped or restricted networks where you already have a private
          OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, an internal gateway) and need
          reports that stay on your network.
        </p>
        <p>
          You attach <strong>results / context</strong> (facts that belong in the report) and{" "}
          <strong>examples</strong> (how prior reports were written). Examples drive wording:
          section names, formality, recurring phrases, terminology, how metrics are stated,
          hedging, and closing patterns — not facts to copy. The model drafts from that pack;
          it does not phone home.
        </p>
      </div>

      <div className="panel about-section">
        <h2>How a report is assembled</h2>
        <ol className="about-steps">
          <li>
            <Link to="/settings">Configure an LLM</Link> — base URL, model, and API key (or use
            the defaults from <code>.env</code>).
          </li>
          <li>
            <Link to="/sources">Add sources</Link> — upload files (including <code>.docx</code>)
            and/or connect a database with read-only SQL. Mark each as context, examples, or both.
          </li>
          <li>
            <Link to="/">Create a report</Link> — write a brief, choose examples vs context, then
            run the pipeline.
          </li>
          <li>
            Edit the draft with the Markdown formatting bar and live preview, then export
            Markdown, HTML, or Word (<code>.docx</code>). Optionally import a visual theme
            (fonts, header/footer, logos) from a <code>.docx</code> example for Word export;
            preview chrome is simplified and browser fonts may not match Word exactly.
          </li>
          <li>
            <strong>Mark done</strong> archives the report into the document library so later jobs
            can reuse it as an example.
          </li>
        </ol>
      </div>

      <div className="grid-2">
        <div className="panel about-section">
          <h2>Pipeline stages</h2>
          <ul className="about-list">
            <li>
              <strong>Style notes</strong> — when examples are attached, extract formulation
              signals (phrases, voice, section naming, metrics/hedging patterns)
            </li>
            <li>
              <strong>Outline</strong> — structure grounded in the brief and results, preferring
              example section names when they fit
            </li>
            <li>
              <strong>Draft</strong> — full Markdown report using example language for similar
              content
            </li>
            <li>
              <strong>Critique</strong> — checks missing/invented claims and drift from example
              wording
            </li>
            <li>
              <strong>Revise</strong> — applies the critique and restores formulation consistency
            </li>
          </ul>
          <p className="about-note">
            “Full generate” runs style notes (if examples exist) plus the four writing stages. You
            can also run stages one at a time from the editor.
          </p>
        </div>

        <div className="panel about-section">
          <h2>Sources</h2>
          <ul className="about-list">
            <li>
              <strong>Files</strong> — text, Markdown, CSV, JSON, and <code>.docx</code> uploads
              used as context or as style examples (legacy <code>.doc</code> is not supported)
            </li>
            <li>
              <strong>Document library</strong> — finished reports and imported documents available
              as examples
            </li>
            <li>
              <strong>Database</strong> — SQLite path or Postgres/MySQL DSN; only{" "}
              <code>SELECT</code> / <code>WITH</code> queries
            </li>
            <li>
              <strong>Example column</strong> — for prior reports stored in a table, set the
              column that holds the report body
            </li>
          </ul>
          <p className="about-note">
            Nav <strong>Sources</strong> is where you upload/tag material. On each report,{" "}
            <strong>Examples</strong> set wording and house style;{" "}
            <strong>Context &amp; results</strong> set the facts. The pipeline extracts style
            notes from examples and prefers those formulations in draft and revise.
          </p>
        </div>
      </div>

      <div className="panel about-section">
        <h2>Air-gap expectations</h2>
        <ul className="about-list">
          <li>LLM traffic goes only to the URLs you configure — no cloud fallback.</li>
          <li>App data (uploads, projects, encrypted credentials) stays under the local data directory.</li>
          <li>The UI build does not load fonts or scripts from the public internet at runtime.</li>
          <li>
            Sample demo data lives in <code>data/samples/</code> if you ran the seed script.
          </li>
          <li>
            Theme import reads only local <code>.docx</code> uploads (or library docs backed by
            one). It is not a full Word clone — complex headers, floating shapes, and exact font
            embedding are out of scope.
          </li>
        </ul>
      </div>
    </>
  );
}
