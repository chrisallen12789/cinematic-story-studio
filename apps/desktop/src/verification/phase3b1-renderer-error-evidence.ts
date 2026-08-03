const PHASE3B1_RENDERER_ERROR_CODE = /^[A-Z][A-Z0-9_]{0,127}$/u;

export class Phase3b1RendererError extends Error {
  readonly rendererErrorCode: string;

  constructor(value: unknown) {
    const rendererErrorCode = requirePhase3b1RendererErrorCode(value);
    super(
      `Phase 3B.1 comparison playback was blocked by renderer error code ${rendererErrorCode}.`
    );
    this.name = "Phase3b1RendererError";
    this.rendererErrorCode = rendererErrorCode;
  }
}

export function requirePhase3b1RendererErrorCode(value: unknown): string {
  if (
    typeof value !== "string" ||
    !PHASE3B1_RENDERER_ERROR_CODE.test(value)
  ) {
    throw new Error("The Phase 3B.1 renderer error code was invalid.");
  }
  return value;
}

export function phase3b1RendererErrorCodeFromError(
  error: unknown
): string | null {
  return error instanceof Phase3b1RendererError
    ? error.rendererErrorCode
    : null;
}
