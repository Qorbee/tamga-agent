import type { NextConfig } from "next";

// What every page is sent with: no other site may frame one (a hidden sign-in under a decoy is
// the clickjacking this stops), a file is read as the type it is sent as, a link out names only
// this origin, and the browser keeps to https here once it has seen it.
const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Strict-Transport-Security", value: "max-age=31536000" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

const nextConfig: NextConfig = {
  // A self-contained server in .next/standalone, which frontend/Dockerfile copies into the image.
  output: "standalone",
  reactCompiler: true,
  poweredByHeader: false,
  // The rail's bottom corner holds the API status dot.
  devIndicators: { position: "bottom-right" },
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
