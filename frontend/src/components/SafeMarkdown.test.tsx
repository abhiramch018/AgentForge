import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeMarkdown } from "./SafeMarkdown";

describe("SafeMarkdown", () => {
  it("renders markdown text", () => {
    render(<SafeMarkdown content={"**bold** and plain"} />);
    expect(screen.getByText("bold")).toBeInTheDocument();
  });

  it("does not execute script tags as HTML", () => {
    const { container } = render(
      <SafeMarkdown
        content={"Safe text\n\n<script>window.__hacked=1</script>"}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/Safe text/)).toBeInTheDocument();
    expect((window as unknown as { __hacked?: number }).__hacked).toBeUndefined();
  });
});

