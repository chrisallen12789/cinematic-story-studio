# Speech worker lifecycle

The speech worker is an owned child of the authenticated local service, not an independent daemon.

1. The service selects an active, verified model installation and an immutable runtime profile.
2. It creates a unique Windows Job Object and a runtime-instance nonce.
3. It launches a fixed executable and worker module with an argv array and `shell=False`.
4. It sends a one-use bootstrap token and nonce through inherited stdin.
5. The worker returns a bounded HMAC-authenticated hello containing worker, launcher, and parent
   PIDs; resolved executable path; a bounded worker timestamp; the nonce; Job Object assignment;
   protocol/provider/runtime/model versions; package fingerprint; and Python socket-denial count.
6. The service compares the topology and identities with its launched process and Job Object. It
   validates the resolved executable identity and then hashes those resolved executable bytes
   itself. The hello does not supply or attest to that hash, and the service does not independently
   query an OS process-creation timestamp. Any mismatch terminates the exact owned job and
   publishes no clip.
7. Requests and responses use bounded JSON frames over stdin/stdout. Text and pronunciation data
   remain in the authenticated frame, never argv, logs, job events, or process listings.
8. One request runs at a time. A deadline, malformed or unauthenticated response, unexpected frame,
   provider error response, or artifact-identity mismatch terminates the exact owned job before the
   error is returned, including on the final/no-retry attempt.
9. On normal completion the worker becomes idle or shuts down at the configured idle deadline.
10. Application shutdown first stops durable work, then requests provider shutdown, verifies the
    exact PID exited, and only then closes the database.

The service persists lifecycle state (`starting`, `ready`, `busy`, `idle`, `stopping`, `stopped`,
or `failed`) and health without storing text. A restart treats a previously running
record as interrupted; it never adopts a process merely because its name or PID resembles an old
worker. Ownership requires the launched process handle, PID/parent topology, resolved executable
path and service-computed executable hash, authenticated nonce, and Job Object membership. If
ownership cannot be established, the runtime does not authorize a name-based or unrelated-process
termination.

Runtime crashes cannot modify published clip records. Provider output remains in unique staging
until audio validation and atomic publication succeed. Retry reuses durable prerequisite and model
resolution evidence only when their fingerprints are still current; it always reacquires a fresh
runtime identity. Internal worker retry is disabled (`maximumRetryAttempts = 0`), so a durable
retry appends a new provider-request/runtime attempt instead of hiding another dispatch inside an
existing attempt.
