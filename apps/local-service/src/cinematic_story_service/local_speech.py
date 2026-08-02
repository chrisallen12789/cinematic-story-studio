from __future__ import annotations

import io
import math
import wave
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal, Protocol, runtime_checkable

from .util import canonical_json, sha256_bytes, sha256_text, utc_now

SPEECH_CONTRACT_VERSION = "1.0.0"
SPEECH_OUTPUT_FORMAT = "pcm_s16le_wav"
SPEECH_SAMPLE_RATE_HZ = 24_000
MAX_SPEECH_TEXT_CHARACTERS = 16_000
MAX_SPEECH_AUDIO_BYTES = 32 * 1024 * 1024
MAX_SPEECH_WARNINGS = 32
MAX_SPEECH_PRONUNCIATION_OVERRIDES = 1_000
MAX_SPEECH_PRONUNCIATION_VALUE_CHARACTERS = 256
SPEECH_PRONUNCIATION_OVERRIDE_PLAN_VERSION = "1.0.0"

ProviderHealthStatus = Literal[
    "available",
    "degraded",
    "unavailable",
    "unauthorized",
    "disabled",
    "restricted",
]
ProviderOperationState = Literal[
    "unknown",
    "pending",
    "succeeded",
    "failed",
    "cancelled",
]
VoiceRightsState = Literal["verified", "restricted", "unknown", "prohibited"]
SpeechPronunciationRepresentation = Literal["ipa", "neutral"]


@dataclass(slots=True)
class SpeechProviderError(Exception):
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


@dataclass(frozen=True, slots=True)
class SpeechProviderDescriptor:
    provider_id: str
    adapter_id: str
    adapter_version: str
    runtime_id: str
    runtime_version: str
    execution_location: Literal["local", "cloud", "development", "test"]
    synthesis_implemented: bool
    network_required: bool
    credentials_required: bool
    deterministic: bool
    license_id: str
    voice_rights_state: VoiceRightsState
    restricted: bool
    capabilities: tuple[str, ...] = ("text_to_speech",)
    contract_version: str = SPEECH_CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class SpeechProviderHealth:
    provider_id: str
    status: ProviderHealthStatus
    reason_code: str
    checked_at: str = field(default_factory=utc_now)
    expires_in_seconds: int = 30
    runtime_version: str | None = None
    model_id: str | None = None
    model_version: str | None = None


@dataclass(frozen=True, slots=True)
class SpeechVoiceDescriptor:
    voice_id: str
    display_name: str
    language: str
    sample_rate_hz: int
    rights_state: VoiceRightsState
    restricted: bool
    model_id: str
    model_version: str


@dataclass(frozen=True, slots=True)
class SpeechVoiceQuery:
    language: str | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 200:
            raise ValueError("Voice query limit must be between 1 and 200.")


@dataclass(frozen=True, slots=True)
class SpeechVoicePage:
    voices: tuple[SpeechVoiceDescriptor, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class SpeechPronunciationOverrideSpan:
    source_start: int
    source_end: int
    grapheme: str
    pronunciation: str
    representation: SpeechPronunciationRepresentation
    entry_id: str
    entry_revision: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_start, int)
            or isinstance(self.source_start, bool)
            or not isinstance(self.source_end, int)
            or isinstance(self.source_end, bool)
            or self.source_start < 0
            or self.source_end <= self.source_start
        ):
            raise ValueError("Pronunciation override source bounds are invalid.")
        if (
            not isinstance(self.grapheme, str)
            or not self.grapheme
            or len(self.grapheme) > MAX_SPEECH_TEXT_CHARACTERS
        ):
            raise ValueError("A bounded pronunciation override grapheme is required.")
        if (
            not isinstance(self.pronunciation, str)
            or not self.pronunciation.strip()
            or len(self.pronunciation) > MAX_SPEECH_PRONUNCIATION_VALUE_CHARACTERS
        ):
            raise ValueError("A bounded pronunciation override value is required.")
        if self.representation not in {"ipa", "neutral"}:
            raise ValueError("The pronunciation override representation is unsupported.")
        if not isinstance(self.entry_id, str) or not self.entry_id or len(self.entry_id) > 160:
            raise ValueError("A bounded pronunciation override entry identifier is required.")
        if (
            not isinstance(self.entry_revision, int)
            or isinstance(self.entry_revision, bool)
            or self.entry_revision < 1
        ):
            raise ValueError("The pronunciation override entry revision must be positive.")
        if _contains_ascii_control(self.grapheme) or _contains_ascii_control(self.pronunciation):
            raise ValueError("Pronunciation overrides cannot contain ASCII control characters.")

    def fingerprint_material(self) -> dict[str, object]:
        return {
            "entryId": self.entry_id,
            "entryRevision": self.entry_revision,
            "grapheme": self.grapheme,
            "pronunciation": self.pronunciation,
            "representation": self.representation,
            "sourceEnd": self.source_end,
            "sourceStart": self.source_start,
        }


@dataclass(frozen=True, slots=True)
class SpeechSynthesisRequest:
    request_id: str
    text: str
    voice_id: str
    language: str = "en-US"
    speed: float = 1.0
    sample_rate_hz: int = SPEECH_SAMPLE_RATE_HZ
    output_format: str = SPEECH_OUTPUT_FORMAT
    pronunciation_overrides: tuple[SpeechPronunciationOverrideSpan, ...] = ()

    def __post_init__(self) -> None:
        if not self.request_id or len(self.request_id) > 160:
            raise ValueError("A bounded request identifier is required.")
        if not self.text.strip():
            raise ValueError("Speech text must not be blank.")
        if len(self.text) > MAX_SPEECH_TEXT_CHARACTERS:
            raise ValueError("Speech text exceeded its fixed character limit.")
        if not self.voice_id or len(self.voice_id) > 160:
            raise ValueError("A bounded voice identifier is required.")
        if not self.language or len(self.language) > 32:
            raise ValueError("A bounded language identifier is required.")
        if not math.isfinite(self.speed) or not 0.5 <= self.speed <= 2.0:
            raise ValueError("Speech speed must be finite and between 0.5 and 2.0.")
        if self.sample_rate_hz != SPEECH_SAMPLE_RATE_HZ:
            raise ValueError("Local speech providers produce exactly 24 kHz source audio.")
        if self.output_format != SPEECH_OUTPUT_FORMAT:
            raise ValueError("Only deterministic PCM WAV output is accepted.")
        if not isinstance(self.pronunciation_overrides, tuple):
            raise ValueError("Pronunciation overrides must be an immutable tuple.")
        if len(self.pronunciation_overrides) > MAX_SPEECH_PRONUNCIATION_OVERRIDES:
            raise ValueError("The pronunciation override plan exceeded its fixed span limit.")
        cursor = 0
        for span in self.pronunciation_overrides:
            if not isinstance(span, SpeechPronunciationOverrideSpan):
                raise ValueError("The pronunciation override plan contained an invalid span.")
            if (
                span.source_start < cursor
                or span.source_end > len(self.text)
                or self.text[span.source_start : span.source_end] != span.grapheme
            ):
                raise ValueError(
                    "The pronunciation override plan was stale, overlapping, or out of bounds."
                )
            cursor = span.source_end

    def pronunciation_override_plan_fingerprint(self) -> str:
        return sha256_text(
            canonical_json(
                {
                    "planVersion": SPEECH_PRONUNCIATION_OVERRIDE_PLAN_VERSION,
                    "sourceTextSha256": sha256_text(self.text),
                    "spans": [span.fingerprint_material() for span in self.pronunciation_overrides],
                }
            )
        )

    def input_fingerprint(self) -> str:
        return sha256_text(
            canonical_json(
                {
                    "contractVersion": SPEECH_CONTRACT_VERSION,
                    "language": self.language,
                    "outputFormat": self.output_format,
                    "pronunciationOverridePlanFingerprint": (
                        self.pronunciation_override_plan_fingerprint()
                    ),
                    "sampleRateHz": self.sample_rate_hz,
                    "speed": self.speed,
                    "text": self.text,
                    "voiceId": self.voice_id,
                }
            )
        )


def _contains_ascii_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


@dataclass(frozen=True, slots=True)
class SpeechInvocationContext:
    correlation_id: str
    job_id: str
    attempt_id: str
    idempotency_key: str
    deadline_monotonic: float
    invocation_purpose: Literal["governed_product_audition", "component_verification"] = (
        "governed_product_audition"
    )
    restricted_voice_acknowledged: bool = False
    rights_record_id: str | None = None
    rights_record_revision: int | None = None
    network_access_permitted: bool = False

    def __post_init__(self) -> None:
        if self.invocation_purpose not in {
            "governed_product_audition",
            "component_verification",
        }:
            raise ValueError("The speech invocation purpose is invalid.")

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - monotonic())

    def require_time(self) -> None:
        if self.remaining_seconds() <= 0:
            raise SpeechProviderError(
                "SPEECH_DEADLINE_EXCEEDED",
                "The speech operation exceeded its deadline.",
                retryable=True,
            )


@dataclass(frozen=True, slots=True)
class SpeechCostEstimate:
    provider_id: str
    estimated_units: int
    unit_name: str
    currency: str | None
    amount_minor: int
    status: Literal["estimate", "not_applicable"] = "not_applicable"


@dataclass(frozen=True, slots=True)
class SpeechArtifact:
    provider_id: str
    adapter_id: str
    adapter_version: str
    runtime_id: str
    runtime_version: str
    model_id: str
    model_version: str
    model_sha256: str
    voice_id: str
    voice_sha256: str
    input_fingerprint: str
    configuration_fingerprint: str
    wav_bytes: bytes
    wav_sha256: str
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    deterministic: bool
    warnings: tuple[str, ...]
    started_at: str
    completed_at: str

    def __post_init__(self) -> None:
        if not self.wav_bytes or len(self.wav_bytes) > MAX_SPEECH_AUDIO_BYTES:
            raise ValueError("The speech artifact exceeded its fixed byte bounds.")
        if sha256_bytes(self.wav_bytes) != self.wav_sha256:
            raise ValueError("The speech artifact hash did not match its bytes.")
        if len(self.warnings) > MAX_SPEECH_WARNINGS:
            raise ValueError("The speech artifact returned too many warnings.")
        metadata = inspect_pcm_wav(self.wav_bytes)
        if metadata != (
            self.sample_rate_hz,
            self.channels,
            self.sample_width_bytes,
            self.frame_count,
        ):
            raise ValueError("The speech artifact WAV metadata was inconsistent.")


@dataclass(frozen=True, slots=True)
class SpeechProviderOperationStatus:
    provider_id: str
    provider_request_id: str
    state: ProviderOperationState
    retryable: bool


@runtime_checkable
class SpeechProvider(Protocol):
    def descriptor(self) -> SpeechProviderDescriptor: ...

    def health(self, context: SpeechInvocationContext | None = None) -> SpeechProviderHealth: ...

    def list_voices(
        self,
        query: SpeechVoiceQuery,
        context: SpeechInvocationContext | None = None,
    ) -> SpeechVoicePage: ...

    def estimate(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> SpeechCostEstimate: ...

    def synthesize(
        self,
        request: SpeechSynthesisRequest,
        context: SpeechInvocationContext,
    ) -> SpeechArtifact: ...

    def reconcile(
        self,
        provider_request_id: str,
        context: SpeechInvocationContext,
    ) -> SpeechProviderOperationStatus: ...


def encode_pcm16_wav(samples: tuple[int, ...], *, sample_rate_hz: int) -> bytes:
    if sample_rate_hz <= 0:
        raise ValueError("A positive sample rate is required.")
    if len(samples) * 2 + 44 > MAX_SPEECH_AUDIO_BYTES:
        raise ValueError("PCM sample data exceeded its fixed byte bound.")
    pcm = bytearray(len(samples) * 2)
    for index, sample in enumerate(samples):
        if not -32_768 <= sample <= 32_767:
            raise ValueError("PCM samples must be signed 16-bit integers.")
        value = sample & 0xFFFF
        pcm[index * 2] = value & 0xFF
        pcm[index * 2 + 1] = value >> 8
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(bytes(pcm))
    return output.getvalue()


def inspect_pcm_wav(value: bytes) -> tuple[int, int, int, int]:
    if not value or len(value) > MAX_SPEECH_AUDIO_BYTES:
        raise ValueError("The WAV value exceeded its fixed byte bounds.")
    try:
        with wave.open(io.BytesIO(value), "rb") as wav:
            metadata = (
                wav.getframerate(),
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getnframes(),
            )
            compression = wav.getcomptype()
    except (EOFError, wave.Error) as exc:
        raise ValueError("The speech artifact was not a valid WAV file.") from exc
    if compression != "NONE" or metadata[1:3] != (1, 2) or metadata[0] != SPEECH_SAMPLE_RATE_HZ:
        raise ValueError("The speech artifact must be 24 kHz mono PCM16 WAV.")
    if metadata[3] <= 0:
        raise ValueError("The speech artifact must contain audio frames.")
    return metadata
