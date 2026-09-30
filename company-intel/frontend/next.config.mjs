/** API calls go through the Next server to the FastAPI backend, so no API keys or backend URL reach the browser. */
const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8000";
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND}/api/:path*` }];
  },
  async headers() {
    return [{
      source: "/(.*)",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "microphone=(self)" },
      ],
    }];
  },
};
export default nextConfig;
