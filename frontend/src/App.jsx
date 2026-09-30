import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import ContextPanel from "./ContextPanel.jsx";
import EvaluatePanel from "./EvaluatePanel.jsx";

function useStored(name) {
  const [value, setValue] = useState(() => localStorage.getItem(name) || "");
  const update = (v) => {
    setValue(v);
    if (v) localStorage.setItem(name, v);
    else localStorage.removeItem(name);
  };
  return [value, update];
}

const TABS = [
  ["evaluate", "Evaluate proposal"],
  ["context", "General context"],
];

export default function App() {
  const [apiKey, setApiKey] = useStored("proposal-reviewer.api-key");
  const [adminKey, setAdminKey] = useStored("proposal-reviewer.admin-key");
  const [tab, setTab] = useState("evaluate");
  const [showKeys, setShowKeys] = useState(false);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");

  const loadInfo = useCallback(() => {
    api
      .info(apiKey)
      .then((data) => {
        setInfo(data);
        setError("");
      })
      .catch((e) => {
        setInfo(null);
        setError(e.status === 401 ? "The service requires an API key: enter it under “API keys”." : e.message);
        if (e.status === 401) setShowKeys(true);
      });
  }, [apiKey]);

  useEffect(loadInfo, [loadInfo]);

  return (
    <>
      <nav className="navbar navbar-dark bg-dark mb-4">
        <div className="container">
          <span className="navbar-brand">Proposal Reviewer</span>
          <span className="navbar-text small me-auto">{info && `${info.provider} · ${info.model}`}</span>
          <button className="btn btn-outline-light btn-sm" onClick={() => setShowKeys(!showKeys)}>
            API keys
          </button>
        </div>
      </nav>

      <main className="container pb-5">
        {showKeys && (
          <div className="card mb-4">
            <div className="card-body row g-3">
              <div className="col-md-6">
                <label className="form-label" htmlFor="api-key">
                  API key
                </label>
                <input
                  id="api-key"
                  type="password"
                  className="form-control"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value.trim())}
                />
                <div className="form-text">Needed to evaluate proposals, unless the service is open.</div>
              </div>
              <div className="col-md-6">
                <label className="form-label" htmlFor="admin-key">
                  Admin key
                </label>
                <input
                  id="admin-key"
                  type="password"
                  className="form-control"
                  value={adminKey}
                  onChange={(e) => setAdminKey(e.target.value.trim())}
                />
                <div className="form-text">Only needed to change the general context.</div>
              </div>
              <div className="col-12 form-text mt-2">Keys are kept in this browser only (local storage).</div>
            </div>
          </div>
        )}

        {error && <div className="alert alert-warning">{error}</div>}

        <ul className="nav nav-tabs mb-4">
          {TABS.map(([id, label]) => (
            <li className="nav-item" key={id}>
              <button className={`nav-link ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
                {label}
              </button>
            </li>
          ))}
        </ul>

        {/* both stay mounted so a running request or a result survives switching tabs */}
        <div hidden={tab !== "evaluate"}>
          <EvaluatePanel apiKey={apiKey} info={info} />
        </div>
        <div hidden={tab !== "context"}>
          <ContextPanel apiKey={apiKey} adminKey={adminKey} />
        </div>
      </main>
    </>
  );
}
