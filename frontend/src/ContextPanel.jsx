import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

const EXAMPLE_PROMPT =
  "Find all areas in https://www.knmf.kit.edu/technologies.php and summarize for each technology the important information";

export default function ContextPanel({ apiKey, adminKey }) {
  const [stored, setStored] = useState(null);
  const [draft, setDraft] = useState("");
  const [prompt, setPrompt] = useState(EXAMPLE_PROMPT);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const show = useCallback((context) => {
    setStored(context);
    setDraft(context?.content || "");
    if (context?.prompt) setPrompt(context.prompt);
  }, []);

  useEffect(() => {
    api
      .context(apiKey)
      .then(show)
      .catch((e) => setError(e.message));
  }, [apiKey, show]);

  async function run(name, action, done) {
    setBusy(name);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(done);
    } catch (e) {
      setError(e.status === 403 ? `${e.message}. Set PROPOSAL_REVIEWER_ADMIN_KEY on the server.` : e.message);
    } finally {
      setBusy("");
    }
  }

  const generate = () =>
    run(
      "generate",
      async () => show((await api.generateContext(adminKey, prompt, true)).context),
      "The context was generated and stored. Review it below."
    );
  const save = () => run("save", async () => show(await api.saveContext(adminKey, draft)), "The context was saved.");
  const remove = () => {
    if (!window.confirm("Delete the general context?")) return;
    run("delete", async () => {
      await api.deleteContext(adminKey);
      show(null);
    }, "The context was deleted.");
  };

  const locked = !adminKey || busy !== "";
  const changed = draft.trim() !== (stored?.content || "");

  return (
    <div>
      <p className="text-body-secondary">
        Background information the reviewer gets with every proposal, in all subject areas.
      </p>
      {!adminKey && (
        <div className="alert alert-info">Enter the admin key under “API keys” to change the context.</div>
      )}
      {error && <div className="alert alert-danger">{error}</div>}
      {notice && <div className="alert alert-success">{notice}</div>}

      <div className="card mb-4">
        <div className="card-body">
          <label className="form-label fw-semibold" htmlFor="context-prompt">
            Generate with the AI
          </label>
          <textarea
            id="context-prompt"
            className="form-control mb-2"
            rows="3"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <div className="form-text mb-2">
            Web pages named in the prompt are read. Generating replaces the current context and can take several
            minutes.
          </div>
          <button className="btn btn-primary" disabled={locked || !prompt.trim()} onClick={generate}>
            {busy === "generate" && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />}
            {busy === "generate" ? "Generating…" : "Generate"}
          </button>
        </div>
      </div>

      <label className="form-label fw-semibold" htmlFor="context-content">
        Current context
      </label>
      {stored?.generated_at && (
        <div className="form-text mt-0 mb-2">
          Generated {new Date(stored.generated_at).toLocaleString()} by {stored.model}
        </div>
      )}
      <textarea
        id="context-content"
        className="form-control font-monospace small mb-2"
        rows="18"
        placeholder="No general context set."
        value={draft}
        readOnly={!adminKey}
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="d-flex gap-2">
        <button className="btn btn-outline-primary" disabled={locked || !changed || !draft.trim()} onClick={save}>
          Save changes
        </button>
        <button className="btn btn-outline-danger" disabled={locked || !stored} onClick={remove}>
          Delete
        </button>
      </div>
    </div>
  );
}
