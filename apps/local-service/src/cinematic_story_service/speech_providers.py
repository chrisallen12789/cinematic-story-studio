from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from .local_speech import (
    SPEECH_SAMPLE_RATE_HZ,
    SpeechArtifact,
    SpeechCostEstimate,
    SpeechInvocationContext,
    SpeechPronunciationOverrideSpan,
    SpeechProvider,
    SpeechProviderDescriptor,
    SpeechProviderError,
    SpeechProviderHealth,
    SpeechProviderOperationStatus,
    SpeechSynthesisRequest,
    SpeechVoiceDescriptor,
    SpeechVoicePage,
    SpeechVoiceQuery,
    encode_pcm16_wav,
    inspect_pcm_wav,
)
from .model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    ModelPackageManifest,
    verify_model_package_path,
)
from .util import canonical_json, sha256_bytes, sha256_text, utc_now

FIXTURE_PROVIDER_ID = "deterministic-pcm-wav-fixture"
FIXTURE_ADAPTER_VERSION = "1.0.0"
KOKORO_PROVIDER_ID = "kokoro-local-onnx"
KOKORO_ADAPTER_VERSION = "1.0.0"
KOKORO_RUNTIME_ID = "onnxruntime-cpu"
KOKORO_G2P_ID = "kokorog2p-dictionary-only"
KOKORO_MAX_CONTENT_TOKENS = 509
KOKORO_MAX_OUTPUT_SAMPLES = SPEECH_SAMPLE_RATE_HZ * 10 * 60
KOKORO_MAX_RESOLVED_OVERRIDE_PHONEMES = 1_024
FIXTURE_PROVIDER_VOICE_IDS = (
    "fixture-narrator-01",
    "fixture-narrator-02",
    *(f"fixture-character-{ordinal:02d}" for ordinal in range(1, 13)),
)


@dataclass(frozen=True, slots=True)
class KokoroInferenceResult:
    wav_bytes: bytes
    token_ids: tuple[int, ...]
    phonemes_sha256: str
    resolved_pronunciation_override_plan_fingerprint: str
    runtime_version: str
    g2p_version: str
    warnings: tuple[str, ...]


class KokoroInferenceBackend(Protocol):
    def synthesize(
        self,
        text: str,
        speed: float,
        pronunciation_overrides: tuple[SpeechPronunciationOverrideSpan, ...],
    ) -> KokoroInferenceResult: ...


class _KokoroPhonemizeResult(Protocol):
    token_ids: list[int] | None
    phonemes: str | None
    warnings: list[str]


KokoroBackendFactory = Callable[[Path, Path], KokoroInferenceBackend]


class DeterministicPcmWavSpeechProvider:
    """Test-only provider that emits stable integer-generated mono PCM WAV."""

    _voices = tuple(
        SpeechVoiceDescriptor(
            voice_id=voice_id,
            display_name=f"Deterministic fixture {voice_id}",
            language="en-US",
            sample_rate_hz=SPEECH_SAMPLE_RATE_HZ,
            rights_state="verified",
            restricted=False,
            model_id="deterministic-square-wave",
            model_version="1.0.0",
        )
        for voice_id in FIXTURE_PROVIDER_VOICE_IDS
    )

    def descriptor(self) -> SpeechProviderDescriptor:
        return SpeechProviderDescriptor(
            provider_id=FIXTURE_PROVIDER_ID,
            adapter_id=FIXTURE_PROVIDER_ID,
            adapter_version=FIXTURE_ADAPTER_VERSION,
            runtime_id="python-integer-pcm",
            runtime_version="1.0.0",
            execution_location="test",
            synthesis_implemented=True,
            network_required=False,
            credentials_required=False,
            deterministic=True,
            license_id="repository-test-fixture",
            voice_rights_state="verified",
            restricted=False,
        )

    def health(self, context: SpeechInvocationContext | None = None) -> SpeechProviderHealth:
        del context
        return SpeechProviderHealth(
            provider_id=FIXTURE_PROVIDER_ID,
            status="available",
            reason_code="FIXTURE_PROVIDER_AVAILABLE",
            runtime_version="1.0.0",
            model_id="deterministic-square-wave",
            model_version="1.0.0",
        )

    def list_voices(
        self,
        query: SpeechVoiceQuery,
        context: SpeechInvocationContext | None = None,
    ) -> SpeechVoicePage:
        del context
        voices = self._voices if query.language in {None, "en-US"} else ()
        return SpeechVoicePage(voices=voices[: query.limit])

    def estimate(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> SpeechCostEstimate:
        self._validate_request(request, context)
        return SpeechCostEstimate(
            provider_id=FIXTURE_PROVIDER_ID,
            estimated_units=len(request.text),
            unit_name="characters",
            currency=None,
            amount_minor=0,
        )

    def synthesize(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> SpeechArtifact:
        self._validate_request(request, context)
        started_at = utc_now()
        fingerprint = request.input_fingerprint()
        period = 48 + int(fingerprint[:2], 16) % 48
        amplitude = 3_000 + int(fingerprint[2:4], 16) * 8
        frame_count = SPEECH_SAMPLE_RATE_HZ // 4
        samples = tuple(
            amplitude if (frame_index % period) < period // 2 else -amplitude
            for frame_index in range(frame_count)
        )
        wav_bytes = encode_pcm16_wav(samples, sample_rate_hz=SPEECH_SAMPLE_RATE_HZ)
        completed_at = utc_now()
        configuration_fingerprint = sha256_text(
            canonical_json(
                {
                    "amplitude": amplitude,
                    "frameCount": frame_count,
                    "period": period,
                    "providerVersion": FIXTURE_ADAPTER_VERSION,
                    "providerVoiceId": request.voice_id,
                }
            )
        )
        return SpeechArtifact(
            provider_id=FIXTURE_PROVIDER_ID,
            adapter_id=FIXTURE_PROVIDER_ID,
            adapter_version=FIXTURE_ADAPTER_VERSION,
            runtime_id="python-integer-pcm",
            runtime_version="1.0.0",
            model_id="deterministic-square-wave",
            model_version="1.0.0",
            model_sha256=sha256_text("deterministic-square-wave:1.0.0"),
            voice_id=request.voice_id,
            voice_sha256=sha256_text(f"{request.voice_id}:1.0.0"),
            input_fingerprint=fingerprint,
            configuration_fingerprint=configuration_fingerprint,
            wav_bytes=wav_bytes,
            wav_sha256=sha256_bytes(wav_bytes),
            sample_rate_hz=SPEECH_SAMPLE_RATE_HZ,
            channels=1,
            sample_width_bytes=2,
            frame_count=frame_count,
            deterministic=True,
            warnings=(),
            started_at=started_at,
            completed_at=completed_at,
        )

    def reconcile(
        self,
        provider_request_id: str,
        context: SpeechInvocationContext,
    ) -> SpeechProviderOperationStatus:
        context.require_time()
        return SpeechProviderOperationStatus(
            provider_id=FIXTURE_PROVIDER_ID,
            provider_request_id=provider_request_id,
            state="unknown",
            retryable=False,
        )

    def _validate_request(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> None:
        context.require_time()
        if context.network_access_permitted:
            raise SpeechProviderError(
                "SPEECH_NETWORK_POLICY_INVALID",
                "The deterministic local provider does not accept network-enabled invocations.",
            )
        if request.voice_id not in FIXTURE_PROVIDER_VOICE_IDS:
            raise SpeechProviderError(
                "SPEECH_VOICE_NOT_FOUND",
                "The requested fixture voice was not found.",
            )
        if request.language != "en-US":
            raise SpeechProviderError(
                "SPEECH_LANGUAGE_UNSUPPORTED",
                "The requested fixture language was not supported.",
            )


class KokoroLocalOnnxSpeechProvider:
    """Restricted, offline Kokoro q8 adapter using ONNX Runtime directly."""

    def __init__(
        self,
        package_path: Path,
        *,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
        backend_factory: KokoroBackendFactory | None = None,
    ) -> None:
        self.package_path = package_path.absolute()
        self.manifest = manifest
        self._backend_factory = backend_factory or _create_onnx_backend
        self._uses_default_backend = backend_factory is None
        self._backend: KokoroInferenceBackend | None = None

    def descriptor(self) -> SpeechProviderDescriptor:
        return SpeechProviderDescriptor(
            provider_id=KOKORO_PROVIDER_ID,
            adapter_id=KOKORO_PROVIDER_ID,
            adapter_version=KOKORO_ADAPTER_VERSION,
            runtime_id=KOKORO_RUNTIME_ID,
            runtime_version="1.28.0",
            execution_location="local",
            synthesis_implemented=True,
            network_required=False,
            credentials_required=False,
            deterministic=False,
            license_id=self.manifest.license_id,
            voice_rights_state=self.manifest.voice_rights_state,
            restricted=True,
        )

    def health(self, context: SpeechInvocationContext | None = None) -> SpeechProviderHealth:
        del context
        verification = verify_model_package_path(self.package_path, self.manifest)
        if not verification.valid:
            return SpeechProviderHealth(
                provider_id=KOKORO_PROVIDER_ID,
                status="unavailable",
                reason_code="MODEL_PACKAGE_UNVERIFIED",
                model_id=self.manifest.model_id,
                model_version=self.manifest.model_version,
            )
        if self._uses_default_backend and any(
            importlib.util.find_spec(module_name) is None
            for module_name in ("numpy", "onnxruntime", "kokorog2p")
        ):
            return SpeechProviderHealth(
                provider_id=KOKORO_PROVIDER_ID,
                status="unavailable",
                reason_code="LOCAL_SPEECH_RUNTIME_MISSING",
                model_id=self.manifest.model_id,
                model_version=self.manifest.model_version,
            )
        return SpeechProviderHealth(
            provider_id=KOKORO_PROVIDER_ID,
            status="restricted",
            reason_code="VOICE_RIGHTS_UNKNOWN_RESTRICTED",
            runtime_version="1.28.0",
            model_id=self.manifest.model_id,
            model_version=self.manifest.model_version,
        )

    def list_voices(
        self,
        query: SpeechVoiceQuery,
        context: SpeechInvocationContext | None = None,
    ) -> SpeechVoicePage:
        del context
        voice = SpeechVoiceDescriptor(
            voice_id=self.manifest.voice_id,
            display_name="Kokoro af_heart (restricted)",
            language="en-US",
            sample_rate_hz=SPEECH_SAMPLE_RATE_HZ,
            rights_state=self.manifest.voice_rights_state,
            restricted=True,
            model_id=self.manifest.model_id,
            model_version=self.manifest.model_version,
        )
        voices = (voice,) if query.language in {None, voice.language} else ()
        return SpeechVoicePage(voices=voices[: query.limit])

    def estimate(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> SpeechCostEstimate:
        self._validate_request(request, context)
        return SpeechCostEstimate(
            provider_id=KOKORO_PROVIDER_ID,
            estimated_units=len(request.text),
            unit_name="characters",
            currency=None,
            amount_minor=0,
        )

    def synthesize(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> SpeechArtifact:
        self._validate_request(request, context)
        started_at = utc_now()
        verification = verify_model_package_path(self.package_path, self.manifest)
        if not verification.valid:
            raise SpeechProviderError(
                "SPEECH_MODEL_PACKAGE_UNVERIFIED",
                "The local speech model package failed exact verification.",
            )
        model_artifact = next(
            artifact for artifact in self.manifest.artifacts if artifact.role == "model"
        )
        voice_artifact = next(
            artifact for artifact in self.manifest.artifacts if artifact.role == "voice"
        )
        if self._backend is None:
            self._backend = self._backend_factory(
                self.package_path / Path(*model_artifact.path.split("/")),
                self.package_path / Path(*voice_artifact.path.split("/")),
            )
        result = self._backend.synthesize(
            request.text,
            request.speed,
            request.pronunciation_overrides,
        )
        context.require_time()
        sample_rate, channels, sample_width, frame_count = inspect_pcm_wav(result.wav_bytes)
        configuration_fingerprint = sha256_text(
            canonical_json(
                {
                    "adapterVersion": KOKORO_ADAPTER_VERSION,
                    "executionProvider": "CPUExecutionProvider",
                    "g2p": KOKORO_G2P_ID,
                    "g2pVersion": result.g2p_version,
                    "modelRevision": self.manifest.source_revision,
                    "phonemesSha256": result.phonemes_sha256,
                    "pronunciationOverridePlanFingerprint": (
                        request.pronunciation_override_plan_fingerprint()
                    ),
                    "resolvedPronunciationOverridePlanFingerprint": (
                        result.resolved_pronunciation_override_plan_fingerprint
                    ),
                    "speed": request.speed,
                    "tokenIds": list(result.token_ids),
                }
            )
        )
        completed_at = utc_now()
        return SpeechArtifact(
            provider_id=KOKORO_PROVIDER_ID,
            adapter_id=KOKORO_PROVIDER_ID,
            adapter_version=KOKORO_ADAPTER_VERSION,
            runtime_id=KOKORO_RUNTIME_ID,
            runtime_version=result.runtime_version,
            model_id=self.manifest.model_id,
            model_version=self.manifest.model_version,
            model_sha256=model_artifact.sha256,
            voice_id=self.manifest.voice_id,
            voice_sha256=voice_artifact.sha256,
            input_fingerprint=request.input_fingerprint(),
            configuration_fingerprint=configuration_fingerprint,
            wav_bytes=result.wav_bytes,
            wav_sha256=sha256_bytes(result.wav_bytes),
            sample_rate_hz=sample_rate,
            channels=channels,
            sample_width_bytes=sample_width,
            frame_count=frame_count,
            deterministic=False,
            warnings=result.warnings,
            started_at=started_at,
            completed_at=completed_at,
        )

    def reconcile(
        self,
        provider_request_id: str,
        context: SpeechInvocationContext,
    ) -> SpeechProviderOperationStatus:
        context.require_time()
        return SpeechProviderOperationStatus(
            provider_id=KOKORO_PROVIDER_ID,
            provider_request_id=provider_request_id,
            state="unknown",
            retryable=False,
        )

    def _validate_request(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> None:
        context.require_time()
        if context.network_access_permitted:
            raise SpeechProviderError(
                "SPEECH_NETWORK_POLICY_INVALID",
                "Kokoro synthesis is an offline-only operation.",
            )
        if request.voice_id != self.manifest.voice_id:
            raise SpeechProviderError(
                "SPEECH_VOICE_NOT_FOUND",
                "The requested local Kokoro voice was not installed.",
            )
        if request.language != "en-US":
            raise SpeechProviderError(
                "SPEECH_LANGUAGE_UNSUPPORTED",
                "The restricted Kokoro adapter currently accepts en-US only.",
            )
        if not context.restricted_voice_acknowledged:
            raise SpeechProviderError(
                "SPEECH_VOICE_RIGHTS_RESTRICTED",
                "The restricted voice requires an exact current rights acknowledgement.",
            )
        if context.invocation_purpose == "governed_product_audition" and (
            not context.rights_record_id
            or context.rights_record_revision is None
            or context.rights_record_revision < 1
        ):
            raise SpeechProviderError(
                "SPEECH_VOICE_RIGHTS_RESTRICTED",
                "The governed audition requires an exact current rights acknowledgement.",
            )
        if context.invocation_purpose == "component_verification" and (
            context.rights_record_id is not None or context.rights_record_revision is not None
        ):
            raise SpeechProviderError(
                "SPEECH_VOICE_RIGHTS_CONTEXT_INVALID",
                "Component verification cannot claim governed product rights evidence.",
            )


class SpeechProviderRegistry:
    def __init__(self, providers: tuple[SpeechProvider, ...]) -> None:
        self._providers: dict[str, SpeechProvider] = {}
        for provider in providers:
            descriptor = provider.descriptor()
            if descriptor.provider_id in self._providers:
                raise ValueError("Speech provider identities must be unique.")
            self._providers[descriptor.provider_id] = provider

    def get(self, provider_id: str) -> SpeechProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise SpeechProviderError(
                "SPEECH_PROVIDER_NOT_FOUND",
                "The requested speech provider was not registered.",
            ) from exc

    def descriptors(self) -> tuple[SpeechProviderDescriptor, ...]:
        return tuple(
            self._providers[provider_id].descriptor() for provider_id in sorted(self._providers)
        )


class _OnnxKokoroBackend:
    def __init__(self, model_path: Path, voice_path: Path) -> None:
        self._np = importlib.import_module("numpy")
        self._ort = importlib.import_module("onnxruntime")
        self._g2p_module = importlib.import_module("kokorog2p")
        self.runtime_version = importlib.metadata.version("onnxruntime")
        self.g2p_version = importlib.metadata.version("kokorog2p")

        options = self._ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        if hasattr(self._ort, "ExecutionMode"):
            options.execution_mode = self._ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = self._ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        if self._session.get_providers() != ["CPUExecutionProvider"]:
            raise SpeechProviderError(
                "SPEECH_RUNTIME_PROVIDER_INVALID",
                "The local speech runtime did not select CPUExecutionProvider exclusively.",
            )
        if {value.name for value in self._session.get_inputs()} != {
            "input_ids",
            "style",
            "speed",
        }:
            raise SpeechProviderError(
                "SPEECH_MODEL_SCHEMA_INVALID",
                "The local speech model input schema was invalid.",
            )
        self._voices = self._np.fromfile(str(voice_path), dtype="<f4")
        if int(self._voices.size) != 510 * 256:
            raise SpeechProviderError(
                "SPEECH_VOICE_SHAPE_INVALID",
                "The local speech voice tensor had an invalid shape.",
            )
        self._voices = self._voices.reshape(510, 256)
        self._g2p = self._g2p_module.get_g2p(
            language="en-us",
            backend="kokorog2p",
            use_espeak_fallback=False,
            use_goruut_fallback=False,
            use_spacy=False,
            strict=True,
            version="1.0",
        )

    def synthesize(
        self,
        text: str,
        speed: float,
        pronunciation_overrides: tuple[SpeechPronunciationOverrideSpan, ...],
    ) -> KokoroInferenceResult:
        result, resolved_override_plan_fingerprint = self._phonemize(
            text,
            pronunciation_overrides,
        )
        token_ids = tuple(int(value) for value in (result.token_ids or ()))
        phonemes = result.phonemes or ""
        if not token_ids or not phonemes:
            raise SpeechProviderError(
                "SPEECH_G2P_EMPTY",
                "The local dictionary G2P did not produce model input.",
            )
        if "❓" in phonemes or result.warnings:
            raise SpeechProviderError(
                "SPEECH_PRONUNCIATION_REVIEW_REQUIRED",
                "The text contains an unknown or review-required pronunciation.",
            )
        if len(token_ids) > KOKORO_MAX_CONTENT_TOKENS:
            raise SpeechProviderError(
                "SPEECH_TEXT_TOKEN_LIMIT",
                "The text exceeded the local model token limit and was not truncated.",
            )
        input_ids = self._np.asarray([[0, *token_ids, 0]], dtype=self._np.int64)
        style = self._np.asarray(self._voices[len(token_ids)][None, :], dtype=self._np.float32)
        speed_input = self._np.asarray([speed], dtype=self._np.float32)
        output = self._session.run(
            None,
            {"input_ids": input_ids, "style": style, "speed": speed_input},
        )
        if len(output) != 1:
            raise SpeechProviderError(
                "SPEECH_MODEL_OUTPUT_INVALID",
                "The local speech model returned an invalid output count.",
            )
        waveform = self._np.asarray(output[0], dtype=self._np.float32).reshape(-1)
        if (
            int(waveform.size) <= 0
            or int(waveform.size) > KOKORO_MAX_OUTPUT_SAMPLES
            or not bool(self._np.isfinite(waveform).all())
        ):
            raise SpeechProviderError(
                "SPEECH_MODEL_OUTPUT_INVALID",
                "The local speech model returned invalid audio samples.",
            )
        clipped = self._np.clip(waveform, -1.0, 1.0)
        pcm = self._np.rint(clipped * 32767.0).astype(self._np.int16)
        wav_bytes = encode_pcm16_wav(
            tuple(int(value) for value in pcm.tolist()),
            sample_rate_hz=SPEECH_SAMPLE_RATE_HZ,
        )
        return KokoroInferenceResult(
            wav_bytes=wav_bytes,
            token_ids=token_ids,
            phonemes_sha256=sha256_text(phonemes),
            resolved_pronunciation_override_plan_fingerprint=(resolved_override_plan_fingerprint),
            runtime_version=self.runtime_version,
            g2p_version=self.g2p_version,
            warnings=(),
        )

    def _phonemize(
        self,
        text: str,
        pronunciation_overrides: tuple[SpeechPronunciationOverrideSpan, ...],
    ) -> tuple[_KokoroPhonemizeResult, str]:
        overrides: list[object] = []
        resolved_material: list[dict[str, object]] = []
        for span in pronunciation_overrides:
            resolved_phonemes = span.pronunciation
            if span.representation == "neutral":
                neutral_result = self._run_g2p(span.pronunciation, overrides=[])
                neutral_token_ids = tuple(int(value) for value in (neutral_result.token_ids or ()))
                resolved_phonemes = neutral_result.phonemes or ""
                if (
                    not neutral_token_ids
                    or not resolved_phonemes
                    or "❓" in resolved_phonemes
                    or neutral_result.warnings
                    or len(neutral_token_ids) > KOKORO_MAX_CONTENT_TOKENS
                    or len(resolved_phonemes) > KOKORO_MAX_RESOLVED_OVERRIDE_PHONEMES
                ):
                    raise SpeechProviderError(
                        "SPEECH_PRONUNCIATION_REVIEW_REQUIRED",
                        "The provider-neutral pronunciation could not be resolved safely.",
                    )
            overrides.append(
                self._g2p_module.OverrideSpan(
                    char_start=span.source_start,
                    char_end=span.source_end,
                    attrs={"ph": resolved_phonemes},
                )
            )
            resolved_material.append(
                {
                    **span.fingerprint_material(),
                    "resolvedPhonemesSha256": sha256_text(resolved_phonemes),
                }
            )
        fingerprint = sha256_text(
            canonical_json(
                {
                    "adapterVersion": KOKORO_ADAPTER_VERSION,
                    "g2p": KOKORO_G2P_ID,
                    "g2pVersion": self.g2p_version,
                    "sourceTextSha256": sha256_text(text),
                    "spans": resolved_material,
                }
            )
        )
        return self._run_g2p(text, overrides=overrides), fingerprint

    def _run_g2p(
        self,
        text: str,
        *,
        overrides: list[object],
    ) -> _KokoroPhonemizeResult:
        return cast(
            _KokoroPhonemizeResult,
            self._g2p_module.phonemize(
                text,
                language="en-us",
                overrides=overrides,
                return_ids=True,
                return_phonemes=True,
                alignment="span",
                overlap="strict",
                use_normalizer_rules=False,
                use_espeak_fallback=False,
                use_goruut_fallback=False,
                use_spacy=False,
                backend="kokorog2p",
                g2p=self._g2p,
            ),
        )


def _create_onnx_backend(model_path: Path, voice_path: Path) -> KokoroInferenceBackend:
    try:
        return _OnnxKokoroBackend(model_path, voice_path)
    except ImportError as exc:
        raise SpeechProviderError(
            "SPEECH_RUNTIME_MISSING",
            "The optional local speech runtime is not installed.",
        ) from exc
    except SpeechProviderError:
        raise
    except Exception as exc:
        raise SpeechProviderError(
            "SPEECH_RUNTIME_INITIALIZATION_FAILED",
            "The optional local speech runtime could not be initialized safely.",
        ) from exc
