import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { SessionProvider } from "./SessionContext";
import * as session from "../session/session";

const health = vi.fn(async () => ({ status: "ok" as const }));
const meta = vi.fn(async () => ({
  api_version: "v1" as const,
  auth_required: true,
  workspace_id: "default",
  memory_enabled_default: true,
  max_inflight_runs: 2,
  max_inflight_tasks: 8,
  max_run_concurrency: 4,
  capacity_scope: "single_process" as const,
  workspace_scope: "single_workspace" as const,
  review_default_enabled: false,
  tools_enabled: false,
  notes: [] as string[],
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>(
    "../api/client",
  );
  return {
    ...actual,
    createClient: () => ({
      health,
      meta,
      submitJob: vi.fn(),
      listJobs: vi.fn(async () => ({
        items: [],
        count: 0,
        status_counts: {
          queued: 0,
          running: 0,
          succeeded: 0,
          failed: 0,
          interrupted: 0,
          awaiting_approval: 0,
        },
      })),
      getJob: vi.fn(),
      getJobResult: vi.fn(),
      getJobObservability: vi.fn(),
      getJobReplay: vi.fn(),
      listMemory: vi.fn(),
      deleteMemory: vi.fn(),
      deleteAllMemory: vi.fn(),
      listApprovals: vi.fn(),
      getApproval: vi.fn(),
      approve: vi.fn(),
      reject: vi.fn(),
      revise: vi.fn(),
      streamJobEvents: vi.fn(),
    }),
  };
});

function mountApp() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <SessionProvider>
        <App />
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe("session bootstrap / loader gate", () => {
  afterEach(() => {
    session.clearSessionToken();
    health.mockClear();
    meta.mockClear();
    health.mockImplementation(async () => ({ status: "ok" as const }));
  });

  it("goes to connect without an endless loader when no token is stored", async () => {
    vi.spyOn(session, "getStoredToken").mockReturnValue(null);
    mountApp();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /connect to agentforge/i }),
      ).toBeInTheDocument();
    });
    expect(health).not.toHaveBeenCalled();
  });

  it("restores a stored session into the overview", async () => {
    vi.spyOn(session, "getStoredToken").mockReturnValue("demo-token");
    mountApp();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /overview/i }),
      ).toBeInTheDocument();
    });
    expect(health).toHaveBeenCalled();
    expect(meta).toHaveBeenCalled();
  });

  it("surfaces bootstrap failure with retry when the API is down", async () => {
    vi.spyOn(session, "getStoredToken").mockReturnValue("demo-token");
    health.mockRejectedValueOnce(new Error("network"));
    mountApp();
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
