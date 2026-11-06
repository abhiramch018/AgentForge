import { describe, expect, it } from "vitest";
import { ApiError, parseApiError } from "./errors";

describe("parseApiError", () => {
  it("extracts structured error body", async () => {
    const response = new Response(
      JSON.stringify({
        error: { code: "unauthorized", message: "Invalid bearer token." },
      }),
      { status: 401 },
    );
    const err = await parseApiError(response);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("unauthorized");
    expect(err.message).toBe("Invalid bearer token.");
    expect(err.status).toBe(401);
  });

  it("falls back when body is not JSON", async () => {
    const response = new Response("nope", { status: 500 });
    const err = await parseApiError(response);
    expect(err.code).toBe("http_error");
    expect(err.status).toBe(500);
  });
});
