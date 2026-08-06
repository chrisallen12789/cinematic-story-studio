import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import {
  CASTING_PROFILE_FINGERPRINT,
  CASTING_PROFILE_ID,
  type CastAssignment,
  type CastingCandidate,
  type CastingConflict,
  type CastingCorrectedValue,
  type CastingCorrection,
  type CastingCorrectionOperation,
  type CastingReview,
  type CastingRun,
  type CastingRunEvidenceInput,
  type ProductionRole,
  type VoiceCatalogResponse,
  type VoiceProfile,
  type VoiceRightsRecord
} from "../shared/casting-api";
import type {
  CinematicStoryDesktopApi,
  DesktopError,
  DesktopResult
} from "../shared/desktop-api";
import type { ProjectDetail } from "@cinematic-story-studio/contracts/api";

import "./casting.css";

interface CastingWorkspaceProps {
  readonly project: ProjectDetail;
  readonly api: CinematicStoryDesktopApi;
  readonly connected: boolean;
  readonly onNotice: (message: string | null) => void;
  readonly onError: (error: DesktopError | null) => void;
}

interface PageState<T> {
  readonly items: readonly T[];
  readonly total: number;
  readonly nextCursor?: string;
}

interface PrerequisiteView {
  readonly id: string;
  readonly label: string;
  readonly approved: boolean;
  readonly current: boolean;
  readonly decisionId: string | null;
  readonly fingerprint: string | null;
}

const emptyPage = <T,>(): PageState<T> => ({ items: [], total: 0 });
const reusableConflictCategories = new Set<CastingConflict["category"]>([
  "incompatible_voice_reuse",
  "narrator_major_character_reuse",
  "metadata_similarity_risk",
  "voice_reuse_threshold_exceeded"
]);

export function CastingWorkspace({
  project,
  api,
  connected,
  onNotice,
  onError
}: CastingWorkspaceProps) {
  const projectId = project.project.projectId;
  const generation = useRef(0);
  const selectedRoleIdRef = useRef<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<VoiceCatalogResponse | null>(null);
  const [runs, setRuns] = useState<PageState<CastingRun>>(emptyPage);
  const [run, setRun] = useState<CastingRun | null>(null);
  const [roles, setRoles] = useState<PageState<ProductionRole>>(emptyPage);
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);
  const [candidates, setCandidates] =
    useState<PageState<CastingCandidate>>(emptyPage);
  const [conflicts, setConflicts] =
    useState<PageState<CastingConflict>>(emptyPage);
  const [assignments, setAssignments] =
    useState<PageState<CastAssignment>>(emptyPage);
  const [corrections, setCorrections] =
    useState<PageState<CastingCorrection>>(emptyPage);
  const [reviews, setReviews] = useState<readonly CastingReview[]>([]);
  const [correctionReason, setCorrectionReason] = useState(
    "Reviewed declared voice metadata, rights, and role requirements."
  );
  const [roleLabel, setRoleLabel] = useState("");
  const [requirementLanguage, setRequirementLanguage] = useState("");
  const [customRationale, setCustomRationale] = useState("");
  const [customRoleDefinitionId, setCustomRoleDefinitionId] = useState(
    () => `custom-${crypto.randomUUID()}`
  );
  const [customRoleLabel, setCustomRoleLabel] = useState("");
  const [customRoleLanguage, setCustomRoleLanguage] = useState("en");
  const [customRoleLocales, setCustomRoleLocales] = useState("en-US");
  const [customRoleLongForm, setCustomRoleLongForm] = useState(false);
  const [customRoleReason, setCustomRoleReason] = useState(
    "Create an explicit content-free custom production role."
  );
  const [reviewRationales, setReviewRationales] = useState<
    Readonly<Record<string, string>>
  >({});
  const [acknowledgedReviews, setAcknowledgedReviews] = useState<
    Readonly<Record<string, boolean>>
  >({});

  const prerequisites = useMemo(
    () => phase2Prerequisites(project),
    [project]
  );
  const allPrerequisitesApproved = prerequisites.every(
    (item) => item.approved && item.current && item.decisionId !== null
  );
  const selectedRole =
    roles.items.find((item) => item.roleId === selectedRoleId) ?? null;
  const assignmentsByRole = useMemo(
    () =>
      new Map(
        assignments.items
          .filter((assignment) => assignment.effective)
          .map((assignment) => [assignment.roleId, assignment])
      ),
    [assignments.items]
  );
  const profilesById = useMemo(
    () =>
      new Map(
        (catalog?.items ?? []).map((profile) => [
          profile.voiceProfileId,
          profile
        ])
      ),
    [catalog?.items]
  );
  const rightsById = useMemo(
    () =>
      new Map(
        (catalog?.rights ?? []).map((record) => [
          record.rightsRecordId,
          record
        ])
      ),
    [catalog?.rights]
  );
  const narratorRoles = roles.items.filter(isNarratorRole);
  const characterRoles = roles.items.filter((item) => !isNarratorRole(item));
  const selectedAssignment =
    selectedRole === null
      ? null
      : assignmentsByRole.get(selectedRole.roleId) ?? null;
  const selectedProfile =
    selectedAssignment === null
      ? null
      : profilesById.get(selectedAssignment.voiceProfileId) ?? null;

  const reportError = useCallback(
    (caught: unknown) => {
      onError(toDesktopError(caught));
    },
    [onError]
  );

  const loadRunResources = useCallback(
    async (nextRun: CastingRun, requestGeneration: number) => {
      const evidence = runEvidence(nextRun);
      const [roleResult, conflictResult, assignmentResult, correctionResult] =
        await Promise.all([
          api.casting.listRoles({ ...evidence, limit: 50 }),
          api.casting.listConflicts({ ...evidence, limit: 50 }),
          api.casting.listAssignments({ ...evidence, limit: 200 }),
          api.casting.listCorrections({ ...evidence, limit: 50 })
        ]);
      const rolePage = unwrap(roleResult);
      const conflictPage = unwrap(conflictResult);
      const assignmentPage = unwrap(assignmentResult);
      const correctionPage = unwrap(correctionResult);
      let reviewItems: readonly CastingReview[] = [];
      if (nextRun.approvedCastSnapshot !== null) {
        reviewItems = unwrap(
          await api.casting.listReviews({
            ...evidence,
            expectedApprovedCastSnapshotId:
              nextRun.approvedCastSnapshot.snapshotId,
            expectedApprovedCastSnapshotRevision:
              nextRun.approvedCastSnapshot.revision
          })
        ).items;
      }
      if (requestGeneration !== generation.current) {
        return;
      }
      setRun(nextRun);
      setRoles(pageState(rolePage));
      setConflicts(pageState(conflictPage));
      setAssignments(pageState(assignmentPage));
      setCorrections(pageState(correctionPage));
      setReviews(reviewItems);
      const nextSelectedRole =
        rolePage.items.find(
          (item) => item.roleId === selectedRoleIdRef.current
        ) ??
        rolePage.items[0] ??
        null;
      selectedRoleIdRef.current = nextSelectedRole?.roleId ?? null;
      setSelectedRoleId(selectedRoleIdRef.current);
      setRoleLabel(nextSelectedRole?.effectiveDisplayLabel ?? "");
      setRequirementLanguage(
        nextSelectedRole?.performanceRequirements.language ?? ""
      );
      setCandidates(emptyPage());
    },
    [api]
  );

  const refreshRun = useCallback(
    async (runId: string, silent = false) => {
      const requestGeneration = generation.current;
      if (!silent) {
        setBusyAction("refresh-run");
      }
      try {
        const response = unwrap(
          await api.casting.getRun({ projectId, runId })
        );
        if (requestGeneration === generation.current) {
          await loadRunResources(response.run, requestGeneration);
        }
      } catch (caught) {
        if (requestGeneration === generation.current) {
          reportError(caught);
        }
      } finally {
        if (!silent && requestGeneration === generation.current) {
          setBusyAction(null);
        }
      }
    },
    [api, loadRunResources, projectId, reportError]
  );

  useEffect(() => {
    const requestGeneration = ++generation.current;
    void (async () => {
      try {
        const [catalogResponse, runResponse] = await Promise.all([
          api.casting.getCatalog({ projectId, limit: 50 }),
          api.casting.listRuns({ projectId, limit: 50 })
        ]);
        const nextCatalog = unwrap(catalogResponse);
        const nextRuns = unwrap(runResponse);
        if (requestGeneration !== generation.current) {
          return;
        }
        setCatalog(nextCatalog);
        setRuns(pageState(nextRuns));
        const firstRun = nextRuns.items[0];
        if (firstRun !== undefined) {
          const detail = unwrap(
            await api.casting.getRun({
              projectId,
              runId: firstRun.castingRunId
            })
          ).run;
          await loadRunResources(detail, requestGeneration);
        }
      } catch (caught) {
        if (requestGeneration === generation.current) {
          reportError(caught);
        }
      } finally {
        if (requestGeneration === generation.current) {
          setLoading(false);
        }
      }
    })();
    return () => {
      generation.current += 1;
    };
  }, [api, loadRunResources, projectId, reportError]);

  useEffect(() => {
    if (
      run === null ||
      !["queued", "running"].includes(run.status) ||
      !connected
    ) {
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      await refreshRun(run.castingRunId, true);
      if (!cancelled) {
        timer = window.setTimeout(() => void poll(), 1_500);
      }
    };
    timer = window.setTimeout(() => void poll(), 1_500);
    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [connected, refreshRun, run]);

  const createRun = async () => {
    const analysisRun = project.currentAnalysisRun;
    const snapshot = analysisRun?.currentSnapshot ?? null;
    if (
      !connected ||
      catalog === null ||
      analysisRun === null ||
      snapshot === null ||
      !allPrerequisitesApproved
    ) {
      return;
    }
    const decisions = approvedAnalysisDecisions(project, analysisRun.runId);
    const importDecision = latestApprovedImportDecision(project);
    if (decisions === null || importDecision === null) {
      return;
    }
    setBusyAction("create-run");
    onError(null);
    try {
      const created = unwrap(
        await api.casting.createRun({
          projectId,
          expectedAnalysisRunId: analysisRun.runId,
          expectedSnapshotId: snapshot.snapshotId,
          expectedSnapshotRevision: snapshot.revision,
          expectedSnapshotFingerprint: snapshot.snapshotFingerprint,
          expectedCorrectionSetFingerprint:
            snapshot.correctionSetFingerprint,
          expectedImportReviewDecisionId: importDecision,
          expectedAnalysisGateDecisionIds: decisions,
          expectedCatalogRevisionId:
            catalog.catalogRevision.catalogRevisionId,
          expectedCatalogFingerprint:
            catalog.catalogRevision.catalogFingerprint,
          expectedCastingProfileFingerprint: CASTING_PROFILE_FINGERPRINT,
          idempotencyKey: crypto.randomUUID()
        })
      );
      setRuns((current) => ({
        ...current,
        items: [
          created.run,
          ...current.items.filter(
            (item) => item.castingRunId !== created.run.castingRunId
          )
        ],
        total: Math.max(current.total, current.items.length + 1)
      }));
      await loadRunResources(created.run, generation.current);
      onNotice("Governed voice casting queued.");
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const loadCandidates = async (
    role: ProductionRole,
    append = false
  ) => {
    if (run === null) {
      return;
    }
    setBusyAction(`candidates-${role.roleId}`);
    try {
      const page = unwrap(
        await api.casting.listCandidates({
          ...runEvidence(run),
          roleId: role.roleId,
          expectedRoleRevision: role.revision,
          ...(append && candidates.nextCursor !== undefined
            ? { cursor: candidates.nextCursor }
            : {}),
          limit: 12
        })
      );
      setCandidates((current) =>
        append ? mergePage(current, page) : pageState(page)
      );
      selectedRoleIdRef.current = role.roleId;
      setSelectedRoleId(role.roleId);
      setRoleLabel(role.effectiveDisplayLabel);
      setRequirementLanguage(role.performanceRequirements.language);
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const loadMoreRoles = async () => {
    if (run === null || roles.nextCursor === undefined) {
      return;
    }
    setBusyAction("more-roles");
    try {
      const page = unwrap(
        await api.casting.listRoles({
          ...runEvidence(run),
          cursor: roles.nextCursor,
          limit: 50
        })
      );
      setRoles((current) => mergePage(current, page));
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const loadMoreRuns = async () => {
    if (runs.nextCursor === undefined) {
      return;
    }
    setBusyAction("more-runs");
    try {
      const page = unwrap(
        await api.casting.listRuns({
          projectId,
          cursor: runs.nextCursor,
          limit: 50
        })
      );
      setRuns((current) => mergePage(current, page));
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const loadMoreConflicts = async () => {
    if (run === null || conflicts.nextCursor === undefined) {
      return;
    }
    setBusyAction("more-conflicts");
    try {
      const page = unwrap(
        await api.casting.listConflicts({
          ...runEvidence(run),
          cursor: conflicts.nextCursor,
          limit: 50
        })
      );
      setConflicts((current) => mergePage(current, page));
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const loadMoreAssignments = async () => {
    if (run === null || assignments.nextCursor === undefined) {
      return;
    }
    setBusyAction("more-assignments");
    try {
      const page = unwrap(
        await api.casting.listAssignments({
          ...runEvidence(run),
          cursor: assignments.nextCursor,
          limit: 200
        })
      );
      setAssignments((current) => mergePage(current, page));
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const loadMoreCorrections = async () => {
    if (run === null || corrections.nextCursor === undefined) {
      return;
    }
    setBusyAction("more-corrections");
    try {
      const page = unwrap(
        await api.casting.listCorrections({
          ...runEvidence(run),
          cursor: corrections.nextCursor,
          limit: 50
        })
      );
      setCorrections((current) => mergePage(current, page));
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const loadMoreCatalog = async () => {
    if (catalog?.nextCursor === undefined) {
      return;
    }
    setBusyAction("more-catalog");
    try {
      const page = unwrap(
        await api.casting.getCatalog({
          projectId,
          expectedCatalogRevisionId:
            catalog.catalogRevision.catalogRevisionId,
          expectedCatalogFingerprint:
            catalog.catalogRevision.catalogFingerprint,
          cursor: catalog.nextCursor,
          limit: 50
        })
      );
      setCatalog((current) =>
        current === null
          ? page
          : {
              ...page,
              providers: dedupeBy(
                [...current.providers, ...page.providers],
                (item) => item.providerId
              ),
              models: dedupeBy(
                [...current.models, ...page.models],
                (item) => item.modelId
              ),
              rights: dedupeBy(
                [...current.rights, ...page.rights],
                (item) => item.rightsRecordId
              ),
              items: dedupeBy(
                [...current.items, ...page.items],
                (item) => item.voiceProfileId
              )
            }
      );
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const appendCorrection = async (
    role: ProductionRole,
    operation: CastingCorrectionOperation,
    correctedValue: CastingCorrectedValue,
    voiceProfileId: string | null
  ) => {
    if (run === null || correctionReason.trim().length === 0) {
      onError({
        code: "CASTING_REASON_REQUIRED",
        message: "Add a reason so the human casting decision remains auditable.",
        retryable: false
      });
      return;
    }
    setBusyAction(`correction-${operation}-${role.roleId}`);
    onError(null);
    const supersededCandidateRejection =
      operation === "select_voice" && voiceProfileId !== null
        ? candidates.items.find(
            (item) =>
              item.roleId === role.roleId &&
              item.voiceProfileId === voiceProfileId
          )?.rejectedByCorrectionId ?? null
        : null;
    try {
      const response = unwrap(
        await api.casting.appendCorrection({
          projectId,
          runId: run.castingRunId,
          operation,
          targetRoleId: role.roleId,
          expectedRoleRevision: role.revision,
          expectedRunFingerprint: runFingerprint(run),
          expectedCatalogFingerprint: run.catalogFingerprint,
          expectedSnapshotFingerprint:
            run.prerequisites.analysisSnapshotFingerprint,
          expectedCorrectionSetFingerprint:
            run.effectiveCorrectionSetFingerprint,
          previousEffectiveFingerprint: roleEffectiveFingerprint(role),
          voiceProfileId,
          correctedValue,
          reason: correctionReason.trim(),
          supersedesCorrectionId: supersededCandidateRejection,
          idempotencyKey: crypto.randomUUID()
        })
      );
      await loadRunResources(response.run, generation.current);
      onNotice(`${sentenceCase(operation)} saved as immutable human provenance.`);
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const createCustomRole = async () => {
    if (run === null || run.status !== "succeeded") {
      return;
    }
    const definitionId = customRoleDefinitionId.trim();
    const label = customRoleLabel.trim();
    const language = customRoleLanguage.trim();
    const locales = [
      ...new Set(
        customRoleLocales
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean)
      )
    ];
    const reason = customRoleReason.trim();
    if (
      definitionId.length === 0 ||
      label.length === 0 ||
      language.length === 0 ||
      locales.length === 0 ||
      reason.length === 0
    ) {
      onError({
        code: "CASTING_CUSTOM_ROLE_REQUIRED",
        message:
          "Definition ID, label, language, locale, and reason are required.",
        retryable: false
      });
      return;
    }
    setBusyAction("create-custom-role");
    onError(null);
    try {
      const response = unwrap(
        await api.casting.createCustomRole({
          ...runEvidence(run),
          definitionId,
          label,
          performanceRequirements: {
            language,
            locales,
            agePresentationRange: null,
            vocalPresentations: [],
            preferredTextures: [],
            speakingRateRange: null,
            requiredExpressiveRange: [],
            longFormRequired: customRoleLongForm
          },
          reason,
          expectedCorrectionSetFingerprint:
            run.effectiveCorrectionSetFingerprint,
          expectedCastingProfileFingerprint: run.profile.fingerprint,
          idempotencyKey: crypto.randomUUID()
        })
      );
      selectedRoleIdRef.current = response.role.roleId;
      await loadRunResources(response.run, generation.current);
      setCustomRoleDefinitionId(`custom-${crypto.randomUUID()}`);
      setCustomRoleLabel("");
      onNotice(
        "Custom role created with content-free provenance and fresh reviews."
      );
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const controlJob = async (
    action: "cancel" | "retry" | "resume"
  ) => {
    if (run === null) {
      return;
    }
    setBusyAction(`${action}-run`);
    try {
      unwrap(await api.jobs[action](run.jobId));
      await refreshRun(run.castingRunId, true);
      onNotice(`Casting job ${action} requested.`);
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const decideReview = async (
    review: CastingReview,
    decision: "approve" | "request_changes" | "reject"
  ) => {
    if (run === null || run.approvedCastSnapshot === null) {
      return;
    }
    const rationale = reviewRationales[review.gateId]?.trim() ?? "";
    if (rationale.length === 0) {
      onError({
        code: "CASTING_REVIEW_RATIONALE_REQUIRED",
        message: "Add a rationale before recording a casting review decision.",
        retryable: false
      });
      return;
    }
    if (
      decision === "approve" &&
      review.openWarningIds.length > 0 &&
      acknowledgedReviews[review.gateId] !== true
    ) {
      onError({
        code: "CASTING_WARNING_ACKNOWLEDGEMENT_REQUIRED",
        message: "Acknowledge the visible warnings before approving this gate.",
        retryable: false
      });
      return;
    }
    setBusyAction(`review-${review.gateId}-${decision}`);
    try {
      const response = unwrap(
        await api.casting.decideReview({
          projectId,
          runId: run.castingRunId,
          gateId: review.gateId,
          decision,
          expectedRevision: review.revision,
          expectedEvidenceFingerprint:
            review.evidence.evidenceFingerprint,
          expectedRunFingerprint: runFingerprint(run),
          expectedApprovedCastSnapshotId:
            run.approvedCastSnapshot.snapshotId,
          expectedApprovedCastSnapshotRevision:
            run.approvedCastSnapshot.revision,
          warningAcknowledgementIds:
            decision === "approve"
              ? review.openWarningIds
              : [],
          rationale,
          supersedesDecisionId:
            review.latestDecision?.decisionId ?? null,
          idempotencyKey: crypto.randomUUID()
        })
      );
      await loadRunResources(response.run, generation.current);
      onNotice(`${gateLabel(review.gateId)} decision recorded.`);
    } catch (caught) {
      reportError(caught);
    } finally {
      setBusyAction(null);
    }
  };

  const restrictedAcknowledged = (assignment: CastAssignment) =>
    corrections.items.some(
      (correction) =>
        correction.category === "acknowledge_restricted_rights" &&
        correction.targetRoleId === assignment.roleId &&
        correction.correctedValue.rightsRecordId ===
          assignment.rightsRecordId &&
        correction.correctedValue.rightsRecordRevision ===
          assignment.rightsRecordRevision
    );
  const ineligibleAssignments = assignments.items.filter(
    (assignment) =>
      assignment.effective &&
      (assignment.rightsState === "unknown" ||
        assignment.rightsState === "prohibited")
  );
  const unacknowledgedRestricted = assignments.items.filter(
    (assignment) =>
      assignment.effective &&
      assignment.rightsState === "restricted" &&
      !restrictedAcknowledged(assignment)
  );
  const blockingConflicts = conflicts.items.filter(
    (conflict) =>
      (conflict.severity === "blocker" ||
        conflict.severity === "error") &&
      conflict.resolutionState === "open"
  );
  const narratorApproved = reviews.some(
    (review) =>
      review.gateId === "narrator_casting_review" &&
      review.state === "approved"
  );
  const characterApproved = reviews.some(
    (review) =>
      review.gateId === "character_casting_review" &&
      review.state === "approved"
  );
  const governanceEvidenceIncomplete =
    roles.nextCursor !== undefined ||
    conflicts.nextCursor !== undefined ||
    assignments.nextCursor !== undefined ||
    corrections.nextCursor !== undefined;
  const finalCastBlocked =
    ineligibleAssignments.length > 0 ||
    unacknowledgedRestricted.length > 0 ||
    blockingConflicts.length > 0 ||
    governanceEvidenceIncomplete;

  if (loading) {
    return (
      <section className="casting-workspace casting-loading" aria-busy="true">
        <span className="casting-spinner" aria-hidden="true" />
        <div>
          <h2>Loading governed casting evidence</h2>
          <p>Catalog, roles, assignments, rights, and review state are bounded.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="casting-workspace" aria-label="Casting workspace">
      <header className="casting-hero">
        <div>
          <span className="eyebrow">Phase 3A · governed voice casting</span>
          <h2>Casting workspace</h2>
          <p>
            Provider-neutral voice metadata, durable author decisions, and
            rights-safe approval. Compatibility is explainable guidance, not
            artistic correctness.
          </p>
        </div>
        <div className="casting-hero-actions">
          {run !== null && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => void refreshRun(run.castingRunId)}
              disabled={!connected || busyAction !== null}
            >
              Refresh evidence
            </button>
          )}
          <button
            type="button"
            onClick={() => void createRun()}
            disabled={
              !connected ||
              !allPrerequisitesApproved ||
              catalog === null ||
              busyAction !== null
            }
          >
            {run === null ? "Start casting analysis" : "Run again"}
          </button>
        </div>
      </header>

      <section className="casting-prerequisites" aria-label="Phase 2 prerequisites">
        <div className="casting-section-heading">
          <div>
            <span className="eyebrow">Frozen entry criteria</span>
            <h3>Phase 2 prerequisite status</h3>
          </div>
          <span
            className={
              allPrerequisitesApproved
                ? "casting-status good"
                : "casting-status blocked"
            }
          >
            {allPrerequisitesApproved ? "Current and approved" : "Blocked"}
          </span>
        </div>
        <div className="prerequisite-grid">
          {prerequisites.map((item) => (
            <article className="prerequisite-card" key={item.id}>
              <span
                className={
                  item.approved && item.current
                    ? "evidence-dot approved"
                    : "evidence-dot"
                }
                aria-hidden="true"
              />
              <div>
                <strong>{item.label}</strong>
                <span>
                  {item.approved
                    ? item.current
                      ? "Approved · current"
                      : "Approved · stale"
                    : "Approval required"}
                </span>
                <code>{shortId(item.decisionId)}</code>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="casting-context-grid">
        <article className="casting-context-card">
          <span className="eyebrow">Deterministic profile</span>
          <h3>{CASTING_PROFILE_ID}</h3>
          <dl>
            <div>
              <dt>Fingerprint</dt>
              <dd><code>{shortHash(CASTING_PROFILE_FINGERPRINT)}</code></dd>
            </div>
            <div>
              <dt>Behavior</dt>
              <dd>Provider-neutral · offline · bounded to 12 candidates</dd>
            </div>
          </dl>
        </article>
        <article className="casting-context-card">
          <span className="eyebrow">Voice catalog</span>
          <h3>
            {catalog?.catalogRevision.semanticVersion ?? "Unavailable"}
          </h3>
          <dl>
            <div>
              <dt>Revision</dt>
              <dd>{catalog?.catalogRevision.catalogRevisionId ?? "—"}</dd>
            </div>
            <div>
              <dt>Loaded</dt>
              <dd>{catalog?.items.length ?? 0} of {catalog?.total ?? 0} profiles</dd>
            </div>
            <div>
              <dt>Fingerprint</dt>
              <dd><code>{shortHash(catalog?.catalogRevision.catalogFingerprint)}</code></dd>
            </div>
          </dl>
          {catalog?.nextCursor !== undefined && (
            <button
              type="button"
              className="text-button"
              onClick={() => void loadMoreCatalog()}
              disabled={busyAction !== null}
            >
              Load more voice profiles
            </button>
          )}
        </article>
        <article className="casting-context-card">
          <span className="eyebrow">Provider state</span>
          <div className="provider-state-list">
            {(catalog?.providers ?? []).map((provider) => (
              <div key={provider.providerId}>
                <strong>{provider.providerId}</strong>
                <span>
                  {sentenceCase(provider.healthStatus)} ·{" "}
                  {sentenceCase(provider.runtimeAvailability)}
                  {provider.networkUseRequired ? " · network declared" : " · offline"}
                </span>
              </div>
            ))}
          </div>
        </article>
      </section>

      {run === null ? (
        <section className="casting-empty">
          <span className="casting-empty-mark" aria-hidden="true">VC</span>
          <h3>No casting run yet</h3>
          <p>
            A run freezes the approved analysis snapshot, exact catalog
            revision, governed profile, and effective correction set.
          </p>
          {!allPrerequisitesApproved && (
            <strong>Approve every Phase 2 prerequisite to continue.</strong>
          )}
        </section>
      ) : (
        <>
          <section className="casting-runbar" aria-label="Casting run controls">
            <div>
              <span className={`run-state ${run.status}`}>
                {sentenceCase(run.status)}
              </span>
              <strong>{run.castingRunId}</strong>
              <span>
                {sentenceCase(run.currentStage)} · {Math.round(run.progress * 100)}%
              </span>
            </div>
            <label>
              Run history
              <select
                value={run.castingRunId}
                onChange={(event) => void refreshRun(event.target.value)}
                disabled={busyAction !== null}
              >
                {runs.items.map((item) => (
                  <option value={item.castingRunId} key={item.castingRunId}>
                    {item.castingRunId} · {sentenceCase(item.status)}
                  </option>
                ))}
              </select>
            </label>
            {runs.nextCursor !== undefined && (
              <button
                type="button"
                className="secondary-button"
                onClick={() => void loadMoreRuns()}
                disabled={busyAction !== null}
              >
                Load more runs
              </button>
            )}
            <div className="casting-job-actions">
              {["queued", "running"].includes(run.status) && (
                <button
                  type="button"
                  className="danger-button"
                  onClick={() => void controlJob("cancel")}
                  disabled={busyAction !== null}
                >
                  Cancel
                </button>
              )}
              {["failed", "cancelled"].includes(run.status) && (
                <button
                  type="button"
                  onClick={() => void controlJob("retry")}
                  disabled={busyAction !== null}
                >
                  Retry
                </button>
              )}
              {run.status === "interrupted" && (
                <button
                  type="button"
                  onClick={() => void controlJob("resume")}
                  disabled={busyAction !== null}
                >
                  Resume
                </button>
              )}
            </div>
          </section>

          <section className="casting-stat-strip" aria-label="Casting run counts">
            <Stat label="Roles" value={run.summary?.productionRoles ?? roles.total} />
            <Stat label="Narrators" value={run.summary?.narratorRoles ?? narratorRoles.length} />
            <Stat label="Characters" value={run.summary?.characterRoles ?? characterRoles.length} />
            <Stat label="Candidates" value={run.summary?.finalCandidates ?? 0} />
            <Stat label="Conflicts" value={run.summary?.conflicts ?? conflicts.total} />
            <Stat label="Corrections" value={run.summary?.corrections ?? corrections.total} />
          </section>

          <div className="casting-board">
            <aside className="role-browser" aria-label="Production roles">
              <div className="casting-section-heading compact">
                <div>
                  <span className="eyebrow">Production roles</span>
                  <h3>Role workload</h3>
                </div>
                <span>{roles.items.length}/{roles.total}</span>
              </div>
              <form
                className="custom-role-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void createCustomRole();
                }}
              >
                <div>
                  <strong>Create custom role</strong>
                  <small>
                    Explicit and content-free; no manuscript material is
                    inferred.
                  </small>
                </div>
                <label>
                  Custom role definition ID
                  <input
                    value={customRoleDefinitionId}
                    onChange={(event) =>
                      setCustomRoleDefinitionId(event.target.value)
                    }
                    maxLength={160}
                  />
                </label>
                <label>
                  Custom role label
                  <input
                    value={customRoleLabel}
                    onChange={(event) =>
                      setCustomRoleLabel(event.target.value)
                    }
                    maxLength={200}
                    placeholder="Example: Station announcement"
                  />
                </label>
                <div className="custom-role-requirements">
                  <label>
                    Language
                    <input
                      value={customRoleLanguage}
                      onChange={(event) =>
                        setCustomRoleLanguage(event.target.value)
                      }
                      maxLength={35}
                    />
                  </label>
                  <label>
                    Locales
                    <input
                      value={customRoleLocales}
                      onChange={(event) =>
                        setCustomRoleLocales(event.target.value)
                      }
                      maxLength={200}
                      placeholder="en-US, en-GB"
                    />
                  </label>
                </div>
                <label className="custom-role-checkbox">
                  <input
                    type="checkbox"
                    checked={customRoleLongForm}
                    onChange={(event) =>
                      setCustomRoleLongForm(event.target.checked)
                    }
                  />
                  Require long-form suitability
                </label>
                <label>
                  Custom role reason
                  <textarea
                    value={customRoleReason}
                    onChange={(event) =>
                      setCustomRoleReason(event.target.value)
                    }
                    maxLength={1_000}
                  />
                </label>
                <button
                  type="submit"
                  disabled={busyAction !== null || run.status !== "succeeded"}
                >
                  Create custom role
                </button>
              </form>
              <RoleGroup
                title="Narrators"
                roles={narratorRoles}
                selectedRoleId={selectedRoleId}
                assignments={assignmentsByRole}
                busy={busyAction !== null}
                onSelect={(role) => void loadCandidates(role)}
              />
              <RoleGroup
                title="Characters and special roles"
                roles={characterRoles}
                selectedRoleId={selectedRoleId}
                assignments={assignmentsByRole}
                busy={busyAction !== null}
                onSelect={(role) => void loadCandidates(role)}
              />
              {roles.nextCursor !== undefined && (
                <button
                  type="button"
                  className="load-more"
                  onClick={() => void loadMoreRoles()}
                  disabled={busyAction !== null}
                >
                  Load more roles
                </button>
              )}
            </aside>

            <main className="role-inspector">
              {selectedRole === null ? (
                <div className="casting-empty compact">
                  <h3>Select a production role</h3>
                  <p>Candidate pages load only for the selected role.</p>
                </div>
              ) : (
                <>
                  <RoleHeader
                    role={selectedRole}
                    assignment={selectedAssignment}
                    profile={selectedProfile}
                  />
                  <section className="correction-panel">
                    <label>
                      Human decision reason
                      <textarea
                        value={correctionReason}
                        onChange={(event) =>
                          setCorrectionReason(event.target.value)
                        }
                        maxLength={1_000}
                      />
                    </label>
                    <div className="correction-actions">
                      {selectedAssignment !== null && (
                        <>
                          <button
                            type="button"
                            onClick={() =>
                              void appendCorrection(
                                selectedRole,
                                "clear_assignment",
                                {
                                  expectedAssignmentId:
                                    selectedAssignment.assignmentId
                                },
                                null
                              )
                            }
                            disabled={busyAction !== null}
                          >
                            Clear assignment
                          </button>
                          {selectedAssignment.authority === "human_locked" ? (
                            <button
                              type="button"
                              onClick={() =>
                                void appendCorrection(
                                  selectedRole,
                                  "unlock_assignment",
                                  {
                                    lockedAssignmentId:
                                      selectedAssignment.assignmentId
                                  },
                                  null
                                )
                              }
                              disabled={busyAction !== null}
                            >
                              Unlock by superseding
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={() =>
                                void appendCorrection(
                                  selectedRole,
                                  "lock_assignment",
                                  {
                                    assignmentId:
                                      selectedAssignment.assignmentId
                                  },
                                  null
                                )
                              }
                              disabled={busyAction !== null}
                            >
                              Lock assignment
                            </button>
                          )}
                          {selectedAssignment.rightsState === "restricted" &&
                            !restrictedAcknowledged(selectedAssignment) && (
                              <button
                                type="button"
                                className="warning-button"
                                onClick={() =>
                                  void appendCorrection(
                                    selectedRole,
                                    "acknowledge_restricted_rights",
                                    {
                                      rightsRecordId:
                                        selectedAssignment.rightsRecordId,
                                      rightsRecordRevision:
                                        selectedAssignment.rightsRecordRevision
                                    },
                                    selectedAssignment.voiceProfileId
                                  )
                                }
                                disabled={busyAction !== null}
                              >
                                Acknowledge restricted rights
                              </button>
                            )}
                        </>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          void appendCorrection(
                            selectedRole,
                            "mark_intentionally_uncast",
                            { intentionallyUncast: true },
                            null
                          )
                        }
                        disabled={busyAction !== null}
                      >
                        Mark intentionally uncast
                      </button>
                    </div>
                  </section>

                  <section className="role-edit-grid">
                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        void appendCorrection(
                          selectedRole,
                          "change_role_label",
                          { effectiveDisplayLabel: roleLabel.trim() },
                          null
                        );
                      }}
                    >
                      <label>
                        Effective role label
                        <input
                          value={roleLabel}
                          onChange={(event) => setRoleLabel(event.target.value)}
                          maxLength={200}
                        />
                      </label>
                      <button
                        type="submit"
                        disabled={
                          roleLabel.trim().length === 0 ||
                          roleLabel.trim() ===
                            selectedRole.effectiveDisplayLabel ||
                          busyAction !== null
                        }
                      >
                        Save label
                      </button>
                    </form>
                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        void appendCorrection(
                          selectedRole,
                          "change_casting_requirement",
                          {
                            requirement: {
                              ...selectedRole.performanceRequirements,
                              language: requirementLanguage.trim()
                            }
                          },
                          null
                        );
                      }}
                    >
                      <label>
                        Required language
                        <input
                          value={requirementLanguage}
                          onChange={(event) =>
                            setRequirementLanguage(event.target.value)
                          }
                          maxLength={40}
                        />
                      </label>
                      <button
                        type="submit"
                        disabled={
                          requirementLanguage.trim().length === 0 ||
                          requirementLanguage.trim() ===
                            selectedRole.performanceRequirements.language ||
                          busyAction !== null
                        }
                      >
                        Save requirement
                      </button>
                    </form>
                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        void appendCorrection(
                          selectedRole,
                          "record_custom_rationale",
                          { rationale: customRationale.trim() },
                          null
                        );
                      }}
                    >
                      <label>
                        Custom casting rationale
                        <input
                          value={customRationale}
                          onChange={(event) =>
                            setCustomRationale(event.target.value)
                          }
                          maxLength={2_000}
                        />
                      </label>
                      <button
                        type="submit"
                        disabled={
                          customRationale.trim().length === 0 ||
                          busyAction !== null
                        }
                      >
                        Record rationale
                      </button>
                    </form>
                  </section>

                  <section className="candidate-section" aria-label="Candidate voices">
                    <div className="casting-section-heading compact">
                      <div>
                        <span className="eyebrow">Bounded candidates</span>
                        <h3>Explainable compatibility</h3>
                      </div>
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => void loadCandidates(selectedRole)}
                        disabled={busyAction !== null}
                      >
                        Load candidates
                      </button>
                    </div>
                    {candidates.items.length === 0 ? (
                      <div className="casting-empty compact">
                        <p>Load this role’s bounded candidate page.</p>
                      </div>
                    ) : (
                      <div className="candidate-list">
                        {candidates.items.map((candidate) => {
                          const profile = profilesById.get(
                            candidate.voiceProfileId
                          );
                          const rightsRecord =
                            profile === undefined
                              ? undefined
                              : rightsById.get(profile.rightsRecordId);
                          return (
                            <CandidateCard
                              key={candidate.candidateId}
                              candidate={candidate}
                              profile={profile}
                              rights={rightsRecord}
                              busy={busyAction !== null}
                              onSelect={() =>
                                void appendCorrection(
                                  selectedRole,
                                  "select_voice",
                                  {
                                    voiceProfileId:
                                      candidate.voiceProfileId
                                  },
                                  candidate.voiceProfileId
                                )
                              }
                              onReject={() =>
                                void appendCorrection(
                                  selectedRole,
                                  "reject_candidate",
                                  {
                                    candidateId:
                                      candidate.candidateId
                                  },
                                  candidate.voiceProfileId
                                )
                              }
                            />
                          );
                        })}
                      </div>
                    )}
                    {candidates.nextCursor !== undefined && (
                      <button
                        type="button"
                        className="load-more"
                        onClick={() =>
                          void loadCandidates(selectedRole, true)
                        }
                        disabled={busyAction !== null}
                      >
                        Load more candidates
                      </button>
                    )}
                  </section>
                </>
              )}
            </main>
          </div>

          <section className="governance-grid">
            <article className="governance-panel">
              <div className="casting-section-heading compact">
                <div>
                  <span className="eyebrow">Metadata only</span>
                  <h3>Cast conflicts</h3>
                </div>
                <span>{conflicts.total}</span>
              </div>
              {conflicts.items.length === 0 ? (
                <p className="quiet">No metadata conflicts published.</p>
              ) : (
                <div className="governance-list">
                  {conflicts.items.map((conflict) => (
                    <div
                      className="conflict-row"
                      data-conflict-id={conflict.conflictId}
                      key={conflict.conflictId}
                    >
                      <div>
                        <span className={`severity ${conflict.severity}`}>
                          {sentenceCase(conflict.severity)}
                        </span>
                        <strong>{sentenceCase(conflict.category)}</strong>
                        <p>{conflict.explanation}</p>
                        <small>
                          Metadata differentiation risk · no acoustic claim ·{" "}
                          {sentenceCase(conflict.resolutionState)}
                        </small>
                      </div>
                      {conflict.resolutionState === "open" &&
                        reusableConflictCategories.has(conflict.category) &&
                        selectedRole !== null &&
                        conflict.roleIds.includes(selectedRole.roleId) && (
                          <button
                            type="button"
                            onClick={() =>
                              void appendCorrection(
                                selectedRole,
                                "approve_voice_reuse",
                                {
                                  conflictId: conflict.conflictId,
                                  approvedRoleIds: conflict.roleIds
                                },
                                null
                              )
                            }
                            disabled={busyAction !== null}
                          >
                            Approve planned reuse
                          </button>
                        )}
                    </div>
                  ))}
                </div>
              )}
              {conflicts.nextCursor !== undefined && (
                <button
                  type="button"
                  className="load-more"
                  onClick={() => void loadMoreConflicts()}
                  disabled={busyAction !== null}
                >
                  Load more conflicts
                </button>
              )}
            </article>

            <article className="governance-panel">
              <div className="casting-section-heading compact">
                <div>
                  <span className="eyebrow">Effective projection</span>
                  <h3>Assignments</h3>
                </div>
                <span>{assignments.total}</span>
              </div>
              <div className="governance-list">
                {roles.items.map((role) => {
                  const assignment = assignmentsByRole.get(role.roleId);
                  const profile =
                    assignment === undefined
                      ? undefined
                      : profilesById.get(assignment.voiceProfileId);
                  return (
                    <div className="assignment-row" key={role.roleId}>
                      <div>
                        <strong>{role.effectiveDisplayLabel}</strong>
                        <span>
                          {assignment === undefined
                            ? role.status === "intentionally_uncast"
                              ? "Intentionally uncast"
                              : "Unassigned"
                            : profile?.displayLabel ??
                              assignment.voiceProfileId}
                        </span>
                      </div>
                      {assignment !== undefined && (
                        <div className="assignment-badges">
                          <span>{sentenceCase(assignment.authority)}</span>
                          <span className={`rights ${assignment.rightsState}`}>
                            {sentenceCase(assignment.rightsState)}
                          </span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              {assignments.nextCursor !== undefined && (
                <button
                  type="button"
                  className="load-more"
                  onClick={() => void loadMoreAssignments()}
                  disabled={busyAction !== null}
                >
                  Load more assignments
                </button>
              )}
            </article>

            <article className="governance-panel">
              <div className="casting-section-heading compact">
                <div>
                  <span className="eyebrow">Append only</span>
                  <h3>Correction history</h3>
                </div>
                <span>{corrections.total}</span>
              </div>
              {corrections.items.length === 0 ? (
                <p className="quiet">No human casting corrections recorded.</p>
              ) : (
                <ol className="correction-history">
                  {corrections.items.map((correction) => (
                    <li key={correction.correctionId}>
                      <strong>{sentenceCase(correction.category)}</strong>
                      <span>{correction.reason}</span>
                      <small>
                        {correction.actor.actorId} ·{" "}
                        {formatDate(correction.recordedAt)} · immutable
                      </small>
                    </li>
                  ))}
                </ol>
              )}
              {corrections.nextCursor !== undefined && (
                <button
                  type="button"
                  className="load-more"
                  onClick={() => void loadMoreCorrections()}
                  disabled={busyAction !== null}
                >
                  Load more corrections
                </button>
              )}
            </article>
          </section>

          <section className="casting-reviews" aria-label="Casting approval gates">
            <div className="casting-section-heading">
              <div>
                <span className="eyebrow">Human authority</span>
                <h3>Casting approval gates</h3>
              </div>
              {finalCastBlocked && (
                <span className="casting-status blocked">
                  Final approval blocked
                </span>
              )}
            </div>
            {(ineligibleAssignments.length > 0 ||
              unacknowledgedRestricted.length > 0 ||
              blockingConflicts.length > 0 ||
              governanceEvidenceIncomplete) && (
              <div className="final-blockers" role="alert">
                <strong>Complete Cast Review cannot be approved.</strong>
                <span>
                  {ineligibleAssignments.length} unknown/prohibited rights ·{" "}
                  {unacknowledgedRestricted.length} restricted rights awaiting
                  acknowledgement · {blockingConflicts.length} open blocking
                  conflicts
                  {governanceEvidenceIncomplete
                    ? " · load all governed evidence pages before approval"
                    : ""}
                </span>
              </div>
            )}
            <div className="review-grid">
              {reviews.map((review) => {
                const completeGate =
                  review.gateId === "complete_cast_review";
                const approveDisabled =
                  busyAction !== null ||
                  (completeGate &&
                    (!narratorApproved ||
                      !characterApproved ||
                      finalCastBlocked));
                return (
                  <article className="review-card" key={review.gateId}>
                    <div>
                      <span className={`review-state ${review.state}`}>
                        {sentenceCase(review.state)}
                      </span>
                      <h4>{gateLabel(review.gateId)}</h4>
                      <p>
                        Evidence {shortHash(review.evidence.evidenceFingerprint)}
                      </p>
                    </div>
                    {completeGate && (
                      <div className="gate-dependencies">
                        <span className={narratorApproved ? "met" : ""}>
                          Narrator review
                        </span>
                        <span className={characterApproved ? "met" : ""}>
                          Character review
                        </span>
                      </div>
                    )}
                    {review.openWarningIds.length > 0 && (
                      <label className="warning-ack">
                        <input
                          type="checkbox"
                          checked={
                            acknowledgedReviews[review.gateId] === true
                          }
                          onChange={(event) =>
                            setAcknowledgedReviews((current) => ({
                              ...current,
                              [review.gateId]: event.target.checked
                            }))
                          }
                        />
                        Acknowledge {review.openWarningIds.length} current
                        warning(s)
                      </label>
                    )}
                    <label>
                      Decision rationale
                      <textarea
                        value={reviewRationales[review.gateId] ?? ""}
                        onChange={(event) =>
                          setReviewRationales((current) => ({
                            ...current,
                            [review.gateId]: event.target.value
                          }))
                        }
                        maxLength={4_000}
                      />
                    </label>
                    <div className="review-actions">
                      <button
                        type="button"
                        onClick={() => void decideReview(review, "approve")}
                        disabled={approveDisabled}
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          void decideReview(review, "request_changes")
                        }
                        disabled={busyAction !== null}
                      >
                        Request changes
                      </button>
                      <button
                        type="button"
                        className="danger-button"
                        onClick={() => void decideReview(review, "reject")}
                        disabled={busyAction !== null}
                      >
                        Reject
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
        </>
      )}
    </section>
  );
}

function RoleGroup({
  title,
  roles,
  selectedRoleId,
  assignments,
  busy,
  onSelect
}: {
  readonly title: string;
  readonly roles: readonly ProductionRole[];
  readonly selectedRoleId: string | null;
  readonly assignments: ReadonlyMap<string, CastAssignment>;
  readonly busy: boolean;
  readonly onSelect: (role: ProductionRole) => void;
}) {
  return (
    <section className="role-group">
      <h4>{title}<span>{roles.length}</span></h4>
      {roles.map((role) => {
        const assignment = assignments.get(role.roleId);
        return (
          <button
            type="button"
            className={selectedRoleId === role.roleId ? "role-row active" : "role-row"}
            key={role.roleId}
            onClick={() => onSelect(role)}
            disabled={busy}
          >
            <span className="role-monogram" aria-hidden="true">
              {role.effectiveDisplayLabel.slice(0, 1).toUpperCase()}
            </span>
            <span>
              <strong>{role.effectiveDisplayLabel}</strong>
              <small>
                {sentenceCase(role.roleType)} ·{" "}
                {role.approximateWordCount.toLocaleString()} words
              </small>
            </span>
            <span
              className={
                assignment?.authority === "human_locked"
                  ? "role-assignment locked"
                  : assignment === undefined
                    ? "role-assignment unassigned"
                    : "role-assignment assigned"
              }
            >
              {assignment?.authority === "human_locked"
                ? "Locked"
                : assignment === undefined
                  ? "Open"
                  : "Cast"}
            </span>
          </button>
        );
      })}
    </section>
  );
}

function RoleHeader({
  role,
  assignment,
  profile
}: {
  readonly role: ProductionRole;
  readonly assignment: CastAssignment | null;
  readonly profile: VoiceProfile | null;
}) {
  return (
    <header className="role-header">
      <div>
        <span className="eyebrow">{sentenceCase(role.roleType)}</span>
        <h3>{role.effectiveDisplayLabel}</h3>
        <p>
          {role.dialogueLineCount} dialogue lines · {role.narrationSpanCount}{" "}
          narration spans · {role.approximateWordCount.toLocaleString()} words ·{" "}
          chapters {rangeLabel(
            role.range.firstChapterOrdinal,
            role.range.lastChapterOrdinal
          )}
        </p>
      </div>
      <div className="effective-assignment">
        <span className={assignment === null ? "unassigned-dot" : "assigned-dot"} />
        <div>
          <strong>{profile?.displayLabel ?? "Unassigned"}</strong>
          <small>
            {assignment === null
              ? "No effective assignment"
              : `${sentenceCase(assignment.authority)} · ${sentenceCase(
                  assignment.rightsState
                )} rights`}
          </small>
        </div>
      </div>
      <dl className="role-requirements">
        <div>
          <dt>Language</dt>
          <dd>{role.performanceRequirements.language}</dd>
        </div>
        <div>
          <dt>Locales</dt>
          <dd>{role.performanceRequirements.locales.join(", ") || "Any declared"}</dd>
        </div>
        <div>
          <dt>Long form</dt>
          <dd>{role.performanceRequirements.longFormRequired ? "Required" : "Not required"}</dd>
        </div>
        <div>
          <dt>Expressive range</dt>
          <dd>
            {role.performanceRequirements.requiredExpressiveRange.join(", ") ||
              "No hard requirement"}
          </dd>
        </div>
      </dl>
    </header>
  );
}

function CandidateCard({
  candidate,
  profile,
  rights,
  busy,
  onSelect,
  onReject
}: {
  readonly candidate: CastingCandidate;
  readonly profile: VoiceProfile | undefined;
  readonly rights: VoiceRightsRecord | undefined;
  readonly busy: boolean;
  readonly onSelect: () => void;
  readonly onReject: () => void;
}) {
  const assessment = candidate.assessment;
  const hardFailures = assessment.hardConstraints.filter(
    (item) => item.result === "fail"
  );
  const hardUnknowns = assessment.hardConstraints.filter(
    (item) => item.result === "unknown"
  );
  return (
    <article className="candidate-card">
      <header>
        <div className="candidate-rank">#{candidate.rank}</div>
        <div>
          <h4>{profile?.displayLabel ?? candidate.voiceProfileId}</h4>
          <p>
            {profile?.locale ?? "Unknown locale"} ·{" "}
            {profile?.vocalTexture ?? "Unspecified texture"} ·{" "}
            {profile?.longFormSuitability ?? "Unknown long-form state"}
          </p>
        </div>
        <span className={`compatibility ${assessment.compatibilityStatus}`}>
          {sentenceCase(assessment.compatibilityStatus)}
        </span>
      </header>
      <div className="candidate-score">
        <strong>{Math.round(assessment.compatibilityScore * 100)}</strong>
        <span>explainable compatibility</span>
        <small>{sentenceCase(assessment.confidence.classification)} confidence</small>
      </div>
      <p className="candidate-explanation">{assessment.explanation}</p>
      <div className="candidate-evidence-grid">
        <div>
          <strong>Hard constraints</strong>
          {assessment.hardConstraints.map((result) => (
            <span className={`check-result ${result.result}`} key={result.constraintId}>
              {sentenceCase(result.constraintId)} · {sentenceCase(result.result)}
              <small>{result.explanation}</small>
            </span>
          ))}
        </div>
        <div>
          <strong>Soft preferences</strong>
          {assessment.softPreferences.map((result) => (
            <span className="preference-result" key={result.preferenceId}>
              {sentenceCase(result.preferenceId)} ·{" "}
              {Math.round(result.score * 100)}
              <small>{result.explanation}</small>
            </span>
          ))}
        </div>
      </div>
      <div className="candidate-metadata">
        <span className={`rights ${profile?.rightsState ?? "unknown"}`}>
          Rights {sentenceCase(profile?.rightsState ?? "unknown")}
        </span>
        <span>Consent {sentenceCase(rights?.consentStatus ?? "unknown")}</span>
        <span>Provider {sentenceCase(assessment.providerAvailability)}</span>
        <span>Model {sentenceCase(assessment.modelAvailability)}</span>
        <span>Long form {sentenceCase(assessment.longFormSuitability)}</span>
      </div>
      {(hardFailures.length > 0 || hardUnknowns.length > 0) && (
        <div className="candidate-warning">
          {hardFailures.length} hard failure(s) · {hardUnknowns.length} unknown
          hard result(s). Human selection cannot make this final-approval
          eligible.
        </div>
      )}
      {candidate.conflictWarnings.map((warning) => (
        <p className="candidate-warning" key={warning.code}>
          {warning.message}
        </p>
      ))}
      {candidate.rejectedByCorrectionId !== null && (
        <p className="candidate-warning">
          This candidate is rejected. Selecting it explicitly supersedes only
          that current rejection.
        </p>
      )}
      <footer>
        <button
          type="button"
          onClick={onSelect}
          disabled={busy}
        >
          {candidate.rejectedByCorrectionId === null
            ? "Select voice"
            : "Select despite rejection"}
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={onReject}
          disabled={busy || candidate.rejectedByCorrectionId !== null}
        >
          Reject candidate
        </button>
      </footer>
    </article>
  );
}

function Stat({ label, value }: { readonly label: string; readonly value: number }) {
  return (
    <div>
      <strong>{value.toLocaleString()}</strong>
      <span>{label}</span>
    </div>
  );
}

function phase2Prerequisites(project: ProjectDetail): readonly PrerequisiteView[] {
  const analysisRun = project.currentAnalysisRun;
  const snapshot = analysisRun?.currentSnapshot ?? null;
  const importReview = [...project.importReviews]
    .reverse()
    .find((review) => review.state === "approved");
  const gates = [
    ["story_structure_review", "Story Structure Review"],
    ["character_registry_review", "Character Registry Review"],
    ["dialogue_attribution_review", "Dialogue Attribution Review"],
    ["whole_book_analysis_review", "Whole-Book Analysis Review"]
  ] as const;
  const importDecision = importReview?.latestDecision;
  return [
    {
      id: "import_review",
      label: "Import Review",
      approved:
        importReview?.state === "approved" &&
        importDecision?.decision === "approved",
      current:
        importReview !== undefined &&
        analysisRun !== null &&
        importReview.extractionId === analysisRun.extractionId &&
        importReview.latestDecision?.evidenceFingerprint ===
          analysisRun.approvedEvidenceFingerprint,
      decisionId: importDecision?.decisionId ?? null,
      fingerprint: importReview?.evidenceFingerprint ?? null
    },
    ...gates.map(([gateId, label]) => {
      const review = project.analysisGateReviews.find(
        (item) =>
          item.gateId === gateId &&
          item.runId === analysisRun?.runId &&
          item.snapshotId === snapshot?.snapshotId
      );
      return {
        id: gateId,
        label,
        approved:
          review?.state === "approved" &&
          review.latestDecision?.decision === "approved",
        current:
          review !== undefined &&
          review.artifactFingerprint ===
            review.latestDecision?.artifactFingerprint &&
          review.evidenceFingerprint ===
            review.latestDecision?.evidenceFingerprint,
        decisionId: review?.latestDecision?.decisionId ?? null,
        fingerprint: review?.evidenceFingerprint ?? null
      };
    })
  ];
}

function approvedAnalysisDecisions(
  project: ProjectDetail,
  runId: string
): {
  readonly storyStructureReview: string;
  readonly characterRegistryReview: string;
  readonly dialogueAttributionReview: string;
  readonly wholeBookAnalysisReview: string;
} | null {
  const find = (gateId: string) =>
    project.analysisGateReviews.find(
      (review) =>
        review.gateId === gateId &&
        review.runId === runId &&
        review.state === "approved" &&
        review.latestDecision?.decision === "approved"
    )?.latestDecision?.decisionId;
  const result = {
    storyStructureReview: find("story_structure_review"),
    characterRegistryReview: find("character_registry_review"),
    dialogueAttributionReview: find("dialogue_attribution_review"),
    wholeBookAnalysisReview: find("whole_book_analysis_review")
  };
  return Object.values(result).some((value) => value === undefined)
    ? null
    : (result as {
        readonly storyStructureReview: string;
        readonly characterRegistryReview: string;
        readonly dialogueAttributionReview: string;
        readonly wholeBookAnalysisReview: string;
      });
}

function latestApprovedImportDecision(project: ProjectDetail): string | null {
  return (
    [...project.importReviews]
      .reverse()
      .find(
        (review) =>
          review.state === "approved" &&
          review.latestDecision?.decision === "approved"
      )?.latestDecision?.decisionId ?? null
  );
}

function runEvidence(run: CastingRun): CastingRunEvidenceInput {
  return {
    projectId: run.projectId,
    runId: run.castingRunId,
    expectedRunFingerprint: runFingerprint(run),
    expectedCatalogRevisionId: run.catalogRevisionId,
    expectedCatalogFingerprint: run.catalogFingerprint,
    expectedSnapshotId: run.prerequisites.analysisSnapshotId,
    expectedSnapshotRevision: run.prerequisites.analysisSnapshotRevision,
    expectedSnapshotFingerprint:
      run.prerequisites.analysisSnapshotFingerprint,
    expectedCastingProfileFingerprint: run.profile.fingerprint
  };
}

function runFingerprint(run: CastingRun): string {
  return run.outputFingerprint ?? run.inputFingerprint;
}

function roleEffectiveFingerprint(role: ProductionRole): string {
  return role.effectiveFingerprint;
}

function isNarratorRole(role: ProductionRole): boolean {
  return (
    role.roleType === "primary_narrator" ||
    role.roleType === "secondary_narrator"
  );
}

function pageState<T>(page: {
  readonly items: readonly T[];
  readonly total: number;
  readonly nextCursor?: string;
}): PageState<T> {
  return {
    items: page.items,
    total: page.total,
    ...(page.nextCursor === undefined
      ? {}
      : { nextCursor: page.nextCursor })
  };
}

function mergePage<T>(
  current: PageState<T>,
  page: {
    readonly items: readonly T[];
    readonly total: number;
    readonly nextCursor?: string;
  }
): PageState<T> {
  return {
    items: [...current.items, ...page.items],
    total: page.total,
    ...(page.nextCursor === undefined
      ? {}
      : { nextCursor: page.nextCursor })
  };
}

function dedupeBy<T>(
  values: readonly T[],
  key: (value: T) => string
): readonly T[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const id = key(value);
    if (seen.has(id)) {
      return false;
    }
    seen.add(id);
    return true;
  });
}

function unwrap<T>(result: DesktopResult<T>): T {
  if (!result.ok) {
    throw new CastingWorkspaceError(result.error);
  }
  return result.value;
}

class CastingWorkspaceError extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(error: DesktopError) {
    super(error.message);
    this.name = "CastingWorkspaceError";
    this.code = error.code;
    this.retryable = error.retryable;
  }
}

function toDesktopError(value: unknown): DesktopError {
  if (
    value !== null &&
    typeof value === "object" &&
    "code" in value &&
    "message" in value
  ) {
    const candidate = value as {
      readonly code: unknown;
      readonly message: unknown;
      readonly retryable?: unknown;
    };
    if (
      typeof candidate.code === "string" &&
      typeof candidate.message === "string"
    ) {
      return {
        code: candidate.code,
        message: candidate.message,
        retryable: candidate.retryable === true
      };
    }
  }
  return {
    code: "CASTING_WORKSPACE_FAILED",
    message: "The governed casting workspace could not complete the operation.",
    retryable: false
  };
}

function gateLabel(gateId: CastingReview["gateId"]): string {
  switch (gateId) {
    case "narrator_casting_review":
      return "Narrator Casting Review";
    case "character_casting_review":
      return "Character Casting Review";
    case "complete_cast_review":
      return "Complete Cast Review";
  }
}

function rangeLabel(first: number | null, last: number | null): string {
  if (first === null || last === null) {
    return "not applicable";
  }
  return first === last ? String(first) : `${first}–${last}`;
}

function shortHash(value: string | undefined | null): string {
  return value === undefined || value === null
    ? "—"
    : `${value.slice(0, 10)}…${value.slice(-8)}`;
}

function shortId(value: string | undefined | null): string {
  return value === undefined || value === null
    ? "No decision"
    : value.length > 28
      ? `${value.slice(0, 16)}…${value.slice(-8)}`
      : value;
}

function sentenceCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/gu, (character) => character.toUpperCase());
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}
