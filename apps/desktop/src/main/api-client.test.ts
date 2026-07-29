// @vitest-environment node

import { mkdtemp, open, rm } from "node:fs/promises";
import {
  createServer,
  type Server,
  type ServerResponse
} from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  BackendApiClient,
  IMPORT_LIMIT_BYTES,
  PROJECT_RESPONSE_LIMIT_BYTES
} from "./api-client";
import type { ServiceManager } from "./service-manager";

describe("BackendApiClient project responses", () => {
  it("keeps the maximum import and reopen response boundaries aligned", () => {
    expect(IMPORT_LIMIT_BYTES).toBe(8 * 1024 * 1024);
    expect(PROJECT_RESPONSE_LIMIT_BYTES).toBe(IMPORT_LIMIT_BYTES * 24);
  });

  it(
    "streams the maximum accepted import and can reopen its project",
    async () => {
      const directory = await mkdtemp(path.join(tmpdir(), "css-api-client-"));
      const storyPath = path.join(directory, "maximum-story.txt");
      const file = await open(storyPath, "w");
      const textChunk = Buffer.alloc(64 * 1024, "a");
      for (
        let offset = 0;
        offset < IMPORT_LIMIT_BYTES;
        offset += textChunk.byteLength
      ) {
        await file.write(textChunk, 0, textChunk.byteLength, offset);
      }
      await file.close();
      let receivedImportBytes = 0;
      const server = createServer((request, response) => {
        request.resume();
        request.once("end", () => {
          if (request.method === "POST") {
            receivedImportBytes = Number(
              request.headers["content-length"] ?? 0
            );
            sendJson(response, {
              correlationId: "import-correlation",
              sourceDocument: {
                documentId: "document-1",
                byteLength: IMPORT_LIMIT_BYTES
              },
              story: { storyId: "story-1" }
            });
            return;
          }
          sendJson(response, minimalProjectDetail());
        });
      });
      await new Promise<void>((resolve) => {
        server.listen(0, "127.0.0.1", resolve);
      });
      const client = new BackendApiClient(serviceForServer(server));

      try {
        const imported = await client.importSelectedFile(
          "project-1",
          storyPath,
          "txt"
        );
        const reopened = await client.openProject("project-1");
        expect(imported.sourceDocument.byteLength).toBe(IMPORT_LIMIT_BYTES);
        expect(receivedImportBytes).toBeGreaterThan(IMPORT_LIMIT_BYTES);
        expect(reopened.project.name).toBe("Large manuscript");
      } finally {
        await closeServer(server);
        await rm(directory, { recursive: true, force: true });
      }
    },
    15_000
  );

  it("reopens a project response larger than the general 16 MiB cap", async () => {
    const responseBody = Buffer.from(
      JSON.stringify({
        ...minimalProjectDetail(),
        responsePadding: "x".repeat(17 * 1024 * 1024)
      }),
      "utf8"
    );
    const server = createServer((_request, response) => {
      response.writeHead(200, {
        "Content-Type": "application/json",
        "Content-Length": responseBody.byteLength
      });
      response.end(responseBody);
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (address === null || typeof address === "string") {
      throw new Error("The test server did not bind to a TCP port.");
    }

    try {
      const detail = await new BackendApiClient(
        serviceForServer(server)
      ).openProject("project-1");
      expect(responseBody.byteLength).toBeGreaterThan(16 * 1024 * 1024);
      expect(detail.project.name).toBe("Large manuscript");
    } finally {
      await closeServer(server);
    }
  });
});

function minimalProjectDetail() {
  return {
    correlationId: "correlation-1",
    project: {
      projectId: "project-1",
      name: "Large manuscript",
      revision: 1
    },
    sourceDocuments: [],
    story: null,
    chapters: [],
    scenes: [],
    beats: [],
    characters: [],
    dialogueLines: [],
    dialogueAttributions: [],
    castingAssignments: [],
    castingPlaceholders: [],
    approvals: [],
    jobs: []
  };
}

function serviceForServer(server: Server): ServiceManager {
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("The test server did not bind to a TCP port.");
  }
  return {
    connection: () => ({
      port: address.port,
      token: "test-token",
      instanceId: "test-instance"
    })
  } as unknown as ServiceManager;
}

function sendJson(
  response: ServerResponse,
  value: unknown
): void {
  const body = Buffer.from(JSON.stringify(value), "utf8");
  response.writeHead(200, {
    "Content-Type": "application/json",
    "Content-Length": body.byteLength
  });
  response.end(body);
}

async function closeServer(server: Server): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => {
      if (error === undefined) {
        resolve();
      } else {
        reject(error);
      }
    });
  });
}
