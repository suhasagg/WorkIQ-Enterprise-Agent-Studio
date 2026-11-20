/** @type {import('next').NextConfig} */
const api = process.env.API_URL || "http://localhost:8140";
export default {
  reactStrictMode: true,
  output: "standalone",
  compress: false, // keep server-sent events unbuffered through the proxy
  // Same-origin proxy to the FastAPI backend, so the session cookie is first-party and no CORS is needed.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      { source: "/metrics", destination: `${api}/metrics` },
    ];
  },
};
