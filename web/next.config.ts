import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";
import { securityHeaders } from "./src/lib/csp.ts";
import { API_URL } from "./src/lib/env.ts";

// A function of the phase, so `next dev` gets no security headers (it needs eval and a websocket
// for hot reload) and `next build` gets the Content Security Policy and its companions on every
// route (plan 7.3). The policy names the API the build was made for: NEXT_PUBLIC_API_URL, inlined
// into the browser code at build time like everything else in lib/env.ts.
export default function nextConfig(phase: string): NextConfig {
  return {
    // The Browser pane may open the dev server as 127.0.0.1:3000 rather than localhost.
    allowedDevOrigins: ["127.0.0.1"],
    poweredByHeader: false,
    async headers() {
      const headers = securityHeaders({ dev: phase === PHASE_DEVELOPMENT_SERVER, apiUrl: API_URL });
      return headers.length ? [{ source: "/:path*", headers }] : [];
    },
  };
}
