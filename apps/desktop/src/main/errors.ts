import type { DesktopError } from "../shared/desktop-api.js";

export class DesktopMainError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly correlationId?: string,
    readonly details?: Readonly<Record<string, string | number | boolean>>
  ) {
    super(message);
    this.name = "DesktopMainError";
  }

  toDesktopError(): DesktopError {
    return {
      code: this.code,
      message: this.message,
      retryable: this.retryable,
      correlationId: this.correlationId,
      details: this.details
    };
  }
}

export class BackendUnavailableError extends DesktopMainError {
  constructor() {
    super(
      "BACKEND_UNAVAILABLE",
      "The local service is unavailable. Reconnect and try again.",
      true
    );
  }
}
