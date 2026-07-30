# Desktop Runtime

## Process model

The installed application has three privilege tiers:

1. **Electron main** is the trusted desktop coordinator. It owns windows, native dialogs, credential operations, the backend child process, its launch token, and authenticated service calls.
2. **Preload** exposes a frozen, allow-listed, versioned API such as
   `projects.list`, `projects.importSelectedFile`, `analysis.createRun`,
   `analysis.listEntities`, `analysis.correct`, `analysis.decideReview`,
   `dialogue.correctSpeaker`, `jobs.cancel`, and typed event subscriptions.
3. **React renderer** presents state. It has no Node.js globals, filesystem/process APIs, arbitrary IPC, service token, or direct service connection.

`contextIsolation` and the renderer sandbox are enabled, `nodeIntegration` and remote modules are disabled, navigation/new-window requests are denied unless explicitly allow-listed, and a restrictive Content Security Policy permits packaged assets only. External policy/help links open through an allow-listed HTTPS handler after user action.

## Production service bootstrap

Electron main performs this sequence for every application launch:

1. Resolve the bundled service executable from immutable application resources and verify that it is inside the expected install root.
2. Generate at least 256 bits with the operating-system cryptographic RNG for a one-time launch bearer token and an independent launch/instance nonce.
3. Spawn the executable directly with an argument array, a private app-data working directory, hidden window flags, constrained inherited handles, and **no shell**. Token and manuscript data never appear in arguments or environment.
4. Write one length-bounded bootstrap record containing the token and nonce to the child's inherited stdin/control pipe, then keep the pipe open for owned-parent liveness and typed shutdown control. The child accepts the secret only in the first record and rejects missing, late, duplicate, or oversized bootstrap data.
5. The service binds `127.0.0.1` with port `0`, lets Windows assign a free port, completes migrations/recovery, and writes one length-bounded readiness record containing protocol version, port, instance ID, and nonce. It never chooses a fixed port or listens on `0.0.0.0`/`::`.
6. Main validates the nonce, port range, protocol, child ownership, and message shape, then calls authenticated `GET /api/v1/health`. It does not trust readiness text alone.
7. Main retains the token in process memory, never persists it or sends it to preload/renderer, and proxies typed requests over authenticated loopback HTTP.

The service rejects all API requests without `Authorization: Bearer <launch-token>`. CORS is not enabled because a browser renderer does not call it. Mutating requests also carry an idempotency key and contract version where required.

Development may run the service separately with `CSS_DEV_MODE=1` and an explicit, non-default `CSS_DEV_TOKEN`. That mode still binds only `127.0.0.1`; it must fail closed if the token is absent/placeholder, and it is never enabled in a packaged build. Development-token values are ignored by logs and diagnostics.

## IPC contract

The preload bridge accepts only discriminated request objects and returns discriminated success/error results. Main validates:

- channel and contract version;
- identifier, string, collection, and payload size limits;
- current project/window ownership;
- operation-specific authorization;
- response/event payloads received from the service.

There is no `invoke(channel: string, payload: unknown)`, raw HTTP proxy, arbitrary path reader, shell command, environment accessor, or generic credential method. Subscription functions return an unsubscribe handle; main removes listeners when a window closes. High-volume source/audio bytes do not pass through renderer IPC.

Phase 2 analysis IPC accepts only allow-listed collection, gate, correction,
filter, cursor, revision, and fingerprint unions. Main validates the request
and the bounded service response. There is no generic analysis route/path
proxy, arbitrary JSON-patch method, or manuscript-text list response.

For import, the renderer asks main to show a native picker with supported filters. Main opens the selected file safely, checks basic metadata, and streams it to the authenticated multipart endpoint. The service repeats authoritative validation. The renderer receives source metadata, not an authority to read the selected path.

## Window and deep-link safety

- Persist only non-sensitive window geometry and a project ID, never source text or credentials.
- Validate geometry against current displays before use.
- Treat file associations, future custom protocols, command-line file arguments, drag/drop, clipboard, and deep links as untrusted input routed through the same import validation.
- Do not load remote UI content, disable web security, or expose DevTools in production by default.
- Redact page titles, crash annotations, recent-file metadata, and notifications that could reveal manuscript text.

## Health, reconnect, and shutdown

Main owns a connection state machine: `starting → ready ↔ degraded/disconnected → stopping → stopped`. It uses bounded health timeouts and exponential backoff with jitter. A service exit is reported by stable code plus sanitized stderr; raw story/provider content is excluded by the service before emission.

Automatic restart is bounded (for example, two retries in a rolling minute) and only for the child main launched. During disconnection, mutating UI controls are disabled while unsaved form state is preserved. After reconnection, the client reloads authoritative revisions rather than replaying unknown writes.

On application exit:

1. main stops accepting new desktop mutations and sends authenticated graceful
   shutdown; the current UI does not add a shutdown-choice dialog;
2. the service checkpoints/marks running work interrupted, closes SQLite, and
   exits;
3. after a bounded timeout, main terminates only the verified owned
   child/process group and records a redacted diagnostic.

The service watches its parent/control channel and self-terminates after checkpointing if the parent disappears. Electron never discovers or kills processes by executable name.

## Updates and compatibility

Renderer, preload, main, service, and contract artifacts carry compatible build/protocol versions. A mismatch displays an actionable startup error and does not attempt database mutation. Phase 0 has no automatic updater. A future signed updater must verify publisher/signature, stage atomically, preserve rollback data, and never update the service independently of its desktop contract.
