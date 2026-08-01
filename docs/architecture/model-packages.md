# Managed speech model packages

Speech models are private application data, not source dependencies. No model, voice tensor,
generated audio, cache, or model-derived binary is committed or uploaded in ordinary CI
artifacts.

The repository contains only trusted manifest metadata. Installation is an authenticated, bounded
multipart upload of a ZIP selected by the user after an explicit restricted-use acknowledgement.
The service writes the upload to a service-generated file below `${data_dir}/model-staging` and
passes only that private path to `ModelPackageManager`, whose managed root is
`${data_dir}/models`. The renderer neither receives nor supplies a filesystem path. The staging
file is deleted after the install or repair attempt. There is no network model installer.

A manifest records package/provider/model/runtime identity; platform and architecture; source and
provenance; license/commercial/attribution classifications; exact file inventory, sizes, hashes,
and expanded total; required dependencies; compatibility; manifest version; and revocation state.
The manifest fingerprint is the trust root. Hashes found only inside an untrusted archive are not
trusted.

The current Kokoro manifest is `windows`/`x64`, binds `kokoro-local-onnx@1.0.0` to
`onnxruntime-cpu@1.28.0`, and classifies the source as a
`maintainer_referenced_conversion`. Its official upstream reference is Hexgrad's Kokoro-82M
repository; immutable conversion and maintainer-reference evidence are separate provenance. Its
commercial-use classification remains `restricted`, regardless of the upstream Apache-2.0
metadata, because the selected voice's rights chain is unresolved. The canonical manifest
fingerprint is `03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0`.

Validation rejects:

- absolute, drive, UNC, traversal, empty, or overlong members;
- ZIP entries marked as symlinks or other special files, and source-tree links or reparse points;
- duplicate or case-fold-colliding names and every name outside the exact allow-listed inventory;
- unexpected, missing, executable, pickle, script, shared-library, or unsupported files;
- excessive member count, member size, expanded total, compression ratio, or archive bytes;
- any size, hash, inventory, or manifest mismatch.

Extraction uses a unique application-owned directory, verifies bytes while streaming, fsyncs, and
atomically renames the complete installation. Verification is repeatable and emits immutable
evidence. Activation requires a current successful byte/inventory verification. It does not parse
the ONNX graph, initialize ONNX Runtime, validate the input schema or voice tensor, or run an
inference health check. Those runtime/schema checks occur when the provider backend is first
initialized by the private component command; a successful activation alone is not evidence that
synthesis works or that Kokoro is available to the governed product. No Phase 3A voice profile,
snapshot-selected assignment, or rights record currently binds provider-internal voice
`af_heart`; an explicit governed binding and legal/rights review remain required. Repair creates
another recorded transition. Removal targets only the exact managed package and is denied while
it is active; historical manifests, verifications, clip metadata, reviews, and unrelated audio
remain.

The directory importer accepts regular files but does not prove that an otherwise regular file has
no additional hard links. ZIP and filesystem safety therefore depend on the exact trusted
inventory and hashes, private service storage, and the local-account trust boundary; they are not
a general-purpose archive sandbox or static ONNX security scanner.
