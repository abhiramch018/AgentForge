import { ApiError, parseApiError } from "./errors";
import type {
  ApprovalListResponse,
  ApprovalRecord,
  JobListResponse,
  JobResultResponse,
  JobStatusResponse,
  JobSubmitResponse,
  MemoryListResponse,
  MetaResponse,
  ObservabilitySummary,
  ProtectedAction,
  ReplayTimeline,
  RunRequestBody,
} from "./types";

export type TokenProvider = () => string | null;

export type AgentForgeClient = ApiClient;

export function createClient(getToken: TokenProvider): ApiClient {
  return new ApiClient(getToken);
}

const JSON_HEADERS = { Accept: "application/json" } as const;

function parseSseBlocks(
  buffer: string,
): { parsed: SseMessage[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const parsed: SseMessage[] = [];
  for (const block of parts) {
    if (!block.trim()) continue;
    let eventName = "message";
    let id: string | undefined;
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("id:")) id = line.slice(3).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length) {
      const raw = dataLines.join("\n");
      let data: unknown = raw;
      try {
        data = JSON.parse(raw);
      } catch {
        /* keep string */
      }
      parsed.push({ id, event: eventName, data });
    }
  }
  return { parsed, rest };
}

interface SseMessage {
  id?: string;
  event: string;
  data: unknown;
}

export class ApiClient {
  constructor(private readonly getToken: TokenProvider) {}

  private authHeaders(): HeadersInit {
    const token = this.getToken();
    if (!token) {
      return { ...JSON_HEADERS };
    }
    return {
      ...JSON_HEADERS,
      Authorization: `Bearer ${token}`,
    };
  }

  private async request<T>(
    method: string,
    path: string,
    init?: RequestInit & { json?: unknown },
  ): Promise<T> {
    const headers: HeadersInit = {
      ...this.authHeaders(),
      ...(init?.headers ?? {}),
    };
    let body: BodyInit | undefined = init?.body ?? undefined;
    if (init?.json !== undefined) {
      (headers as Record<string, string>)["Content-Type"] =
        "application/json";
      body = JSON.stringify(init.json);
    }
    let response: Response;
    try {
      response = await fetch(path, {
        ...init,
        method,
        headers,
        body,
      });
    } catch {
      throw new ApiError(0, "network_error", "Backend unreachable.");
    }
    if (!response.ok) {
      throw await parseApiError(response);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  health(): Promise<{ status: "ok" }> {
    return this.request("GET", "/health");
  }

  meta(): Promise<MetaResponse> {
    return this.request("GET", "/v1/meta");
  }

  listJobs(limit = 50, offset = 0): Promise<JobListResponse> {
    return this.request(
      "GET",
      `/v1/jobs?limit=${limit}&offset=${offset}`,
    );
  }

  submitJob(body: RunRequestBody): Promise<JobSubmitResponse> {
    return this.request("POST", "/v1/jobs", { json: body });
  }

  jobStatus(jobId: string): Promise<JobStatusResponse> {
    return this.request("GET", `/v1/jobs/${encodeURIComponent(jobId)}`);
  }

  getJob(jobId: string): Promise<JobStatusResponse> {
    return this.jobStatus(jobId);
  }

  jobResult(jobId: string): Promise<JobResultResponse> {
    return this.request("GET", `/v1/jobs/${encodeURIComponent(jobId)}/result`);
  }

  getJobResult(jobId: string): Promise<JobResultResponse> {
    return this.jobResult(jobId);
  }

  jobObservability(jobId: string): Promise<ObservabilitySummary> {
    return this.request(
      "GET",
      `/v1/jobs/${encodeURIComponent(jobId)}/observability`,
    );
  }

  getObservability(jobId: string): Promise<ObservabilitySummary> {
    return this.jobObservability(jobId);
  }

  jobReplay(jobId: string): Promise<ReplayTimeline> {
    return this.request("GET", `/v1/jobs/${encodeURIComponent(jobId)}/replay`);
  }

  getReplay(jobId: string): Promise<ReplayTimeline> {
    return this.jobReplay(jobId);
  }

  listMemory(limit = 50, offset = 0): Promise<MemoryListResponse> {
    return this.request(
      "GET",
      `/v1/memory?limit=${limit}&offset=${offset}`,
    );
  }

  deleteMemory(memoryId: string): Promise<{ deleted: number }> {
    return this.request(
      "DELETE",
      `/v1/memory/${encodeURIComponent(memoryId)}`,
    );
  }

  deleteAllMemory(): Promise<{ deleted: number }> {
    return this.request("DELETE", "/v1/memory");
  }

  listApprovals(limit = 50): Promise<ApprovalListResponse> {
    return this.request("GET", `/v1/approvals?limit=${limit}`);
  }

  getApproval(approvalId: string): Promise<ApprovalRecord> {
    return this.request(
      "GET",
      `/v1/approvals/${encodeURIComponent(approvalId)}`,
    );
  }

  approve(
    approvalId: string,
    fingerprint: string,
    note?: string,
  ): Promise<ApprovalRecord> {
    return this.request(
      "POST",
      `/v1/approvals/${encodeURIComponent(approvalId)}/approve`,
      { json: { fingerprint, note: note ?? null } },
    );
  }

  reject(approvalId: string, note?: string): Promise<ApprovalRecord> {
    return this.request(
      "POST",
      `/v1/approvals/${encodeURIComponent(approvalId)}/reject`,
      { json: { note: note ?? null } },
    );
  }

  revise(
    approvalId: string,
    action: ProtectedAction,
    note?: string,
  ): Promise<ApprovalRecord> {
    return this.request(
      "POST",
      `/v1/approvals/${encodeURIComponent(approvalId)}/revise`,
      { json: { action, note: note ?? null } },
    );
  }

  /** Authenticated SSE via fetch; invokes callback per parsed event. */
  async openJobEventStream(
    jobId: string,
    options: {
      lastEventId: number | null;
      signal: AbortSignal;
      onEvent: (payload: { id?: string; event: string; data: unknown }) => void;
    },
  ): Promise<void> {
    const headers: HeadersInit = {
      Accept: "text/event-stream",
      ...this.authHeaders(),
    };
    if (options.lastEventId != null && options.lastEventId > 0) {
      (headers as Record<string, string>)["Last-Event-ID"] = String(
        options.lastEventId,
      );
    }
    const response = await fetch(
      `/v1/jobs/${encodeURIComponent(jobId)}/events`,
      {
        method: "GET",
        headers,
        signal: options.signal,
      },
    );
    if (!response.ok) {
      throw await parseApiError(response);
    }
    if (!response.body) {
      throw new Error("SSE response has no body");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { parsed, rest } = parseSseBlocks(buffer);
      buffer = rest;
      for (const msg of parsed) {
        options.onEvent({
          id: msg.id,
          event: msg.event,
          data: msg.data,
        });
      }
    }
  }
}

export { ApiError };
