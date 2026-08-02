# Phase 3B.1 voice rights and provenance review

Evidence date: 2026-08-02.

This review applies only to the exact allow-listed package fingerprint
`03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0`.
It is a technical and repository-governance record, not legal advice or a claim
of performer consent.

## Primary sources

- S1: [Pinned ONNX conversion repository](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/tree/1939ad2a8e416c0acfeecc08a694d14ef25f2231)
- S2: [Pinned ONNX model card](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/blob/1939ad2a8e416c0acfeecc08a694d14ef25f2231/README.md)
- S3: [Official upstream model card](https://huggingface.co/hexgrad/Kokoro-82M)
- S4: [Maintainer repository reference at the pinned commit](https://github.com/hexgrad/kokoro/blob/dfb907a02bba8152ca444717ca5d78747ccb4bec/kokoro.js/README.md)
- S5: [ONNX Runtime 1.28.0 source and license](https://github.com/microsoft/onnxruntime/tree/v1.28.0)
- L3-L8: exact installed Python package metadata for `onnxruntime==1.28.0`,
  `kokorog2p==0.6.7`, and `numpy==2.5.1`
- Exact local file inventory and SHA-256 values listed below

## Exact evidence register

External source claims use the pinned URLs above and evidence date 2026-08-02.
Local distribution fingerprints were recomputed from every existing path in
the installed wheel `RECORD`, sorted by its slash-normalized relative name. For
each path the canonical line is `relative-name<TAB>byte-size<TAB>sha256<LF>`;
the distribution fingerprint is the SHA-256 of the complete UTF-8 line set.
This binds the conclusion to exact installed bytes without publishing a local
filesystem path.

| Evidence ID | Exact local evidence | Files / bytes | SHA-256 or canonical fingerprint |
| --- | --- | ---: | --- |
| L1 | Allow-listed Kokoro package manifest | 5 / 92,887,010 | `03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0` |
| L2 | Reproducible local package ZIP containing exactly L1 | 1 / 63,531,819 | `65ed269691814c8319d81e6dcb122d66b027988daa5a1cbbdab97d1a7a9f2c69` |
| L3 | Installed `onnxruntime==1.28.0` distribution | 632 / 44,753,047 | `f31f2780ed57412d2f4c96b57ed34b0ee0068a99d56391c31bb3d4e391119538` |
| L4 | Installed `kokorog2p==0.6.7` distribution | 228 / 48,101,552 | `1e4622a500762c326157e2cd52979298da7164d514dfb5df38fd420768e17f72` |
| L5 | Installed `numpy==2.5.1` distribution | 1,358 / 53,742,566 | `c9bc5832643658478b257807bc48e3bd75d2afe1beb07d98c3da468977070823` |
| L6 | ONNX Runtime installed `METADATA` / `RECORD` | 5,703 / 60,839 | `0dc3c5eccdb5ae4d121390131d3db0ff3a077bbdc1e5530b862d85df14d783b7` / `b724b865e60be8615beeca41ce052a2f6c432343442d7bbbfe4985e7e073afe4` |
| L7 | kokorog2p installed `METADATA` / `licenses/LICENSE` / `RECORD` | 31,875 / 11,357 / 16,150 | `c274616d3e7a613dbce1c9b9db32af5c42befd7897385ff897dc4b42fb6b36fd` / `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` / `068417dc891702180a74db8779f45c56a7e9538f1606d20274bcf93ba347c70d` |
| L8 | NumPy installed `METADATA` / `licenses/LICENSE.txt` / `RECORD` | 6,584 / 45,831 / 113,582 | `6ae45122ee97050e48849438320430d05f01814f72e66e69cbeed027d2c6a1e8` / `a804dff0ead9fadc5293456410bcbfc32bf024be9c4513459663fb7b442d2341` / `f365cae968709d6ec0533bad1d76d17b6d720de543e3467d43b31971f0ba5689` |

## Source-by-source matrix

| Question | Primary evidence and conclusion (2026-08-02) | Exact local artifact binding | Product classification |
| --- | --- | --- | --- |
| Runtime code license | S5 plus installed metadata identify MIT; exact kokorog2p metadata/license identify Apache-2.0; exact NumPy metadata/license declare its SPDX expression and bundled obligations. | L3/L6, L4/L7, L5/L8 | Runtime dependencies may execute locally subject to their licenses; preserve applicable notices. |
| ONNX architecture/conversion code | S1/S2 identify the exact converted model family and immutable source revision; S4 is the pinned maintainer implementation reference. | L1; `onnx/model_quantized.onnx`, 92,361,116 bytes, `fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478` | Trusted only for the pinned private local runtime; no mutable download or broader provenance inference. |
| Model weights license | S2/S3 identify Apache-2.0 for the model weights. | Same exact ONNX file and L1/L2 | The weights license does not establish voice-data consent, identity, likeness, or commercial clearance. |
| Voice-style tensor license | No separately scoped exact license for the tensor was established in the reviewed primary material. | `voices/af_heart.bin`, 522,240 bytes, `d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b`; L1/L2 | Restricted/unknown; private local audition only. |
| Dataset provenance | Model-level materials do not provide a complete source and custody chain for this exact voice tensor. | Exact tensor hash and L1/L2 | Incomplete; no dataset-provenance-certainty claim. |
| Performer consent | No exact performer-consent record was found for the exact tensor. | Exact tensor hash and rights-record fingerprint `e801171e684b1125b54bfc4317ae17dac4ca5b92c1500b82b333dc6da357c038` | Unknown; acknowledgement never claims or substitutes for consent. |
| Identity and likeness | No reliable primary-source mapping from the provider-internal identifier to a real person is used or exposed. | Exact tensor hash and rights-record fingerprint | Unknown; mapping, enrollment, cloning, and imitation are prohibited. |
| Commercial use | Permissive runtime/model licenses do not resolve the separate tensor, dataset, consent, and likeness questions. | L1-L8 and exact tensor hash | Restricted; no commercial-clearance claim and production export remains false. |
| Redistribution | Covered code/model obligations exist, but exact tensor scope and performer/voice rights remain unresolved. | L1-L8, exact ONNX hash, exact tensor hash | Raw model/tensor redistribution and marketplace upload are prohibited in Phase 3B.1. |
| Sublicensing | No primary evidence establishes a sublicensable performer or exact voice-tensor right. | Exact tensor hash and rights-record fingerprint | Unknown; no sublicensing claim. |
| Attribution and NOTICE | Official and installed MIT/Apache/NumPy license evidence identifies obligations for covered components; a future distribution requires an exact human notice review. | L3-L8 plus L1 exact file hashes | Preserve exact covered notices; this phase performs no model/tensor distribution. |
| Geographic restrictions | No complete exact voice-rights evidence establishes a cleared geographic grant. | Exact tensor hash and rights-record fingerprint | Unknown; no geographic clearance claim. |
| Field-of-use restrictions | The only repository-authorized field is bounded private local audition under the exact warning; this is a product restriction, not proof of an upstream grant. | Catalog fingerprint `994a2f77daed881cc4e24201d628ef32a732aa6ee0ff0815745a19772d2828cc`, rights-record fingerprint, exact tensor hash | Production, commercial distribution, resale, cloning, and imitation remain prohibited. |
| Revocation/deprecation | Pinned sources and exact installed/local artifacts had no recorded revocation event at the evidence date; future upstream state is not promised. | L1-L8 and catalog/rights fingerprints | Recheck before any later phase; catalog or rights drift invalidates dependent evidence. |

## Exact package inventory

| Relative allow-listed identifier | Bytes | SHA-256 | Role |
| --- | ---: | --- | --- |
| `config.json` | 44 | `df34b4f930b23447cd4dc410fabfb42eb3f24e803e6c3f97d618fb359380a36f` | configuration |
| `onnx/model_quantized.onnx` | 92,361,116 | `fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478` | model |
| `tokenizer.json` | 3,497 | `77a02c8e164413299b4b4c403b14f8e0e1c1b727db4d46a09d6327b861060a34` | tokenizer |
| `tokenizer_config.json` | 113 | `be1cb066d6ef6b074b3f15e6a6dd21ac88ff3cdaedf325f0aaed686c70f75d20` | tokenizer |
| `voices/af_heart.bin` | 522,240 | `d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b` | voice data |

Total: 5 files, 92,887,010 bytes. The voice tensor is little-endian float32
with shape `[510, 256]` (130,560 values). The ONNX model input/output contract
is `input_ids` int64 `[1, sequence_length]`, `style` float32 `[1, 256]`,
`speed` float32 `[1]`, and waveform float32 `[1, num_samples]`; only
`CPUExecutionProvider` is permitted by the governed profile.

## Governing decision

`Local Voice 001` is technically compatible but rights-restricted. Its rights
record remains restricted with consent unknown, commercial use restricted,
redistribution restricted, human verification pending, and production export
false. A displayed acknowledgement authorizes only one bounded private local
audition. It cannot turn an unknown or restricted field into verified evidence.
