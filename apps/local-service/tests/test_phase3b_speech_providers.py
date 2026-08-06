from __future__ import annotations

import hashlib
import importlib.metadata
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cinematic_story_service.local_speech import (
    SpeechInvocationContext,
    SpeechPronunciationOverrideSpan,
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechVoiceQuery,
    encode_pcm16_wav,
    inspect_pcm_wav,
)
from cinematic_story_service.model_packages import (
    ModelPackageArtifact,
    ModelPackageManifest,
    ModelPackageProvenance,
)
from cinematic_story_service.speech_providers import (
    FIXTURE_PROVIDER_ID,
    KOKORO_PROVIDER_ID,
    DeterministicPcmWavSpeechProvider,
    KokoroInferenceResult,
    KokoroLocalOnnxSpeechProvider,
    SpeechProviderRegistry,
    _OnnxKokoroBackend,
)
from cinematic_story_service.util import canonical_json, sha256_text

_REVISION = "1939ad2a8e416c0acfeecc08a694d14ef25f2231"
_REPOSITORY = "onnx-community/Kokoro-82M-v1.0-ONNX"
_FILES = {
    "onnx/model_quantized.onnx": b"model",
    "voices/af_heart.bin": b"voice",
}


def _context(*, restricted: bool = False, network: bool = False) -> SpeechInvocationContext:
    return SpeechInvocationContext(
        correlation_id="correlation-1",
        job_id="job-1",
        attempt_id="attempt-1",
        idempotency_key="idempotency-1",
        deadline_monotonic=time.monotonic() + 10,
        restricted_voice_acknowledged=restricted,
        rights_record_id="rights-1" if restricted else None,
        rights_record_revision=1 if restricted else None,
        network_access_permitted=network,
    )


def _fixture_request() -> SpeechSynthesisRequest:
    return SpeechSynthesisRequest(
        request_id="request-1",
        text="A deterministic local fixture.",
        voice_id="fixture-narrator-01",
    )


def _tiny_manifest() -> ModelPackageManifest:
    prefix = f"https://huggingface.co/{_REPOSITORY}/resolve/{_REVISION}/"
    return ModelPackageManifest(
        package_id="kokoro-provider-test",
        package_version=f"1.0.0+{_REVISION}",
        provider_id="kokoro-local-onnx",
        provider_version="1.0.0",
        model_id=_REPOSITORY,
        model_version="1.0-test",
        runtime_id="onnxruntime-cpu",
        runtime_version="1.28.0",
        platform="windows",
        architecture="x64",
        source_repository=_REPOSITORY,
        source_revision=_REVISION,
        source_classification="maintainer_referenced_conversion",
        official_source_reference="https://huggingface.co/hexgrad/Kokoro-82M",
        license_id="Apache-2.0",
        commercial_use_classification="restricted",
        attribution_requirements=("Retain Apache-2.0 license text.",),
        required_runtime_dependencies=("onnxruntime==1.28.0",),
        compatibility_constraints=("platform:windows", "architecture:x64"),
        revocation_state="active",
        provenance=ModelPackageProvenance(
            conversion_repository=f"https://huggingface.co/{_REPOSITORY}",
            conversion_revision=_REVISION,
            official_upstream_repository="https://huggingface.co/hexgrad/Kokoro-82M",
            official_upstream_model_sha256=(
                "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4"
            ),
            maintainer_reference_repository="https://github.com/hexgrad/kokoro",
            maintainer_reference_revision="dfb907a02bba8152ca444717ca5d78747ccb4bec",
            maintainer_reference_path="kokoro.js/README.md",
        ),
        voice_id="af_heart",
        voice_rights_state="unknown",
        usage_classification="restricted",
        redistribution_approved=False,
        artifacts=(
            ModelPackageArtifact(
                path="onnx/model_quantized.onnx",
                size_bytes=len(_FILES["onnx/model_quantized.onnx"]),
                sha256=hashlib.sha256(_FILES["onnx/model_quantized.onnx"]).hexdigest(),
                source_url=f"{prefix}onnx/model_quantized.onnx",
                role="model",
            ),
            ModelPackageArtifact(
                path="voices/af_heart.bin",
                size_bytes=len(_FILES["voices/af_heart.bin"]),
                sha256=hashlib.sha256(_FILES["voices/af_heart.bin"]).hexdigest(),
                source_url=f"{prefix}voices/af_heart.bin",
                role="voice",
            ),
        ),
    )


def _write_package(root: Path) -> None:
    for relative, value in _FILES.items():
        destination = root / Path(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)


class _FakeKokoroBackend:
    def synthesize(
        self,
        text: str,
        speed: float,
        pronunciation_overrides: tuple[SpeechPronunciationOverrideSpan, ...],
    ) -> KokoroInferenceResult:
        assert text == "A restricted local voice."
        assert speed == 1.0
        assert pronunciation_overrides == (_restricted_override(),)
        wav_bytes = encode_pcm16_wav(tuple(range(-120, 120)), sample_rate_hz=24_000)
        return KokoroInferenceResult(
            wav_bytes=wav_bytes,
            token_ids=(50, 51, 52),
            phonemes_sha256=hashlib.sha256(b"phonemes").hexdigest(),
            resolved_pronunciation_override_plan_fingerprint="f" * 64,
            runtime_version="1.28.0",
            g2p_version="0.6.7",
            warnings=(),
        )


def _restricted_override() -> SpeechPronunciationOverrideSpan:
    return SpeechPronunciationOverrideSpan(
        source_start=2,
        source_end=12,
        grapheme="restricted",
        pronunciation="ɹɪstɹˈɪktɪd",
        representation="ipa",
        entry_id="pronunciation-entry-1",
        entry_revision=3,
    )


def test_speech_request_validates_and_hash_binds_exact_immutable_override_plan() -> None:
    span = _restricted_override()
    request = SpeechSynthesisRequest(
        request_id="request-override-fingerprint",
        text="A restricted local voice.",
        voice_id="af_heart",
        pronunciation_overrides=(span,),
    )
    changed_span = replace(span, pronunciation="ɹɪstɹɪktɪd")
    changed = replace(request, pronunciation_overrides=(changed_span,))

    assert request.pronunciation_overrides == (span,)
    assert request.pronunciation_override_plan_fingerprint() != (
        changed.pronunciation_override_plan_fingerprint()
    )
    assert request.input_fingerprint() != changed.input_fingerprint()

    with pytest.raises(ValueError, match="immutable tuple"):
        replace(request, pronunciation_overrides=[span])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="stale, overlapping, or out of bounds"):
        replace(request, pronunciation_overrides=(replace(span, source_start=3),))


def test_pinned_kokorog2p_receives_clean_text_and_exact_typed_override_span() -> None:
    import kokorog2p

    assert importlib.metadata.version("kokorog2p") == "0.6.7"
    captured: dict[str, Any] = {}

    class RecordingPinnedG2P:
        OverrideSpan = kokorog2p.OverrideSpan

        @staticmethod
        def phonemize(text: str, **kwargs: Any) -> Any:
            captured["text"] = text
            captured["kwargs"] = kwargs
            return kokorog2p.phonemize(text, **kwargs)

    backend = _OnnxKokoroBackend.__new__(_OnnxKokoroBackend)
    backend._g2p_module = RecordingPinnedG2P()
    backend.g2p_version = "0.6.7"
    backend._g2p = kokorog2p.get_g2p(
        language="en-us",
        backend="kokorog2p",
        use_espeak_fallback=False,
        use_goruut_fallback=False,
        use_spacy=False,
        strict=True,
        version="1.0",
    )
    span = SpeechPronunciationOverrideSpan(
        source_start=0,
        source_end=5,
        grapheme="Aster",
        pronunciation="ˈæstɚ",
        representation="ipa",
        entry_id="pronunciation-entry-aster",
        entry_revision=7,
    )

    result, resolved_plan_fingerprint = backend._phonemize("Aster", (span,))

    assert captured["text"] == "Aster"
    assert "[Aster]" not in captured["text"]
    overrides = captured["kwargs"]["overrides"]
    assert len(overrides) == 1
    assert (overrides[0].char_start, overrides[0].char_end, overrides[0].attrs) == (
        0,
        5,
        {"ph": "ˈæstɚ"},
    )
    assert captured["kwargs"]["alignment"] == "span"
    assert captured["kwargs"]["overlap"] == "strict"
    assert captured["kwargs"]["use_normalizer_rules"] is False
    assert result.phonemes == "ˈæstɚ"
    assert result.token_ids == [156, 72, 61, 62, 85]
    assert result.warnings == []
    assert resolved_plan_fingerprint == sha256_text(
        canonical_json(
            {
                "adapterVersion": "1.0.0",
                "g2p": "kokorog2p-dictionary-only",
                "g2pVersion": "0.6.7",
                "sourceTextSha256": sha256_text("Aster"),
                "spans": [
                    {
                        **span.fingerprint_material(),
                        "resolvedPhonemesSha256": sha256_text("ˈæstɚ"),
                    }
                ],
            }
        )
    )


def test_pinned_kokorog2p_resolves_provider_neutral_value_before_span_override() -> None:
    import kokorog2p

    calls: list[tuple[str, dict[str, Any]]] = []

    class RecordingPinnedG2P:
        OverrideSpan = kokorog2p.OverrideSpan

        @staticmethod
        def phonemize(text: str, **kwargs: Any) -> Any:
            calls.append((text, kwargs))
            return kokorog2p.phonemize(text, **kwargs)

    backend = _OnnxKokoroBackend.__new__(_OnnxKokoroBackend)
    backend._g2p_module = RecordingPinnedG2P()
    backend.g2p_version = "0.6.7"
    backend._g2p = kokorog2p.get_g2p(
        language="en-us",
        backend="kokorog2p",
        use_espeak_fallback=False,
        use_goruut_fallback=False,
        use_spacy=False,
        strict=True,
        version="1.0",
    )
    span = SpeechPronunciationOverrideSpan(
        source_start=0,
        source_end=5,
        grapheme="Aster",
        pronunciation="AS-ter",
        representation="neutral",
        entry_id="pronunciation-entry-aster",
        entry_revision=8,
    )

    result, resolved_plan_fingerprint = backend._phonemize("Aster", (span,))

    assert [call[0] for call in calls] == ["AS-ter", "Aster"]
    assert calls[0][1]["overrides"] == []
    resolved_override = calls[1][1]["overrides"][0]
    assert (
        resolved_override.char_start,
        resolved_override.char_end,
        resolved_override.attrs,
    ) == (0, 5, {"ph": "ˈæztˈɜɹ"})
    assert result.phonemes == "ˈæztˈɜɹ"
    assert result.warnings == []
    assert resolved_plan_fingerprint == sha256_text(
        canonical_json(
            {
                "adapterVersion": "1.0.0",
                "g2p": "kokorog2p-dictionary-only",
                "g2pVersion": "0.6.7",
                "sourceTextSha256": sha256_text("Aster"),
                "spans": [
                    {
                        **span.fingerprint_material(),
                        "resolvedPhonemesSha256": sha256_text("ˈæztˈɜɹ"),
                    }
                ],
            }
        )
    )


def test_pinned_kokorog2p_neutral_warning_fails_closed_before_main_text() -> None:
    import kokorog2p

    backend = _OnnxKokoroBackend.__new__(_OnnxKokoroBackend)
    backend._g2p_module = kokorog2p
    backend.g2p_version = "0.6.7"
    backend._g2p = kokorog2p.get_g2p(
        language="en-us",
        backend="kokorog2p",
        use_espeak_fallback=False,
        use_goruut_fallback=False,
        use_spacy=False,
        strict=True,
        version="1.0",
    )
    invalid = SpeechPronunciationOverrideSpan(
        source_start=0,
        source_end=5,
        grapheme="Aster",
        pronunciation="qzxv",
        representation="neutral",
        entry_id="pronunciation-entry-invalid",
        entry_revision=1,
    )

    with pytest.raises(SpeechProviderError) as error:
        backend._phonemize("Aster", (invalid,))
    assert error.value.code == "SPEECH_PRONUNCIATION_REVIEW_REQUIRED"


@pytest.mark.parametrize(
    ("token_ids", "phonemes"),
    [([], ""), ([1], "x" * 1_025)],
    ids=["empty", "oversized"],
)
def test_provider_neutral_unbounded_g2p_result_fails_closed(
    token_ids: list[int],
    phonemes: str,
) -> None:
    class InvalidG2PModule:
        @staticmethod
        def phonemize(_text: str, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(token_ids=token_ids, phonemes=phonemes, warnings=[])

    backend = _OnnxKokoroBackend.__new__(_OnnxKokoroBackend)
    backend._g2p_module = InvalidG2PModule()
    backend.g2p_version = "0.6.7"
    backend._g2p = object()
    invalid = SpeechPronunciationOverrideSpan(
        source_start=0,
        source_end=5,
        grapheme="Aster",
        pronunciation="empty",
        representation="neutral",
        entry_id="pronunciation-entry-empty",
        entry_revision=1,
    )

    with pytest.raises(SpeechProviderError) as error:
        backend._phonemize("Aster", (invalid,))
    assert error.value.code == "SPEECH_PRONUNCIATION_REVIEW_REQUIRED"


def test_kokoro_token_boundary_accepts_509_and_rejects_510_without_inference_or_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import numpy as np

    class BoundaryG2PModule:
        @staticmethod
        def phonemize(text: str, **_kwargs: Any) -> SimpleNamespace:
            token_count = int(text.removeprefix("tokens-"))
            return SimpleNamespace(
                token_ids=list(range(1, token_count + 1)),
                phonemes="a" * token_count,
                warnings=[],
            )

    class RecordingSession:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run(self, _outputs: None, inputs: dict[str, Any]) -> list[Any]:
            self.calls.append(inputs)
            return [np.asarray([0.0, 0.25, -0.25], dtype=np.float32)]

    backend = _OnnxKokoroBackend.__new__(_OnnxKokoroBackend)
    backend._np = np
    backend._g2p_module = BoundaryG2PModule()
    backend._g2p = object()
    backend._voices = np.zeros((510, 256), dtype=np.float32)
    backend._session = RecordingSession()
    backend.runtime_version = "test-runtime"
    backend.g2p_version = "test-g2p"

    encoded_artifacts: list[tuple[int, ...]] = []

    def recording_encoder(
        samples: tuple[int, ...],
        *,
        sample_rate_hz: int,
    ) -> bytes:
        encoded_artifacts.append(samples)
        return encode_pcm16_wav(samples, sample_rate_hz=sample_rate_hz)

    monkeypatch.setattr(
        "cinematic_story_service.speech_providers.encode_pcm16_wav",
        recording_encoder,
    )

    accepted = backend.synthesize("tokens-509", 1.0, ())
    assert len(accepted.token_ids) == 509
    assert len(backend._session.calls) == 1
    assert backend._session.calls[0]["input_ids"].shape == (1, 511)
    assert len(encoded_artifacts) == 1

    with pytest.raises(SpeechProviderError) as error:
        backend.synthesize("tokens-510", 1.0, ())
    assert error.value.code == "SPEECH_TEXT_TOKEN_LIMIT"
    assert len(backend._session.calls) == 1
    assert len(encoded_artifacts) == 1


def test_provider_protocol_and_fixture_are_deterministic_pcm_wav() -> None:
    provider = DeterministicPcmWavSpeechProvider()
    request = _fixture_request()

    assert isinstance(provider, SpeechProvider)
    assert provider.descriptor().provider_id == FIXTURE_PROVIDER_ID
    assert provider.health().status == "available"
    voices = provider.list_voices(query=SpeechVoiceQuery(language="en-US"))
    assert voices.voices[0].restricted is False
    first = provider.synthesize(request, _context())
    second = provider.synthesize(request, _context())

    assert first.wav_bytes == second.wav_bytes
    assert first.wav_sha256 == second.wav_sha256
    assert first.configuration_fingerprint == second.configuration_fingerprint
    assert first.input_fingerprint == request.input_fingerprint()
    assert inspect_pcm_wav(first.wav_bytes) == (24_000, 1, 2, 6_000)
    assert first.deterministic is True


def test_fixture_provider_rejects_network_enabled_invocation() -> None:
    provider = DeterministicPcmWavSpeechProvider()
    with pytest.raises(SpeechProviderError) as error:
        provider.synthesize(_fixture_request(), _context(network=True))
    assert error.value.code == "SPEECH_NETWORK_POLICY_INVALID"


def test_kokoro_provider_is_restricted_and_lazy_until_exact_acknowledged_synthesis(
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "package"
    _write_package(package_path)
    manifest = _tiny_manifest()
    created_paths: list[tuple[Path, Path]] = []

    def factory(model_path: Path, voice_path: Path) -> _FakeKokoroBackend:
        created_paths.append((model_path, voice_path))
        return _FakeKokoroBackend()

    provider = KokoroLocalOnnxSpeechProvider(
        package_path,
        manifest=manifest,
        backend_factory=factory,
    )
    request = SpeechSynthesisRequest(
        request_id="request-kokoro",
        text="A restricted local voice.",
        voice_id="af_heart",
        pronunciation_overrides=(_restricted_override(),),
    )

    descriptor = provider.descriptor()
    assert isinstance(provider, SpeechProvider)
    assert descriptor.provider_id == KOKORO_PROVIDER_ID
    assert descriptor.runtime_id == "onnxruntime-cpu"
    assert descriptor.network_required is False
    assert descriptor.voice_rights_state == "unknown"
    assert descriptor.restricted is True
    assert provider.health().status == "restricted"

    with pytest.raises(SpeechProviderError) as rights_error:
        provider.synthesize(request, _context())
    assert rights_error.value.code == "SPEECH_VOICE_RIGHTS_RESTRICTED"
    assert created_paths == []

    artifact = provider.synthesize(request, _context(restricted=True))
    assert created_paths == [
        (
            package_path / "onnx" / "model_quantized.onnx",
            package_path / "voices" / "af_heart.bin",
        )
    ]
    assert artifact.provider_id == KOKORO_PROVIDER_ID
    assert artifact.runtime_id == "onnxruntime-cpu"
    assert artifact.runtime_version == "1.28.0"
    assert artifact.model_id == manifest.model_id
    assert artifact.model_version == manifest.model_version
    assert artifact.voice_id == "af_heart"
    assert artifact.deterministic is False
    assert inspect_pcm_wav(artifact.wav_bytes) == (24_000, 1, 2, 240)
    assert artifact.configuration_fingerprint == sha256_text(
        canonical_json(
            {
                "adapterVersion": "1.0.0",
                "executionProvider": "CPUExecutionProvider",
                "g2p": "kokorog2p-dictionary-only",
                "g2pVersion": "0.6.7",
                "modelRevision": manifest.source_revision,
                "phonemesSha256": hashlib.sha256(b"phonemes").hexdigest(),
                "pronunciationOverridePlanFingerprint": (
                    request.pronunciation_override_plan_fingerprint()
                ),
                "resolvedPronunciationOverridePlanFingerprint": "f" * 64,
                "speed": 1.0,
                "tokenIds": [50, 51, 52],
            }
        )
    )


def test_provider_registry_rejects_duplicates_and_unknown_provider() -> None:
    provider = DeterministicPcmWavSpeechProvider()
    registry = SpeechProviderRegistry((provider,))
    assert registry.get(FIXTURE_PROVIDER_ID) is provider
    assert registry.descriptors() == (provider.descriptor(),)

    with pytest.raises(ValueError, match="unique"):
        SpeechProviderRegistry((provider, provider))
    with pytest.raises(SpeechProviderError) as missing:
        registry.get("missing")
    assert missing.value.code == "SPEECH_PROVIDER_NOT_FOUND"
