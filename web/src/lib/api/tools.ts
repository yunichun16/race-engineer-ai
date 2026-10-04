"use client";

/** The tools through the resource store: one request and one answer per tool and parameters. */

import { apiUrl, callTool, toolPath, type ApiQuery } from "./client.ts";
import { recheckOnUnreachable } from "./health.ts";
import { useResource, type ResourceState } from "./resource.ts";
import type { ToolName, ToolParams, ToolResult } from "./types.ts";

/** The store key of a tool call: its URL, the same for equal parameters in any order. */
export function toolKey<N extends ToolName>(name: N, params: ToolParams[N]): string {
  return apiUrl(toolPath(name), params as ApiQuery);
}

/**
 * One tool's answer; idle while `params` is null. A change of parameters aborts the previous
 * request (when nothing else watches it) and keeps its answer as `stale` until the new one lands.
 */
export function useTool<N extends ToolName>(name: N, params: ToolParams[N] | null): ResourceState<ToolResult<N>> {
  const key = params === null ? null : toolKey(name, params);
  return useResource(key, (signal) => recheckOnUnreachable(callTool(name, params as ToolParams[N], { signal })));
}
