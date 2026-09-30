// Thin wrapper around the Proposal Reviewer REST API. Keys are sent in the X-API-Key header.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(path, { key, method = "GET", json, form } = {}) {
  const headers = {};
  if (key) headers["X-API-Key"] = key;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = form;
  }
  let response;
  try {
    response = await fetch(path, { method, headers, body });
  } catch (e) {
    throw new ApiError(0, `Cannot reach the service: ${e.message}`);
  }
  if (response.status === 204) return null;
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* not JSON */
  }
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : text || response.statusText;
    throw new ApiError(response.status, message);
  }
  return data;
}

const areaQuery = (area) => (area ? `?area=${encodeURIComponent(area)}` : "");

export const api = {
  info: (key) => request("/api/v1/info", { key }),
  skills: (key, area) => request(`/api/v1/skills${areaQuery(area)}`, { key }),

  evaluateText: (key, area, body) =>
    request(`/api/v1/proposals/evaluate${areaQuery(area)}`, { key, method: "POST", json: body }),

  evaluateFile: (key, area, file, { title, skills }) => {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    if (skills) form.append("skills", skills.join(","));
    return request(`/api/v1/proposals/evaluate/file${areaQuery(area)}`, { key, method: "POST", form });
  },

  // resolves to null when no context is set
  context: (key) =>
    request("/api/v1/context", { key }).catch((e) => {
      if (e.status === 404) return null;
      throw e;
    }),
  generateContext: (key, prompt, save) =>
    request("/api/v1/context/generate", { key, method: "POST", json: { prompt, save } }),
  saveContext: (key, content) => request("/api/v1/context", { key, method: "PUT", json: { content } }),
  deleteContext: (key) => request("/api/v1/context", { key, method: "DELETE" }),
};
