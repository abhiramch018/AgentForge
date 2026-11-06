const SESSION_KEY = "agentforge.session.token";
const REMEMBER_KEY = "agentforge.session.remember";

let memoryToken: string | null = null;

export function getStoredToken(): string | null {
  if (memoryToken) {
    return memoryToken;
  }
  if (typeof sessionStorage === "undefined") {
    return null;
  }
  if (sessionStorage.getItem(REMEMBER_KEY) !== "1") {
    return null;
  }
  return sessionStorage.getItem(SESSION_KEY);
}

export function setSessionToken(token: string, rememberTab: boolean): void {
  const trimmed = token.trim();
  memoryToken = trimmed || null;
  if (typeof sessionStorage === "undefined") {
    return;
  }
  if (rememberTab && trimmed) {
    sessionStorage.setItem(SESSION_KEY, trimmed);
    sessionStorage.setItem(REMEMBER_KEY, "1");
  } else {
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(REMEMBER_KEY);
  }
}

export function clearSessionToken(): void {
  memoryToken = null;
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(REMEMBER_KEY);
  }
}

export function peekMemoryToken(): string | null {
  return memoryToken;
}
