import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` serves the UI on :5173 and forwards API calls to the service on :8000.
// `npm run build` writes into the Python package, where FastAPI serves it at /.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000", "/health": "http://localhost:8000" } },
  build: { outDir: "../proposal_reviewer/static", emptyOutDir: true },
});
