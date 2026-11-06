import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoadingScreen } from "./LoadingScreen";

describe("LoadingScreen", () => {
  afterEach(() => {
    cleanup();
  });

  it("shows brand, subtitle, and default status", () => {
    render(<LoadingScreen />);
    expect(
      screen.getByRole("heading", { name: "AgentForge" }),
    ).toBeInTheDocument();
    expect(screen.getByText("AI Orchestration Platform")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading workspace…",
    );
  });

  it("marks the SVG logo decorative when wordmark is present", () => {
    const { container } = render(<LoadingScreen />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  it("shows error alert and keyboard-accessible retry", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    const { container } = render(
      <LoadingScreen error="Backend unavailable." onRetry={onRetry} />,
    );
    const root = within(container);
    expect(root.getByRole("alert")).toHaveTextContent(
      "Backend unavailable.",
    );
    expect(root.queryByRole("status")).not.toBeInTheDocument();
    const retry = root.getByRole("button", { name: "Retry" });
    retry.focus();
    expect(retry).toHaveFocus();
    await user.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
