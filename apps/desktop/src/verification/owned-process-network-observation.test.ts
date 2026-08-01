import { describe, expect, it } from "vitest";

import {
  observeOwnedProcessNetworkEndpoints,
  queryExactProcessIdentities
} from "./owned-process-network-observation";
import {
  serviceExecutableName,
  type OwnedProcess,
  type ProcessCommandRequest
} from "./packaged-process-inventory";

const servicePath =
  "C:\\Release\\resources\\service\\cinematic-story-service.exe";

describe("owned-process network observation", () => {
  it("queries only exact, revalidated owned PIDs and returns the bounded count", async () => {
    const requests: ProcessCommandRequest[] = [];
    const values = [owned(4102, 4101), owned(4101, 4100)];
    const observation = await observeOwnedProcessNetworkEndpoints(values, {
      platform: "win32",
      run: async (request) => {
        requests.push(request);
        return JSON.stringify({
          observedPids: [4101, 4102],
          observedNonLoopbackEndpointCount: 0
        });
      }
    });

    expect(observation).toEqual({
      method: "owned_pid_tcp_endpoint_inventory",
      ownedPidsOnly: true,
      observedNonLoopbackEndpointCount: 0
    });
    expect(requests).toHaveLength(1);
    expect(requests[0]?.command).toBe("powershell.exe");
    expect(requests[0]?.timeoutMs).toBe(15_000);
    expect(requests[0]?.maximumOutputBytes).toBe(16 * 1024);
    const script = requests[0]?.arguments.at(-1) ?? "";
    expect(script).toContain("Get-NetTCPConnection -OwningProcess $ownedPid");
    expect(script).toContain(
      'Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId = {0}" -f $ownedPid)'
    );
    expect(script).not.toContain("Get-Process");
    expect(script).not.toContain("Win32_Process -Property");
  });

  it("fails closed when the helper does not observe the exact requested PIDs", async () => {
    await expect(
      observeOwnedProcessNetworkEndpoints([owned(4101, 4100)], {
        platform: "win32",
        run: async () =>
          JSON.stringify({
            observedPids: [9999],
            observedNonLoopbackEndpointCount: 0
          })
      })
    ).rejects.toThrow("exact requested PIDs");
  });

  it("queries process identity by only the exact requested numeric PIDs", async () => {
    const requests: ProcessCommandRequest[] = [];
    const identity = owned(4101, 4100);
    await expect(
      queryExactProcessIdentities([4101], {
        platform: "win32",
        run: async (request) => {
          requests.push(request);
          return JSON.stringify([
            {
              pid: identity.pid,
              parentPid: identity.parentPid,
              name: identity.name,
              executablePath: identity.executablePath,
              creationDate: identity.creationDate
            }
          ]);
        }
      })
    ).resolves.toEqual([
      {
        pid: identity.pid,
        parentPid: identity.parentPid,
        name: identity.name,
        executablePath: identity.executablePath,
        creationDate: identity.creationDate
      }
    ]);
    const script = requests[0]?.arguments.at(-1) ?? "";
    expect(script).toContain("$requestedPids = @(4101)");
    expect(script).toContain(
      'Win32_Process -Filter ("ProcessId = {0}" -f $requestedPid)'
    );
    expect(script).not.toContain("Get-Process");
  });

  it("rejects duplicate, pathless, and non-Windows observations", async () => {
    const value = owned(4101, 4100);
    await expect(
      observeOwnedProcessNetworkEndpoints([value, value], {
        platform: "win32",
        run: async () => "{}"
      })
    ).rejects.toThrow("invalid");
    await expect(
      observeOwnedProcessNetworkEndpoints(
        [{ ...value, executablePath: null }],
        { platform: "win32", run: async () => "{}" }
      )
    ).rejects.toThrow("invalid");
    await expect(
      observeOwnedProcessNetworkEndpoints([value], {
        platform: "linux",
        run: async () => "{}"
      })
    ).rejects.toThrow("requires Windows");
  });
});

function owned(pid: number, parentPid: number): OwnedProcess {
  return {
    pid,
    parentPid,
    name: serviceExecutableName,
    executablePath: servicePath,
    creationDate: `2026-07-31T12:00:${String(pid % 60).padStart(2, "0")}.0000000Z`,
    kind: pid === 4101 ? "service" : "provider_worker"
  };
}
