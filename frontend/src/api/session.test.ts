import { afterEach, describe, expect, it } from "vitest";
import {
  clearSessionToken,
  getStoredToken,
  setSessionToken,
} from "../session/session";

describe("session storage policy (api alias)", () => {
  afterEach(() => {
    clearSessionToken();
  });

  it("does not write sessionStorage unless rememberTab is true", () => {
    setSessionToken("secret", false);
    expect(sessionStorage.getItem("agentforge.session.token")).toBeNull();
    expect(getStoredToken()).toBe("secret");
  });
});
