import { useEffect, useState } from "react";
import { api } from "./api.js";
import Result from "./Result.jsx";

export default function EvaluatePanel({ apiKey, info }) {
  const [area, setArea] = useState("");
  const [skills, setSkills] = useState([]);
  // null = let the service pick its defaults; otherwise the names ticked by the user
  const [selected, setSelected] = useState(null);
  const [mode, setMode] = useState("file");
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);

  useEffect(() => {
    let current = true;
    setSelected(null);
    api
      .skills(apiKey, area)
      .then((list) => current && setSkills(list))
      .catch(() => current && setSkills([]));
    return () => {
      current = false;
    };
  }, [apiKey, area]);

  const configured = info?.default_skills;
  const isDefault = (skill) => (area || !configured ? skill.enabled : configured.includes(skill.name));
  const isChecked = (skill) => (selected ? selected.includes(skill.name) : isDefault(skill));
  const toggle = (name) => {
    const names = selected ?? skills.filter(isDefault).map((s) => s.name);
    setSelected(names.includes(name) ? names.filter((n) => n !== name) : [...names, name]);
  };

  const ready = !busy && (mode === "file" ? file : text.trim()) && (!selected || selected.length > 0);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setResponse(null);
    try {
      const chosen = selected || undefined;
      const result =
        mode === "file"
          ? await api.evaluateFile(apiKey, area, file, { title, skills: chosen })
          : await api.evaluateText(apiKey, area, { title: title || null, text, skills: chosen });
      setResponse(result);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="row g-4">
      <form className="col-lg-5" onSubmit={submit}>
        <div className="mb-3">
          <label className="form-label" htmlFor="title">
            Title <span className="text-body-secondary">(optional)</span>
          </label>
          <input id="title" className="form-control" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>

        <div className="btn-group mb-3" role="group">
          {[
            ["file", "Upload file"],
            ["text", "Paste text"],
          ].map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`btn btn-sm ${mode === id ? "btn-secondary" : "btn-outline-secondary"}`}
              onClick={() => setMode(id)}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === "file" ? (
          <div className="mb-3">
            <input
              type="file"
              className="form-control"
              accept=".pdf,.txt,.md"
              aria-label="Proposal file"
              onChange={(e) => setFile(e.target.files[0] || null)}
            />
            <div className="form-text">PDF, .txt or .md, up to 20 MB.</div>
          </div>
        ) : (
          <div className="mb-3">
            <textarea
              className="form-control"
              rows="10"
              placeholder="Full proposal text"
              aria-label="Proposal text"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
        )}

        {info?.areas?.length > 0 && (
          <div className="mb-3">
            <label className="form-label" htmlFor="area">
              Subject area
            </label>
            <select id="area" className="form-select" value={area} onChange={(e) => setArea(e.target.value)}>
              <option value="">General (no area)</option>
              {info.areas.map((a) => (
                <option key={a}>{a}</option>
              ))}
            </select>
          </div>
        )}

        <fieldset className="mb-3">
          <legend className="form-label fs-6">Skills</legend>
          {skills.length === 0 && <div className="text-body-secondary small">No skills available.</div>}
          {skills.map((skill) => (
            <div className="form-check" key={skill.name}>
              <input
                id={`skill-${skill.name}`}
                type="checkbox"
                className="form-check-input"
                checked={isChecked(skill)}
                onChange={() => toggle(skill.name)}
              />
              <label className="form-check-label" htmlFor={`skill-${skill.name}`}>
                {skill.name} <span className="text-body-secondary small">weight {skill.weight}</span>
                {skill.area && <span className="badge text-bg-info ms-2">{skill.area}</span>}
                {skill.description && <div className="text-body-secondary small">{skill.description}</div>}
              </label>
            </div>
          ))}
        </fieldset>

        <button className="btn btn-primary" disabled={!ready}>
          {busy && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />}
          {busy ? "Evaluating…" : "Evaluate"}
        </button>
        {busy && <div className="form-text">This can take a few minutes.</div>}
      </form>

      <div className="col-lg-7">
        {error && <div className="alert alert-danger">{error}</div>}
        {response ? (
          <Result response={response} scoreRange={info?.score_range} />
        ) : (
          !error && !busy && <p className="text-body-secondary">The evaluation appears here.</p>
        )}
      </div>
    </div>
  );
}
