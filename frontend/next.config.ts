import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  experimental: {
    // The same-origin rewrite handles video uploads in development. Next's
    // 10 MB development proxy default must not undercut FastAPI's 2 GB limit.
    middlewareClientMaxBodySize: "2gb",
  },
  async headers() {
    // Never inside another site's frame: /envoyer and the settings' buttons cannot be clickjacked.
    return [{
      source: "/:path*",
      headers: [
        { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
        { key: "X-Frame-Options", value: "DENY" },
      ],
    }];
  },
  async rewrites() {
    return [{
      source: "/api/:path*",
      destination: "http://api:8000/:path*",
    }];
  },
};

export default nextConfig;
