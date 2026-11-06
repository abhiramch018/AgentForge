import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge, statusLabel } from "./StatusBadge";

describe("StatusBadge", () => {
  it("labels awaiting approval distinctly", () => {
    expect(statusLabel("awaiting_approval")).toBe("Awaiting approval");
    render(<StatusBadge status="awaiting_approval" />);
    expect(screen.getByText("Awaiting approval")).toBeInTheDocument();
  });

  it("labels succeeded as completed", () => {
    render(<StatusBadge status="succeeded" />);
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });
});
