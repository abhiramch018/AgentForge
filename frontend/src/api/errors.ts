import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export async function parseApiError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody | { detail?: ApiErrorBody } | null = null;
  try {
    body = (await response.json()) as ApiErrorBody | { detail?: ApiErrorBody };
  } catch {
    /* non-JSON */
  }
  const detail =
    body && "detail" in body && body.detail && typeof body.detail === "object"
      ? body.detail
      : (body as ApiErrorBody | null);
  const code = detail?.error?.code ?? "http_error";
  const message =
    detail?.error?.message ??
    `Request failed with status ${response.status}.`;
  return new ApiError(response.status, code, message);
}
