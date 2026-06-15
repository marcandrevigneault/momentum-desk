import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// The dashboard talks to the FastAPI backend on :8000. Proxy both REST and the
// WebSocket so the frontend can use same-origin relative URLs in dev.
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5180,
        proxy: {
            "/api": { target: "http://localhost:8000", changeOrigin: true },
            "/ws": { target: "ws://localhost:8000", ws: true },
        },
    },
});
