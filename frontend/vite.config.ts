import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // 127.0.0.1 so `tailscale serve 5173` can reach it (Vite's "localhost" may bind IPv6 only).
    // WEB_HOST=0.0.0.0 also serves the tailnet IP / LAN directly.
    host: process.env.WEB_HOST ?? '127.0.0.1',
    // Tailscale MagicDNS names (https://<machine>.<tailnet>.ts.net) besides localhost; IPs are always allowed.
    allowedHosts: ['.ts.net'],
    proxy: { '/api': { target: `http://localhost:${process.env.API_PORT ?? '8000'}`, changeOrigin: true } },
  },
})
