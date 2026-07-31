import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import type {
  Job,
  ProjectDetail
} from "@cinematic-story-studio/contracts/api";

import {
  ANALYSIS_GATE_IDS,
  WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
  WHOLE_BOOK_ANALYSIS_PROFILE_ID,
  WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
  type AnalysisCollection,
  type AnalysisCorrection,
  type AnalysisCorrectionCategory,
  type AnalysisCorrectionRequestSelection,
  type AnalysisEntity,
  type AnalysisEvidenceExcerpt,
  type AnalysisGateDecisionAction,
  type AnalysisGateId,
  type AnalysisReview,
  type AnalysisRun,
  type AnalysisWarning,
  type DialogueSpeakerState,
  type HumanEffectiveBoundary,
  type HumanEffectiveRegistry
} from "../shared/analysis-api";
import type {
  CinematicStoryDesktopApi,
  DesktopError
} from "../shared/desktop-api";
import "./analysis.css";

type CorrectableAnalysisEntity = Extract<
  AnalysisEntity,
  { readonly entityId: string }
>;

type AnalysisSection =
  | "overview"
  | "structure"
  | "characters"
  | "dialogue"
  | "whole-book"
  | "corrections";

const wholeBookCollections = [
  "pov-segments",
  "locations",
  "timeline-events",
  "temporal-constraints",
  "relationships",
  "emotional-states",
  "dramatic-intents",
  "continuity-findings"
] as const satisfies readonly AnalysisCollection[];

const gateOrder = ANALYSIS_GATE_IDS;
const activeRunStatuses = new Set<AnalysisRun["status"]>([
  "queued",
  "running"
]);
// The service quantizes confidence to millionths with round(). One full
// millionth below the class boundary keeps the inclusive API filter from
// admitting the first entity in the next class.
const LOW_CONFIDENCE_MAX = 0.749999;
const MEDIUM_CONFIDENCE_MAX = 0.849999;
const CORRECTION_PAGE_SIZE = 200;
const MAX_CORRECTIONS = 10_000;

interface PageState {
  readonly items: readonly AnalysisEntity[];
  readonly nextCursor?: string;
  readonly total: number;
  readonly snapshotId: string;
  readonly loading: boolean;
}

interface RunRequestContext {
  readonly generation: number;
  readonly key: string;
}

interface CorrectionDraft {
  readonly category: AnalysisCorrectionCategory;
  readonly primary: string;
  readonly secondary: string;
  readonly tertiary: string;
  readonly operation: string;
  readonly parentEntityId: string;
  readonly ordinal: string;
  readonly boundaryKind: string;
  readonly sourceCharacterId: string;
  readonly targetCharacterId: string;
  readonly scopeKind: string;
  readonly scopeFirstSceneId: string;
  readonly scopeLastSceneId: string;
  readonly supersedeLatest: boolean;
  readonly reason: string;
}

export interface AnalysisWorkspaceProps {
  readonly project: ProjectDetail;
  readonly api: CinematicStoryDesktopApi;
  readonly connected: boolean;
  readonly onNotice: (message: string) => void;
  readonly onError: (error: DesktopError) => void;
  readonly onRunChange?: (run: AnalysisRun, job?: Job) => void;
  readonly onProjectRefresh?: () => Promise<void> | void;
}

export function AnalysisWorkspace({
  project,
  api,
  connected,
  onNotice,
  onError,
  onRunChange,
  onProjectRefresh
}: AnalysisWorkspaceProps) {
  const projectId = project.project.projectId;
  const [runs, setRuns] = useState<readonly AnalysisRun[]>([]);
  const [run, setRun] = useState<AnalysisRun | null>(
    project.currentAnalysisRun
  );
  const [reviews, setReviews] = useState<readonly AnalysisReview[]>(
    project.analysisGateReviews
  );
  const [corrections, setCorrections] = useState<
    readonly AnalysisCorrection[]
  >([]);
  const [pages, setPages] = useState<
    Partial<Record<AnalysisCollection, PageState>>
  >({});
  const [section, setSection] = useState<AnalysisSection>("overview");
  const [wholeBookCollection, setWholeBookCollection] =
    useState<(typeof wholeBookCollections)[number]>("pov-segments");
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(
    null
  );
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [dialogueConfidenceMax, setDialogueConfidenceMax] = useState<
    number | null
  >(null);
  const [dialogueReviewOnly, setDialogueReviewOnly] = useState(false);
  const [dialogueSpeakerState, setDialogueSpeakerState] =
    useState<DialogueSpeakerState | "all">("all");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [correctionTarget, setCorrectionTarget] =
    useState<CorrectableAnalysisEntity | null>(null);
  const [correctionDraft, setCorrectionDraft] =
    useState<CorrectionDraft | null>(null);
  const [reviewRationales, setReviewRationales] = useState<
    Readonly<Record<AnalysisGateId, string>>
  >(emptyGateText());
  const [acknowledgeWarnings, setAcknowledgeWarnings] = useState<
    Readonly<Record<AnalysisGateId, boolean>>
  >(emptyGateBoolean());
  const contextActive = useRef(true);
  const selectedRunRef = useRef<AnalysisRun | null>(
    project.currentAnalysisRun
  );
  const runGeneration = useRef(0);
  const runListRequestGeneration = useRef(0);
  const reviewRequestGeneration = useRef(0);
  const correctionRequestGeneration = useRef(0);
  const correctionMutationGeneration = useRef(0);
  const gateMutationGeneration = useRef(0);
  const pollRequestGeneration = useRef(0);
  const jobRequestGeneration = useRef(0);
  const collectionRequestGenerations = useRef(
    new Map<AnalysisCollection, number>()
  );
  const filterGeneration = useRef(0);

  useEffect(() => {
    contextActive.current = true;
    return () => {
      contextActive.current = false;
    };
  }, []);

  const currentImportReview = useMemo(
    () =>
      [...project.importReviews]
        .sort(compareRevisionTime)
        .at(-1) ?? null,
    [project.importReviews]
  );
  const approvedImport = useMemo(() => {
    if (
      !project.analysisAllowed ||
      project.story === null ||
      currentImportReview === null ||
      currentImportReview.state !== "approved" ||
      currentImportReview.latestDecision?.decision !== "approved" ||
      currentImportReview.candidateStoryId !== project.story.storyId ||
      currentImportReview.candidateStoryRevision !==
        project.story.revision ||
      !project.story.sourceDocumentIds.includes(
        currentImportReview.sourceDocumentId
      )
    ) {
      return null;
    }
    const extraction = project.extractions.find(
      (item) =>
        item.extractionId === currentImportReview.extractionId &&
        item.sourceDocumentId === currentImportReview.sourceDocumentId
    );
    return extraction === undefined ? null : currentImportReview;
  }, [
    currentImportReview,
    project.analysisAllowed,
    project.extractions,
    project.story
  ]);
  const approvedExtraction =
    approvedImport === null
      ? null
      : (project.extractions.find(
          (item) =>
            item.extractionId === approvedImport.extractionId &&
            item.sourceDocumentId === approvedImport.sourceDocumentId
        ) ?? null);

  const invalidateCollectionRequests = useCallback(() => {
    collectionRequestGenerations.current.clear();
  }, []);

  const applyRun = useCallback(
    (
      nextRun: AnalysisRun,
      options: {
        readonly propagate?: boolean;
        readonly job?: Job;
        readonly clearDerived?: boolean;
      } = {}
    ) => {
      const contextChanged =
        runRequestKey(selectedRunRef.current) !== runRequestKey(nextRun);
      if (contextChanged) {
        runGeneration.current += 1;
        reviewRequestGeneration.current += 1;
        correctionRequestGeneration.current += 1;
        correctionMutationGeneration.current += 1;
        gateMutationGeneration.current += 1;
        pollRequestGeneration.current += 1;
        jobRequestGeneration.current += 1;
        invalidateCollectionRequests();
      }
      selectedRunRef.current = nextRun;
      setRun(nextRun);
      setRuns((current) => [
        nextRun,
        ...current.filter(
          (candidate) => candidate.runId !== nextRun.runId
        )
      ]);
      if (contextChanged || options.clearDerived === true) {
        setPages({});
        setReviews([]);
        setCorrections([]);
        setBusyAction(null);
        setCorrectionTarget(null);
        setCorrectionDraft(null);
      }
      if (options.propagate === true) {
        onRunChange?.(nextRun, options.job);
      }
    },
    [invalidateCollectionRequests, onRunChange]
  );

  const captureRunContext = useCallback(
    (selectedRun: AnalysisRun): RunRequestContext | null => {
      const key = runRequestKey(selectedRun);
      return runRequestKey(selectedRunRef.current) === key
        ? { generation: runGeneration.current, key }
        : null;
    },
    []
  );

  const runContextIsCurrent = useCallback(
    (context: RunRequestContext): boolean =>
      contextActive.current &&
      runGeneration.current === context.generation &&
      runRequestKey(selectedRunRef.current) === context.key,
    []
  );

  const loadReviews = useCallback(
    async (selectedRun: AnalysisRun) => {
      if (selectedRun.currentSnapshot === null) {
        if (
          runRequestKey(selectedRunRef.current) ===
          runRequestKey(selectedRun)
        ) {
          setReviews([]);
        }
        return;
      }
      const context = captureRunContext(selectedRun);
      if (context === null) {
        return;
      }
      const requestGeneration = ++reviewRequestGeneration.current;
      const result = await api.analysis.listReviews({
        projectId,
        runId: selectedRun.runId,
        expectedSourceDocumentId: selectedRun.sourceDocumentId,
        expectedExtractionId: selectedRun.extractionId,
        expectedExtractionRevision: selectedRun.extractionRevision,
        expectedStoryId: selectedRun.storyId,
        expectedProfileId: selectedRun.profile.profileId,
        expectedProfileFingerprint: selectedRun.profile.fingerprint,
        expectedRunFingerprint: selectedRun.runFingerprint,
        expectedSnapshotId: selectedRun.currentSnapshot.snapshotId,
        expectedSnapshotRevision: selectedRun.currentSnapshot.revision,
        expectedSnapshotFingerprint:
          selectedRun.currentSnapshot.snapshotFingerprint
      });
      if (
        requestGeneration !== reviewRequestGeneration.current ||
        !runContextIsCurrent(context)
      ) {
        return;
      }
      if (result.ok) {
        setReviews(orderReviews(result.value.items));
      } else {
        onError(result.error);
      }
    },
    [
      api,
      captureRunContext,
      onError,
      projectId,
      runContextIsCurrent
    ]
  );

  const loadCorrections = useCallback(
    async (selectedRun: AnalysisRun) => {
      const context = captureRunContext(selectedRun);
      if (context === null) {
        return;
      }
      const requestGeneration = ++correctionRequestGeneration.current;
      const items: AnalysisCorrection[] = [];
      const seenCursors = new Set<string>();
      let cursor: string | undefined;
      for (;;) {
        const result = await api.analysis.listCorrections({
          projectId,
          runId: selectedRun.runId,
          limit: CORRECTION_PAGE_SIZE,
          ...(cursor === undefined ? {} : { cursor })
        });
        if (
          requestGeneration !== correctionRequestGeneration.current ||
          !runContextIsCurrent(context)
        ) {
          return;
        }
        if (!result.ok) {
          onError(result.error);
          return;
        }
        items.push(...result.value.items);
        if (items.length > MAX_CORRECTIONS) {
          setCorrections([]);
          onError({
            code: "CORRECTION_HISTORY_LIMIT",
            message:
              "The correction history exceeded the bounded desktop review limit.",
            retryable: false
          });
          return;
        }
        const nextCursor = result.value.nextCursor;
        if (nextCursor === undefined) {
          setCorrections(items);
          return;
        }
        if (seenCursors.has(nextCursor)) {
          setCorrections([]);
          onError({
            code: "CORRECTION_CURSOR_INVALID",
            message:
              "The correction history returned a repeated page cursor.",
            retryable: false
          });
          return;
        }
        seenCursors.add(nextCursor);
        cursor = nextCursor;
      }
    },
    [
      api,
      captureRunContext,
      onError,
      projectId,
      runContextIsCurrent
    ]
  );

  const loadRuns = useCallback(async () => {
    if (!connected) {
      return;
    }
    const requestGeneration = ++runListRequestGeneration.current;
    const selectedGeneration = runGeneration.current;
    const result = await api.analysis.listRuns({ projectId, limit: 50 });
    if (
      !contextActive.current ||
      requestGeneration !== runListRequestGeneration.current ||
      selectedGeneration !== runGeneration.current
    ) {
      return;
    }
    if (!result.ok) {
      onError(result.error);
      return;
    }
    const ordered = [...result.value.runs].sort((left, right) =>
      right.createdAt.localeCompare(left.createdAt)
    );
    setRuns(ordered);
    const currentSelection = selectedRunRef.current;
    const selected =
      (currentSelection === null
        ? ordered[0]
        : ordered.find(
            (candidate) => candidate.runId === currentSelection.runId
          )) ??
      ordered[0] ??
      null;
    if (selected === null) {
      selectedRunRef.current = null;
      runGeneration.current += 1;
      setRun(null);
      setPages({});
      setReviews([]);
      setCorrections([]);
      return;
    }
    applyRun(selected, { clearDerived: true });
    if (selected !== null) {
      await Promise.all([loadReviews(selected), loadCorrections(selected)]);
    }
  }, [
    api,
    applyRun,
    connected,
    loadCorrections,
    loadReviews,
    onError,
    projectId
  ]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      runGeneration.current += 1;
      runListRequestGeneration.current += 1;
      reviewRequestGeneration.current += 1;
      correctionRequestGeneration.current += 1;
      correctionMutationGeneration.current += 1;
      gateMutationGeneration.current += 1;
      pollRequestGeneration.current += 1;
      jobRequestGeneration.current += 1;
      invalidateCollectionRequests();
      selectedRunRef.current = project.currentAnalysisRun;
      setRuns([]);
      setRun(project.currentAnalysisRun);
      setReviews(project.analysisGateReviews);
      setCorrections([]);
      setPages({});
      setSelectedChapterId(null);
      setSelectedSceneId(null);
      if (connected) {
        void loadRuns();
      }
    }, 0);
    return () => {
      window.clearTimeout(timer);
    };
    // The project identity, rather than the currently selected run, owns this
    // reset. `loadRuns` intentionally retains the selected run on refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, projectId]);

  useEffect(() => {
    if (
      run === null ||
      !connected ||
      !activeRunStatuses.has(run.status)
    ) {
      return;
    }
    const context = captureRunContext(run);
    if (context === null) {
      return;
    }
    const runId = run.runId;
    let inFlight = false;
    const timer = window.setInterval(() => {
      if (inFlight) {
        return;
      }
      inFlight = true;
      const requestGeneration = ++pollRequestGeneration.current;
      void api.analysis
        .getRun({ projectId, runId })
        .then(async (result) => {
          if (
            requestGeneration !== pollRequestGeneration.current ||
            !runContextIsCurrent(context) ||
            !result.ok
          ) {
            return;
          }
          const previous = selectedRunRef.current;
          if (previous === null || previous.runId !== runId) {
            return;
          }
          const propagate =
            project.currentAnalysisRun?.runId === result.value.run.runId;
          applyRun(result.value.run, { propagate });
          if (
            previous.status !== result.value.run.status ||
            previous.currentSnapshot?.snapshotId !==
              result.value.run.currentSnapshot?.snapshotId
          ) {
            setPages({});
            await Promise.all([
              loadReviews(result.value.run),
              loadCorrections(result.value.run)
            ]);
          }
        })
        .finally(() => {
          inFlight = false;
        });
    }, 1_250);
    return () => {
      window.clearInterval(timer);
    };
  }, [
    api,
    applyRun,
    captureRunContext,
    connected,
    loadCorrections,
    loadReviews,
    projectId,
    project.currentAnalysisRun?.runId,
    run,
    runContextIsCurrent
  ]);

  const loadCollection = useCallback(
    async (
      collection: AnalysisCollection,
      options: { readonly reset?: boolean } = {}
    ) => {
      const selectedRun = selectedRunRef.current;
      if (
        selectedRun === null ||
        selectedRun.currentSnapshot === null ||
        !connected
      ) {
        return;
      }
      const context = captureRunContext(selectedRun);
      if (context === null) {
        return;
      }
      const current = pages[collection];
      if (current?.loading === true) {
        return;
      }
      const reset = options.reset === true;
      const cursor = reset ? undefined : current?.nextCursor;
      if (!reset && current !== undefined && current.nextCursor === undefined) {
        return;
      }
      const requestGeneration =
        (collectionRequestGenerations.current.get(collection) ?? 0) + 1;
      collectionRequestGenerations.current.set(
        collection,
        requestGeneration
      );
      const requestFilterGeneration = filterGeneration.current;
      const snapshotId = selectedRun.currentSnapshot.snapshotId;
      setPages((value) => ({
        ...value,
        [collection]: {
          items: reset ? [] : (current?.items ?? []),
          total: current?.total ?? 0,
          snapshotId,
          nextCursor: cursor,
          loading: true
        }
      }));
      const result = await api.analysis.listEntities({
        projectId,
        runId: selectedRun.runId,
        expectedSnapshotId: snapshotId,
        collection,
        limit: 50,
        ...(cursor === undefined ? {} : { cursor }),
        ...(collection === "agent-executions" ||
        dialogueConfidenceMax === null
          ? {}
          : { confidenceMax: dialogueConfidenceMax }),
        ...(collection !== "agent-executions" && dialogueReviewOnly
          ? { requiresReview: true }
          : {}),
        ...(collection !== "dialogue-lines" ||
        dialogueSpeakerState === "all"
          ? {}
          : { speakerState: dialogueSpeakerState })
      });
      if (
        !runContextIsCurrent(context) ||
        requestGeneration !==
          collectionRequestGenerations.current.get(collection) ||
        requestFilterGeneration !== filterGeneration.current
      ) {
        return;
      }
      if (!result.ok) {
        setPages((value) => ({
          ...value,
          [collection]: {
            ...(value[collection] ?? {
              items: [],
              total: 0,
              snapshotId
            }),
            loading: false
          }
        }));
        onError(result.error);
        return;
      }
      if (
        result.value.runId !== selectedRun.runId ||
        result.value.snapshotId !== snapshotId
      ) {
        onError({
          code: "ANALYSIS_CONTEXT_MISMATCH",
          message:
            "The analysis page did not belong to the selected run snapshot.",
          retryable: false
        });
        setPages((value) => withoutPage(value, collection));
        return;
      }
      setPages((value) => {
        if (
          requestGeneration !==
            collectionRequestGenerations.current.get(collection) ||
          !runContextIsCurrent(context)
        ) {
          return value;
        }
        const prior = reset ? [] : (value[collection]?.items ?? []);
        return {
          ...value,
          [collection]: {
            items: [
              ...prior,
              ...(result.value.items as readonly AnalysisEntity[])
            ],
            total: result.value.total,
            snapshotId: result.value.snapshotId,
            nextCursor: result.value.nextCursor,
            loading: false
          }
        };
      });
    },
    [
      api,
      captureRunContext,
      connected,
      dialogueConfidenceMax,
      dialogueReviewOnly,
      dialogueSpeakerState,
      onError,
      pages,
      projectId,
      runContextIsCurrent
    ]
  );

  useEffect(() => {
    if (run?.currentSnapshot === null || run === null) {
      return;
    }
    const timer = window.setTimeout(() => {
      const needed = sectionCollections(section, wholeBookCollection);
      for (const collection of needed) {
        if (pages[collection] === undefined) {
          void loadCollection(collection, { reset: true });
        }
      }
    }, 0);
    return () => {
      window.clearTimeout(timer);
    };
  }, [
    loadCollection,
    pages,
    run,
    section,
    wholeBookCollection
  ]);

  const createRun = async () => {
    if (
      approvedImport === null ||
      approvedExtraction === null ||
      project.story === null ||
      !project.analysisAllowed ||
      !connected
    ) {
      return;
    }
    const requestGeneration = runGeneration.current;
    setBusyAction("create-run");
    const result = await api.analysis.createRun({
      projectId,
      expectedExtractionId: approvedExtraction.extractionId,
      expectedExtractionRevision: approvedExtraction.revision,
      expectedReviewId: approvedImport.reviewId,
      expectedReviewRevision: approvedImport.revision,
      expectedEvidenceFingerprint: approvedImport.evidenceFingerprint,
      expectedProfileFingerprint:
        WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT,
      profile: {
        profileId: WHOLE_BOOK_ANALYSIS_PROFILE_ID,
        semanticVersion: WHOLE_BOOK_ANALYSIS_PROFILE_VERSION,
        fingerprint: WHOLE_BOOK_ANALYSIS_PROFILE_FINGERPRINT
      },
      idempotencyKey: crypto.randomUUID()
    });
    if (
      !contextActive.current ||
      requestGeneration !== runGeneration.current
    ) {
      return;
    }
    setBusyAction(null);
    if (!result.ok) {
      onError(result.error);
      return;
    }
    applyRun(result.value.run, {
      propagate: true,
      job: result.value.job,
      clearDerived: true
    });
    onNotice("Whole-book story intelligence started.");
  };

  const controlJob = async (
    action: "cancel" | "retry" | "resume"
  ) => {
    const selectedRun = selectedRunRef.current;
    if (selectedRun === null) {
      return;
    }
    const context = captureRunContext(selectedRun);
    if (context === null) {
      return;
    }
    const requestGeneration = ++jobRequestGeneration.current;
    setBusyAction(action);
    const result = await api.jobs[action](selectedRun.jobId);
    if (
      requestGeneration !== jobRequestGeneration.current ||
      !runContextIsCurrent(context)
    ) {
      return;
    }
    setBusyAction(null);
    if (!result.ok) {
      onError(result.error);
      return;
    }
    onNotice(
      action === "cancel"
        ? "Cancellation requested."
        : action === "retry"
          ? "Analysis retry requested."
          : "Interrupted analysis resumed."
    );
    window.setTimeout(() => {
      if (
        requestGeneration !== jobRequestGeneration.current ||
        !runContextIsCurrent(context)
      ) {
        return;
      }
      const refreshGeneration = ++jobRequestGeneration.current;
      void api.analysis
        .getRun({ projectId, runId: selectedRun.runId })
        .then((refresh) => {
          if (
            refreshGeneration === jobRequestGeneration.current &&
            runContextIsCurrent(context) &&
            refresh.ok
          ) {
            applyRun(refresh.value.run, {
              propagate:
                project.currentAnalysisRun?.runId ===
                refresh.value.run.runId
            });
          }
        });
    }, 250);
  };

  const chooseRun = async (runId: string) => {
    const selected = runs.find((candidate) => candidate.runId === runId);
    if (selected === undefined) {
      return;
    }
    applyRun(selected, { clearDerived: true });
    await Promise.all([loadReviews(selected), loadCorrections(selected)]);
  };

  const openCorrection = (entity: AnalysisEntity) => {
    const collection = entityCollection(entity);
    if (
      collection === "agent-executions" ||
      !isCorrectableEntity(entity)
    ) {
      return;
    }
    const category = correctionCategories(collection)[0];
    if (category === undefined) {
      return;
    }
    const prior = latestEntityCorrection(corrections, entity.entityId);
    setCorrectionTarget(entity);
    setCorrectionDraft({
      ...defaultCorrectionDraft(entity, category, ""),
      supersedeLatest: prior !== undefined
    });
  };

  const saveCorrection = async () => {
    const selectedRun = selectedRunRef.current;
    if (
      selectedRun === null ||
      correctionTarget === null ||
      correctionDraft === null ||
      correctionDraft.reason.trim().length === 0
    ) {
      return;
    }
    const context = captureRunContext(selectedRun);
    if (context === null) {
      return;
    }
    const collection = entityCollection(correctionTarget);
    if (collection === "agent-executions") {
      return;
    }
    let selection: AnalysisCorrectionRequestSelection;
    try {
      selection = correctionSelection(
        correctionTarget,
        correctionDraft,
        selectedRun.storyId
      );
    } catch {
      onError({
        code: "CORRECTION_VALUE_INVALID",
        message: "The correction value is invalid.",
        retryable: false
      });
      return;
    }
    const priorCorrection = latestEntityCorrection(
      corrections,
      correctionTarget.entityId
    );
    const requestGeneration = ++correctionMutationGeneration.current;
    setBusyAction(`correction-${correctionTarget.entityId}`);
    const result = await api.analysis.appendCorrection({
      projectId,
      runId: selectedRun.runId,
      ...selection,
      targetEntityId: correctionTarget.entityId,
      expectedTargetRevision: correctionTarget.effectiveRevision,
      expectedRunFingerprint: selectedRun.runFingerprint,
      previousValueFingerprint:
        correctionTarget.effectiveValueFingerprint,
      reason: correctionDraft.reason.trim(),
      ...(priorCorrection === undefined
        ? {}
        : { supersedesCorrectionId: priorCorrection.correctionId }),
      idempotencyKey: crypto.randomUUID()
    });
    if (
      requestGeneration !== correctionMutationGeneration.current ||
      !runContextIsCurrent(context)
    ) {
      return;
    }
    if (!result.ok) {
      setBusyAction(null);
      onError(result.error);
      return;
    }
    applyRun(result.value.run, {
      propagate:
        project.currentAnalysisRun?.runId === result.value.run.runId
    });
    await onProjectRefresh?.();
    setReviews(orderReviews(result.value.reviews));
    setCorrections((current) => [
      result.value.correction,
      ...current.filter(
        (item) => item.correctionId !== result.value.correction.correctionId
      )
    ]);
    setPages((current) => {
      const next = { ...current };
      delete next[collection];
      return next;
    });
    setCorrectionTarget(null);
    setCorrectionDraft(null);
    await loadCorrections(result.value.run);
    setBusyAction(null);
    onNotice(
      `Human correction saved; ${result.value.invalidatedGateIds.length} review gate${
        result.value.invalidatedGateIds.length === 1 ? "" : "s"
      } invalidated.`
    );
  };

  const decideGate = async (
    review: AnalysisReview,
    decision: AnalysisGateDecisionAction
  ) => {
    const selectedRun = selectedRunRef.current;
    if (selectedRun === null) {
      return;
    }
    const context = captureRunContext(selectedRun);
    if (context === null) {
      return;
    }
    const rationale = reviewRationales[review.gateId].trim();
    if (rationale.length === 0) {
      return;
    }
    const warningIds = acknowledgeWarnings[review.gateId]
      ? [...review.acknowledgedWarningIds, ...review.openWarningIds]
      : [...review.acknowledgedWarningIds];
    const acknowledgedWarningIds = [...new Set(warningIds)];
    const requestGeneration = ++gateMutationGeneration.current;
    setBusyAction(`review-${review.gateId}-${decision}`);
    const result = await api.analysis.decideReview({
      projectId,
      runId: selectedRun.runId,
      gateId: review.gateId,
      decision,
      expectedRevision: review.revision,
      expectedArtifactFingerprint: review.artifactFingerprint,
      expectedEvidenceFingerprint: review.evidenceFingerprint,
      acknowledgedWarningIds,
      rationale,
      idempotencyKey: crypto.randomUUID()
    });
    if (
      requestGeneration !== gateMutationGeneration.current ||
      !runContextIsCurrent(context)
    ) {
      return;
    }
    if (!result.ok) {
      setBusyAction(null);
      onError(result.error);
      return;
    }
    applyRun(result.value.run, {
      propagate:
        project.currentAnalysisRun?.runId === result.value.run.runId
    });
    await onProjectRefresh?.();
    setReviews((current) =>
      orderReviews([
        result.value.review,
        ...current.filter((item) => item.gateId !== review.gateId)
      ])
    );
    setReviewRationales((current) => ({
      ...current,
      [review.gateId]: ""
    }));
    setAcknowledgeWarnings((current) => ({
      ...current,
      [review.gateId]: false
    }));
    await loadReviews(result.value.run);
    setBusyAction(null);
    onNotice(
      `${gateTitle(review.gateId)} recorded as ${decisionTitle(decision)}.`
    );
  };

  const characters = pageItems(pages, "characters");
  const chapters = pageItems(pages, "chapters").filter(
    isEffectivelyIncluded
  );
  const scenes = pageItems(pages, "scenes").filter(
    isEffectivelyIncluded
  );
  const beats = pageItems(pages, "beats");
  const effectiveChapterId =
    selectedChapterId ??
    (chapters[0] === undefined ? null : entityPrimaryId(chapters[0]));

  const visibleScenes = scenes.filter(
    (entity) =>
      effectiveChapterId === null ||
      recordValue(entity, "chapterId") === effectiveChapterId
  );
  const effectiveSceneId =
    selectedSceneId ??
    (visibleScenes[0] === undefined
      ? null
      : entityPrimaryId(visibleScenes[0]));

  const visibleBeats = beats.filter(
    (entity) =>
      effectiveSceneId === null ||
      recordValue(entity, "sceneId") === effectiveSceneId
  );

  return (
    <main className="analysis-workspace">
      <header className="analysis-heading">
        <div>
          <span className="eyebrow">Phase 2 / governed local analysis</span>
          <h2>Story intelligence</h2>
          <p>
            Inspect bounded, source-grounded claims. Machine outputs,
            effective human corrections, warnings, and immutable review
            evidence remain distinct.
          </p>
        </div>
        <div className="analysis-heading-actions">
          {runs.length > 0 && (
            <label>
              Analysis run
              <select
                aria-label="Analysis run"
                value={run?.runId ?? ""}
                onChange={(event) => {
                  void chooseRun(event.target.value);
                }}
              >
                {runs.map((candidate) => (
                  <option key={candidate.runId} value={candidate.runId}>
                    {shortId(candidate.runId)} / {sentenceCase(candidate.status)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            className="primary"
            disabled={
              !connected ||
              approvedImport === null ||
              approvedExtraction === null ||
              project.story === null ||
              busyAction !== null
            }
            onClick={() => {
              void createRun();
            }}
          >
            {busyAction === "create-run"
              ? "Starting..."
              : run === null
                ? "Analyze whole book"
                : "Rerun analysis"}
          </button>
        </div>
      </header>

      {approvedImport === null || approvedExtraction === null ? (
        <section className="analysis-empty">
          <span className="eyebrow">Import Review is a separate gate</span>
          <h3>Approve the current extraction before analysis</h3>
          <p>
            Whole-book intelligence binds to one approved extraction, story
            revision, review revision, evidence fingerprint, and profile.
          </p>
        </section>
      ) : run === null ? (
        <section className="analysis-empty">
          <span className="eyebrow">Ready</span>
          <h3>No whole-book analysis run yet</h3>
          <p>
            Start the deterministic local pipeline to build the first
            immutable snapshot.
          </p>
        </section>
      ) : (
        <>
          <RunStatus
            run={run}
            busyAction={busyAction}
            onControl={(action) => {
              void controlJob(action);
            }}
          />
          <RunIdentity run={run} />
          <ReviewGateStrip
            run={run}
            reviews={reviews}
            rationales={reviewRationales}
            acknowledgements={acknowledgeWarnings}
            busyAction={busyAction}
            onRationale={(gateId, value) => {
              setReviewRationales((current) => ({
                ...current,
                [gateId]: value
              }));
            }}
            onAcknowledge={(gateId, value) => {
              setAcknowledgeWarnings((current) => ({
                ...current,
                [gateId]: value
              }));
            }}
            onDecision={(review, decision) => {
              void decideGate(review, decision);
            }}
          />

          <nav className="analysis-tabs" aria-label="Analysis sections">
            {[
              ["overview", "Overview"],
              ["structure", "Structure"],
              ["characters", "Characters"],
              ["dialogue", "Dialogue & narration"],
              ["whole-book", "Whole book"],
              ["corrections", "Corrections"]
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={section === value ? "active" : ""}
                aria-current={section === value ? "page" : undefined}
                onClick={() => {
                  setSection(value as AnalysisSection);
                }}
              >
                {label}
              </button>
            ))}
          </nav>
          <GlobalAnalysisFilters
            confidenceMax={dialogueConfidenceMax}
            reviewOnly={dialogueReviewOnly}
            onConfidence={(value) => {
              filterGeneration.current += 1;
              invalidateCollectionRequests();
              setDialogueConfidenceMax(value);
              setPages({});
            }}
            onReviewOnly={(value) => {
              filterGeneration.current += 1;
              invalidateCollectionRequests();
              setDialogueReviewOnly(value);
              setPages({});
            }}
          />

          {run.currentSnapshot === null ? (
            <section className="analysis-empty compact">
              <h3>The first immutable snapshot is not available yet</h3>
              <p>
                Stage and progress above are durable. Cancel, retry, or resume
                without losing completed agent execution history.
              </p>
            </section>
          ) : (
            <section className="analysis-content">
              {section === "overview" && (
                <OverviewSection
                  run={run}
                  executions={pageItems(pages, "agent-executions")}
                  page={pages["agent-executions"]}
                  onLoadMore={() => {
                    void loadCollection("agent-executions");
                  }}
                />
              )}
              {section === "structure" && (
                <StructureSection
                  chapters={chapters}
                  scenes={visibleScenes}
                  beats={visibleBeats}
                  selectedChapterId={effectiveChapterId}
                  selectedSceneId={effectiveSceneId}
                  characters={characters}
                  pages={pages}
                  onChapter={(entityId) => {
                    setSelectedChapterId(entityId);
                    const first = scenes.find(
                      (entity) =>
                        recordValue(entity, "chapterId") === entityId
                    );
                    setSelectedSceneId(
                      first === undefined ? null : entityPrimaryId(first)
                    );
                  }}
                  onScene={setSelectedSceneId}
                  onCorrect={openCorrection}
                  onLoadMore={(collection) => {
                    void loadCollection(collection);
                  }}
                />
              )}
              {section === "characters" && (
                <CharacterSection
                  characters={characters}
                  mentions={pageItems(pages, "mentions")}
                  pages={pages}
                  onCorrect={openCorrection}
                  onLoadMore={(collection) => {
                    void loadCollection(collection);
                  }}
                />
              )}
              {section === "dialogue" && (
                <DialogueSection
                  dialogue={pageItems(pages, "dialogue-lines")}
                  narration={pageItems(pages, "narration-spans")}
                  characters={characters}
                  dialoguePage={pages["dialogue-lines"]}
                  narrationPage={pages["narration-spans"]}
                  speakerState={dialogueSpeakerState}
                  onSpeakerState={(value) => {
                    collectionRequestGenerations.current.set(
                      "dialogue-lines",
                      (collectionRequestGenerations.current.get(
                        "dialogue-lines"
                      ) ?? 0) + 1
                    );
                    setDialogueSpeakerState(value);
                    setPages((current) =>
                      withoutPage(current, "dialogue-lines")
                    );
                  }}
                  onCorrect={openCorrection}
                  onLoadDialogue={() => {
                    void loadCollection("dialogue-lines");
                  }}
                  onLoadNarration={() => {
                    void loadCollection("narration-spans");
                  }}
                />
              )}
              {section === "whole-book" && (
                <WholeBookSection
                  collection={wholeBookCollection}
                  entities={pageItems(pages, wholeBookCollection)}
                  page={pages[wholeBookCollection]}
                  characters={characters}
                  onCollection={setWholeBookCollection}
                  onCorrect={openCorrection}
                  onLoadMore={() => {
                    void loadCollection(wholeBookCollection);
                  }}
                />
              )}
              {section === "corrections" && (
                <CorrectionsSection corrections={corrections} />
              )}
            </section>
          )}
        </>
      )}

      {correctionTarget !== null && correctionDraft !== null && (
        <CorrectionDialog
          entity={correctionTarget}
          characters={characters}
          draft={correctionDraft}
          latestCorrectionId={
            latestEntityCorrection(
              corrections,
              correctionTarget.entityId
            )?.correctionId
          }
          saving={
            busyAction === `correction-${correctionTarget.entityId}`
          }
          onDraft={setCorrectionDraft}
          onCancel={() => {
            setCorrectionTarget(null);
            setCorrectionDraft(null);
          }}
          onSave={() => {
            void saveCorrection();
          }}
        />
      )}
    </main>
  );
}

function RunStatus({
  run,
  busyAction,
  onControl
}: {
  readonly run: AnalysisRun;
  readonly busyAction: string | null;
  readonly onControl: (action: "cancel" | "retry" | "resume") => void;
}) {
  return (
    <section className="analysis-run-status" aria-label="Analysis run status">
      <div className="run-status-copy">
        <span className={`analysis-state state-${run.status}`}>
          {sentenceCase(run.status)}
        </span>
        <strong>{sentenceCase(run.currentStage)}</strong>
        <span>
          Snapshot {run.snapshotCount} / attempt{" "}
          {run.latestExecution?.attempt ?? 1}
        </span>
      </div>
      <progress
        aria-label="Whole-book analysis progress"
        value={run.progress}
        max={1}
      />
      <span>{Math.round(run.progress * 100)}%</span>
      <div className="run-controls">
        {(run.status === "queued" || run.status === "running") && (
          <button
            type="button"
            disabled={busyAction !== null}
            onClick={() => {
              onControl("cancel");
            }}
          >
            {busyAction === "cancel" ? "Cancelling..." : "Cancel"}
          </button>
        )}
        {run.status === "failed" && (
          <button
            type="button"
            disabled={busyAction !== null}
            onClick={() => {
              onControl("retry");
            }}
          >
            {busyAction === "retry" ? "Retrying..." : "Retry"}
          </button>
        )}
        {run.status === "interrupted" && (
          <button
            type="button"
            disabled={busyAction !== null}
            onClick={() => {
              onControl("resume");
            }}
          >
            {busyAction === "resume" ? "Resuming..." : "Resume"}
          </button>
        )}
      </div>
    </section>
  );
}

function RunIdentity({ run }: { readonly run: AnalysisRun }) {
  return (
    <details className="analysis-identity">
      <summary>Exact run, input, profile, producer, and snapshot identities</summary>
      <div className="identity-grid">
        <IdentityRow label="Run" value={run.runId} fingerprint={run.runFingerprint} />
        <IdentityRow label="Job" value={run.jobId} />
        <IdentityRow
          label="Story"
          value={run.storyId}
          revision={run.storyRevision}
          fingerprint={run.storyFingerprint}
        />
        <IdentityRow
          label="Source document"
          value={run.sourceDocumentId}
          revision={run.sourceRevision}
        />
        <IdentityRow
          label="Extraction"
          value={run.extractionId}
          revision={run.extractionRevision}
          fingerprint={run.extractedTextSha256}
        />
        <IdentityRow
          label="Import Review"
          value={run.importReviewId}
          revision={run.importReviewRevision}
          fingerprint={run.approvedEvidenceFingerprint}
        />
        <IdentityRow
          label="Import decision"
          value={run.importReviewDecisionId}
        />
        <IdentityRow
          label="Profile"
          value={`${run.profile.profileId}@${run.profile.semanticVersion}`}
          fingerprint={run.profile.fingerprint}
        />
        <IdentityRow
          label="Producer"
          value={`${run.producer.producerId}@${run.producer.producerVersion}`}
        />
        <IdentityRow
          label="Input binding"
          value={run.inputFingerprint}
        />
        {run.currentSnapshot !== null && (
          <>
            <IdentityRow
              label="Snapshot"
              value={run.currentSnapshot.snapshotId}
              revision={run.currentSnapshot.revision}
              fingerprint={run.currentSnapshot.snapshotFingerprint}
            />
            <IdentityRow
              label="Correction set"
              value={run.currentSnapshot.correctionSetFingerprint}
            />
          </>
        )}
      </div>
      <details className="agent-registry">
        <summary>Canonical agent versions ({run.agentVersions.length})</summary>
        <ol>
          {run.agentVersions.map((agent) => (
            <li key={agent.agentId}>
              <code>
                {agent.agentId}@{agent.version}
              </code>
            </li>
          ))}
        </ol>
      </details>
    </details>
  );
}

function IdentityRow({
  label,
  value,
  revision,
  fingerprint
}: {
  readonly label: string;
  readonly value: string;
  readonly revision?: number;
  readonly fingerprint?: string;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        <code>{value}</code>
        {revision === undefined ? null : <span>revision {revision}</span>}
        {fingerprint === undefined ? null : (
          <code className="fingerprint">{fingerprint}</code>
        )}
      </dd>
    </div>
  );
}

function ReviewGateStrip({
  run,
  reviews,
  rationales,
  acknowledgements,
  busyAction,
  onRationale,
  onAcknowledge,
  onDecision
}: {
  readonly run: AnalysisRun;
  readonly reviews: readonly AnalysisReview[];
  readonly rationales: Readonly<Record<AnalysisGateId, string>>;
  readonly acknowledgements: Readonly<Record<AnalysisGateId, boolean>>;
  readonly busyAction: string | null;
  readonly onRationale: (gateId: AnalysisGateId, value: string) => void;
  readonly onAcknowledge: (gateId: AnalysisGateId, value: boolean) => void;
  readonly onDecision: (
    review: AnalysisReview,
    decision: AnalysisGateDecisionAction
  ) => void;
}) {
  return (
    <section className="analysis-gates" aria-label="Analysis review gates">
      <header>
        <div>
          <span className="eyebrow">Four explicit human gates</span>
          <h3>
            Review three independent gates, then the whole-book gate
          </h3>
        </div>
        <span>{reviews.filter((review) => review.state === "approved").length}/4 approved</span>
      </header>
      <div className="gate-grid">
        {gateOrder.map((gateId, index) => {
          const review = reviews.find((item) => item.gateId === gateId);
          const dependencyApproved =
            gateId !== "whole_book_analysis_review" ||
            gateOrder.slice(0, 3).every(
              (requiredGateId) =>
                reviews.find(
                  (item) => item.gateId === requiredGateId
                )?.state === "approved"
            );
          const warningsAcknowledged =
            review === undefined ||
            review.openWarningIds.length === 0 ||
            acknowledgements[gateId];
          const eligible =
            review !== undefined &&
            run.currentSnapshot !== null &&
            run.status === "succeeded" &&
            run.currentStage === "complete" &&
            run.progress === 1 &&
            (run.reviewEligibility === "ready" ||
              run.reviewEligibility === "blocked_by_warnings" ||
              run.reviewEligibility === "invalidated") &&
            dependencyApproved &&
            warningsAcknowledged;
          return (
            <article
              key={gateId}
              className={
                review === undefined
                  ? "analysis-gate state-pending"
                  : `analysis-gate state-${review.state}`
              }
            >
              <header>
                <span>{index + 1}</span>
                <div>
                  <h4>{gateTitle(gateId)}</h4>
                  <strong>
                    {review === undefined
                      ? "Waiting"
                      : sentenceCase(review.state)}
                  </strong>
                </div>
              </header>
              {review === undefined ? (
                <p>Gate evidence has not been published.</p>
              ) : (
                <>
                  <dl>
                    <div>
                      <dt>Review</dt>
                      <dd>{shortId(review.reviewId)} / revision {review.revision}</dd>
                    </div>
                    <div>
                      <dt>Snapshot</dt>
                      <dd>{shortId(review.snapshotId)}</dd>
                    </div>
                  </dl>
                  <code className="fingerprint">{review.evidenceFingerprint}</code>
                  {!dependencyApproved && (
                    <p className="gate-blocker">
                      Approve all three source-analysis gates first.
                    </p>
                  )}
                  {review.openWarningIds.length > 0 && (
                    <label className="warning-acknowledgement">
                      <input
                        type="checkbox"
                        checked={acknowledgements[gateId]}
                        onChange={(event) => {
                          onAcknowledge(gateId, event.target.checked);
                        }}
                      />
                      I reviewed and acknowledge {review.openWarningIds.length} open
                      warning{review.openWarningIds.length === 1 ? "" : "s"}.
                    </label>
                  )}
                  <label>
                    Review rationale
                    <textarea
                      aria-label={`${gateTitle(gateId)} rationale`}
                      value={rationales[gateId]}
                      maxLength={4_000}
                      placeholder="Required for every immutable gate decision"
                      onChange={(event) => {
                        onRationale(gateId, event.target.value);
                      }}
                    />
                  </label>
                  <div className="gate-actions">
                    <button
                      type="button"
                      className="primary"
                      disabled={
                        !eligible ||
                        rationales[gateId].trim().length === 0 ||
                        busyAction !== null
                      }
                      onClick={() => {
                        onDecision(review, "approve");
                      }}
                    >
                      {review.state === "approved"
                        ? `Reapprove ${gateTitle(gateId)}`
                        : `Approve ${gateTitle(gateId)}`}
                    </button>
                    <button
                      type="button"
                      disabled={
                        !eligible ||
                        rationales[gateId].trim().length === 0 ||
                        busyAction !== null
                      }
                      onClick={() => {
                        onDecision(review, "request_changes");
                      }}
                    >
                      Request changes
                    </button>
                    <button
                      type="button"
                      className="danger"
                      disabled={
                        !eligible ||
                        rationales[gateId].trim().length === 0 ||
                        busyAction !== null
                      }
                      onClick={() => {
                        onDecision(review, "reject");
                      }}
                    >
                      Reject
                    </button>
                  </div>
                  {review.latestDecisionId !== undefined && (
                    <p className="decision-proof">
                      New actions append and supersede immutable decision{" "}
                      <code>{review.latestDecisionId}</code>
                    </p>
                  )}
                </>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function OverviewSection({
  run,
  executions,
  page,
  onLoadMore
}: {
  readonly run: AnalysisRun;
  readonly executions: readonly AnalysisEntity[];
  readonly page: PageState | undefined;
  readonly onLoadMore: () => void;
}) {
  const counts = run.currentSnapshot?.counts ?? run.summary;
  const countEntries =
    counts === undefined
      ? []
      : Object.entries(
          counts as unknown as Readonly<Record<string, number>>
        );
  const total = countEntries.reduce((sum, [, value]) => sum + value, 0);
  return (
    <div className="analysis-panel">
      <header className="collection-header">
        <div>
          <span className="eyebrow">Snapshot overview</span>
          <h3>One bounded projection of the whole book</h3>
        </div>
      </header>
      <div className="metric-grid">
        <Metric label="Claims in snapshot" value={total} />
        <Metric label="Snapshots" value={run.snapshotCount} />
        <Metric label="Run warnings" value={run.warnings.length} />
        <Metric label="Review state" value={sentenceCase(run.reviewEligibility)} />
      </div>
      {counts !== undefined && (
        <div className="count-grid">
          {countEntries.map(([key, value]) => (
            <Metric key={key} label={sentenceCase(key)} value={value} />
          ))}
        </div>
      )}
      {run.warnings.length > 0 && <WarningList warnings={run.warnings} />}
      <CollectionHeader
        eyebrow="Durable execution ledger"
        title="Runtime agent executions"
        page={page}
      />
      <EntityGrid
        entities={executions}
        characters={[]}
        onCorrect={() => undefined}
      />
      <LoadMore page={page} onLoadMore={onLoadMore} />
    </div>
  );
}

function StructureSection({
  chapters,
  scenes,
  beats,
  selectedChapterId,
  selectedSceneId,
  characters,
  pages,
  onChapter,
  onScene,
  onCorrect,
  onLoadMore
}: {
  readonly chapters: readonly AnalysisEntity[];
  readonly scenes: readonly AnalysisEntity[];
  readonly beats: readonly AnalysisEntity[];
  readonly selectedChapterId: string | null;
  readonly selectedSceneId: string | null;
  readonly characters: readonly AnalysisEntity[];
  readonly pages: Partial<Record<AnalysisCollection, PageState>>;
  readonly onChapter: (entityId: string) => void;
  readonly onScene: (entityId: string) => void;
  readonly onCorrect: (entity: AnalysisEntity) => void;
  readonly onLoadMore: (collection: AnalysisCollection) => void;
}) {
  const selectedEntities = [
    chapters.find(
      (chapter) => entityPrimaryId(chapter) === selectedChapterId
    ),
    scenes.find((scene) => entityPrimaryId(scene) === selectedSceneId)
  ].filter((entity): entity is AnalysisEntity => entity !== undefined);

  return (
    <div className="structure-layout">
      <aside>
        <CollectionHeader
          eyebrow="Navigation"
          title="Chapters"
          page={pages.chapters}
        />
        <nav aria-label="Analysis chapters" className="entity-navigation">
          {chapters.map((chapter) => (
            <button
              key={entityPrimaryId(chapter)}
              type="button"
              className={
                selectedChapterId === entityPrimaryId(chapter) ? "active" : ""
              }
              onClick={() => {
                onChapter(entityPrimaryId(chapter));
              }}
            >
              {entityTitle(chapter)}
            </button>
          ))}
        </nav>
        <LoadMore
          page={pages.chapters}
          onLoadMore={() => {
            onLoadMore("chapters");
          }}
        />
        <CollectionHeader
          eyebrow="Within chapter"
          title="Scenes"
          page={pages.scenes}
        />
        <nav aria-label="Analysis scenes" className="entity-navigation">
          {scenes.map((scene) => (
            <button
              key={entityPrimaryId(scene)}
              type="button"
              className={
                selectedSceneId === entityPrimaryId(scene) ? "active" : ""
              }
              onClick={() => {
                onScene(entityPrimaryId(scene));
              }}
            >
              {entityTitle(scene)}
            </button>
          ))}
        </nav>
        <LoadMore
          page={pages.scenes}
          onLoadMore={() => {
            onLoadMore("scenes");
          }}
        />
      </aside>
      <div>
        <CollectionHeader
          eyebrow="Machine proposal and effective view"
          title="Selected structural boundaries"
        />
        <EntityGrid
          entities={selectedEntities}
          characters={characters}
          onCorrect={onCorrect}
        />
        <CollectionHeader
          eyebrow="Scene decomposition"
          title="Beats"
          page={pages.beats}
        />
        <EntityGrid
          entities={beats}
          characters={characters}
          onCorrect={onCorrect}
        />
        <LoadMore
          page={pages.beats}
          onLoadMore={() => {
            onLoadMore("beats");
          }}
        />
      </div>
    </div>
  );
}

function CharacterSection({
  characters,
  mentions,
  pages,
  onCorrect,
  onLoadMore
}: {
  readonly characters: readonly AnalysisEntity[];
  readonly mentions: readonly AnalysisEntity[];
  readonly pages: Partial<Record<AnalysisCollection, PageState>>;
  readonly onCorrect: (entity: AnalysisEntity) => void;
  readonly onLoadMore: (collection: AnalysisCollection) => void;
}) {
  return (
    <div className="analysis-panel">
      <CollectionHeader
        eyebrow="Identity registry"
        title="Canonical characters and aliases"
        page={pages.characters}
      />
      <EntityGrid
        entities={characters}
        characters={characters}
        onCorrect={onCorrect}
      />
      <LoadMore
        page={pages.characters}
        onLoadMore={() => {
          onLoadMore("characters");
        }}
      />
      <CollectionHeader
        eyebrow="Source-grounded identity resolution"
        title="Mentions"
        page={pages.mentions}
      />
      <EntityGrid
        entities={mentions}
        characters={characters}
        onCorrect={onCorrect}
      />
      <LoadMore
        page={pages.mentions}
        onLoadMore={() => {
          onLoadMore("mentions");
        }}
      />
    </div>
  );
}

function GlobalAnalysisFilters({
  confidenceMax,
  reviewOnly,
  onConfidence,
  onReviewOnly
}: {
  readonly confidenceMax: number | null;
  readonly reviewOnly: boolean;
  readonly onConfidence: (value: number | null) => void;
  readonly onReviewOnly: (value: boolean) => void;
}) {
  return (
    <aside className="dialogue-toolbar" aria-label="Global analysis filters">
      <div>
        <span className="eyebrow">Server-side bounded filters</span>
        <strong>All analysis collections</strong>
      </div>
      <label>
        Maximum confidence
        <select
          aria-label="Maximum confidence"
          value={confidenceMax === null ? "all" : String(confidenceMax)}
          onChange={(event) => {
            onConfidence(
              event.target.value === "all"
                ? null
                : Number(event.target.value)
            );
          }}
        >
          <option value="all">All confidence</option>
          <option value={String(LOW_CONFIDENCE_MAX)}>
            Low and unknown (below 75%)
          </option>
          <option value={String(MEDIUM_CONFIDENCE_MAX)}>
            Medium or lower (below 85%)
          </option>
        </select>
      </label>
      <label className="toggle-label">
        <input
          type="checkbox"
          checked={reviewOnly}
          onChange={(event) => {
            onReviewOnly(event.target.checked);
          }}
        />
        Human review only
      </label>
    </aside>
  );
}

function DialogueSection({
  dialogue,
  narration,
  characters,
  dialoguePage,
  narrationPage,
  speakerState,
  onSpeakerState,
  onCorrect,
  onLoadDialogue,
  onLoadNarration
}: {
  readonly dialogue: readonly AnalysisEntity[];
  readonly narration: readonly AnalysisEntity[];
  readonly characters: readonly AnalysisEntity[];
  readonly dialoguePage: PageState | undefined;
  readonly narrationPage: PageState | undefined;
  readonly speakerState: DialogueSpeakerState | "all";
  readonly onSpeakerState: (value: DialogueSpeakerState | "all") => void;
  readonly onCorrect: (entity: AnalysisEntity) => void;
  readonly onLoadDialogue: () => void;
  readonly onLoadNarration: () => void;
}) {
  return (
    <div className="analysis-panel">
      <header className="dialogue-toolbar">
        <div>
          <span className="eyebrow">Server-side bounded filters</span>
          <h3>Dialogue attribution</h3>
        </div>
        <label>
          Speaker state
          <select
            aria-label="Speaker state"
            value={speakerState}
            onChange={(event) => {
              onSpeakerState(
                event.target.value as DialogueSpeakerState | "all"
              );
            }}
          >
            <option value="all">All states</option>
            <option value="unknown">Unknown</option>
            <option value="ambiguous">Ambiguous</option>
            <option value="proposed">Proposed</option>
            <option value="corrected">Human corrected</option>
          </select>
        </label>
      </header>
      <CollectionHeader
        eyebrow="Spoken lines and candidates"
        title="Dialogue"
        page={dialoguePage}
      />
      <EntityGrid
        entities={dialogue}
        characters={characters}
        onCorrect={onCorrect}
      />
      <LoadMore page={dialoguePage} onLoadMore={onLoadDialogue} />
      <CollectionHeader
        eyebrow="Distinct from spoken dialogue"
        title="Narration spans"
        page={narrationPage}
      />
      <EntityGrid
        entities={narration}
        characters={characters}
        onCorrect={onCorrect}
      />
      <LoadMore page={narrationPage} onLoadMore={onLoadNarration} />
    </div>
  );
}

function WholeBookSection({
  collection,
  entities,
  page,
  characters,
  onCollection,
  onCorrect,
  onLoadMore
}: {
  readonly collection: (typeof wholeBookCollections)[number];
  readonly entities: readonly AnalysisEntity[];
  readonly page: PageState | undefined;
  readonly characters: readonly AnalysisEntity[];
  readonly onCollection: (
    value: (typeof wholeBookCollections)[number]
  ) => void;
  readonly onCorrect: (entity: AnalysisEntity) => void;
  readonly onLoadMore: () => void;
}) {
  return (
    <div className="analysis-panel">
      <header className="whole-book-header">
        <div>
          <span className="eyebrow">Whole-book continuity map</span>
          <h3>{collectionTitle(collection)}</h3>
        </div>
        <label>
          Analysis layer
          <select
            aria-label="Analysis layer"
            value={collection}
            onChange={(event) => {
              onCollection(
                event.target
                  .value as (typeof wholeBookCollections)[number]
              );
            }}
          >
            {wholeBookCollections.map((value) => (
              <option key={value} value={value}>
                {collectionTitle(value)}
              </option>
            ))}
          </select>
        </label>
      </header>
      <CollectionHeader
        eyebrow="Bounded claim page"
        title={collectionTitle(collection)}
        page={page}
      />
      <EntityGrid
        entities={entities}
        characters={characters}
        onCorrect={onCorrect}
      />
      <LoadMore page={page} onLoadMore={onLoadMore} />
    </div>
  );
}

function CorrectionsSection({
  corrections
}: {
  readonly corrections: readonly AnalysisCorrection[];
}) {
  const ordered = [...corrections].sort((left, right) =>
    right.recordedAt.localeCompare(left.recordedAt)
  );
  return (
    <div className="analysis-panel">
      <CollectionHeader
        eyebrow="Append-only human authority"
        title="Durable correction history"
      />
      {ordered.length === 0 ? (
        <p className="empty-collection">
          No Phase 2 corrections have been recorded for this run.
        </p>
      ) : (
        <div className="correction-history">
          {ordered.map((correction) => (
            <article key={correction.correctionId}>
              <header>
                <strong>{sentenceCase(correction.category)}</strong>
                <span className="authority authority-human">
                  Human / locked
                </span>
              </header>
              <p>{correction.reason}</p>
              <dl>
                <div>
                  <dt>Target</dt>
                  <dd>
                    {collectionTitle(correction.targetCollection)} /{" "}
                    {shortId(correction.targetEntityId)}
                  </dd>
                </div>
                <div>
                  <dt>Expected revision</dt>
                  <dd>{correction.expectedTargetRevision}</dd>
                </div>
                <div>
                  <dt>Actor</dt>
                  <dd>{correction.actor.actorId}</dd>
                </div>
                <div>
                  <dt>Recorded</dt>
                  <dd>{new Date(correction.recordedAt).toLocaleString()}</dd>
                </div>
                {correction.supersedesCorrectionId !== undefined && (
                  <div>
                    <dt>Supersedes</dt>
                    <dd>{correction.supersedesCorrectionId}</dd>
                  </div>
                )}
              </dl>
              <details>
                <summary>Correction evidence and patch</summary>
                <code>{correction.previousValueFingerprint}</code>
                <code>{correction.correctedValueFingerprint}</code>
                <pre>{JSON.stringify(correction.patch, null, 2)}</pre>
              </details>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function EntityGrid({
  entities,
  characters,
  onCorrect
}: {
  readonly entities: readonly AnalysisEntity[];
  readonly characters: readonly AnalysisEntity[];
  readonly onCorrect: (entity: AnalysisEntity) => void;
}) {
  if (entities.length === 0) {
    return <p className="empty-collection">No claims in this bounded page.</p>;
  }
  return (
    <div className="entity-grid">
      {entities.map((entity) => (
        <EntityCard
          key={`${entityCollection(entity)}-${entityPrimaryId(entity)}`}
          entity={entity}
          characters={characters}
          onCorrect={onCorrect}
        />
      ))}
    </div>
  );
}

function EntityCard({
  entity,
  characters,
  onCorrect
}: {
  readonly entity: AnalysisEntity;
  readonly characters: readonly AnalysisEntity[];
  readonly onCorrect: (entity: AnalysisEntity) => void;
}) {
  const collection = entityCollection(entity);
  const record = entityRecord(entity);
  const correctableEntity = isCorrectableEntity(entity) ? entity : null;
  const confidence = record.confidence as
    | { readonly score?: unknown; readonly classification?: unknown; readonly basis?: unknown }
    | undefined;
  const classification =
    typeof confidence?.classification === "string"
      ? confidence.classification
      : "unknown";
  const score =
    typeof confidence?.score === "number" ? confidence.score : 0;
  const authority =
    record.effectiveAuthority === "human" ? "human" : "runtime_agent";
  const evidence =
    (Array.isArray(record.evidence)
      ? record.evidence
      : []) as readonly AnalysisEvidenceExcerpt[];
  const warnings =
    (Array.isArray(record.warnings)
      ? record.warnings
      : []) as readonly AnalysisWarning[];
  const exactText = exactTextValue(record.exactText);
  return (
    <article
      className="analysis-entity"
      data-collection={collection}
      data-effective-authority={authority}
      data-identity-status={stringRecordValue(record, "identityStatus") ?? ""}
      data-speaker-state={stringRecordValue(record, "speakerState") ?? ""}
    >
      <header>
        <div>
          <span className="eyebrow">{collectionTitle(collection)}</span>
          <h4>{entityTitle(entity)}</h4>
        </div>
        <div className="entity-badges">
          <span className={`confidence confidence-${classification}`}>
            {classification} / {Math.round(score * 100)}%
          </span>
          <span
            className={
              authority === "human"
                ? "authority authority-human"
                : "authority authority-machine"
            }
          >
            {authority === "human" ? "Human effective" : "Machine proposal"}
          </span>
        </div>
      </header>
      {exactText !== null && <blockquote>{exactText}</blockquote>}
      <EffectiveHumanView entity={entity} characters={characters} />
      <EntityValues
        entity={entity}
        characters={characters}
      />
      {typeof confidence?.basis === "string" && (
        <p className="confidence-basis">{confidence.basis}</p>
      )}
      {warnings.length > 0 && <WarningList warnings={warnings} />}
      {evidence.length > 0 && (
        <details className="evidence-list">
          <summary>
            Source evidence ({evidence.length}, exact bounded excerpts)
          </summary>
          {evidence.map((item, index) => (
            <figure key={`${item.excerptSha256}-${index}`}>
              <blockquote>{item.excerptText}</blockquote>
              <figcaption>
                Unicode code points {item.startOffset}–{item.endOffset} /{" "}
                <code>{item.textSha256}</code>
                {item.excerptTruncated ? " / excerpt truncated" : ""}
              </figcaption>
            </figure>
          ))}
        </details>
      )}
      <details className="entity-proof">
        <summary>Identity and fingerprints</summary>
        <dl>
          {correctableEntity !== null && (
            <>
              <div>
                <dt>Entity</dt>
                <dd>{correctableEntity.entityId}</dd>
              </div>
              <div>
                <dt>Stable semantic ID</dt>
                <dd>{correctableEntity.stableSemanticId}</dd>
              </div>
              <div>
                <dt>Machine fingerprint</dt>
                <dd>{correctableEntity.machineEntityFingerprint}</dd>
              </div>
              <div>
                <dt>Effective fingerprint</dt>
                <dd>{correctableEntity.effectiveValueFingerprint}</dd>
              </div>
              <div>
                <dt>Revision</dt>
                <dd>
                  machine {correctableEntity.revision} / effective{" "}
                  {correctableEntity.effectiveRevision}
                </dd>
              </div>
            </>
          )}
          <div>
            <dt>Run</dt>
            <dd>{record.runId as string}</dd>
          </div>
          <div>
            <dt>Snapshot</dt>
            <dd>
              {typeof record.snapshotId === "string"
                ? record.snapshotId
                : "not published"}
            </dd>
          </div>
        </dl>
      </details>
      {correctableEntity !== null &&
        correctionCategories(collection).length > 0 && (
        <button
          type="button"
          className="correct-entity"
          aria-label={`Correct ${entityTitle(entity)}`}
          onClick={() => {
            onCorrect(entity);
          }}
        >
          Add human correction
        </button>
      )}
    </article>
  );
}

function EffectiveHumanView({
  entity,
  characters
}: {
  readonly entity: AnalysisEntity;
  readonly characters: readonly AnalysisEntity[];
}) {
  const record = entityRecord(entity);
  const boundary = record.effectiveBoundary as
    | HumanEffectiveBoundary
    | undefined;
  const registry = record.effectiveRegistry as
    | HumanEffectiveRegistry
    | undefined;

  if (boundary !== undefined) {
    return (
      <section
        className="effective-human-view"
        data-effective-view="boundary"
        aria-label="Human-controlled effective boundary"
      >
        <header>
          <div>
            <span className="eyebrow">Effective human view</span>
            <strong>Human-controlled boundary</strong>
          </div>
          <span className="authority authority-human">Human authority</span>
        </header>
        <p>
          This boundary is {boundary.included ? "included in" : "excluded from"}{" "}
          the effective structure. The machine proposal and evidence remain
          unchanged.
        </p>
        <dl className="effective-human-values">
          <div>
            <dt>Operation</dt>
            <dd>{sentenceCase(boundary.operation)}</dd>
          </div>
          <div>
            <dt>Effective state</dt>
            <dd>{boundary.included ? "Included" : "Excluded"}</dd>
          </div>
          <div>
            <dt>Parent entity</dt>
            <dd>{boundary.parentEntityId}</dd>
          </div>
          <div>
            <dt>Ordinal</dt>
            <dd>{boundary.ordinal}</dd>
          </div>
          <div className="effective-human-values-wide">
            <dt>Source span</dt>
            <dd>{formatEffectiveSourceSpan(boundary)}</dd>
          </div>
          <div className="effective-human-values-wide">
            <dt>Source SHA-256</dt>
            <dd>
              <code>{boundary.sourceSpan.textSha256}</code>
            </dd>
          </div>
          <div className="effective-human-values-wide">
            <dt>Correction ID</dt>
            <dd>
              <code>{boundary.correctionId}</code>
            </dd>
          </div>
        </dl>
      </section>
    );
  }

  if (registry === undefined) {
    return null;
  }

  return (
    <section
      className="effective-human-view"
      data-effective-view="registry"
      aria-label="Human-controlled effective registry"
    >
      <header>
        <div>
          <span className="eyebrow">Effective human view</span>
          <strong>Human-controlled character registry</strong>
        </div>
        <span className="authority authority-human">Human authority</span>
      </header>
      <p>
        This human correction controls the effective identity while the
        machine-produced identity and evidence remain unchanged.
      </p>
      <dl className="effective-human-values">
        <div>
          <dt>Operation</dt>
          <dd>{sentenceCase(registry.operation)}</dd>
        </div>
        {registry.operation === "merge" ? (
          <div>
            <dt>Merge into</dt>
            <dd>
              {formatIdentityReference(
                registry.mergeIntoCharacterId,
                characters
              )}
            </dd>
          </div>
        ) : (
          <>
            <div>
              <dt>Registry identity</dt>
              <dd>{registry.splitIdentity.registryCharacterId}</dd>
            </div>
            <div>
              <dt>Canonical name</dt>
              <dd>{registry.splitIdentity.canonicalName}</dd>
            </div>
            <div>
              <dt>Normalized name</dt>
              <dd>{registry.splitIdentity.normalizedCanonicalName}</dd>
            </div>
            <div className="effective-human-values-wide">
              <dt>Mention IDs</dt>
              <dd>
                {registry.splitIdentity.mentionIds.length === 0
                  ? "None"
                  : registry.splitIdentity.mentionIds.join(", ")}
              </dd>
            </div>
          </>
        )}
        <div className="effective-human-values-wide">
          <dt>Correction ID</dt>
          <dd>
            <code>{registry.correctionId}</code>
          </dd>
        </div>
      </dl>
    </section>
  );
}

function EntityValues({
  entity,
  characters
}: {
  readonly entity: AnalysisEntity;
  readonly characters: readonly AnalysisEntity[];
}) {
  const record = entityRecord(entity);
  const hidden = new Set([
    "contractVersion",
    "entityId",
    "stableSemanticId",
    "runId",
    "snapshotId",
    "revision",
    "effectiveRevision",
    "machineEntityFingerprint",
    "effectiveValueFingerprint",
    "effectiveAuthority",
    "ordinal",
    "confidence",
    "warnings",
    "provenance",
    "evidence",
    "exactText",
    "effectiveBoundary",
    "effectiveRegistry"
  ]);
  const values = Object.entries(record).filter(
    ([key]) => !hidden.has(key)
  );
  return (
    <dl className="entity-values">
      {values.map(([key, value]) => (
        <div key={key}>
          <dt>{sentenceCase(key)}</dt>
          <dd>{formatValue(value, characters)}</dd>
        </div>
      ))}
    </dl>
  );
}

function CorrectionDialog({
  entity,
  characters,
  draft,
  latestCorrectionId,
  saving,
  onDraft,
  onCancel,
  onSave
}: {
  readonly entity: CorrectableAnalysisEntity;
  readonly characters: readonly AnalysisEntity[];
  readonly draft: CorrectionDraft;
  readonly latestCorrectionId?: string;
  readonly saving: boolean;
  readonly onDraft: (draft: CorrectionDraft) => void;
  readonly onCancel: () => void;
  readonly onSave: () => void;
}) {
  const collection = entityCollection(entity);
  const available = correctionCategories(collection);
  const labels = correctionFieldLabels(draft.category);
  return (
    <div className="correction-overlay" role="presentation">
      <section
        className="correction-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="correction-title"
      >
        <header>
          <div>
            <span className="eyebrow">Durable human overlay</span>
            <h3 id="correction-title">Correct {entityTitle(entity)}</h3>
          </div>
          <button
            type="button"
            aria-label="Close correction"
            onClick={onCancel}
          >
            ×
          </button>
        </header>
        <p>
          The service-issued effective fingerprint and revision are passed
          verbatim. The machine fingerprint stays visible for comparison.
        </p>
        {latestCorrectionId !== undefined && (
          <p className="warning-acknowledgement">
            This correction will supersede the latest immutable correction{" "}
            <code>{latestCorrectionId}</code>
            .
          </p>
        )}
        <label>
          Correction type
          <select
            aria-label="Correction type"
            value={draft.category}
            onChange={(event) => {
              const category = event.target
                .value as AnalysisCorrectionCategory;
              onDraft(
                {
                  ...defaultCorrectionDraft(
                    entity,
                    category,
                    draft.reason
                  ),
                  supersedeLatest: draft.supersedeLatest
                }
              );
            }}
          >
            {available.map((category) => (
              <option key={category} value={category}>
                {sentenceCase(category)}
              </option>
            ))}
          </select>
        </label>
        <CorrectionFields
          draft={draft}
          labels={labels}
          characters={characters}
          onDraft={onDraft}
        />
        <label>
          Correction reason
          <textarea
            aria-label="Correction reason"
            value={draft.reason}
            maxLength={1_000}
            placeholder="What source evidence or reasoning supports this correction?"
            onChange={(event) => {
              onDraft({ ...draft, reason: event.target.value });
            }}
          />
        </label>
        <div className="correction-preconditions">
          <span>Effective revision {entity.effectiveRevision}</span>
          <code>{entity.effectiveValueFingerprint}</code>
        </div>
        <footer>
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            disabled={
              saving ||
              draft.reason.trim().length === 0 ||
              (correctionPrimaryRequired(draft.category) &&
                draft.primary.trim().length === 0) ||
              (draft.category === "character_split" &&
                !draft.secondary
                  .split(",")
                  .some((identifier) => identifier.trim().length > 0))
            }
            onClick={onSave}
          >
            {saving ? "Saving..." : "Save human correction"}
          </button>
        </footer>
      </section>
    </div>
  );
}

function correctionPrimaryRequired(
  category: AnalysisCorrectionCategory
): boolean {
  return (
    category !== "structure_label" &&
    category !== "mention_resolution" &&
    category !== "dialogue_speaker"
  );
}

function CorrectionFields({
  draft,
  labels,
  characters,
  onDraft
}: {
  readonly draft: CorrectionDraft;
  readonly labels: readonly [string, string?, string?];
  readonly characters: readonly AnalysisEntity[];
  readonly onDraft: (draft: CorrectionDraft) => void;
}) {
  const [primaryLabel, secondaryLabel, tertiaryLabel] = labels;
  if (draft.category === "structure_boundary") {
    return (
      <div className="correction-field-row">
        <label>
          Structure operation
          <select
            aria-label="Structure operation"
            value={draft.operation}
            onChange={(event) => {
              onDraft({ ...draft, operation: event.target.value });
            }}
          >
            <option value="add">Add boundary</option>
            <option value="move">Move boundary</option>
            <option value="remove">Remove boundary</option>
          </select>
        </label>
        <label>
          Parent entity ID
          <input
            aria-label="Parent entity ID"
            value={draft.parentEntityId}
            onChange={(event) => {
              onDraft({ ...draft, parentEntityId: event.target.value });
            }}
          />
        </label>
        <label>
          Structure ordinal
          <input
            aria-label="Structure ordinal"
            inputMode="numeric"
            value={draft.ordinal}
            onChange={(event) => {
              onDraft({ ...draft, ordinal: event.target.value });
            }}
          />
        </label>
        <label>
          Selected start offset
          <input
            aria-label="Selected start offset"
            type="number"
            inputMode="numeric"
            min={0}
            step={1}
            value={draft.primary}
            readOnly={draft.operation === "remove"}
            onChange={(event) => {
              onDraft({ ...draft, primary: event.target.value });
            }}
          />
        </label>
        <label>
          Selected end offset
          <input
            aria-label="Selected end offset"
            type="number"
            inputMode="numeric"
            min={1}
            step={1}
            value={draft.secondary}
            readOnly={draft.operation === "remove"}
            onChange={(event) => {
              onDraft({ ...draft, secondary: event.target.value });
            }}
          />
        </label>
        <p className="correction-field-note">
          The local service derives the selected span fingerprint from the
          approved extraction when this correction is saved.
        </p>
        <label>
          Scene boundary kind
          <select
            aria-label="Scene boundary kind"
            value={draft.boundaryKind}
            onChange={(event) => {
              onDraft({ ...draft, boundaryKind: event.target.value });
            }}
          >
            {[
              "chapter_start",
              "explicit_scene_break",
              "heading",
              "inferred"
            ].map((value) => (
              <option key={value} value={value}>
                {sentenceCase(value)}
              </option>
            ))}
          </select>
        </label>
      </div>
    );
  }
  if (draft.category === "relationship") {
    return (
      <div className="correction-field-row">
        <label>
          Source character
          <CharacterSelect
            label="Source character"
            value={draft.sourceCharacterId}
            characters={characters}
            allowEmpty
            onChange={(value) => {
              onDraft({ ...draft, sourceCharacterId: value });
            }}
          />
        </label>
        <label>
          Target character
          <CharacterSelect
            label="Target character"
            value={draft.targetCharacterId}
            characters={characters}
            allowEmpty
            onChange={(value) => {
              onDraft({ ...draft, targetCharacterId: value });
            }}
          />
        </label>
        <label>
          Relationship kind
          <select
            aria-label="Relationship kind"
            value={draft.primary}
            onChange={(event) => {
              onDraft({ ...draft, primary: event.target.value });
            }}
          >
            {[
              "family",
              "friendship",
              "romantic",
              "professional",
              "adversarial",
              "authority",
              "dependency",
              "alliance",
              "unknown",
              "custom"
            ].map((value) => (
              <option key={value} value={value}>
                {sentenceCase(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Relationship state
          <input
            aria-label="Relationship state"
            value={draft.secondary}
            onChange={(event) => {
              onDraft({ ...draft, secondary: event.target.value });
            }}
          />
        </label>
        <label>
          Relationship change
          <select
            aria-label="Relationship change"
            value={draft.tertiary}
            onChange={(event) => {
              onDraft({ ...draft, tertiary: event.target.value });
            }}
          >
            {[
              "established",
              "strengthened",
              "weakened",
              "reversed",
              "unchanged",
              "uncertain"
            ].map((value) => (
              <option key={value} value={value}>
                {sentenceCase(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Relationship scope
          <select
            aria-label="Relationship scope"
            value={draft.scopeKind}
            onChange={(event) => {
              onDraft({ ...draft, scopeKind: event.target.value });
            }}
          >
            <option value="scene">Scene</option>
            <option value="chapter">Chapter</option>
            <option value="scene_range">Scene range</option>
          </select>
        </label>
        <label>
          First scope scene ID
          <input
            aria-label="First scope scene ID"
            value={draft.scopeFirstSceneId}
            onChange={(event) => {
              onDraft({ ...draft, scopeFirstSceneId: event.target.value });
            }}
          />
        </label>
        <label>
          Last scope scene ID
          <input
            aria-label="Last scope scene ID"
            value={draft.scopeLastSceneId}
            onChange={(event) => {
              onDraft({ ...draft, scopeLastSceneId: event.target.value });
            }}
          />
        </label>
      </div>
    );
  }
  const primaryIsCharacter = [
    "character_merge",
    "mention_resolution",
    "dialogue_speaker"
  ].includes(draft.category);
  const primaryIsDisposition =
    draft.category === "continuity_disposition";
  return (
    <div className="correction-field-row">
      <label>
        {primaryLabel}
        {primaryIsCharacter ? (
          <CharacterSelect
            label={primaryLabel}
            value={draft.primary}
            characters={characters}
            allowEmpty
            onChange={(value) => {
              onDraft({ ...draft, primary: value });
            }}
          />
        ) : primaryIsDisposition ? (
          <select
            aria-label={primaryLabel}
            value={draft.primary}
            onChange={(event) => {
              onDraft({ ...draft, primary: event.target.value });
            }}
          >
            {[
              "confirmed_issue",
              "intentional",
              "false_positive",
              "deferred",
              "corrected",
              "unresolved"
            ].map((value) => (
              <option key={value} value={value}>
                {sentenceCase(value)}
              </option>
            ))}
          </select>
        ) : (
          <input
            aria-label={primaryLabel}
            value={draft.primary}
            maxLength={2_000}
            onChange={(event) => {
              onDraft({ ...draft, primary: event.target.value });
            }}
          />
        )}
      </label>
      {secondaryLabel !== undefined && (
        <label>
          {secondaryLabel}
          <input
            aria-label={secondaryLabel}
            value={draft.secondary}
            maxLength={2_000}
            onChange={(event) => {
              onDraft({ ...draft, secondary: event.target.value });
            }}
          />
        </label>
      )}
      {tertiaryLabel !== undefined && (
        <label>
          {tertiaryLabel}
          <input
            aria-label={tertiaryLabel}
            value={draft.tertiary}
            maxLength={2_000}
            onChange={(event) => {
              onDraft({ ...draft, tertiary: event.target.value });
            }}
          />
        </label>
      )}
    </div>
  );
}

function CharacterSelect({
  label,
  value,
  characters,
  allowEmpty,
  onChange
}: {
  readonly label: string;
  readonly value: string;
  readonly characters: readonly AnalysisEntity[];
  readonly allowEmpty: boolean;
  readonly onChange: (value: string) => void;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(event) => {
        onChange(event.target.value);
      }}
    >
      {allowEmpty && <option value="">Unresolved / none</option>}
      {characters.map((character) => (
        <option
          key={entityPrimaryId(character)}
          value={entityPrimaryId(character)}
        >
          {entityTitle(character)}
        </option>
      ))}
    </select>
  );
}

function CollectionHeader({
  eyebrow,
  title,
  page
}: {
  readonly eyebrow: string;
  readonly title: string;
  readonly page?: PageState;
}) {
  return (
    <header className="collection-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h3>{title}</h3>
      </div>
      {page !== undefined && (
        <span>
          {page.items.length} of {page.total}
        </span>
      )}
    </header>
  );
}

function LoadMore({
  page,
  onLoadMore
}: {
  readonly page: PageState | undefined;
  readonly onLoadMore: () => void;
}) {
  if (page === undefined || page.nextCursor === undefined) {
    return null;
  }
  return (
    <button
      type="button"
      className="load-more"
      disabled={page.loading}
      onClick={onLoadMore}
    >
      {page.loading ? "Loading..." : "Load next bounded page"}
    </button>
  );
}

function Metric({
  label,
  value
}: {
  readonly label: string;
  readonly value: string | number;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function WarningList({
  warnings
}: {
  readonly warnings: readonly AnalysisWarning[];
}) {
  return (
    <ul className="warning-list">
      {warnings.map((warning, index) => (
        <li
          key={`${warning.code}-${index}`}
          className={`warning-${warning.severity}`}
        >
          <strong>{warning.code}</strong>
          <span>{warning.message}</span>
          {warning.requiresHumanReview && <em>Human review required</em>}
        </li>
      ))}
    </ul>
  );
}

function correctionCategories(
  collection: AnalysisCollection
): readonly AnalysisCorrectionCategory[] {
  switch (collection) {
    case "chapters":
    case "scenes":
      return ["structure_boundary", "structure_label"];
    case "beats":
      return [];
    case "characters":
      return [
        "character_identity",
        "character_alias",
        "character_merge",
        "character_split"
      ];
    case "mentions":
      return ["mention_resolution"];
    case "dialogue-lines":
      return ["dialogue_speaker"];
    case "pov-segments":
      return ["point_of_view"];
    case "locations":
      return ["location_identity", "location_alias"];
    case "temporal-constraints":
      return ["temporal_order"];
    case "relationships":
      return ["relationship"];
    case "emotional-states":
      return ["emotional_state"];
    case "dramatic-intents":
      return ["dramatic_intent"];
    case "continuity-findings":
      return ["continuity_disposition"];
    case "agent-executions":
    case "narration-spans":
    case "timeline-events":
      return [];
  }
}

function defaultCorrectionDraft(
  entity: AnalysisEntity,
  category: AnalysisCorrectionCategory,
  reason: string
): CorrectionDraft {
  const record = entityRecord(entity);
  const collection = entityCollection(entity);
  const scope = requiredObjectOrEmpty(record.scope);
  const effectiveBoundary = requiredObjectOrEmpty(
    record.effectiveBoundary
  );
  const effectiveSourceSpan = requiredObjectOrEmpty(
    effectiveBoundary.sourceSpan
  );
  const sourceSpan =
    Object.keys(effectiveSourceSpan).length === 0
      ? requiredObjectOrEmpty(record.sourceSpan)
      : effectiveSourceSpan;
  return {
    category,
    primary:
      category === "structure_boundary"
        ? numberRecordValue(sourceSpan, "startOffset")?.toString() ?? "0"
        : defaultCorrectionValue(entity, category),
    secondary:
      category === "structure_boundary"
        ? numberRecordValue(sourceSpan, "endOffset")?.toString() ?? ""
        : category === "character_identity"
          ? stringRecordValue(record, "identityStatus") ?? "resolved"
          : category === "point_of_view"
            ? stringRecordValue(record, "viewpointCharacterId") ?? ""
            : category === "location_identity"
              ? stringRecordValue(record, "parentLocationId") ?? ""
              : category === "temporal_order"
                ? record.approximate === true
                  ? "true"
                  : "false"
                : category === "relationship"
                  ? stringRecordValue(record, "state") ?? ""
                  : category === "emotional_state"
                    ? numberRecordValue(record, "intensity")?.toString() ??
                      "0.5"
                    : category === "dramatic_intent"
                      ? stringRecordValue(record, "status") ?? "pursued"
                      : "",
    tertiary:
      category === "point_of_view"
        ? stringRecordValue(record, "narratorCharacterId") ?? ""
        : category === "temporal_order"
          ? stringRecordValue(record, "status") ?? "unresolved"
          : category === "relationship"
            ? stringRecordValue(record, "change") ?? "unchanged"
            : category === "emotional_state"
              ? numberRecordValue(record, "valence")?.toString() ?? "0"
              : category === "dramatic_intent"
                ? stringRecordValue(record, "targetCharacterId") ?? ""
                : "",
    operation:
      category === "structure_boundary"
        ? stringRecordValue(effectiveBoundary, "operation") ?? "move"
        : "",
    parentEntityId:
      category === "structure_boundary"
        ? stringRecordValue(effectiveBoundary, "parentEntityId") ??
          (collection === "scenes"
            ? stringRecordValue(record, "chapterId") ?? ""
            : "")
        : "",
    ordinal:
      category === "structure_boundary"
        ? (
            numberRecordValue(effectiveBoundary, "ordinal") ??
            numberRecordValue(record, "ordinal") ??
            0
          ).toString()
        : "",
    boundaryKind:
      category === "structure_boundary"
        ? stringRecordValue(record, "boundaryKind") ?? "inferred"
        : "",
    sourceCharacterId:
      category === "relationship"
        ? stringRecordValue(record, "sourceCharacterId") ?? ""
        : "",
    targetCharacterId:
      category === "relationship"
        ? stringRecordValue(record, "targetCharacterId") ?? ""
        : "",
    scopeKind:
      category === "relationship"
        ? stringRecordValue(scope, "kind") ?? "scene"
        : "",
    scopeFirstSceneId:
      category === "relationship"
        ? stringRecordValue(scope, "firstSceneId") ?? ""
        : "",
    scopeLastSceneId:
      category === "relationship"
        ? stringRecordValue(scope, "lastSceneId") ?? ""
        : "",
    supersedeLatest: false,
    reason
  };
}

function defaultCorrectionValue(
  entity: AnalysisEntity,
  category: AnalysisCorrectionCategory
): string {
  const record = entityRecord(entity);
  switch (category) {
    case "structure_boundary":
      return numberRecordValue(record.sourceSpan, "startOffset")?.toString() ?? "0";
    case "structure_label":
      return (
        stringRecordValue(record, "title") ??
        stringRecordValue(record, "heading") ??
        stringRecordValue(record, "summary") ??
        ""
      );
    case "character_identity":
      return stringRecordValue(record, "canonicalName") ?? "";
    case "character_alias":
    case "character_merge":
    case "character_split":
    case "mention_resolution":
    case "dialogue_speaker":
      return "";
    case "point_of_view":
      return stringRecordValue(record, "mode") ?? "unknown";
    case "location_identity":
      return stringRecordValue(record, "canonicalName") ?? "";
    case "location_alias":
      return "";
    case "temporal_order":
      return stringRecordValue(record, "relation") ?? "unknown";
    case "relationship":
      return stringRecordValue(record, "kind") ?? "unknown";
    case "emotional_state":
      return stringRecordValue(record, "emotion") ?? "";
    case "dramatic_intent":
      return stringRecordValue(record, "intent") ?? "";
    case "continuity_disposition":
      return "confirmed_issue";
  }
}

function correctionFieldLabels(
  category: AnalysisCorrectionCategory
): readonly [string, string?, string?] {
  switch (category) {
    case "structure_boundary":
      return ["Start offset", "End offset"];
    case "structure_label":
      return ["Corrected label"];
    case "character_identity":
      return ["Canonical name", "Identity status"];
    case "character_alias":
      return ["Alias", "Operation (add/remove)"];
    case "character_merge":
      return ["Merge into character"];
    case "character_split":
      return ["New canonical name", "Mention IDs (comma separated)"];
    case "mention_resolution":
      return ["Resolved character"];
    case "dialogue_speaker":
      return ["Effective speaker"];
    case "point_of_view":
      return [
        "POV mode",
        "Viewpoint character ID",
        "Narrator character ID"
      ];
    case "location_identity":
      return ["Canonical location", "Parent location ID"];
    case "location_alias":
      return ["Location alias", "Operation (add/remove)"];
    case "temporal_order":
      return ["Relation", "Approximate (true/false)", "Status"];
    case "relationship":
      return ["Relationship kind", "State", "Change"];
    case "emotional_state":
      return ["Emotion", "Intensity", "Valence"];
    case "dramatic_intent":
      return ["Intent", "Status", "Target character ID"];
    case "continuity_disposition":
      return ["Disposition", "Explanation"];
  }
}

function correctionSelection(
  entity: CorrectableAnalysisEntity,
  draft: CorrectionDraft,
  storyId: string
): AnalysisCorrectionRequestSelection {
  const collection = entityCollection(entity);
  if (!correctionCategories(collection).includes(draft.category)) {
    throw new Error("Unsupported correction target");
  }
  const record = entityRecord(entity);
  const primary = draft.primary.trim();
  const secondary = draft.secondary.trim();
  const tertiary = draft.tertiary.trim();
  let patch: unknown;
  switch (draft.category) {
    case "structure_boundary": {
      const startOffset = strictInteger(primary);
      const endOffset = strictInteger(secondary);
      if (endOffset <= startOffset) {
        throw new Error("Invalid boundary");
      }
      const selectedSource = sourceSpanValue(
        requiredObjectOrEmpty(record.effectiveBoundary).sourceSpan ??
          record.sourceSpan
      );
      patch = {
        operation: draft.operation,
        parentEntityId:
          draft.parentEntityId.trim() ||
          (collection === "scenes"
            ? requiredIdentifier(record.chapterId, "chapterId")
            : storyId),
        ordinal: requiredNonNegativeInteger(
          strictInteger(draft.ordinal),
          "structure ordinal"
        ),
        sourceSpan: {
          sourceDocumentId: selectedSource.sourceDocumentId,
          extractionId: selectedSource.extractionId,
          extractionRevision: selectedSource.extractionRevision,
          offsetUnit: selectedSource.offsetUnit,
          startOffset,
          endOffset
        },
        ...(collection === "scenes"
          ? {
              boundaryKind: draft.boundaryKind
            }
          : {})
      };
      break;
    }
    case "structure_label":
      patch =
        collection === "chapters"
          ? { title: primary || null }
          : { heading: primary || null };
      break;
    case "character_identity":
      patch = {
        canonicalName: primary,
        normalizedCanonicalName: normalizeCorrectionText(primary),
        identityStatus: secondary || "resolved"
      };
      break;
    case "character_alias":
      patch =
        secondary === "remove"
          ? { operation: "remove", aliasId: primary }
          : {
              operation: secondary === "replace" ? "replace" : "add",
              alias: newCharacterAlias(record, primary)
            };
      break;
    case "character_merge":
      patch = { mergeIntoCharacterId: primary };
      break;
    case "character_split":
      patch = {
        newRegistryCharacterId: crypto.randomUUID(),
        canonicalName: primary,
        normalizedCanonicalName: normalizeCorrectionText(primary),
        mentionIds: identifierTextList(secondary)
      };
      break;
    case "mention_resolution":
      patch = {
        resolution: primary.length === 0 ? "unresolved" : "resolved",
        effectiveCharacterId: primary || null,
        candidateCharacterIds: mergeIdentifier(
          identifierArray(record.candidateCharacterIds),
          primary || null
        )
      };
      break;
    case "dialogue_speaker":
      patch = {
        speakerCharacterId: primary || null,
        selectedCandidateId: dialogueCandidateId(
          record.candidates,
          primary
        ),
        requiresHumanReview: primary.length === 0
      };
      break;
    case "point_of_view":
      patch = {
        mode: primary,
        viewpointCharacterId: secondary || null,
        narratorCharacterId:
          tertiary ||
          nullableIdentifier(
            record.narratorCharacterId,
            "narratorCharacterId"
          )
      };
      break;
    case "location_identity":
      patch = {
        canonicalName: primary,
        normalizedCanonicalName: normalizeCorrectionText(primary),
        kind: requiredString(record.kind, "location kind"),
        parentLocationId:
          secondary ||
          nullableIdentifier(record.parentLocationId, "parentLocationId")
      };
      break;
    case "location_alias":
      patch = {
        operation: secondary === "remove" ? "remove" : "add",
        alias: primary
      };
      break;
    case "temporal_order":
      patch = {
        relation: primary,
        approximate:
          secondary.length === 0
            ? requiredBoolean(record.approximate, "approximate")
            : parseBooleanText(secondary),
        status:
          tertiary || requiredString(record.status, "temporal status")
      };
      break;
    case "relationship": {
      const scope = requiredObject(record.scope, "relationship scope");
      patch = {
        sourceCharacterId: requiredIdentifier(
          draft.sourceCharacterId,
          "sourceCharacterId"
        ),
        targetCharacterId: requiredIdentifier(
          draft.targetCharacterId,
          "targetCharacterId"
        ),
        kind: primary,
        state: secondary,
        change: tertiary || "unchanged",
        scope: {
          kind: draft.scopeKind,
          firstSceneId: requiredIdentifier(
            draft.scopeFirstSceneId,
            "scope firstSceneId"
          ),
          lastSceneId: requiredIdentifier(
            draft.scopeLastSceneId,
            "scope lastSceneId"
          ),
          sourceRange: requiredObject(
            scope.sourceRange,
            "relationship sourceRange"
          )
        },
        validFromEventId: nullableIdentifier(
          record.validFromEventId,
          "validFromEventId"
        ),
        validThroughEventId: nullableIdentifier(
          record.validThroughEventId,
          "validThroughEventId"
        )
      };
      break;
    }
    case "emotional_state":
      patch = {
        emotion: primary,
        customEmotion:
          primary === "custom"
            ? nullableText(record.customEmotion) ?? "Custom"
            : null,
        note: nullableText(record.note) ?? "",
        valence: strictSignedUnitInterval(tertiary || "0"),
        arousal: requiredUnitInterval(record.arousal, "arousal"),
        intensity: strictUnitInterval(secondary || "0.5"),
        progression: requiredString(
          record.progression,
          "emotional progression"
        )
      };
      break;
    case "dramatic_intent":
      patch = {
        intent: primary,
        customIntent:
          primary === "custom"
            ? nullableText(record.customIntent) ?? "Custom"
            : null,
        dramaticFunction: requiredString(
          record.dramaticFunction,
          "dramaticFunction"
        ),
        customDramaticFunction: nullableText(
          record.customDramaticFunction
        ),
        note: nullableText(record.note) ?? "",
        targetCharacterId: tertiary || null,
        status: secondary || "pursued"
      };
      break;
    case "continuity_disposition":
      patch = {
        disposition: primary,
        explanation: secondary || draft.reason.trim()
      };
      break;
  }
  return {
    category: draft.category,
    targetCollection: collection,
    patch
  } as unknown as AnalysisCorrectionRequestSelection;
}

function sectionCollections(
  section: AnalysisSection,
  wholeBookCollection: (typeof wholeBookCollections)[number]
): readonly AnalysisCollection[] {
  switch (section) {
    case "overview":
      return ["agent-executions"];
    case "structure":
      return ["chapters", "scenes", "beats"];
    case "characters":
      return ["characters", "mentions"];
    case "dialogue":
      return ["characters", "dialogue-lines", "narration-spans"];
    case "whole-book":
      return ["characters", wholeBookCollection];
    case "corrections":
      return [];
  }
}

function pageItems(
  pages: Partial<Record<AnalysisCollection, PageState>>,
  collection: AnalysisCollection
): readonly AnalysisEntity[] {
  return pages[collection]?.items ?? [];
}

function isEffectivelyIncluded(entity: AnalysisEntity): boolean {
  const boundary = entityRecord(entity).effectiveBoundary;
  if (
    boundary === null ||
    typeof boundary !== "object" ||
    Array.isArray(boundary)
  ) {
    return true;
  }
  return (boundary as Readonly<Record<string, unknown>>).included !== false;
}

function withoutPage(
  pages: Partial<Record<AnalysisCollection, PageState>>,
  collection: AnalysisCollection
): Partial<Record<AnalysisCollection, PageState>> {
  const next = { ...pages };
  delete next[collection];
  return next;
}

function entityCollection(entity: AnalysisEntity): AnalysisCollection {
  const record = entityRecord(entity);
  if ("executionId" in record) {
    return "agent-executions";
  }
  if ("chapterId" in record && !("sceneId" in record)) {
    return "chapters";
  }
  if ("sceneId" in record && "boundaryKind" in record) {
    return "scenes";
  }
  if ("beatId" in record && "kind" in record && "sourceSpan" in record) {
    return "beats";
  }
  if ("canonicalName" in record && "identityStatus" in record) {
    return "characters";
  }
  if ("mentionId" in record) {
    return "mentions";
  }
  if ("dialogueLineId" in record) {
    return "dialogue-lines";
  }
  if ("narrationSpanId" in record) {
    return "narration-spans";
  }
  if ("povSegmentId" in record) {
    return "pov-segments";
  }
  if ("locationId" in record && "sceneCount" in record) {
    return "locations";
  }
  if ("timelineEventId" in record) {
    return "timeline-events";
  }
  if ("temporalConstraintId" in record) {
    return "temporal-constraints";
  }
  if ("relationshipId" in record) {
    return "relationships";
  }
  if ("emotionalStateId" in record) {
    return "emotional-states";
  }
  if ("dramaticIntentId" in record) {
    return "dramatic-intents";
  }
  return "continuity-findings";
}

function entityPrimaryId(entity: AnalysisEntity): string {
  const record = entityRecord(entity);
  if (typeof record.entityId === "string") {
    return record.entityId;
  }
  for (const key of [
    "executionId",
    "chapterId",
    "sceneId",
    "beatId",
    "characterId",
    "mentionId",
    "dialogueLineId",
    "narrationSpanId",
    "povSegmentId",
    "locationId",
    "timelineEventId",
    "temporalConstraintId",
    "relationshipId",
    "emotionalStateId",
    "dramaticIntentId",
    "continuityFindingId"
  ]) {
    if (typeof record[key] === "string") {
      return record[key];
    }
  }
  const ordinal = typeof record.ordinal === "number" ? record.ordinal : 0;
  return `${entityCollection(entity)}-${ordinal}`;
}

function entityTitle(entity: AnalysisEntity): string {
  const record = entityRecord(entity);
  for (const key of [
    "canonicalName",
    "title",
    "heading",
    "label",
    "emotion",
    "intent",
    "explanation",
    "summary",
    "agentId",
    "classification",
    "kind"
  ]) {
    const value = stringRecordValue(record, key);
    if (value !== undefined && value.length > 0) {
      return truncate(value, 96);
    }
  }
  const exact = exactTextValue(record.exactText);
  if (exact !== null && exact.length > 0) {
    return truncate(exact, 96);
  }
  return `${collectionTitle(entityCollection(entity))} ${
    typeof record.ordinal === "number" ? record.ordinal + 1 : ""
  }`.trim();
}

function entityRecord(entity: AnalysisEntity): Record<string, unknown> {
  return entity as unknown as Record<string, unknown>;
}

function isCorrectableEntity(
  entity: AnalysisEntity
): entity is CorrectableAnalysisEntity {
  return "entityId" in entity;
}

function recordValue(entity: AnalysisEntity, key: string): unknown {
  return entityRecord(entity)[key];
}

function stringRecordValue(
  value: unknown,
  key: string
): string | undefined {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const item = (value as Record<string, unknown>)[key];
  return typeof item === "string" ? item : undefined;
}

function numberRecordValue(
  value: unknown,
  key: string
): number | undefined {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const item = (value as Record<string, unknown>)[key];
  return typeof item === "number" ? item : undefined;
}

function exactTextValue(value: unknown): string | null {
  return stringRecordValue(value, "exactText") ?? null;
}

function formatValue(
  value: unknown,
  characters: readonly AnalysisEntity[]
): string {
  if (value === null) {
    return "None";
  }
  if (typeof value === "string") {
    const character = characters.find(
      (candidate) =>
        entityPrimaryId(candidate) === value ||
        recordValue(candidate, "characterId") === value
    );
    return character === undefined ? value : entityTitle(character);
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.length === 0
      ? "None"
      : value.map((item) => formatValue(item, characters)).join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return "Unknown";
}

function formatIdentityReference(
  characterId: string,
  characters: readonly AnalysisEntity[]
): string {
  const label = formatValue(characterId, characters);
  return label === characterId ? characterId : `${label} (${characterId})`;
}

function formatEffectiveSourceSpan(
  boundary: HumanEffectiveBoundary
): string {
  const span = boundary.sourceSpan;
  return `${span.sourceDocumentId} / ${span.extractionId} r${span.extractionRevision} / Unicode code points ${span.startOffset}\u2013${span.endOffset}`;
}

function latestEntityCorrection(
  corrections: readonly AnalysisCorrection[],
  targetEntityId: string
): AnalysisCorrection | undefined {
  return corrections
    .filter((item) => item.targetEntityId === targetEntityId)
    .sort((left, right) => right.recordedAt.localeCompare(left.recordedAt))[0];
}

function sourceSpanValue(value: unknown) {
  const record = requiredObject(value, "source span");
  return {
    sourceDocumentId: requiredIdentifier(
      record.sourceDocumentId,
      "sourceDocumentId"
    ),
    extractionId: requiredIdentifier(record.extractionId, "extractionId"),
    extractionRevision: requiredPositiveInteger(
      record.extractionRevision,
      "extractionRevision"
    ),
    offsetUnit: requiredExactString(
      record.offsetUnit,
      "unicode-code-point",
      "offsetUnit"
    ),
    startOffset: requiredNonNegativeInteger(
      record.startOffset,
      "startOffset"
    ),
    endOffset: requiredPositiveInteger(record.endOffset, "endOffset"),
    textSha256: requiredSha256(record.textSha256, "textSha256")
  } as const;
}

function newCharacterAlias(
  character: Record<string, unknown>,
  alias: string
) {
  const evidence = objectArray(
    Array.isArray(character.firstEvidence)
      ? character.firstEvidence
      : character.evidence
  );
  const sourceEvidence = evidence[0];
  if (sourceEvidence === undefined) {
    throw new Error("Character alias requires source evidence");
  }
  return {
    aliasId: crypto.randomUUID(),
    characterId: requiredIdentifier(character.characterId, "characterId"),
    alias,
    normalizedAlias: normalizeCorrectionText(alias),
    kind: "other",
    ambiguous: false,
    effectiveRange: {
      sourceRange: sourceSpanValue(sourceEvidence),
      validFromEventId: null,
      validThroughEventId: null
    },
    change: "introduced",
    confidence: requiredObject(character.confidence, "confidence"),
    evidence
  } as const;
}

function normalizeCorrectionText(value: string): string {
  return value.normalize("NFKC").trim().toLocaleLowerCase("en-US");
}

function identifierTextList(value: string): readonly string[] {
  const identifiers = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  if (new Set(identifiers).size !== identifiers.length) {
    throw new Error("Duplicate identifiers");
  }
  return identifiers;
}

function identifierArray(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error("Expected identifier collection");
  }
  return value.map((item) => String(item));
}

function objectArray(
  value: unknown
): readonly Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    throw new Error("Expected object collection");
  }
  return value.map((item) => requiredObject(item, "collection item"));
}

function mergeIdentifier(
  identifiers: readonly string[],
  identifier: string | null
): readonly string[] {
  return identifier === null || identifiers.includes(identifier)
    ? identifiers
    : [...identifiers, identifier];
}

function dialogueCandidateId(
  value: unknown,
  characterId: string
): string | null {
  if (characterId.length === 0) {
    return null;
  }
  const candidate = objectArray(value).find(
    (item) => item.characterId === characterId
  );
  return candidate === undefined
    ? null
    : requiredIdentifier(candidate.candidateId, "candidateId");
}

function requiredObject(
  value: unknown,
  field: string
): Record<string, unknown> {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
  ) {
    throw new Error(`Expected ${field}`);
  }
  return value as Record<string, unknown>;
}

function requiredObjectOrEmpty(
  value: unknown
): Record<string, unknown> {
  return value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
    ? {}
    : (value as Record<string, unknown>);
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Expected ${field}`);
  }
  return value;
}

function requiredExactString<TValue extends string>(
  value: unknown,
  expected: TValue,
  field: string
): TValue {
  if (value !== expected) {
    throw new Error(`Expected ${field}`);
  }
  return expected;
}

function requiredIdentifier(value: unknown, field: string): string {
  const identifier = requiredString(value, field);
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(identifier)) {
    throw new Error(`Expected ${field}`);
  }
  return identifier;
}

function nullableIdentifier(
  value: unknown,
  field: string
): string | null {
  return value === null || value === undefined
    ? null
    : requiredIdentifier(value, field);
}

function nullableText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function requiredPositiveInteger(
  value: unknown,
  field: string
): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new Error(`Expected ${field}`);
  }
  return value as number;
}

function requiredNonNegativeInteger(
  value: unknown,
  field: string
): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new Error(`Expected ${field}`);
  }
  return value as number;
}

function requiredUnitInterval(value: unknown, field: string): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 1
  ) {
    throw new Error(`Expected ${field}`);
  }
  return value;
}

function requiredBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`Expected ${field}`);
  }
  return value;
}

function requiredSha256(value: unknown, field: string): string {
  const digest = requiredString(value, field);
  if (!/^[a-f0-9]{64}$/u.test(digest)) {
    throw new Error(`Expected ${field}`);
  }
  return digest;
}

function parseBooleanText(value: string): boolean {
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  throw new Error("Expected boolean");
}

function strictInteger(value: string): number {
  if (!/^-?\d+$/u.test(value)) {
    throw new Error("Expected integer");
  }
  const result = Number(value);
  if (!Number.isSafeInteger(result)) {
    throw new Error("Expected safe integer");
  }
  return result;
}

function strictUnitInterval(value: string): number {
  const result = Number(value);
  if (!Number.isFinite(result) || result < 0 || result > 1) {
    throw new Error("Expected unit interval");
  }
  return result;
}

function strictSignedUnitInterval(value: string): number {
  const result = Number(value);
  if (!Number.isFinite(result) || result < -1 || result > 1) {
    throw new Error("Expected signed unit interval");
  }
  return result;
}

function collectionTitle(collection: AnalysisCollection): string {
  const titles: Record<AnalysisCollection, string> = {
    "agent-executions": "Agent executions",
    chapters: "Chapters",
    scenes: "Scenes",
    beats: "Beats",
    characters: "Characters",
    mentions: "Character mentions",
    "dialogue-lines": "Dialogue lines",
    "narration-spans": "Narration spans",
    "pov-segments": "Point of view",
    locations: "Locations",
    "timeline-events": "Timeline events",
    "temporal-constraints": "Temporal constraints",
    relationships: "Character relationships",
    "emotional-states": "Emotional progression",
    "dramatic-intents": "Dramatic intent",
    "continuity-findings": "Continuity findings"
  };
  return titles[collection];
}

function gateTitle(gateId: AnalysisGateId): string {
  const titles: Record<AnalysisGateId, string> = {
    story_structure_review: "Story structure review",
    character_registry_review: "Character registry review",
    dialogue_attribution_review: "Dialogue attribution review",
    whole_book_analysis_review: "Whole-book analysis review"
  };
  return titles[gateId];
}

function decisionTitle(value: AnalysisGateDecisionAction): string {
  if (value === "approve") {
    return "approved";
  }
  if (value === "request_changes") {
    return "changes requested";
  }
  return "rejected";
}

function orderReviews(
  reviews: readonly AnalysisReview[]
): readonly AnalysisReview[] {
  return [...reviews].sort(
    (left, right) =>
      gateOrder.indexOf(left.gateId) - gateOrder.indexOf(right.gateId)
  );
}

function emptyGateText(): Readonly<Record<AnalysisGateId, string>> {
  return {
    story_structure_review: "",
    character_registry_review: "",
    dialogue_attribution_review: "",
    whole_book_analysis_review: ""
  };
}

function emptyGateBoolean(): Readonly<Record<AnalysisGateId, boolean>> {
  return {
    story_structure_review: false,
    character_registry_review: false,
    dialogue_attribution_review: false,
    whole_book_analysis_review: false
  };
}

function runRequestKey(run: AnalysisRun | null): string {
  if (run === null) {
    return "none";
  }
  const snapshot = run.currentSnapshot;
  return [
    run.runId,
    run.runFingerprint,
    snapshot?.snapshotId ?? "no-snapshot",
    snapshot?.snapshotFingerprint ?? "no-snapshot-fingerprint",
    snapshot?.correctionSetFingerprint ?? "no-corrections"
  ].join("\u0000");
}

function compareRevisionTime(
  left: { readonly revision: number; readonly updatedAt: string },
  right: { readonly revision: number; readonly updatedAt: string }
): number {
  const byTime = left.updatedAt.localeCompare(right.updatedAt);
  return byTime === 0 ? left.revision - right.revision : byTime;
}

function sentenceCase(value: string): string {
  return value
    .replace(/([a-z])([A-Z])/gu, "$1 $2")
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/^./u, (character) => character.toUpperCase());
}

function shortId(value: string): string {
  return value.length <= 14 ? value : `${value.slice(0, 8)}…${value.slice(-4)}`;
}

function truncate(value: string, maximum: number): string {
  return value.length <= maximum
    ? value
    : `${value.slice(0, maximum - 1)}…`;
}
