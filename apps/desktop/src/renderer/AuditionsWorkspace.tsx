import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode
} from "react";

import type { Job, ProjectDetail } from "@cinematic-story-studio/contracts/api";
import {
  GOVERNED_PRIVATE_AUDITION_WARNING,
  GOVERNED_PRIVATE_AUDITION_WARNING_SHA256,
  SPEECH_AUDITION_LIMITS
} from "@cinematic-story-studio/contracts";
import type {
  AuditionClip,
  AuditionReview,
  AuditionReviewDecision,
  AuditionRoleStatus,
  AuditionSession,
  AuditionWorkspaceSnapshot,
  HumanListeningAttestationRequest,
  ModelInstallationRecord,
  ModelVerificationRecord,
  PronunciationEntry,
  RestrictedLocalAuditionActivationRequest,
  TextNormalizationPlan
} from "@cinematic-story-studio/contracts";

import type {
  CinematicStoryDesktopApi,
  DesktopError,
  DesktopResult
} from "../shared/desktop-api";
import "./auditions.css";

const syntheticPreviewText =
  "A lantern glowed beside the quiet harbor as the watch changed.";

interface AuditionsWorkspaceProps {
  readonly project: ProjectDetail;
  readonly api: CinematicStoryDesktopApi;
  readonly connected: boolean;
  readonly onNotice: (message: string | null) => void;
  readonly onError: (error: DesktopError | null) => void;
}

interface PronunciationDraft {
  readonly writtenForm: string;
  readonly pronunciation: string;
  readonly reason: string;
}

interface CollectionPageState {
  readonly currentCursor: string | null;
  readonly previousCursors: readonly (string | null)[];
  readonly shown: number;
  readonly total: number;
  readonly nextCursor: string | null;
}

type PageDirection = "previous" | "next" | "restart";

interface ReviewHistoryState {
  readonly items: readonly AuditionReviewDecision[];
  readonly loaded: number;
  readonly total: number;
  readonly nextCursor: string | null;
  readonly pageNumber: number;
}

interface ReviewHistoryCollection {
  readonly projectId: string;
  readonly byScope: Readonly<Record<string, ReviewHistoryState>>;
}

interface WorkspaceLoadRequest {
  readonly projectId: string;
  readonly generation: number;
  readonly hydrate: (generation: number) => Promise<void>;
}

const collectionPageLimit = 50;
const rolePageSize = 12;
const maximumCollectionCursorDepth = 199;
const maximumJobPollAttempts = 40;
const jobPollIntervalMilliseconds = 750;
const terminalJobStates = new Set<Job["state"]>([
  "cancelled",
  "failed",
  "interrupted",
  "paused",
  "succeeded"
]);
const emptyCollectionPage = (): CollectionPageState => ({
  currentCursor: null,
  previousCursors: [],
  shown: 0,
  total: 0,
  nextCursor: null
});

function firstCollectionPage(
  shown: number,
  total: number,
  nextCursor: string | null
): CollectionPageState {
  return {
    currentCursor: null,
    previousCursors: [],
    shown,
    total,
    nextCursor
  };
}

function collectionPageTransition(
  page: CollectionPageState,
  direction: PageDirection
): {
  readonly cursor: string | null;
  readonly currentCursor: string | null;
  readonly previousCursors: readonly (string | null)[];
} | null {
  if (direction === "restart") {
    if (page.previousCursors.length === 0) return null;
    return { cursor: null, currentCursor: null, previousCursors: [] };
  }
  if (direction === "previous") {
    const cursor = page.previousCursors.at(-1);
    if (cursor === undefined) return null;
    return {
      cursor,
      currentCursor: cursor,
      previousCursors: page.previousCursors.slice(0, -1)
    };
  }
  if (
    page.nextCursor === null ||
    page.previousCursors.length >= maximumCollectionCursorDepth
  ) {
    return null;
  }
  return {
    cursor: page.nextCursor,
    currentCursor: page.nextCursor,
    previousCursors: [...page.previousCursors, page.currentCursor]
  };
}

function navigatedCollectionPage(
  transition: NonNullable<ReturnType<typeof collectionPageTransition>>,
  shown: number,
  total: number,
  nextCursor: string | null
): CollectionPageState {
  return {
    currentCursor: transition.currentCursor,
    previousCursors: transition.previousCursors,
    shown,
    total,
    nextCursor
  };
}

export function AuditionsWorkspace({
  project,
  api,
  connected,
  onNotice,
  onError
}: AuditionsWorkspaceProps) {
  const projectId = project.project.projectId;
  const [workspace, setWorkspace] =
    useState<AuditionWorkspaceSnapshot | null>(null);
  const [models, setModels] = useState<readonly {
    readonly installation: ModelInstallationRecord | null;
    readonly verification: ModelVerificationRecord | null;
    readonly modelPackageId: string;
    readonly modelId: string;
    readonly modelVersion: string;
    readonly manifestFingerprint: string;
    readonly sourceClassification:
      | "official_release"
      | "maintainer_referenced_conversion"
      | "repository_fixture";
    readonly licenseIdentifier: string;
    readonly commercialUseClassification:
      | "allowed"
      | "restricted"
      | "fixture_only"
      | "unknown";
  }[]>([]);
  const [pronunciations, setPronunciations] =
    useState<readonly PronunciationEntry[]>([]);
  const [sessions, setSessions] = useState<readonly AuditionSession[]>([]);
  const [clips, setClips] = useState<readonly AuditionClip[]>([]);
  const [modelPage, setModelPage] = useState(emptyCollectionPage);
  const [pronunciationPage, setPronunciationPage] =
    useState(emptyCollectionPage);
  const [sessionPage, setSessionPage] = useState(emptyCollectionPage);
  const [clipPage, setClipPage] = useState(emptyCollectionPage);
  const [rolePage, setRolePage] = useState(emptyCollectionPage);
  const [normalizationPlan, setNormalizationPlan] =
    useState<TextNormalizationPlan | null>(null);
  const [pronunciationTestText, setPronunciationTestText] = useState(
    "Harbor lanterns glowed beside the quiet water."
  );
  const [selectedSessionId, setSelectedSessionId] =
    useState<string | null>(null);
  const [comparisonIds, setComparisonIds] = useState<readonly string[]>([]);
  const [reviewRationales, setReviewRationales] =
    useState<Readonly<Record<string, string>>>({});
  const [reviewHistoryState, setReviewHistoryState] =
    useState<ReviewHistoryCollection>({ projectId, byScope: {} });
  const reviewHistoryByScope: Readonly<Record<string, ReviewHistoryState>> =
    reviewHistoryState.projectId === projectId
      ? reviewHistoryState.byScope
      : {};
  const [pronunciationDecisionRationales, setPronunciationDecisionRationales] =
    useState<Readonly<Record<string, string>>>({});
  const [supersedesEntryId, setSupersedesEntryId] = useState<string | null>(
    null
  );
  const [cacheClearReason, setCacheClearReason] = useState(
    "User requested removal of private audition cache artifacts."
  );
  const [restrictedModelUseAcknowledged, setRestrictedModelUseAcknowledged] =
    useState(false);
  const [modelPackageReason, setModelPackageReason] = useState(
    "Use these exact local model bytes only for restricted local auditions."
  );
  const [restrictedVoiceUseAcknowledged, setRestrictedVoiceUseAcknowledged] =
    useState(false);
  const [restrictedVoiceReason, setRestrictedVoiceReason] = useState(
    "Generate a bounded private local audition under the displayed restrictions."
  );
  const [listenedClipIds, setListenedClipIds] = useState<readonly string[]>([]);
  const [expectedCacheProjectRevision, setExpectedCacheProjectRevision] =
    useState(project.project.revision);
  const [draft, setDraft] = useState<PronunciationDraft>({
    writtenForm: "",
    pronunciation: "",
    reason: "Reviewed this project pronunciation locally."
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [loadedClipId, setLoadedClipId] = useState<string | null>(null);
  const [inspectedJobId, setInspectedJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<Job | null>(null);
  const [jobLoading, setJobLoading] = useState(false);
  const [jobControlBusy, setJobControlBusy] = useState<
    "cancel" | "retry" | "resume" | null
  >(null);
  const [jobPollExhausted, setJobPollExhausted] = useState(false);
  const [jobRefreshToken, setJobRefreshToken] = useState(0);
  const [jobPollingSuppressed, setJobPollingSuppressed] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const loadGeneration = useRef(0);
  const currentProjectId = useRef<string | null>(projectId);
  const workspaceLoadPromise = useRef<Promise<void> | null>(null);
  const workspaceLoadRequest = useRef<WorkspaceLoadRequest | null>(null);

  useLayoutEffect(() => {
    currentProjectId.current = projectId;
    return () => {
      currentProjectId.current = null;
    };
  }, [projectId]);

  const replaceSessionPage = useCallback(
    (items: readonly AuditionSession[]) => {
      const sessionIds = new Set(
        items.map((item) => item.auditionSessionId)
      );
      setSessions(items);
      setNormalizationPlan(null);
      setSelectedSessionId((current) => {
        if (current !== null && sessionIds.has(current)) return current;
        return items[0]?.auditionSessionId ?? null;
      });
    },
    []
  );

  const replacePronunciationPage = useCallback(
    (items: readonly PronunciationEntry[]) => {
      const entryIds = new Set(items.map((item) => item.entryId));
      setPronunciations(items);
      setSupersedesEntryId((current) =>
        current !== null && entryIds.has(current) ? current : null
      );
      setPronunciationDecisionRationales((current) =>
        Object.fromEntries(
          Object.entries(current).filter(([entryId]) => entryIds.has(entryId))
        )
      );
    },
    []
  );

  const replaceClipPage = useCallback(
    (items: readonly AuditionClip[]) => {
      const clipIds = new Set(items.map((item) => item.auditionClipId));
      setClips(items);
      setComparisonIds((current) =>
        current.filter((clipId) => clipIds.has(clipId))
      );
      setListenedClipIds((current) =>
        current.filter((clipId) => clipIds.has(clipId))
      );
      setLoadedClipId(null);
      setAudioUrl((currentUrl) => {
        if (currentUrl !== null) URL.revokeObjectURL(currentUrl);
        return null;
      });
    },
    []
  );

  const selectedSession = useMemo(
    () =>
      sessions.find(
        (session) => session.auditionSessionId === selectedSessionId
      ) ?? sessions[0] ?? null,
    [selectedSessionId, sessions]
  );
  const selectedSessionEvidenceId =
    selectedSession?.auditionSessionId ?? null;
  const comparedClips = useMemo(
    () => clips.filter((clip) => comparisonIds.includes(clip.auditionClipId)),
    [clips, comparisonIds]
  );
  const normalizationReviewRequired =
    normalizationPlan?.humanReviewRequired === true;
  const selectedSessionJobId = selectedSession?.jobId ?? null;

  useEffect(
    () => () => {
      if (audioUrl !== null) URL.revokeObjectURL(audioUrl);
    },
    [audioUrl]
  );

  useEffect(() => {
    let disposed = false;
    queueMicrotask(() => {
      if (disposed) return;
      const restoredJob =
        selectedSessionJobId === null
          ? null
          : (project.jobs?.find(
              (job) => job.jobId === selectedSessionJobId
            ) ?? null);
      const restoredJobIsTerminal =
        restoredJob !== null &&
        restoredJob.projectId === projectId &&
        restoredJob.target.type === "audition_session" &&
        restoredJob.target.id === selectedSession?.auditionSessionId &&
        terminalJobStates.has(restoredJob.state);
      setInspectedJobId(selectedSessionJobId);
      setJobStatus(restoredJob);
      setJobLoading(
        selectedSessionJobId !== null && !restoredJobIsTerminal
      );
      setJobPollingSuppressed(restoredJobIsTerminal);
      setJobControlBusy(null);
      setJobPollExhausted(false);
    });
    return () => {
      disposed = true;
    };
  }, [
    project.jobs,
    projectId,
    selectedSession?.auditionSessionId,
    selectedSessionJobId
  ]);

  const hydrateWorkspace = useCallback(async (generation: number) => {
    const requestIsCurrent = () =>
      generation === loadGeneration.current &&
      projectId === currentProjectId.current;
    if (!requestIsCurrent()) return;
    onError(null);
    const overview = await api.auditions.getWorkspace({
      projectId,
      roleLimit: rolePageSize
    });
    if (!requestIsCurrent()) return;
    if (!overview.ok) {
      onError(overview.error);
      return;
    }
    const next = overview.value.workspace;
    setWorkspace(next);
    setRolePage(
      firstCollectionPage(
        next.roles.items.length,
        next.roles.total,
        next.roles.nextCursor ?? null
      )
    );

    const modelResult = await api.auditions.listModelPackages({
      projectId,
      limit: collectionPageLimit
    });
    if (!requestIsCurrent()) return;
    if (modelResult.ok) {
      setModels(
        modelResult.value.items.map((item) => ({
          installation: item.installation,
          verification: item.verification,
          modelPackageId: item.manifest.modelPackageId,
          modelId: item.manifest.modelId,
          modelVersion: item.manifest.modelVersion,
          manifestFingerprint: item.manifest.manifestFingerprint,
          sourceClassification: item.manifest.sourceClassification,
          licenseIdentifier: item.manifest.licenseIdentifier,
          commercialUseClassification:
            item.manifest.commercialUseClassification
        }))
      );
      setModelPage(
        firstCollectionPage(
          modelResult.value.items.length,
          modelResult.value.total,
          modelResult.value.nextCursor ?? null
        )
      );
    } else {
      setModels([]);
      setModelPage(emptyCollectionPage());
      onError(modelResult.error);
    }

    const sessionResult = await api.auditions.listSessions({
      projectId,
      limit: collectionPageLimit
    });
    if (!requestIsCurrent()) return;
    if (sessionResult.ok) {
      replaceSessionPage(sessionResult.value.items);
      setSessionPage(
        firstCollectionPage(
          sessionResult.value.items.length,
          sessionResult.value.total,
          sessionResult.value.nextCursor ?? null
        )
      );
    } else {
      setSessions([]);
      setSessionPage(emptyCollectionPage());
      onError(sessionResult.error);
    }

    const clipResult = await api.auditions.listClips({
      projectId,
      limit: collectionPageLimit
    });
    if (!requestIsCurrent()) return;
    if (clipResult.ok) {
      replaceClipPage(clipResult.value.items);
      setClipPage(
        firstCollectionPage(
          clipResult.value.items.length,
          clipResult.value.total,
          clipResult.value.nextCursor ?? null
        )
      );
    } else {
      setClips([]);
      setClipPage(emptyCollectionPage());
      onError(clipResult.error);
    }

    if (next.currentDictionary !== null) {
      const pronunciationResult = await api.auditions.listPronunciations({
        projectId,
        limit: collectionPageLimit,
        expectedDictionaryRevision: next.currentDictionary.revision,
        expectedDictionaryFingerprint:
          next.currentDictionary.dictionaryFingerprint
      });
      if (!requestIsCurrent()) return;
      if (pronunciationResult.ok) {
        replacePronunciationPage(pronunciationResult.value.items);
        setPronunciationPage(
          firstCollectionPage(
            pronunciationResult.value.items.length,
            pronunciationResult.value.total,
            pronunciationResult.value.nextCursor ?? null
          )
        );
      } else {
        setPronunciations([]);
        setPronunciationPage(emptyCollectionPage());
        onError(pronunciationResult.error);
      }
    } else {
      setPronunciations([]);
      setPronunciationPage(emptyCollectionPage());
    }
  }, [
    api,
    onError,
    projectId,
    replaceClipPage,
    replacePronunciationPage,
    replaceSessionPage
  ]);

  const loadWorkspace = useCallback(
    (generation = loadGeneration.current): Promise<void> => {
      const activeLoad = workspaceLoadPromise.current;
      if (
        projectId !== currentProjectId.current ||
        generation !== loadGeneration.current
      ) {
        return activeLoad ?? Promise.resolve();
      }
      workspaceLoadRequest.current = {
        projectId,
        generation,
        hydrate: hydrateWorkspace
      };
      if (workspaceLoadPromise.current !== null) {
        return workspaceLoadPromise.current;
      }

      const run = (async () => {
        setLoading(true);
        try {
          while (workspaceLoadRequest.current !== null) {
            const request = workspaceLoadRequest.current;
            workspaceLoadRequest.current = null;
            if (
              request.projectId === currentProjectId.current &&
              request.generation === loadGeneration.current
            ) {
              await request.hydrate(request.generation);
            }
          }
        } finally {
          workspaceLoadPromise.current = null;
          setLoading(false);
        }
      })();
      workspaceLoadPromise.current = run;
      return run;
    },
    [hydrateWorkspace, projectId]
  );

  useEffect(() => {
    const generation = ++loadGeneration.current;
    void loadWorkspace(generation);
    return () => {
      loadGeneration.current += 1;
    };
  }, [loadWorkspace]);

  useEffect(() => {
    let disposed = false;
    if (
      !connected ||
      inspectedJobId === null ||
      selectedSessionEvidenceId === null ||
      jobPollingSuppressed
    ) {
      queueMicrotask(() => {
        if (!disposed) setJobLoading(false);
      });
      return () => {
        disposed = true;
      };
    }
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    const expectedSessionId = selectedSessionEvidenceId;

    const poll = async () => {
      const result = await api.jobs.get(inspectedJobId);
      if (disposed) return;
      setJobLoading(false);
      if (!result.ok) {
        onError(result.error);
        return;
      }
      const job = result.value.job;
      if (
        job.projectId !== projectId ||
        job.target.type !== "audition_session" ||
        job.target.id !== expectedSessionId
      ) {
        onError({
          code: "AUDITION_JOB_EVIDENCE_MISMATCH",
          message:
            "The returned job did not belong to the selected audition session.",
          retryable: false
        });
        return;
      }
      setJobStatus(job);
      if (terminalJobStates.has(job.state)) {
        setJobPollingSuppressed(true);
        void loadWorkspace();
        return;
      }
      attempts += 1;
      if (attempts >= maximumJobPollAttempts) {
        setJobPollExhausted(true);
        return;
      }
      timeoutId = setTimeout(() => void poll(), jobPollIntervalMilliseconds);
    };

    queueMicrotask(() => {
      if (disposed) return;
      setJobLoading(true);
      setJobPollExhausted(false);
      void poll();
    });
    return () => {
      disposed = true;
      if (timeoutId !== null) clearTimeout(timeoutId);
    };
  }, [
    api,
    connected,
    inspectedJobId,
    jobPollingSuppressed,
    jobRefreshToken,
    loadWorkspace,
    onError,
    projectId,
    selectedSessionEvidenceId
  ]);

  async function perform<T>(
    key: string,
    operation: () => Promise<DesktopResult<T>>,
    successMessage: string,
    afterSuccess?: () => Promise<void>
  ): Promise<T | null> {
    if (!connected || busy !== null || loading) return null;
    setBusy(key);
    onError(null);
    try {
      const result = await operation();
      if (!result.ok) {
        onError(result.error);
        return null;
      }
      await afterSuccess?.();
      onNotice(successMessage);
      return result.value;
    } finally {
      setBusy(null);
    }
  }

  async function loadModelPage(direction: PageDirection) {
    const transition = collectionPageTransition(modelPage, direction);
    if (transition === null) return;
    const result = await perform(
      "model-page",
      () =>
        api.auditions.listModelPackages({
          projectId,
          ...(transition.cursor === null ? {} : { cursor: transition.cursor }),
          limit: collectionPageLimit
        }),
      "The requested bounded model-package page was loaded."
    );
    if (result === null) return;
    const items = result.items.map((item) => ({
      installation: item.installation,
      verification: item.verification,
      modelPackageId: item.manifest.modelPackageId,
      modelId: item.manifest.modelId,
      modelVersion: item.manifest.modelVersion,
      manifestFingerprint: item.manifest.manifestFingerprint,
      sourceClassification: item.manifest.sourceClassification,
      licenseIdentifier: item.manifest.licenseIdentifier,
      commercialUseClassification:
        item.manifest.commercialUseClassification
    }));
    setModels(items);
    setModelPage(
      navigatedCollectionPage(
        transition,
        items.length,
        result.total,
        result.nextCursor ?? null
      )
    );
  }

  async function loadRolePage(direction: PageDirection) {
    const transition = collectionPageTransition(rolePage, direction);
    if (transition === null) return;
    const result = await perform(
      "role-page",
      () =>
        api.auditions.getWorkspace({
          projectId,
          ...(transition.cursor === null
            ? {}
            : { roleCursor: transition.cursor }),
          roleLimit: rolePageSize
        }),
      "The requested bounded audition-role page was loaded."
    );
    if (result === null) return;
    setWorkspace(result.workspace);
    setRolePage(
      navigatedCollectionPage(
        transition,
        result.workspace.roles.items.length,
        result.workspace.roles.total,
        result.workspace.roles.nextCursor ?? null
      )
    );
  }

  async function loadSessionPage(direction: PageDirection) {
    const transition = collectionPageTransition(sessionPage, direction);
    if (transition === null) return;
    const result = await perform(
      "session-page",
      () =>
        api.auditions.listSessions({
          projectId,
          ...(transition.cursor === null ? {} : { cursor: transition.cursor }),
          limit: collectionPageLimit
        }),
      "The requested bounded audition-session page was loaded."
    );
    if (result === null) return;
    replaceSessionPage(result.items);
    setSessionPage(
      navigatedCollectionPage(
        transition,
        result.items.length,
        result.total,
        result.nextCursor ?? null
      )
    );
  }

  async function loadClipPage(direction: PageDirection) {
    const transition = collectionPageTransition(clipPage, direction);
    if (transition === null) return;
    const result = await perform(
      "clip-page",
      () =>
        api.auditions.listClips({
          projectId,
          ...(transition.cursor === null ? {} : { cursor: transition.cursor }),
          limit: collectionPageLimit
        }),
      "The requested bounded audition-clip page was loaded."
    );
    if (result === null) return;
    replaceClipPage(result.items);
    setClipPage(
      navigatedCollectionPage(
        transition,
        result.items.length,
        result.total,
        result.nextCursor ?? null
      )
    );
  }

  async function loadPronunciationPage(direction: PageDirection) {
    const transition = collectionPageTransition(pronunciationPage, direction);
    const dictionary = workspace?.currentDictionary;
    if (
      transition === null ||
      dictionary === null ||
      dictionary === undefined
    ) {
      return;
    }
    const result = await perform(
      "pronunciation-page",
      () =>
        api.auditions.listPronunciations({
          projectId,
          ...(transition.cursor === null ? {} : { cursor: transition.cursor }),
          limit: collectionPageLimit,
          expectedDictionaryRevision: dictionary.revision,
          expectedDictionaryFingerprint: dictionary.dictionaryFingerprint
        }),
      "The requested bounded pronunciation page was loaded."
    );
    if (result === null) return;
    if (
      result.dictionary.revision !== dictionary.revision ||
      result.dictionary.dictionaryFingerprint !==
        dictionary.dictionaryFingerprint
    ) {
      onError({
        code: "PRONUNCIATION_PAGE_STALE",
        message:
          "The pronunciation dictionary changed while paging; refresh current evidence.",
        retryable: true
      });
      return;
    }
    replacePronunciationPage(result.items);
    setPronunciationPage(
      navigatedCollectionPage(
        transition,
        result.items.length,
        result.total,
        result.nextCursor ?? null
      )
    );
  }

  async function appendPronunciation(event: FormEvent) {
    event.preventDefault();
    const dictionary = workspace?.currentDictionary;
    if (dictionary === null || dictionary === undefined) return;
    const result = await perform(
      "append-pronunciation",
      () =>
        api.auditions.appendPronunciation({
          projectId,
          expectedDictionaryRevision: dictionary.revision,
          expectedDictionaryFingerprint: dictionary.dictionaryFingerprint,
          writtenForm: draft.writtenForm.trim(),
          language: "en",
          locale: null,
          scope: "project",
          scopeId: null,
          representation: "provider_neutral",
          pronunciation: draft.pronunciation.trim(),
          ipa: null,
          providerId: null,
          providerCompiledValue: null,
          caseSensitive: false,
          matchRule: "whole_word",
          priority: 0,
          reason: draft.reason.trim(),
          supersedesEntryId,
          idempotencyKey: idempotency("pronunciation")
        }),
      supersedesEntryId === null
        ? "The pronunciation entry was appended for review."
        : "The pronunciation revision was appended without changing history."
    );
    if (result !== null) {
      setDraft((current) => ({ ...current, writtenForm: "", pronunciation: "" }));
      setSupersedesEntryId(null);
      await loadWorkspace();
    }
  }

  function startPronunciationRevision(entry: PronunciationEntry) {
    setSupersedesEntryId(entry.entryId);
    setDraft({
      writtenForm: entry.writtenForm,
      pronunciation: entry.pronunciation,
      reason: `Revised after review of ${entry.entryId}.`
    });
  }

  function cancelPronunciationRevision() {
    setSupersedesEntryId(null);
    setDraft((current) => ({
      ...current,
      writtenForm: "",
      pronunciation: "",
      reason: "Reviewed this project pronunciation locally."
    }));
  }

  async function decidePronunciation(
    entry: PronunciationEntry,
    decision: "approve" | "request_changes" | "reject"
  ) {
    if (loading) return;
    const dictionary = workspace?.currentDictionary;
    const rationale = pronunciationDecisionRationales[entry.entryId]?.trim();
    if (dictionary === null || dictionary === undefined) return;
    if (rationale === undefined || rationale.length === 0) {
      onError({
        code: "PRONUNCIATION_RATIONALE_REQUIRED",
        message: "Add a rationale before recording a pronunciation decision.",
        retryable: false
      });
      return;
    }
    const result = await perform(
      `pronunciation-decision-${entry.entryId}`,
      () =>
        api.auditions.decidePronunciation({
          projectId,
          entryId: entry.entryId,
          expectedEntryRevision: entry.revision,
          expectedEntryFingerprint: entry.entryFingerprint,
          expectedDictionaryRevision: dictionary.revision,
          expectedDictionaryFingerprint: dictionary.dictionaryFingerprint,
          decision,
          rationale,
          idempotencyKey: idempotency(`pronunciation-${decision}`)
        }),
      "The append-only pronunciation decision was recorded."
    );
    if (result !== null) await loadWorkspace();
  }

  async function loadReviewHistory(
    review: AuditionReview,
    cursor?: string
  ) {
    const scopeKey = reviewHistoryKey(review);
    const result = await perform(
      `review-history-${scopeKey}`,
      () =>
        api.auditions.listReviewDecisions({
          projectId,
          gateId: review.gateId,
          roleId: review.roleId,
          limit: collectionPageLimit,
          ...(cursor === undefined ? {} : { cursor })
        }),
      cursor === undefined
        ? `${gateLabel(review.gateId)} decision history loaded.`
        : `The next bounded ${gateLabel(review.gateId).toLowerCase()} decision page was loaded.`
    );
    if (result === null) return;
    setReviewHistoryState((current) => {
      const currentByScope =
        current.projectId === projectId ? current.byScope : {};
      const existing = currentByScope[scopeKey];
      return {
        projectId,
        byScope: {
          ...currentByScope,
          [scopeKey]: {
            items: result.items,
            loaded: result.items.length,
            total: result.total,
            nextCursor: result.nextCursor ?? null,
            pageNumber:
              cursor === undefined || existing === undefined
                ? 1
                : existing.pageNumber + 1
          }
        }
      };
    });
  }

  async function previewNormalization() {
    if (selectedSession === null) return;
    const sourceTextSha256 = await sha256Text(syntheticPreviewText);
    const result = await perform(
      "preview-normalization",
      () =>
        api.auditions.previewNormalization({
          projectId,
          auditionSessionId: selectedSession.auditionSessionId,
          expectedSessionRevision: selectedSession.revision,
          text: syntheticPreviewText,
          sourceTextSha256,
          acceptedOptionalNormalizationIds: []
        }),
      "The normalization plan is ready for inspection."
    );
    if (result !== null) setNormalizationPlan(result.plan);
  }

  async function saveSyntheticScript() {
    await saveScript(
      "standardized_synthetic",
      syntheticPreviewText,
      "create-script",
      "The local synthetic audition script was saved."
    );
  }

  async function savePronunciationTest() {
    if (loading) return;
    const text = pronunciationTestText.trim();
    if (text.length === 0 || selectedSession === null || workspace === null) {
      return;
    }
    const role = workspace.roles.items.find(
      (item) => item.roleId === selectedSession.roleId
    );
    if (role?.sessionEvidence === null || role === undefined) {
      onError({
        code: "AUDITION_SESSION_EVIDENCE_UNAVAILABLE",
        message:
          "Current role evidence is required before creating a pronunciation test.",
        retryable: true
      });
      return;
    }
    if (!connected || busy !== null) return;
    const restrictedActivation = restrictedActivationForRole(role);
    if (restrictedActivation === null) return;
    setBusy(`create-pronunciation-test-${role.roleId}`);
    onError(null);
    const sessionResult = await api.auditions.createSession({
      projectId,
      roleId: role.roleId,
      evidence: role.sessionEvidence,
      ...(restrictedActivation === undefined
        ? {}
        : { restrictedLocalAuditionActivation: restrictedActivation }),
      idempotencyKey: idempotency("pronunciation-test-session")
    });
    if (!sessionResult.ok) {
      setBusy(null);
      onError(sessionResult.error);
      return;
    }
    const currentSession = sessionResult.value.session;
    if (restrictedActivation !== undefined) {
      setRestrictedVoiceUseAcknowledged(false);
    }
    const sourceTextSha256 = await sha256Text(text);
    const scriptResult = await api.auditions.createScript({
      projectId,
      auditionSessionId: currentSession.auditionSessionId,
      expectedSessionRevision: currentSession.revision,
      kind: "pronunciation_test",
      text,
      sourceDocumentId: null,
      sourceRevision: null,
      sourceSpan: null,
      sourceTextSha256,
      acceptedOptionalNormalizationIds: [],
      idempotencyKey: idempotency("pronunciation_test")
    });
    setBusy(null);
    setSelectedSessionId(currentSession.auditionSessionId);
    if (!scriptResult.ok) {
      onError(scriptResult.error);
      await loadWorkspace();
      return;
    }
    setNormalizationPlan(scriptResult.value.normalizationPlan);
    onNotice(
      `The governed pronunciation test is the current script for ${role.displayLabel}; Generate audition will synthesize this exact script.`
    );
    await loadWorkspace();
  }

  async function saveScript(
    kind: "standardized_synthetic" | "pronunciation_test",
    text: string,
    operation: string,
    notice: string
  ) {
    if (selectedSession === null) return;
    const sourceTextSha256 = await sha256Text(text);
    const result = await perform(
      operation,
      () =>
        api.auditions.createScript({
          projectId,
          auditionSessionId: selectedSession.auditionSessionId,
          expectedSessionRevision: selectedSession.revision,
          kind,
          text,
          sourceDocumentId: null,
          sourceRevision: null,
          sourceSpan: null,
          sourceTextSha256,
          acceptedOptionalNormalizationIds: [],
          idempotencyKey: idempotency(kind)
        }),
      notice
    );
    if (result !== null) {
      setNormalizationPlan(result.normalizationPlan);
      await loadWorkspace();
    }
  }

  async function generate(role: AuditionRoleStatus) {
    if (role.generationRequest === null) return;
    const result = await perform(
      `generate-${role.roleId}`,
      () =>
        api.auditions.generate({
          projectId,
          preview: role.generationRequest as NonNullable<
            AuditionRoleStatus["generationRequest"]
          >
        }),
      `Audition generation was queued for ${role.displayLabel}.`
    );
    if (result !== null) {
      setJobPollingSuppressed(false);
      setInspectedJobId(result.jobId);
      setJobStatus(null);
      setJobRefreshToken((current) => current + 1);
      await loadWorkspace();
    }
  }

  async function controlAuditionJob(
    action: "cancel" | "retry" | "resume"
  ) {
    if (
      !connected ||
      loading ||
      selectedSession === null ||
      jobStatus === null ||
      jobControlBusy !== null
    ) {
      return;
    }
    setJobControlBusy(action);
    onError(null);
    const result = await api.jobs[action](jobStatus.jobId);
    setJobControlBusy(null);
    if (!result.ok) {
      onError(result.error);
      return;
    }
    const job = result.value.job;
    if (
      job.projectId !== projectId ||
      job.target.type !== "audition_session" ||
      job.target.id !== selectedSession.auditionSessionId
    ) {
      onError({
        code: "AUDITION_JOB_EVIDENCE_MISMATCH",
        message:
          "The job control result did not belong to the selected audition session.",
        retryable: false
      });
      return;
    }
    setJobStatus(job);
    setJobPollingSuppressed(false);
    setInspectedJobId(job.jobId);
    setJobRefreshToken((current) => current + 1);
    onNotice(`The audition job ${action} result is ${humanize(job.state)}.`);
    await loadWorkspace();
  }

  async function createSession(role: AuditionRoleStatus) {
    if (loading) return;
    if (role.sessionEvidence === null) return;
    const restrictedActivation = restrictedActivationForRole(role);
    if (restrictedActivation === null) return;
    const result = await perform(
      `create-session-${role.roleId}`,
      () =>
        api.auditions.createSession({
          projectId,
          roleId: role.roleId,
          evidence: role.sessionEvidence as NonNullable<
            AuditionRoleStatus["sessionEvidence"]
          >,
          ...(restrictedActivation === undefined
            ? {}
            : { restrictedLocalAuditionActivation: restrictedActivation }),
          idempotencyKey: idempotency("audition-session")
        }),
      `A governed audition session was created for ${role.displayLabel}.`
    );
    if (result !== null) {
      if (restrictedActivation !== undefined) {
        setRestrictedVoiceUseAcknowledged(false);
      }
      setSelectedSessionId(result.session.auditionSessionId);
      await loadWorkspace();
    }
  }

  function restrictedActivationForRole(
    role: AuditionRoleStatus
  ): RestrictedLocalAuditionActivationRequest | null | undefined {
    if (role.governedLocalVoice === undefined || role.governedLocalVoice === null) {
      return undefined;
    }
    const inventory = workspace?.voiceInventory;
    const reason = restrictedVoiceReason.trim();
    if (
      inventory === undefined ||
      inventory.warningText !== GOVERNED_PRIVATE_AUDITION_WARNING ||
      inventory.warningFingerprint !==
        GOVERNED_PRIVATE_AUDITION_WARNING_SHA256
    ) {
      onError({
        code: "RESTRICTED_AUDITION_INVENTORY_UNAVAILABLE",
        message:
          "The exact governed voice inventory and restriction warning are required before creating a real-local session.",
        retryable: true
      });
      return null;
    }
    if (!restrictedVoiceUseAcknowledged || reason.length === 0) {
      onError({
        code: "RESTRICTED_AUDITION_ACKNOWLEDGEMENT_REQUIRED",
        message:
          "Acknowledge the exact private-audition restriction and add a reason before creating a real-local session.",
        retryable: false
      });
      return null;
    }
    return {
      expectedInventoryFingerprint: inventory.inventoryFingerprint,
      expectedWarningFingerprint: inventory.warningFingerprint,
      reason
    };
  }

  async function modelAction(
    item: (typeof models)[number],
    action: "verify" | "activate" | "deactivate" | "repair" | "remove"
  ) {
    await perform(
      `model-${action}-${item.modelPackageId}`,
      () =>
        api.auditions.performModelPackageAction({
          projectId,
          modelPackageId: item.modelPackageId,
          expectedManifestFingerprint: item.manifestFingerprint,
          expectedInstallationRevision:
            item.installation?.installationRevision ?? null,
          action,
          reason:
            action === "remove"
              ? "User requested governed removal of this exact inactive local model package."
              : `User requested the governed ${action} operation.`,
          idempotencyKey: idempotency(`model-${action}`)
        }),
      `The model ${action} operation completed.`,
      loadWorkspace
    );
  }

  async function selectLocalModelPackage(
    item: (typeof models)[number],
    operation: "install" | "repair"
  ) {
    if (loading) return;
    const reason = modelPackageReason.trim();
    if (!restrictedModelUseAcknowledged || reason.length === 0) {
      onError({
        code: "MODEL_PACKAGE_ACKNOWLEDGEMENT_REQUIRED",
        message:
          "Acknowledge restricted local use and add a reason before choosing a model ZIP.",
        retryable: false
      });
      return;
    }
    if (!connected || busy !== null) return;
    setBusy(`model-${operation}-${item.modelPackageId}`);
    onError(null);
    try {
      const result = await api.auditions.selectLocalModelPackage({
        projectId,
        modelPackageId: item.modelPackageId,
        expectedManifestFingerprint: item.manifestFingerprint,
        expectedInstallationRevision:
          item.installation?.installationRevision ?? null,
        operation,
        acknowledgeRestrictedLocalUse: true,
        reason,
        idempotencyKey: idempotency(`model-${operation}`)
      });
      if (!result.ok) {
        onError(result.error);
        return;
      }
      if (result.value === null) {
        onNotice("No local model package was selected.");
        return;
      }
      setRestrictedModelUseAcknowledged(false);
      await loadWorkspace();
      onNotice(`The local model ${operation} completed from verified ZIP bytes.`);
    } finally {
      setBusy(null);
    }
  }

  async function decide(
    review: AuditionReview,
    decision: "approve" | "request_changes" | "reject",
    listeningDisposition?: "undecided"
  ) {
    if (loading) return;
    const rationale = reviewRationales[review.reviewId]?.trim();
    if (rationale === undefined || rationale.length === 0) {
      onError({
        code: "REVIEW_RATIONALE_REQUIRED",
        message: "Add a rationale before recording an audition decision.",
        retryable: false
      });
      return;
    }
    const listeningAttestation = listeningAttestationFor(
      review,
      decision,
      listeningDisposition
    );
    if (listeningAttestation === null) return;
    const result = await perform(
      `review-${review.reviewId}`,
      () =>
        api.auditions.decideReview({
          projectId,
          gateId: review.gateId,
          roleId: review.roleId,
          reviewId: review.reviewId,
          expectedReviewRevision: review.revision,
          expectedEvidenceFingerprint: review.evidence.evidenceFingerprint,
          decision,
          rationale,
          supersedesDecisionId: latestScopeDecisionId(review),
          ...(listeningAttestation === undefined
            ? {}
            : { listeningAttestation }),
          idempotencyKey: idempotency(`review-${review.gateId}`)
        }),
      `${gateLabel(review.gateId)} decision recorded.`
    );
    if (result !== null) {
      const historyWasLoaded =
        reviewHistoryByScope[reviewHistoryKey(review)] !== undefined;
      await loadWorkspace();
      if (historyWasLoaded) await loadReviewHistory(review);
    }
  }

  async function decideExactClip(
    clip: AuditionClip,
    disposition:
      | "acceptable"
      | "unacceptable"
      | "needs_changes"
      | "undecided"
  ) {
    if (loading) return;
    const review = exactClipReviewBinding(clip);
    if (review === null || clip.providerClass !== "real_local") {
      onError({
        code: "EXACT_REAL_AUDITION_REVIEW_REQUIRED",
        message:
          "This clip does not carry an exact governed real-local review binding.",
        retryable: true
      });
      return;
    }
    if (!listenedClipIds.includes(clip.auditionClipId)) {
      onError({
        code: "HUMAN_LISTENING_ATTESTATION_REQUIRED",
        message:
          "Explicitly confirm that you listened to this exact clip before recording its human decision.",
        retryable: false
      });
      return;
    }
    const rationale = reviewRationales[review.reviewId]?.trim();
    if (rationale === undefined || rationale.length === 0) {
      onError({
        code: "REVIEW_RATIONALE_REQUIRED",
        message: "Add a rationale before recording an audition decision.",
        retryable: false
      });
      return;
    }
    const decision =
      disposition === "acceptable"
        ? "approve"
        : disposition === "unacceptable"
          ? "reject"
          : "request_changes";
    const result = await perform(
      `clip-review-${review.reviewId}`,
      () =>
        api.auditions.decideReview({
          projectId,
          gateId: review.gateId,
          roleId: review.roleId,
          reviewId: review.reviewId,
          expectedReviewRevision: review.revision,
          expectedEvidenceFingerprint: review.evidence.evidenceFingerprint,
          decision,
          rationale,
          supersedesDecisionId: latestScopeDecisionId(review),
          listeningAttestation: {
            auditionClipId: clip.auditionClipId,
            auditionClipRevision: clip.revision,
            auditionClipFingerprint: clip.clipFingerprint,
            audioArtifactId: clip.audioArtifact.audioArtifactId,
            audioArtifactSha256: clip.audioArtifact.sha256,
            listened: true,
            disposition
          },
          idempotencyKey: idempotency("exact-clip-review")
        }),
      "The exact-clip listening decision was recorded."
    );
    if (result !== null) await loadWorkspace();
  }

  function latestScopeDecisionId(review: AuditionReview): string | null {
    return (
      workspace?.reviews.find(
        (candidate) =>
          candidate.gateId === review.gateId &&
          candidate.roleId === review.roleId
      )?.latestDecision?.decisionId ?? null
    );
  }

  function exactClipReviewBinding(clip: AuditionClip): AuditionReview | null {
    const review = clip.review as AuditionReview | null | undefined;
    if (
      review == null ||
      review.projectId !== clip.projectId ||
      review.gateId !== "per_role_audition_review" ||
      review.roleId !== clip.roleId ||
      review.evidence.projectId !== clip.projectId ||
      review.evidence.gateId !== "per_role_audition_review" ||
      review.evidence.roleId !== clip.roleId ||
      review.evidence.auditionSessionId !== clip.auditionSessionId ||
      review.evidence.auditionClipId !== clip.auditionClipId ||
      review.evidence.auditionClipRevision !== clip.revision ||
      review.evidence.runtimeProfileFingerprint !==
        clip.runtimeProfileFingerprint ||
      review.evidence.audioQualityFingerprint !==
        clip.audioQuality.qualityFingerprint
    ) {
      return null;
    }
    const decision = review.latestDecision;
    if (
      decision !== null &&
      (decision.reviewId !== review.reviewId ||
        decision.expectedReviewRevision !== review.revision ||
        decision.evidenceFingerprint !== review.evidence.evidenceFingerprint)
    ) {
      return null;
    }
    const attestation = decision?.listeningAttestation;
    if (
      attestation !== undefined &&
      attestation !== null &&
      (attestation.auditionClipId !== clip.auditionClipId ||
        attestation.auditionClipRevision !== clip.revision ||
        attestation.auditionClipFingerprint !== clip.clipFingerprint ||
        attestation.audioArtifactId !== clip.audioArtifact.audioArtifactId ||
        attestation.audioArtifactSha256 !== clip.audioArtifact.sha256)
    ) {
      return null;
    }
    return review;
  }

  function exactReviewClip(review: AuditionReview): AuditionClip | null {
    if (review.evidence.auditionClipId === null) return null;
    return (
      clips.find(
        (clip) =>
          exactClipReviewBinding(clip)?.reviewId === review.reviewId &&
          clip.auditionClipId === review.evidence.auditionClipId &&
          clip.revision === review.evidence.auditionClipRevision
      ) ?? null
    );
  }

  function reviewRequiresListeningAttestation(review: AuditionReview): boolean {
    if (review.gateId !== "per_role_audition_review" || review.roleId === null) {
      return false;
    }
    const clip = exactReviewClip(review);
    // A missing exact clip is unresolved, not fixture evidence. Fail closed
    // until the bounded clip page containing the review evidence is loaded.
    return clip === null || clip.providerClass === "real_local";
  }

  function listeningAttestationFor(
    review: AuditionReview,
    decision: "approve" | "request_changes" | "reject",
    listeningDisposition?: "undecided"
  ): HumanListeningAttestationRequest | null | undefined {
    if (!reviewRequiresListeningAttestation(review)) return undefined;
    const clip = exactReviewClip(review);
    if (clip === null || clip.providerClass !== "real_local") {
      onError({
        code: "EXACT_REAL_AUDITION_CLIP_REQUIRED",
        message:
          "Load the bounded page containing this review's exact clip before recording a human decision.",
        retryable: true
      });
      return null;
    }
    if (!listenedClipIds.includes(clip.auditionClipId)) {
      onError({
        code: "HUMAN_LISTENING_ATTESTATION_REQUIRED",
        message:
          "Explicitly confirm that you listened to this exact clip before recording its human decision.",
        retryable: false
      });
      return null;
    }
    return {
      auditionClipId: clip.auditionClipId,
      auditionClipRevision: clip.revision,
      auditionClipFingerprint: clip.clipFingerprint,
      audioArtifactId: clip.audioArtifact.audioArtifactId,
      audioArtifactSha256: clip.audioArtifact.sha256,
      listened: true,
      disposition:
        listeningDisposition ??
        (decision === "approve"
          ? "acceptable"
          : decision === "request_changes"
            ? "needs_changes"
            : "unacceptable")
    };
  }

  async function loadAudio(clip: AuditionClip) {
    if (loading) return;
    if (
      clip.audioArtifact.availability !== "present" ||
      !clip.audioArtifact.playbackEligible
    ) {
      onError({
        code: "AUDITION_AUDIO_UNAVAILABLE",
        message: "This governed audition artifact is not eligible for playback.",
        retryable: false
      });
      return;
    }
    const result = await perform(
      `audio-${clip.auditionClipId}`,
      () =>
        api.auditions.loadAudio({
          projectId,
          auditionClipId: clip.auditionClipId,
          auditionSessionId: clip.auditionSessionId,
          audioArtifactId: clip.audioArtifact.audioArtifactId,
          expectedClipRevision: clip.revision,
          expectedClipFingerprint: clip.clipFingerprint,
          expectedArtifactSha256: clip.audioArtifact.sha256,
          mediaType: "audio/wav",
          byteSize: clip.audioArtifact.byteSize
        }),
      "The authenticated audition audio is ready."
    );
    if (result === null) return;
    if (typeof URL.createObjectURL !== "function") {
      onError({
        code: "AUDIO_PLAYBACK_UNAVAILABLE",
        message: "This renderer cannot create a private audio object URL.",
        retryable: false
      });
      return;
    }
    const nextUrl = URL.createObjectURL(
      new Blob([result.bytes], { type: result.mediaType })
    );
    setAudioUrl((current) => {
      if (current !== null) URL.revokeObjectURL(current);
      return nextUrl;
    });
    setLoadedClipId(clip.auditionClipId);
  }

  async function clearAuditionCache() {
    if (loading) return;
    const reason = cacheClearReason.trim();
    if (reason.length === 0) {
      onError({
        code: "CACHE_CLEAR_REASON_REQUIRED",
        message: "Add a reason before clearing private audition cache records.",
        retryable: false
      });
      return;
    }
    const result = await perform(
      "clear-audition-cache",
      () =>
        api.auditions.clearCache({
          projectId,
          expectedProjectRevision: Math.max(
            expectedCacheProjectRevision,
            project.project.revision
          ),
          reason,
          idempotencyKey: idempotency("audition-cache-clear")
        }),
      "The governed private audition cache clear completed."
    );
    if (result !== null) {
      stopAudio();
      setAudioUrl(null);
      setLoadedClipId(null);
      setExpectedCacheProjectRevision(result.projectRevision);
      onNotice(
        `Cleared ${result.clearedRecordCount} cache records; ${result.alreadyClearedRecordCount} were already cleared.`
      );
      await loadWorkspace();
    }
  }

  function compare(clipId: string, selected: boolean) {
    setComparisonIds((current) => {
      if (!selected) return current.filter((id) => id !== clipId);
      if (current.includes(clipId) || current.length >= 3) return current;
      return [...current, clipId];
    });
  }

  function stopAudio() {
    const audio = audioRef.current;
    if (audio === null || audioUrl === null) return;
    audio.pause();
    audio.currentTime = 0;
  }

  function seekAudio() {
    const audio = audioRef.current;
    if (audio === null) return;
    audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 5);
  }

  function replayAudio() {
    const audio = audioRef.current;
    if (audio === null) return;
    audio.currentTime = 0;
    void audio.play();
  }

  if (loading && workspace === null) {
    return (
      <section className="auditions-workspace" aria-busy="true">
        <p className="quiet">Loading governed local speech evidence…</p>
      </section>
    );
  }

  if (workspace === null) {
    return (
      <section className="auditions-workspace">
        <h2>Auditions &amp; Pronunciation</h2>
        <p className="quiet">The local audition workspace is unavailable.</p>
      </section>
    );
  }

  const generationRoles = workspace.roles.items;
  const selectedRole =
    selectedSession === null
      ? null
      : generationRoles.find((role) => role.roleId === selectedSession.roleId) ??
        null;
  const selectedRoleActivationReady =
    selectedRole?.governedLocalVoice == null ||
    (restrictedVoiceUseAcknowledged &&
      restrictedVoiceReason.trim().length > 0);
  const rolePageStart = rolePage.previousCursors.length * rolePageSize;
  const currentRolePage = rolePage.previousCursors.length + 1;
  const rolePageCount = Math.max(1, Math.ceil(rolePage.total / rolePageSize));

  return (
    <section
      className="auditions-workspace"
      aria-busy={loading}
      inert={loading}
    >
      <header className="auditions-heading">
        <div>
          <span className="eyebrow">Phase 3B · local speech</span>
          <h2>Auditions &amp; Pronunciation</h2>
          <p>
            Short local previews with exact cast, model, runtime,
            pronunciation, cache, and human-review evidence.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadWorkspace()}
          disabled={!connected || busy !== null || loading}
        >
          Refresh evidence
        </button>
      </header>

      <div className="auditions-summary-grid">
        <EvidencePanel
          title="Prerequisites"
          value={`${workspace.prerequisites.filter((item) => item.current).length}/${workspace.prerequisites.length} current`}
        >
          <ul className="audit-list">
            {workspace.prerequisites.map((item) => (
              <li key={item.prerequisiteId}>
                <StatusBadge state={item.current ? "current" : "blocked"} />
                <span>{humanize(item.prerequisiteId)}</span>
                <small>{item.statusCode}</small>
              </li>
            ))}
          </ul>
        </EvidencePanel>

        <EvidencePanel
          title="Approved cast"
          value={
            workspace.approvedCastSnapshot === null
              ? "Not current"
              : `Revision ${workspace.approvedCastSnapshot.revision}`
          }
        >
          <EvidenceHash
            value={workspace.approvedCastSnapshot?.fingerprint ?? null}
          />
          <p className="quiet">
            A different voice is selected only through governed Phase 3A
            casting corrections.
          </p>
        </EvidencePanel>

        <EvidencePanel
          title="Voice readiness"
          value={
            workspace.voiceReadinessSnapshot?.reviewEligible === true
              ? "Review eligible"
              : "Not ready"
          }
        >
          <p>
            {workspace.voiceReadinessSnapshot === null
              ? "No current readiness snapshot."
              : `${workspace.voiceReadinessSnapshot.approvedRoleCount}/${workspace.voiceReadinessSnapshot.requiredRoleCount} required roles approved.`}
          </p>
          <small>Authorizes later performance direction only.</small>
        </EvidencePanel>
      </div>

      <section className="auditions-section" aria-labelledby="provider-heading">
        <div className="section-title-row">
          <div>
            <span className="eyebrow">Local runtime</span>
            <h3 id="provider-heading">Providers &amp; verified models</h3>
          </div>
          <span className="local-only">Network off during synthesis</span>
        </div>
        <div className="provider-grid">
          {workspace.providers.map((provider) => {
            const health = workspace.runtimeHealth.find(
              (item) => item.providerId === provider.providerId
            );
            return (
              <article className="provider-card" key={provider.providerId}>
                <div className="card-title-row">
                  <strong>{provider.displayName}</strong>
                  <StatusBadge state={health?.status ?? provider.status} />
                </div>
                <p>
                  {provider.providerClass === "deterministic_fixture"
                    ? "Fixture-provider clip · lifecycle evidence only"
                    : "Real local provider · human listening still required"}
                </p>
                <dl className="mini-facts">
                  <dt>Adapter</dt><dd>{provider.adapterVersion}</dd>
                  <dt>License</dt><dd>{provider.licenseIdentifier}</dd>
                  <dt>Export</dt><dd>{provider.productionExportEligible ? "Eligible" : "Not eligible"}</dd>
                  <dt>Runtime</dt><dd>{health?.status ?? "not checked"}</dd>
                </dl>
              </article>
            );
          })}
        </div>

        <div className="runtime-instance-panel" aria-labelledby="runtime-instances-heading">
          <div className="card-title-row">
            <strong id="runtime-instances-heading">Owned provider workers</strong>
            <span>{workspace.runtimeInstances.length} reported</span>
          </div>
          {workspace.runtimeInstances.length === 0 ? (
            <p className="quiet">No provider worker is currently reported.</p>
          ) : (
            <div className="provider-grid">
              {workspace.runtimeInstances.map((instance) => (
                <article className="provider-card" key={instance.runtimeInstanceId}>
                  <div className="card-title-row">
                    <strong>{instance.executableIdentity}</strong>
                    <StatusBadge state={instance.state} />
                  </div>
                  <dl className="mini-facts">
                    <dt>Provider</dt><dd>{instance.providerId}</dd>
                    <dt>Worker PID</dt><dd>{instance.workerPid}</dd>
                    <dt>Parent PID</dt><dd>{instance.parentPid}</dd>
                    <dt>Network policy</dt><dd>Python socket API denied</dd>
                    <dt>External observation</dt>
                    <dd>
                      {instance.observedNetworkRequestCount === null
                        ? "Not externally observed"
                        : `${instance.observedNetworkRequestCount} requests observed`}
                    </dd>
                  </dl>
                  {instance.observedNetworkRequestCount === null ? (
                    <small>Not externally observed means unknown, not zero.</small>
                  ) : null}
                </article>
              ))}
            </div>
          )}
        </div>

        <section
          className="real-local-voice-inventory"
          aria-labelledby="real-local-voices-heading"
          data-testid="real-local-voice-inventory"
        >
          <div className="section-title-row">
            <div>
              <span className="eyebrow">Governed private comparison</span>
              <h3 id="real-local-voices-heading">Real Local Voices</h3>
            </div>
            <span className="production-ineligible">Production ineligible</span>
          </div>
          {workspace.voiceInventory === undefined ? (
            <p className="quiet">
              No governed real-local voice inventory is available. Fixture
              audition behavior remains unchanged.
            </p>
          ) : (
            <>
              <div className="restriction-banner" role="alert">
                <strong>Private local audition only</strong>
                <p>{workspace.voiceInventory.warningText}</p>
                <small>
                  Warning SHA-256: {workspace.voiceInventory.warningFingerprint}
                </small>
              </div>
              <fieldset className="restricted-voice-activation-controls">
                <legend>Restricted local-audition activation</legend>
                <label className="restricted-use-acknowledgement">
                  <input
                    type="checkbox"
                    checked={restrictedVoiceUseAcknowledged}
                    data-testid="restricted-local-audition-acknowledgement"
                    onChange={(event) =>
                      setRestrictedVoiceUseAcknowledged(event.target.checked)
                    }
                  />
                  I acknowledge this exact restricted voice for a bounded private
                  local audition only.
                </label>
                <label>
                  Restricted-audition reason
                  <textarea
                    value={restrictedVoiceReason}
                    maxLength={1_000}
                    onChange={(event) =>
                      setRestrictedVoiceReason(event.target.value)
                    }
                  />
                </label>
                <small>
                  This acknowledgement records review of the displayed
                  restriction. It is not legal clearance, performer consent,
                  production approval, commercial-use approval, or a quality
                  decision.
                </small>
              </fieldset>
              <div className="voice-inventory-grid">
                {workspace.voiceInventory.items.map((voice) => {
                  const model = models.find(
                    (item) =>
                      item.modelPackageId === voice.modelPackageId &&
                      item.manifestFingerprint === voice.modelPackageFingerprint
                  );
                  const health = workspace.runtimeHealth.find(
                    (item) => item.providerId === voice.providerId
                  );
                  const runtimeProfile = workspace.runtimeProfiles.find(
                    (item) =>
                      item.providerIds.includes(voice.providerId) &&
                      item.compatibleModelPackageIds.includes(
                        voice.modelPackageId
                      )
                  );
                  const boundRoles = generationRoles.filter(
                    (role) => role.voiceProfileId === voice.voiceProfileId
                  );
                  const reapprovalRequired = boundRoles.some(
                    (role) => role.sessionEvidence === null
                  );
                  return (
                    <article
                      className="voice-inventory-card"
                      data-testid={`real-local-voice-${voice.inventoryRecordId}`}
                      key={voice.inventoryRecordId}
                    >
                      <div className="card-title-row">
                        <div>
                          <span className="eyebrow">Real Kokoro speech</span>
                          <strong>{voice.neutralDisplayLabel}</strong>
                        </div>
                        <StatusBadge state={voice.activationEligibility} />
                      </div>
                      <div className="eligibility-labels">
                        <span>Private audition: restricted</span>
                        <span>Production export: prohibited</span>
                      </div>
                      <dl className="mini-facts">
                        <dt>Technical status</dt>
                        <dd>{humanize(voice.technicalCompatibility)}</dd>
                        <dt>Provider</dt>
                        <dd>{voice.providerId} @ {voice.providerVersion}</dd>
                        <dt>Runtime health</dt>
                        <dd>{health === undefined ? "Not reported" : humanize(health.status)}</dd>
                        <dt>Runtime profile</dt>
                        <dd>
                          {runtimeProfile === undefined ? "Not reported" : runtimeProfile.runtimeProfileId}
                        </dd>
                        <dt>Runtime profile fingerprint</dt>
                        <dd>
                          {runtimeProfile === undefined ? "Not reported" : <EvidenceHash value={runtimeProfile.profileFingerprint} />}
                        </dd>
                        <dt>Model</dt>
                        <dd>{voice.modelId} @ {voice.modelVersion}</dd>
                        <dt>Package identity</dt>
                        <dd>{voice.modelPackageId}</dd>
                        <dt>Package state</dt>
                        <dd>
                          {model?.installation?.status ?? "not installed"} / {model?.verification?.status ?? "not verified"}
                        </dd>
                        <dt>Package fingerprint</dt>
                        <dd><EvidenceHash value={voice.modelPackageFingerprint} /></dd>
                        <dt>Catalog</dt>
                        <dd>{voice.catalogRevisionId} / <EvidenceHash value={voice.catalogRevisionFingerprint} /></dd>
                        <dt>Voice profile</dt>
                        <dd>{voice.voiceProfileId} @ {voice.voiceProfileVersion}</dd>
                        <dt>Voice profile fingerprint</dt>
                        <dd><EvidenceHash value={voice.voiceProfileFingerprint} /></dd>
                        <dt>Tensor</dt>
                        <dd>
                          {voice.voiceTensor.scalarFormat} [{voice.voiceTensor.shape.join(", ")}] / <EvidenceHash value={voice.voiceTensor.sha256} />
                        </dd>
                        <dt>Rights</dt>
                        <dd>{humanize(voice.rights.rightsState)}</dd>
                        <dt>Rights record</dt>
                        <dd>
                          {voice.rights.rightsRecordId} r{voice.rights.rightsRecordRevision} / <EvidenceHash value={voice.rights.rightsRecordFingerprint} />
                        </dd>
                        <dt>Consent</dt>
                        <dd>{humanize(voice.rights.consentStatus)}</dd>
                        <dt>Commercial use</dt>
                        <dd>{humanize(voice.rights.commercialUseClassification)}</dd>
                        <dt>Redistribution</dt>
                        <dd>{humanize(voice.rights.redistributionClassification)}</dd>
                        <dt>Language</dt>
                        <dd>{voice.locale ?? voice.language}</dd>
                        <dt>Provenance</dt>
                        <dd>
                          {humanize(voice.provenance.origin)} / {voice.provenance.producerId} @ {voice.provenance.producerVersion}
                        </dd>
                      </dl>
                      {voice.providerDeclaredPresentationCategory === null ? null : (
                        <small>
                          Provider-declared presentation category: {voice.providerDeclaredPresentationCategory}. Not independently verified.
                        </small>
                      )}
                      <div className="voice-evidence-lists">
                        <strong>Unresolved evidence</strong>
                        {voice.unresolvedEvidenceCodes.length === 0 ? (
                          <span className="quiet">None reported</span>
                        ) : (
                          <ul>{voice.unresolvedEvidenceCodes.map((code) => <li key={code}><code>{code}</code></li>)}</ul>
                        )}
                        <strong>Known limitations</strong>
                        {voice.knownLimitations.length === 0 ? (
                          <span className="quiet">None reported</span>
                        ) : (
                          <ul>{voice.knownLimitations.map((item) => <li key={item}>{item}</li>)}</ul>
                        )}
                      </div>
                      <p className={reapprovalRequired ? "phase3a-reapproval-required" : "quiet"}>
                        {boundRoles.length === 0
                          ? "Not assigned on this bounded role page. Use the Casting workspace and its immutable correction path to select this voice."
                          : reapprovalRequired
                            ? "Phase 3A reapproval required. Return to Casting and explicitly reapprove the applicable Narrator or Character Casting Review and Complete Cast Review after the assignment correction."
                            : `Current Phase 3A binding available for ${boundRoles.map((role) => role.displayLabel).join(", ")}.`}
                      </p>
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </section>

        <div className="model-table-wrap">
          <fieldset className="model-package-install-controls">
            <legend>Local model ZIP install &amp; repair</legend>
            <label className="restricted-use-acknowledgement">
              <input
                type="checkbox"
                checked={restrictedModelUseAcknowledged}
                onChange={(event) =>
                  setRestrictedModelUseAcknowledged(event.target.checked)
                }
              />
              I acknowledge these model and voice bytes may have restricted or
              unknown usage rights. They are for local auditions only and are
              not approved for redistribution or production export.
            </label>
            <label htmlFor="model-package-install-reason">
              Install or repair reason
              <textarea
                id="model-package-install-reason"
                value={modelPackageReason}
                maxLength={1_000}
                onChange={(event) => setModelPackageReason(event.target.value)}
              />
            </label>
            <small>
              Electron selects one ZIP with the native file dialog; its private
              path is never exposed to this renderer.
            </small>
          </fieldset>
          <table className="auditions-table">
            <caption>Installed model packages and verification</caption>
            <thead><tr><th>Model</th><th>Install</th><th>Verify</th><th>Fingerprint</th><th>Actions</th></tr></thead>
            <tbody>
              {models.map((item) => (
                <tr key={item.modelPackageId}>
                  <td>
                    <strong>{item.modelId}</strong>
                    <small>{item.modelVersion} · {item.licenseIdentifier} · {humanize(item.commercialUseClassification)}</small>
                  </td>
                  <td>{item.installation?.status ?? "not installed"}</td>
                  <td>{item.verification?.status ?? "not verified"}</td>
                  <td><EvidenceHash value={item.manifestFingerprint} /></td>
                  <td className="button-cluster">
                    {item.installation === null || item.installation.status === "removed" ? (
                      <button
                        type="button"
                        onClick={() => void selectLocalModelPackage(item, "install")}
                        disabled={
                          !connected ||
                          busy !== null ||
                          !restrictedModelUseAcknowledged ||
                          modelPackageReason.trim().length === 0 ||
                          item.sourceClassification === "repository_fixture"
                        }
                      >
                        Choose ZIP &amp; install
                      </button>
                    ) : null}
                    {item.installation !== null && item.sourceClassification !== "repository_fixture" ? (
                      <button
                        type="button"
                        onClick={() => void selectLocalModelPackage(item, "repair")}
                        disabled={
                          !connected ||
                          busy !== null ||
                          !restrictedModelUseAcknowledged ||
                          modelPackageReason.trim().length === 0 ||
                          item.installation.active
                        }
                      >
                        Choose ZIP &amp; repair
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => void modelAction(item, "verify")}
                      disabled={
                        !connected ||
                        busy !== null ||
                        item.installation?.active === true ||
                        item.installation?.status === "removed"
                      }
                    >
                      Verify
                    </button>
                    <button type="button" onClick={() => void modelAction(item, "activate")} disabled={!connected || busy !== null || item.installation?.active === true || item.installation?.status === "removed" || item.verification?.status !== "verified"}>Activate</button>
                    {item.installation?.active === true ? (
                      <button type="button" onClick={() => void modelAction(item, "deactivate")} disabled={!connected || busy !== null}>Deactivate</button>
                    ) : null}
                    {item.installation !== null &&
                    item.installation.active === false &&
                    item.installation.status !== "removed" ? (
                      <button
                        type="button"
                        onClick={() => void modelAction(item, "remove")}
                        disabled={!connected || busy !== null}
                      >
                        Remove inactive model
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <CollectionPageControl
            label="model packages"
            page={modelPage}
            loading={busy === "model-page"}
            disabled={!connected || busy !== null}
            onPrevious={() => void loadModelPage("previous")}
            onNext={() => void loadModelPage("next")}
            onRestart={() => void loadModelPage("restart")}
          />
        </div>
      </section>

      <section className="auditions-section" aria-labelledby="generation-heading">
        <div className="section-title-row">
          <div>
            <span className="eyebrow">Bounded comparison</span>
            <h3 id="generation-heading">Role audition controls</h3>
          </div>
          <span>
            {rolePage.total === 0
              ? "No required-role records"
              : `${rolePageStart + 1}-${Math.min(rolePageStart + generationRoles.length, rolePage.total)} of ${rolePage.total} required-role records`}
          </span>
        </div>
        <div className="role-preview-grid">
          {generationRoles.map((role) => (
            <RoleAuditionCard
              key={role.roleId}
              role={role}
              disabled={!connected || busy !== null}
              busy={
                busy === `generate-${role.roleId}` ||
                busy === `create-session-${role.roleId}`
              }
              activationReady={
                role.governedLocalVoice == null ||
                (restrictedVoiceUseAcknowledged &&
                  restrictedVoiceReason.trim().length > 0)
              }
              onCreateSession={() => void createSession(role)}
              onGenerate={() => void generate(role)}
            />
          ))}
        </div>
        <nav className="bounded-pager" aria-label="Required audition roles">
          <button
            type="button"
            onClick={() => void loadRolePage("previous")}
            disabled={rolePage.previousCursors.length === 0 || busy !== null}
          >
            Previous roles
          </button>
          <span>Page {currentRolePage} of {rolePageCount}</span>
          <button
            type="button"
            onClick={() => void loadRolePage("restart")}
            disabled={rolePage.previousCursors.length === 0 || busy !== null}
          >
            Restart roles
          </button>
          <button
            type="button"
            onClick={() => void loadRolePage("next")}
            disabled={rolePage.nextCursor === null || busy !== null}
          >
            Next roles
          </button>
        </nav>
      </section>

      <div className="auditions-two-column">
        <section className="auditions-section" aria-labelledby="script-heading">
          <span className="eyebrow">Exact text · private project storage</span>
          <h3 id="script-heading">Sessions, scripts &amp; normalization</h3>
          <label>
            Audition session
            <select value={selectedSession?.auditionSessionId ?? ""} onChange={(event) => setSelectedSessionId(event.target.value)}>
              {sessions.map((session) => (
                <option value={session.auditionSessionId} key={session.auditionSessionId}>
                  {session.roleId} · {session.state} · {session.clipCount} clips
                </option>
              ))}
            </select>
          </label>
          <CollectionPageControl
            label="audition sessions"
            page={sessionPage}
            loading={busy === "session-page"}
            disabled={!connected || busy !== null}
            onPrevious={() => void loadSessionPage("previous")}
            onNext={() => void loadSessionPage("next")}
            onRestart={() => void loadSessionPage("restart")}
          />
          <div className="script-preview">
            <strong>Repository-owned synthetic script</strong>
            <p>{syntheticPreviewText}</p>
          </div>
          <div className="button-cluster">
            <button type="button" onClick={() => void previewNormalization()} disabled={selectedSession === null || busy !== null}>Preview normalization</button>
            <button type="button" onClick={() => void saveSyntheticScript()} disabled={selectedSession === null || busy !== null}>Save script</button>
          </div>
          <div className="pronunciation-test-control">
            <label>
              Pronunciation-test text
              <textarea
                value={pronunciationTestText}
                maxLength={SPEECH_AUDITION_LIMITS.maximumScriptCodePoints}
                onChange={(event) => setPronunciationTestText(event.target.value)}
              />
            </label>
            <button
              type="button"
              onClick={() => void savePronunciationTest()}
              disabled={
                selectedSession === null ||
                busy !== null ||
                pronunciationTestText.trim().length === 0 ||
                !selectedRoleActivationReady
              }
            >
              Create pronunciation test
            </button>
            <small>
              Creates a current governed session for the selected role and saves
              a <code>pronunciation_test</code> script there. Generate audition
              will synthesize that exact current script.
            </small>
          </div>
          {normalizationPlan !== null ? (
            <div className="normalization-plan" data-testid="normalization-plan">
              <div className="card-title-row"><strong>Inspect normalization plan</strong><EvidenceHash value={normalizationPlan.planFingerprint} /></div>
              <div
                className={`normalization-review-state ${
                  normalizationReviewRequired ? "review-required" : "review-clean"
                }`}
                data-testid="normalization-review-state"
                role="status"
              >
                <strong>
                  {normalizationReviewRequired
                    ? "Review required"
                    : "Review clean"}
                </strong>
                <span>
                  {normalizationReviewRequired
                    ? "Unsupported provider characters or unresolved normalization decisions prevent this plan from being treated as review-clean."
                    : "No unsupported provider characters or unresolved normalization decisions were reported."}
                </span>
              </div>
              <p>{normalizationPlan.transformations.length} visible transformations · {normalizationPlan.appliedPronunciationEntryIds.length} pronunciation entries applied.</p>
              {normalizationPlan.transformations.length === 0 ? (
                <p className="quiet">No text transformations were required.</p>
              ) : (
                <ul className="normalization-transformations">
                  {normalizationPlan.transformations.map((item) => (
                    <li key={item.transformationId}>
                      <strong>{humanize(item.kind)}</strong> · {item.reasonCode}
                      <dl className="mini-facts">
                        <dt>Original</dt><dd><code>{item.originalText ?? `SHA-256 ${item.originalTextSha256}`}</code></dd>
                        <dt>Replacement</dt><dd><code>{item.replacementText ?? `SHA-256 ${item.replacementTextSha256}`}</code></dd>
                        <dt>Source span</dt><dd>{item.sourceSpan.start}–{item.sourceSpan.end}</dd>
                        <dt>Destination span</dt><dd>{item.destinationSpan.start}–{item.destinationSpan.end}</dd>
                        <dt>Provider required</dt><dd>{item.requiredByProvider ? "Yes" : "No"}</dd>
                        <dt>Human approval</dt><dd>{item.humanApprovalRequired ? (item.approved ? "Approved" : "Required") : "Not required"}</dd>
                      </dl>
                    </li>
                  ))}
                </ul>
              )}
              <div className="normalization-detail-grid">
                <div>
                  <strong>Applied pronunciation entries</strong>
                  {normalizationPlan.appliedPronunciationEntryIds.length === 0 ? (
                    <p className="quiet">None</p>
                  ) : (
                    <ul>{normalizationPlan.appliedPronunciationEntryIds.map((entryId) => <li key={entryId}><code>{entryId}</code></li>)}</ul>
                  )}
                </div>
                <div>
                  <strong>Unsupported characters</strong>
                  {normalizationPlan.unsupportedCharacterCodePoints.length === 0 ? (
                    <p className="quiet">None</p>
                  ) : (
                    <ul>{normalizationPlan.unsupportedCharacterCodePoints.map((codePoint) => <li key={codePoint}><code>{codePoint}</code></li>)}</ul>
                  )}
                </div>
                <div>
                  <strong>Plan warnings</strong>
                  {normalizationPlan.warnings.length === 0 ? (
                    <p className="quiet">None</p>
                  ) : (
                    <ul>{normalizationPlan.warnings.map((warning) => <li key={warning}><code>{warning}</code></li>)}</ul>
                  )}
                </div>
              </div>
            </div>
          ) : null}
          {inspectedJobId !== null ? (
            <div
              className="audition-job-inspector"
              data-testid="audition-job-inspector"
              aria-live="polite"
            >
              <div className="card-title-row">
                <strong>Job {shortId(inspectedJobId)}</strong>
                <button
                  type="button"
                  onClick={() => {
                    setJobPollingSuppressed(false);
                    setJobRefreshToken((current) => current + 1);
                  }}
                  disabled={!connected || jobLoading || jobControlBusy !== null}
                >
                  Refresh job
                </button>
              </div>
              {jobStatus === null ? (
                <p className="quiet">
                  {jobLoading
                    ? "Loading current job evidenceâ€¦"
                    : "Current job evidence is unavailable."}
                </p>
              ) : (
                <>
                  <div className="job-title">
                    <strong>{humanize(jobStatus.stage || jobStatus.type)}</strong>
                    <span className={`job-state state-${jobStatus.state}`}>
                      {humanize(jobStatus.state)}
                    </span>
                  </div>
                  <progress
                    max={1}
                    value={jobStatus.progress}
                    aria-label="Audition job progress"
                  />
                  <div className="job-meta">
                    <span>{Math.round(jobStatus.progress * 100)}%</span>
                    <span>Attempt {jobStatus.attempt}</span>
                  </div>
                  {jobStatus.error === undefined ? null : (
                    <p className="job-error">
                      {jobStatus.error.code}: {jobStatus.error.message}
                    </p>
                  )}
                  <div className="job-controls">
                    {(jobStatus.state === "queued" ||
                      jobStatus.state === "running") ? (
                      <button
                        type="button"
                        onClick={() => void controlAuditionJob("cancel")}
                        disabled={!connected || jobControlBusy !== null}
                      >
                        Cancel
                      </button>
                    ) : null}
                    {jobStatus.state === "failed" ? (
                      <button
                        type="button"
                        onClick={() => void controlAuditionJob("retry")}
                        disabled={!connected || jobControlBusy !== null}
                      >
                        Retry
                      </button>
                    ) : null}
                    {(jobStatus.state === "interrupted" ||
                      jobStatus.state === "paused") ? (
                      <button
                        type="button"
                        onClick={() => void controlAuditionJob("resume")}
                        disabled={!connected || jobControlBusy !== null}
                      >
                        Resume
                      </button>
                    ) : null}
                  </div>
                </>
              )}
              {jobPollExhausted ? (
                <small>
                  Automatic polling stopped at its fixed limit. Refresh job to
                  start another bounded observation window.
                </small>
              ) : null}
            </div>
          ) : null}
        </section>

        <section className="auditions-section" aria-labelledby="pronunciation-heading">
          <span className="eyebrow">Append-only governance</span>
          <h3 id="pronunciation-heading">Pronunciation dictionary</h3>
          <p className="quiet">
            {workspace.currentDictionary === null
              ? "No current dictionary."
              : `Revision ${workspace.currentDictionary.revision} · ${workspace.currentDictionary.currentEntryCount} current entries`}
          </p>
          <form className="pronunciation-form" onSubmit={(event) => void appendPronunciation(event)}>
            {supersedesEntryId === null ? null : (
              <div className="append-only-notice" role="status">
                Appending a revision of {shortId(supersedesEntryId)}. The
                prior entry remains immutable.
              </div>
            )}
            <label>Written form<input value={draft.writtenForm} maxLength={120} onChange={(event) => setDraft((current) => ({ ...current, writtenForm: event.target.value }))} /></label>
            <label>Pronunciation<input value={draft.pronunciation} maxLength={256} onChange={(event) => setDraft((current) => ({ ...current, pronunciation: event.target.value }))} /></label>
            <label>Reason<textarea value={draft.reason} maxLength={1000} onChange={(event) => setDraft((current) => ({ ...current, reason: event.target.value }))} /></label>
            <div className="button-cluster">
              <button type="submit" disabled={!connected || busy !== null || workspace.currentDictionary === null || draft.writtenForm.trim().length === 0 || draft.pronunciation.trim().length === 0 || draft.reason.trim().length === 0}>
                {supersedesEntryId === null ? "Add for review" : "Append revision"}
              </button>
              {supersedesEntryId === null ? null : (
                <button type="button" onClick={cancelPronunciationRevision} disabled={busy !== null}>
                  Cancel revision
                </button>
              )}
            </div>
          </form>
          <ul className="pronunciation-list">
            {pronunciations.map((entry) => (
              <li key={`${entry.entryId}-${entry.revision}`}>
                <div><strong>{entry.writtenForm}</strong><span aria-hidden="true"> → </span><span>{entry.pronunciation}</span></div>
                <small>
                  {humanize(entry.scope)} · {humanize(entry.verificationState)}
                  {" · "}revision {entry.revision} · human {entry.actor.actorId}
                </small>
                <small>
                  {entry.supersedesEntryId === null
                    ? "Original append-only entry"
                    : `Supersedes ${shortId(entry.supersedesEntryId)}`}
                </small>
                <label>
                  Pronunciation decision rationale
                  <textarea
                    value={pronunciationDecisionRationales[entry.entryId] ?? ""}
                    maxLength={4000}
                    onChange={(event) =>
                      setPronunciationDecisionRationales((current) => ({
                        ...current,
                        [entry.entryId]: event.target.value
                      }))
                    }
                  />
                </label>
                <div className="button-cluster">
                  <button
                    type="button"
                    onClick={() => void decidePronunciation(entry, "approve")}
                    disabled={busy !== null || entry.verificationState === "superseded"}
                  >
                    Approve entry
                  </button>
                  <button
                    type="button"
                    onClick={() => void decidePronunciation(entry, "request_changes")}
                    disabled={busy !== null || entry.verificationState === "superseded"}
                  >
                    Request changes
                  </button>
                  <button
                    type="button"
                    onClick={() => void decidePronunciation(entry, "reject")}
                    disabled={busy !== null || entry.verificationState === "superseded"}
                  >
                    Reject entry
                  </button>
                  <button
                    type="button"
                    onClick={() => startPronunciationRevision(entry)}
                    disabled={busy !== null || entry.supersededByEntryId !== null}
                  >
                    Revise append-only entry
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <CollectionPageControl
            label="pronunciation entries"
            page={pronunciationPage}
            loading={busy === "pronunciation-page"}
            disabled={!connected || busy !== null}
            onPrevious={() => void loadPronunciationPage("previous")}
            onNext={() => void loadPronunciationPage("next")}
            onRestart={() => void loadPronunciationPage("restart")}
          />
        </section>
      </div>

      <section className="auditions-section" aria-labelledby="clips-heading">
        <div className="section-title-row"><div><span className="eyebrow">Authenticated binary playback</span><h3 id="clips-heading">Clip history &amp; cache</h3></div><span>{comparisonIds.length}/3 compared</span></div>
        <details className="cache-maintenance">
          <summary>Private cache maintenance</summary>
          <p className="quiet">
            Clearing removes only owned audition cache artifacts. Governed
            metadata remains inspectable, and clips can be regenerated.
          </p>
          <label>
            Cache-clear reason
            <textarea
              value={cacheClearReason}
              maxLength={1000}
              onChange={(event) => setCacheClearReason(event.target.value)}
            />
          </label>
          <button
            type="button"
            onClick={() => void clearAuditionCache()}
            disabled={!connected || busy !== null || cacheClearReason.trim().length === 0}
          >
            Clear private audition cache
          </button>
        </details>
        <div className="clip-list">
          {clips.map((clip) => {
            const exactClipReview = exactClipReviewBinding(clip);
            const persistedDecision = exactClipReview?.latestDecision ?? null;
            const persistedListening =
              persistedDecision?.listeningAttestation ?? null;
            const clipListeningReady = listenedClipIds.includes(
              clip.auditionClipId
            );
            return (
              <article
                className={loadedClipId === clip.auditionClipId ? "clip-card active" : "clip-card"}
                data-testid={`audition-clip-${clip.auditionClipId}`}
                key={clip.auditionClipId}
              >
              <div className="card-title-row"><strong>{clip.roleId}</strong><StatusBadge state={clip.state} /></div>
              <p>{clip.providerClass === "deterministic_fixture" ? "Fixture-provider clip" : "Real-provider clip"} · {humanize(clip.cacheStatus)}</p>
              <small>Exact clip <code>{clip.auditionClipId}</code></small>
              <dl className="mini-facts"><dt>Duration</dt><dd>{(clip.audioArtifact.durationMilliseconds / 1000).toFixed(2)} s</dd><dt>Format</dt><dd>{clip.audioArtifact.sampleRateHz / 1000} kHz · {clip.audioArtifact.channels === 1 ? "mono" : `${clip.audioArtifact.channels} ch`} · PCM16</dd><dt>Bytes</dt><dd>{clip.audioArtifact.byteSize.toLocaleString()}</dd><dt>Availability</dt><dd>{humanize(clip.audioArtifact.availability)} · {clip.audioArtifact.playbackEligible ? "Playback eligible" : "Playback unavailable"}</dd><dt>QC</dt><dd>{clip.audioQuality.blockingFindingCodes.length === 0 ? "Machine integrity passed" : `${clip.audioQuality.blockingFindingCodes.length} blockers`}</dd></dl>
              <div className="clip-qc-findings">
                <strong>QC blockers</strong>
                {clip.audioQuality.blockingFindingCodes.length === 0 ? <span className="quiet">None</span> : <ul>{clip.audioQuality.blockingFindingCodes.map((code) => <li key={code}><code>{code}</code></li>)}</ul>}
                <strong>QC warnings</strong>
                {clip.audioQuality.warningCodes.length === 0 ? <span className="quiet">None</span> : <ul>{clip.audioQuality.warningCodes.map((code) => <li key={code}><code>{code}</code></li>)}</ul>}
              </div>
              <div className="button-cluster">
                {clip.audioArtifact.availability === "present" && clip.audioArtifact.playbackEligible ? (
                  <button type="button" onClick={() => void loadAudio(clip)} disabled={busy !== null}>Load audio</button>
                ) : (
                  <span className="quiet" role="status">Playback unavailable</span>
                )}
                <label className="compare-check">
                  <input
                    type="checkbox"
                    checked={comparisonIds.includes(clip.auditionClipId)}
                    disabled={!comparisonIds.includes(clip.auditionClipId) && comparisonIds.length >= 3}
                    onChange={(event) => compare(clip.auditionClipId, event.target.checked)}
                  />
                  Compare
                </label>
                {clip.providerClass === "real_local" && exactClipReview !== null ? (
                  <label className="listened-exact-clip-check">
                    <input
                      type="checkbox"
                      checked={listenedClipIds.includes(clip.auditionClipId)}
                      data-testid={`listened-exact-clip-${clip.auditionClipId}`}
                      onChange={(event) =>
                        setListenedClipIds((current) =>
                          event.target.checked
                            ? [...new Set([...current, clip.auditionClipId])]
                            : current.filter(
                                (clipId) => clipId !== clip.auditionClipId
                              )
                        )
                      }
                    />
                    I listened to this exact clip
                  </label>
                ) : (
                  <small className="quiet">
                    {clip.providerClass === "deterministic_fixture"
                      ? "Fixture lifecycle clip / listening attestation omitted."
                      : "Exact governed review binding unavailable; human controls are disabled."}
                  </small>
                )}
              </div>
              {clip.providerClass === "real_local" && exactClipReview !== null ? (
                <div className="pronunciation-test-control exact-clip-listening-decision">
                  {persistedListening === null ? (
                    <small className="quiet">
                      No persisted human listening decision for this exact clip.
                    </small>
                  ) : (
                    <div
                      data-testid={`persisted-listening-decision-${clip.auditionClipId}`}
                    >
                      <strong>
                        Recorded listening: {humanize(persistedListening.disposition)}
                      </strong>
                      <p>{persistedDecision?.rationale}</p>
                      <small>
                        {persistedListening.actor.actorId} · {persistedListening.recordedAt} · immutable
                      </small>
                    </div>
                  )}
                  <label>
                    Exact-clip decision rationale
                    <textarea
                      value={reviewRationales[exactClipReview.reviewId] ?? ""}
                      maxLength={4000}
                      onChange={(event) =>
                        setReviewRationales((current) => ({
                          ...current,
                          [exactClipReview.reviewId]: event.target.value
                        }))
                      }
                    />
                  </label>
                  <div className="button-cluster">
                    <button
                      type="button"
                      onClick={() => void decideExactClip(clip, "acceptable")}
                      disabled={
                        busy !== null ||
                        !clipListeningReady ||
                        persistedListening !== null ||
                        exactClipReview.blockerCodes.length > 0
                      }
                    >
                      Record acceptable
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        void decideExactClip(clip, "needs_changes")
                      }
                      disabled={
                        busy !== null ||
                        !clipListeningReady ||
                        persistedListening !== null
                      }
                    >
                      Record needs changes
                    </button>
                    <button
                      type="button"
                      onClick={() => void decideExactClip(clip, "undecided")}
                      disabled={
                        busy !== null ||
                        !clipListeningReady ||
                        persistedListening !== null
                      }
                    >
                      Record undecided
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        void decideExactClip(clip, "unacceptable")
                      }
                      disabled={
                        busy !== null ||
                        !clipListeningReady ||
                        persistedListening !== null
                      }
                    >
                      Record unacceptable
                    </button>
                  </div>
                </div>
              ) : null}
              </article>
            );
          })}
        </div>
        <CollectionPageControl
          label="audition clips"
          page={clipPage}
          loading={busy === "clip-page"}
          disabled={!connected || busy !== null}
          onPrevious={() => void loadClipPage("previous")}
          onNext={() => void loadClipPage("next")}
          onRestart={() => void loadClipPage("restart")}
        />
        <div className="audio-player-card">
          <audio ref={audioRef} controls preload="none" src={audioUrl ?? undefined} aria-label="Audition clip player" />
          <div className="button-cluster"><button type="button" onClick={() => audioRef.current?.pause()} disabled={audioUrl === null}>Pause</button><button type="button" onClick={stopAudio} disabled={audioUrl === null}>Stop</button><button type="button" onClick={seekAudio} disabled={audioUrl === null}>Seek +5s</button><button type="button" onClick={replayAudio} disabled={audioUrl === null}>Replay</button></div>
          <small>No autoplay. Audio is fetched by owned artifact ID through Electron main and exposed only as a revocable Blob URL.</small>
        </div>
        {comparedClips.length > 0 ? (
          <table className="auditions-table"><caption>Bounded audition alternatives</caption><thead><tr><th>Clip</th><th>Provider</th><th>Cache</th><th>Peak</th><th>Human state</th></tr></thead><tbody>{comparedClips.map((clip) => <tr key={clip.auditionClipId}><td>{shortId(clip.auditionClipId)}</td><td>{clip.providerClass}</td><td>{clip.cacheStatus}</td><td>{clip.audioQuality.peakDbfs.toFixed(1)} dBFS</td><td>{clip.state}</td></tr>)}</tbody></table>
        ) : null}
      </section>

      <section className="auditions-section" aria-labelledby="reviews-heading">
        <span className="eyebrow">Human authority</span>
        <h3 id="reviews-heading">Five audition and readiness reviews</h3>
        <div className="review-grid">
          {workspace.reviews.map((review) => {
            const history = reviewHistoryByScope[reviewHistoryKey(review)];
            const historyBusyKey = `review-history-${reviewHistoryKey(review)}`;
            const exactClip = exactReviewClip(review);
            const listeningRequired = reviewRequiresListeningAttestation(review);
            const listeningReady =
              !listeningRequired ||
              (exactClip !== null &&
                exactClip.providerClass === "real_local" &&
                listenedClipIds.includes(exactClip.auditionClipId));
            return (
              <article className="review-card" key={review.reviewId}>
                <div className="card-title-row"><strong>{gateLabel(review.gateId)}{review.roleId === null ? "" : ` · ${review.roleId}`}</strong><StatusBadge state={review.state} /></div>
                <p>{review.blockerCodes.length} blockers · {review.warningCodes.length} warnings</p>
                <label>Decision rationale<textarea value={reviewRationales[review.reviewId] ?? ""} maxLength={4000} onChange={(event) => setReviewRationales((current) => ({ ...current, [review.reviewId]: event.target.value }))} /></label>
                {listeningRequired ? (
                  <div
                    className={listeningReady ? "listening-ready" : "listening-required"}
                    role="status"
                  >
                    {exactClip === null
                      ? "Exact review clip is not on this bounded page. Load it before deciding whether listening evidence applies."
                      : listeningReady
                        ? "Exact-clip listening attestation is ready."
                        : "Listen to the exact real-local clip and check its explicit listened box before deciding."}
                  </div>
                ) : (
                  <small className="quiet">
                    Fixture or aggregate review / listening attestation omitted.
                  </small>
                )}
                <div className="button-cluster">
                  <button
                    type="button"
                    onClick={() => void decide(review, "approve")}
                    disabled={busy !== null || review.blockerCodes.length > 0 || !listeningReady}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => void decide(review, "request_changes")}
                    disabled={busy !== null || !listeningReady}
                  >
                    Request changes
                  </button>
                  {listeningRequired ? (
                    <button
                      type="button"
                      onClick={() =>
                        void decide(review, "request_changes", "undecided")
                      }
                      disabled={busy !== null || !listeningReady}
                    >
                      Record undecided
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => void decide(review, "reject")}
                    disabled={busy !== null || !listeningReady}
                  >
                    Reject
                  </button>
                </div>
                <small>
                  {review.latestDecision === null
                    ? "No human decision"
                    : `${humanize(review.latestDecision.decision)}${
                        review.latestDecision.listeningAttestation == null
                          ? ""
                          : ` · Listening ${humanize(review.latestDecision.listeningAttestation.disposition)}`
                      } · immutable history`}
                </small>
                {review.latestDecision?.listeningAttestation?.disposition ===
                "undecided" ? (
                  <small className="quiet">
                    Undecided is not approval; voice readiness remains blocked.
                  </small>
                ) : null}
                <div className="review-history-controls">
                  {history === undefined ? (
                    <button
                      type="button"
                      onClick={() => void loadReviewHistory(review)}
                      disabled={!connected || busy !== null}
                    >
                      Load decision history
                    </button>
                  ) : (
                    <>
                      <span>Page {history.pageNumber.toLocaleString()} · showing {history.loaded.toLocaleString()} of {history.total.toLocaleString()} immutable decisions</span>
                      {history.pageNumber > 1 ? (
                        <button
                          type="button"
                          onClick={() => void loadReviewHistory(review)}
                          disabled={!connected || busy !== null}
                        >
                          Restart decision history
                        </button>
                      ) : null}
                      {history.nextCursor !== null ? (
                        <button
                          type="button"
                          onClick={() => void loadReviewHistory(review, history.nextCursor ?? undefined)}
                          disabled={!connected || busy !== null}
                        >
                          {busy === historyBusyKey ? "Loading…" : "Next decision page"}
                        </button>
                      ) : null}
                    </>
                  )}
                </div>
                {history === undefined ? null : history.items.length === 0 ? (
                  <small>No immutable decisions exist for this exact gate scope.</small>
                ) : (
                  <ol className="review-decision-history" aria-label={`${gateLabel(review.gateId)} decision history`}>
                    {history.items.map((decision) => (
                      <li key={decision.decisionId}>
                        <div className="card-title-row">
                          <strong>{humanize(decision.decision)}</strong>
                          <StatusBadge state={decision.actor.classification} />
                        </div>
                        <p>{decision.rationale}</p>
                        <dl className="mini-facts">
                          <dt>Actor</dt><dd>{decision.actor.actorId}</dd>
                          <dt>Decided</dt><dd>{decision.decidedAt}</dd>
                          <dt>Review</dt><dd>{decision.reviewId} · r{decision.expectedReviewRevision}</dd>
                          <dt>Supersedes</dt><dd>{decision.supersedesDecisionId ?? "none"}</dd>
                          <dt>Evidence</dt><dd className="evidence-hash">{decision.evidenceFingerprint}</dd>
                          <dt>Listening</dt>
                          <dd>
                            {decision.listeningAttestation == null
                              ? "not applicable / omitted"
                              : `${humanize(decision.listeningAttestation.disposition)} · exact clip ${shortId(decision.listeningAttestation.auditionClipId)}`}
                          </dd>
                          <dt>Record</dt><dd>immutable</dd>
                        </dl>
                      </li>
                    ))}
                  </ol>
                )}
              </article>
            );
          })}
        </div>
      </section>
    </section>
  );
}

function CollectionPageControl({
  label,
  page,
  loading,
  disabled,
  onPrevious,
  onNext,
  onRestart
}: {
  readonly label: string;
  readonly page: CollectionPageState;
  readonly loading: boolean;
  readonly disabled: boolean;
  readonly onPrevious: () => void;
  readonly onNext: () => void;
  readonly onRestart: () => void;
}) {
  const pageNumber = page.previousCursors.length + 1;
  const atCursorLimit =
    page.previousCursors.length >= maximumCollectionCursorDepth;
  return (
    <div className="bounded-pager" aria-label={`${humanize(label)} pagination`}>
      <span>
        Page {pageNumber.toLocaleString()} · showing {page.shown.toLocaleString()} of{" "}
        {page.total.toLocaleString()} {label}
      </span>
      <button
        type="button"
        onClick={onPrevious}
        disabled={disabled || page.previousCursors.length === 0}
      >
        Previous {label}
      </button>
      <button
        type="button"
        onClick={onRestart}
        disabled={disabled || page.previousCursors.length === 0}
      >
        Restart {label}
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={disabled || page.nextCursor === null || atCursorLimit}
      >
        {loading ? "Loading…" : `Next ${label}`}
      </button>
      {atCursorLimit && page.nextCursor !== null ? (
        <small>
          The validated collection exceeds the bounded renderer cursor history.
        </small>
      ) : null}
    </div>
  );
}

function EvidencePanel({ title, value, children }: { readonly title: string; readonly value: string; readonly children: ReactNode }) {
  return <article className="evidence-panel"><span className="eyebrow">{title}</span><strong className="panel-value">{value}</strong>{children}</article>;
}

function RoleAuditionCard({
  role,
  disabled,
  busy,
  activationReady,
  onCreateSession,
  onGenerate
}: {
  readonly role: AuditionRoleStatus;
  readonly disabled: boolean;
  readonly busy: boolean;
  readonly activationReady: boolean;
  readonly onCreateSession: () => void;
  readonly onGenerate: () => void;
}) {
  const needsSession = role.latestSessionId === null;
  const canCreateSession = role.sessionEvidence !== null;
  const canGenerate = role.generationRequest !== null;
  return (
    <article className="role-preview-card">
      <div className="card-title-row">
        <div>
          <span className="eyebrow">{role.roleType}</span>
          <strong>{role.displayLabel}</strong>
        </div>
        <StatusBadge state={role.reviewState} />
      </div>
      <dl className="mini-facts">
        <dt>Provider class</dt>
        <dd>
          {role.governedLocalVoice == null
            ? "Deterministic fixture lifecycle"
            : "Real local voice / private audition only"}
        </dd>
        <dt>Voice</dt><dd>{role.voiceDisplayLabel}</dd>
        <dt>Voice profile</dt>
        <dd>
          {role.voiceRuntimeBinding === null
            ? shortId(role.voiceProfileId)
            : `${shortId(role.voiceRuntimeBinding.voiceProfileId)} @ ${role.voiceRuntimeBinding.voiceProfileVersion}`}
        </dd>
        <dt>Provider voice</dt>
        <dd>
          {role.voiceRuntimeBinding === null
            ? "unavailable"
            : `${role.voiceRuntimeBinding.providerId} / ${role.voiceRuntimeBinding.providerVoiceId}`}
        </dd>
        <dt>Model</dt>
        <dd>
          {role.voiceRuntimeBinding === null
            ? "unavailable"
            : `${role.voiceRuntimeBinding.modelId} @ ${role.voiceRuntimeBinding.modelVersion}`}
        </dd>
        <dt>Runtime binding</dt>
        <dd>
          {role.voiceRuntimeBinding === null
            ? `${humanize(role.runtimeBindingStatus)} · ${humanize(role.runtimeBindingReasonCode ?? "unknown")}`
            : `${shortId(role.voiceRuntimeBinding.bindingId)} · ${role.voiceRuntimeBinding.bindingFingerprint.slice(0, 12)}…`}
        </dd>
        <dt>Rights</dt><dd>{role.rightsState}</dd>
        <dt>Assignment</dt><dd>r{role.assignmentRevision}</dd>
        <dt>Latest clip</dt><dd>{role.latestClipId === null ? "none" : shortId(role.latestClipId)}</dd>
        <dt>Production export</dt><dd>Not eligible</dd>
      </dl>
      {role.governedLocalVoice == null ? null : (
        <div className="role-real-local-status">
          <strong>{role.governedLocalVoice.neutralDisplayLabel}</strong>
          <span>{humanize(role.governedLocalVoice.activationEligibility)}</span>
          {role.sessionEvidence === null ? (
            <small>
              Phase 3A reapproval required or another current prerequisite is
              blocked. Return to Casting and explicitly reapprove the affected
              role gate and Complete Cast Review after the immutable assignment
              correction.
            </small>
          ) : (
            <small>
              Creating the session requires the exact restricted-audition
              acknowledgement above.
            </small>
          )}
        </div>
      )}
      {needsSession ? (
        <button
          type="button"
          onClick={onCreateSession}
          disabled={disabled || !canCreateSession || !activationReady}
        >
          {busy ? "Creating…" : "Create audition session"}
        </button>
      ) : (
        <button
          type="button"
          onClick={onGenerate}
          disabled={disabled || !canGenerate}
        >
          {busy
            ? "Queuing…"
            : canGenerate
              ? "Generate audition"
              : "Save a script to generate"}
        </button>
      )}
    </article>
  );
}

function StatusBadge({ state }: { readonly state: string }) {
  return <span className={`status-badge status-${state.replaceAll("_", "-")}`}>{humanize(state)}</span>;
}

function EvidenceHash({ value }: { readonly value: string | null }) {
  return <code className="evidence-hash" title={value ?? undefined}>{value === null ? "No current fingerprint" : `${value.slice(0, 10)}…${value.slice(-6)}`}</code>;
}

function gateLabel(gateId: string): string {
  switch (gateId) {
    case "per_role_audition_review": return "Per-Role Audition Review";
    case "narrator_audition_review": return "Narrator Audition Review";
    case "character_audition_review": return "Character Audition Review";
    case "pronunciation_review": return "Pronunciation Review";
    case "voice_readiness_review": return "Voice Readiness Review";
    default: return humanize(gateId);
  }
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/gu, (letter) => letter.toUpperCase());
}

function shortId(value: string): string {
  return value.length <= 16 ? value : `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function idempotency(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

function reviewHistoryKey(
  review: Pick<AuditionReview, "gateId" | "roleId">
): string {
  return `${review.gateId}:${review.roleId ?? "aggregate"}`;
}

async function sha256Text(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value)
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
