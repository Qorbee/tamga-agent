import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A self-contained server in .next/standalone, which frontend/Dockerfile copies into the image.
  output: "standalone",
  reactCompiler: true,
  // The rail's bottom corner holds the API status dot.
  devIndicators: { position: "bottom-right" },
};

export default nextConfig;
