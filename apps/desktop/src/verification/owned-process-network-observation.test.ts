import { describe, expect, it } from "vitest";

import {
  observeLiveOwnedProcessNetworkEndpoints,
  observeOwnedProcessNetworkEndpoints,
  observeStableOwnedProcessNetworkEndpoints,
  queryExactProcessIdentities
} from "./owned-process-network-observation";
import {
  ProcessInventoryError,
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
    const encodedInput =
      requests[0]?.environment?.CSS_OWNED_PROCESS_OBSERVATION_INPUT;
    expect(encodedInput).toBeTypeOf("string");
    expect(
      JSON.parse(Buffer.from(encodedInput ?? "", "base64").toString("utf8"))
    ).toHaveLength(2);
    const script = requests[0]?.arguments.at(-1) ?? "";
    expect(script.length).toBeLessThan(7_000);
    expect(script).not.toContain(encodedInput);
    expect(script).toContain(
      "& $networkCommand -OwningProcess $ownedPid"
    );
    expect(script).toContain("-ErrorAction Stop");
    expect(script).not.toContain("-ErrorAction SilentlyContinue");
    expect(script).toContain(
      "CmdletizationQuery_NotFound_OwningProcess,Get-NetTCPConnection"
    );
    expect(script).toContain("$failureStage = 'identity_requery'");
    expect(script.match(/Win32_Process -Filter/g)).toHaveLength(2);
    expect(script).toContain(
      "Modules\\NetTCPIP\\NetTCPIP.psd1"
    );
    expect(script).toContain(
      "$networkModules[0].ExportedCommands['Get-NetTCPConnection']"
    );
    expect(script).toContain("Import-Module -Name $netTcpIpManifest -Force -PassThru");
    expect(script).not.toContain("Get-Command");
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

  it("reports only allowlisted fail-closed helper stages", async () => {
    await expect(
      observeOwnedProcessNetworkEndpoints([owned(4101, 4100)], {
        platform: "win32",
        run: async () =>
          JSON.stringify({
            commandObjectType: "FunctionInfo",
            failureCode: "OBSERVATION_IDENTITY_CHANGED",
            failureStage: "identity_compare",
            failureType: "RuntimeException",
            ownedPid: 4101
          })
      })
    ).rejects.toThrow(
      "OBSERVATION_IDENTITY_CHANGED, identity_compare, RuntimeException, FunctionInfo, owned PID 4101"
    );
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

  it("excludes an exited historical helper without changing the shutdown ledger", async () => {
    const service = owned(4101, 4100);
    const worker = owned(4102, 4101);
    const exitedParser = {
      ...owned(4103, 4101),
      kind: "service" as const
    };
    const historical = [service, worker, exitedParser];
    const preservedHistory = structuredClone(historical);
    const requests: ProcessCommandRequest[] = [];

    const observation = await observeLiveOwnedProcessNetworkEndpoints(
      historical,
      [service.pid, worker.pid],
      {
        platform: "win32",
        run: async (request) => {
          requests.push(request);
          if (requests.length === 1) {
            return JSON.stringify([
              processIdentityRecord(service),
              processIdentityRecord(worker)
            ]);
          }
          return JSON.stringify({
            observedPids: [service.pid, worker.pid],
            observedNonLoopbackEndpointCount: 0
          });
        }
      }
    );

    expect(observation.observedNonLoopbackEndpointCount).toBe(0);
    expect(historical).toEqual(preservedHistory);
    expect(requests).toHaveLength(2);
    expect(requests[0]?.arguments.at(-1)).toContain(
      "$requestedPids = @(4101,4102,4103)"
    );
    expect(observationInputPids(requests[1])).toEqual([4101, 4102]);
  });

  it("observes every still-live owned helper in addition to the runtime anchors", async () => {
    const service = owned(4101, 4100);
    const worker = owned(4102, 4101);
    const liveHelper = {
      ...owned(4103, 4101),
      kind: "service" as const
    };
    const requests: ProcessCommandRequest[] = [];

    await observeLiveOwnedProcessNetworkEndpoints(
      [service, worker, liveHelper],
      [service.pid, worker.pid],
      {
        platform: "win32",
        run: async (request) => {
          requests.push(request);
          if (requests.length === 1) {
            return JSON.stringify(
              [service, worker, liveHelper].map(processIdentityRecord)
            );
          }
          return JSON.stringify({
            observedPids: [service.pid, worker.pid, liveHelper.pid],
            observedNonLoopbackEndpointCount: 0
          });
        }
      }
    );

    expect(observationInputPids(requests[1])).toEqual([4101, 4102, 4103]);
  });

  it("rejects an absent authenticated anchor and a reused historical PID", async () => {
    const service = owned(4101, 4100);
    const worker = owned(4102, 4101);
    await expect(
      observeLiveOwnedProcessNetworkEndpoints(
        [service, worker],
        [service.pid, worker.pid],
        {
          platform: "win32",
          run: async () => JSON.stringify([processIdentityRecord(service)])
        }
      )
    ).rejects.toThrow("was not live");

    await expect(
      observeLiveOwnedProcessNetworkEndpoints(
        [service, worker],
        [service.pid, worker.pid],
        {
          platform: "win32",
          run: async () =>
            JSON.stringify([
              processIdentityRecord(service),
              {
                ...processIdentityRecord(worker),
                creationDate: "2026-07-31T13:00:00.0000000Z"
              }
            ])
        }
      )
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_AMBIGUOUS_IDENTITY"
    });
  });

  it("re-observes an enlarged ledger and requires a stable post-observation refresh", async () => {
    const service = owned(4101, 4100);
    const worker = owned(4102, 4101);
    const helper = {
      ...owned(4103, 4101),
      kind: "service" as const
    };
    const observations: number[][] = [];
    const requiredObservations: number[][] = [];
    let refreshCount = 0;

    const result = await observeStableOwnedProcessNetworkEndpoints(
      [service, worker],
      [service.pid, worker.pid],
      async (current) => {
        refreshCount += 1;
        return refreshCount === 1 ? current : [service, worker, helper];
      },
      {
        observe: async (current, required) => {
          observations.push(current.map((item) => item.pid));
          requiredObservations.push([...required]);
          return {
            method: "owned_pid_tcp_endpoint_inventory",
            ownedPidsOnly: true,
            observedNonLoopbackEndpointCount: 0
          };
        }
      }
    );

    expect(observations).toEqual([[4101, 4102], [4101, 4102, 4103]]);
    expect(requiredObservations).toEqual([
      [4101, 4102],
      [4101, 4102, 4103]
    ]);
    expect(result.ownedProcesses.map((item) => item.pid)).toEqual([
      4101,
      4102,
      4103
    ]);
  });

  it("retains a non-loopback finding from an invalidated observation attempt", async () => {
    const service = owned(4101, 4100);
    const worker = owned(4102, 4101);
    const helper = {
      ...owned(4103, 4101),
      kind: "service" as const
    };
    let refreshCount = 0;
    let observationCount = 0;

    const result = await observeStableOwnedProcessNetworkEndpoints(
      [service, worker],
      [service.pid, worker.pid],
      async (current) => {
        refreshCount += 1;
        return refreshCount === 1 ? current : [service, worker, helper];
      },
      {
        observe: async () => {
          observationCount += 1;
          return {
            method: "owned_pid_tcp_endpoint_inventory",
            ownedPidsOnly: true,
            observedNonLoopbackEndpointCount:
              observationCount === 1 ? 1 : 0
          };
        }
      }
    );

    expect(result.observation.observedNonLoopbackEndpointCount).toBe(1);
  });

  it("fails closed on perpetual ownership churn and established identity changes", async () => {
    const service = owned(4101, 4100);
    const worker = owned(4102, 4101);
    let nextPid = 4200;
    await expect(
      observeStableOwnedProcessNetworkEndpoints(
        [service, worker],
        [service.pid, worker.pid],
        async (current) => [
          ...current,
          { ...owned(nextPid++, service.pid), kind: "service" as const }
        ],
        {
          observe: async () => ({
            method: "owned_pid_tcp_endpoint_inventory",
            ownedPidsOnly: true,
            observedNonLoopbackEndpointCount: 0
          })
        }
      )
    ).rejects.toThrow("did not stabilize");

    await expect(
      observeStableOwnedProcessNetworkEndpoints(
        [service, worker],
        [service.pid, worker.pid],
        async () => [
          service,
          {
            ...worker,
            creationDate: "2026-07-31T13:00:00.0000000Z"
          }
        ],
        {
          observe: async () => ({
            method: "owned_pid_tcp_endpoint_inventory",
            ownedPidsOnly: true,
            observedNonLoopbackEndpointCount: 0
          })
        }
      )
    ).rejects.toThrow("lost or changed");
  });

  it("retries only bounded transient helper failures", async () => {
    const delays: number[] = [];
    let attempts = 0;
    const identity = owned(4101, 4100);
    await expect(
      queryExactProcessIdentities([identity.pid], {
        platform: "win32",
        delay: async (milliseconds) => {
          delays.push(milliseconds);
        },
        run: async () => {
          attempts += 1;
          if (attempts < 3) {
            throw new ProcessInventoryError(
              "PROCESS_INVENTORY_COMMAND_FAILED",
              true
            );
          }
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
    ).resolves.toHaveLength(1);
    expect(attempts).toBe(3);
    expect(delays).toEqual([250, 750]);
  });

  it("does not retry non-transient helper failures", async () => {
    let attempts = 0;
    await expect(
      queryExactProcessIdentities([4101], {
        platform: "win32",
        delay: async () => undefined,
        run: async () => {
          attempts += 1;
          throw new ProcessInventoryError(
            "PROCESS_INVENTORY_MALFORMED_OUTPUT",
            false
          );
        }
      })
    ).rejects.toMatchObject({
      code: "PROCESS_INVENTORY_MALFORMED_OUTPUT"
    });
    expect(attempts).toBe(1);
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

function processIdentityRecord(value: OwnedProcess) {
  return {
    pid: value.pid,
    parentPid: value.parentPid,
    name: value.name,
    executablePath: value.executablePath,
    creationDate: value.creationDate
  };
}

function observationInputPids(
  request: ProcessCommandRequest | undefined
): readonly number[] {
  const encoded = request?.environment?.CSS_OWNED_PROCESS_OBSERVATION_INPUT;
  if (encoded === undefined) return [];
  const parsed = JSON.parse(
    Buffer.from(encoded, "base64").toString("utf8")
  ) as readonly { readonly pid: number }[];
  return parsed.map((item) => item.pid);
}
