import { afterEach, describe, expect, it } from "vitest";
import {
  clearSessionToken,
  getStoredToken,
  peekMemoryToken,
  setSessionToken,
} from "./session";

describe("session token storage", () => {
  afterEach(() => {
    clearSessionToken();
  });

  it("keeps token in memory when remember is false", () => {
    setSessionToken("abc", false);
    expect(peekMemoryToken()).toBe("abc");
    expect(sessionStorage.getItem("agentforge.session.token")).toBeNull();
    expect(getStoredToken()).toBe("abc");
  });

  it("persists to sessionStorage when remember tab is checked", () => {
    setSessionToken("tab-token", true);
    expect(sessionStorage.getItem("agentforge.session.remember")).toBe("1");
    expect(sessionStorage.getItem("agentforge.session.token")).toBe("tab-token");
    clearSessionToken();
    expect(sessionStorage.getItem("agentforge.session.token")).toBeNull();
  });
});
