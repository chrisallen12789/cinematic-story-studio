// @vitest-environment node

import { describe, expect, it } from "vitest";

import {
  parseCreateProjectRequest,
  parseReadyLine,
  ValidationError
} from "./validation";

describe("desktop boundary validation", () => {
  it("accepts the bounded service readiness record", () => {
    expect(
      parseReadyLine(
        'CSS_READY {"port":43129,"instanceId":"instance-1","nonce":"nonce-1","protocolVersion":"1.0.0"}',
        "nonce-1"
      )
    ).toEqual({ port: 43129, instanceId: "instance-1" });
  });

  it("rejects a readiness record with the wrong nonce", () => {
    expect(() =>
      parseReadyLine(
        'CSS_READY {"port":43129,"instanceId":"instance-1","nonce":"other","protocolVersion":"1.0.0"}',
        "nonce-1"
      )
    ).toThrow(ValidationError);
  });

  it("rejects missing or incompatible readiness protocol versions", () => {
    expect(() =>
      parseReadyLine(
        'CSS_READY {"port":43129,"instanceId":"instance-1","nonce":"nonce-1"}',
        "nonce-1"
      )
    ).toThrow("protocol version");

    expect(() =>
      parseReadyLine(
        'CSS_READY {"port":43129,"instanceId":"instance-1","nonce":"nonce-1","protocolVersion":"2.0.0"}',
        "nonce-1"
      )
    ).toThrow("protocol version");
  });

  it("rejects unknown IPC fields and oversized project names", () => {
    expect(() =>
      parseCreateProjectRequest({
        contractVersion: "1.0.0",
        payload: {
          name: "Synthetic Demo",
          idempotencyKey: "request-1",
          arbitraryPath: "C:\\private\\story.md"
        }
      })
    ).toThrow("unknown field");

    expect(() =>
      parseCreateProjectRequest({
        contractVersion: "1.0.0",
        payload: {
          name: "x".repeat(121),
          idempotencyKey: "request-1"
        }
      })
    ).toThrow("invalid length");
  });
});
