/**
 * Fails a Vercel production build that would ship pointing at the wrong API (plan 7.1).
 *
 *   node scripts/check-env.ts      (the first step of npm run build:vercel)
 *
 * NEXT_PUBLIC_API_URL is inlined into the browser code and the Content Security Policy at build
 * time. Without it, a production site would call http://127.0.0.1:8000 and every page would look
 * broken, so when VERCEL_ENV is "production" it must be the API's https:// address. Other builds
 * (previews, local ones) always pass; the rule is `buildEnvProblems` in src/lib/site-url.ts.
 */

import { buildEnvProblems, siteUrl } from "../src/lib/site-url.ts";

const env = {
  VERCEL_ENV: process.env.VERCEL_ENV,
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_SITE_URL: process.env.NEXT_PUBLIC_SITE_URL,
  VERCEL_PROJECT_PRODUCTION_URL: process.env.VERCEL_PROJECT_PRODUCTION_URL,
};
const { problems, warnings } = buildEnvProblems(env);
const where = env.VERCEL_ENV ? `VERCEL_ENV=${env.VERCEL_ENV}` : "not on Vercel";
for (const warning of warnings) console.warn(`check-env: warning: ${warning}`);
if (problems.length) {
  for (const problem of problems) console.error(`check-env: ${problem}`);
  console.error(`check-env: failed (${where}). Set NEXT_PUBLIC_API_URL in the Vercel project's settings, then redeploy.`);
  process.exitCode = 1;
} else {
  console.log(`check-env: ok (${where}; API ${env.NEXT_PUBLIC_API_URL || "not set"}; site ${siteUrl(env)})`);
}
