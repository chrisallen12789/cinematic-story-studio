import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent
} from "react";

import type {
  FfmpegCapabilityResponse,
  Job,
  ProjectDetail,
  ProjectSummary,
  ProviderHealthResponse
} from "@cinematic-story-studio/contracts/api";
import type {
  Character,
  DialogueAttribution,
  DialogueLine,
  Scene,
  StoryBeat
} from "@cinematic-story-studio/contracts/domain";

import {
  type BackendSnapshot,
  type CinematicStoryDesktopApi,
  type DesktopError,
  type DesktopResult
} from "../shared/desktop-api";

type WorkspaceView = "studio" | "casting" | "systems";

interface DialogueDraft {
  readonly characterId: string;
  readonly reason: string;
}

const initialBackend: BackendSnapshot = {
  state: "starting",
  message: "Starting the local service...",
  checkedAt: new Date(0).toISOString()
};

const activeJobStates = new Set<Job["state"]>([
  "queued",
  "running",
  "cancel_requested"
]);

export interface AppProps {
  readonly api?: CinematicStoryDesktopApi;
}

export function App({ api = window.cinematicStory }: AppProps) {
  const [backend, setBackend] = useState<BackendSnapshot>(initialBackend);
  const [projects, setProjects] = useState<readonly ProjectSummary[]>([]);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [projectLoading, setProjectLoading] = useState(false);
  const [view, setView] = useState<WorkspaceView>("studio");
  const [projectName, setProjectName] = useState("");
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(
    null
  );
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [dialogueDrafts, setDialogueDrafts] = useState<
    Readonly<Record<string, DialogueDraft>>
  >({});
  const [conflictLineId, setConflictLineId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<DesktopError | null>(null);
  const [providerHealth, setProviderHealth] =
    useState<ProviderHealthResponse | null>(null);
  const [ffmpeg, setFfmpeg] =
    useState<FfmpegCapabilityResponse | null>(null);
  const wasConnected = useRef(false);

  const connected =
    backend.state === "ready" || backend.state === "degraded";

  const applyProject = useCallback((detail: ProjectDetail) => {
    setProject(detail);
    const chapters = [...detail.chapters].sort(
      (left, right) => left.ordinal - right.ordinal
    );
    const firstChapter = chapters[0];
    setSelectedChapterId((current) =>
      current !== null &&
      detail.chapters.some((chapter) => chapter.chapterId === current)
        ? current
        : (firstChapter?.chapterId ?? null)
    );
    const firstScene =
      firstChapter === undefined
        ? undefined
        : [...detail.scenes]
            .filter((scene) => scene.chapterId === firstChapter.chapterId)
            .sort((left, right) => left.ordinal - right.ordinal)[0];
    setSelectedSceneId((current) =>
      current !== null &&
      detail.scenes.some((scene) => scene.sceneId === current)
        ? current
        : (firstScene?.sceneId ?? null)
    );
    setDialogueDrafts((current) => {
      const next: Record<string, DialogueDraft> = { ...current };
      for (const line of detail.dialogueLines) {
        if (next[line.lineId] === undefined) {
          const attribution = detail.dialogueAttributions.find(
            (item) => item.lineId === line.lineId
          );
          next[line.lineId] = {
            characterId: attribution?.effectiveSpeakerId ?? "",
            reason: ""
          };
        }
      }
      return next;
    });
  }, []);

  const openProject = useCallback(
    async (projectId: string, showLoading = true) => {
      if (showLoading) {
        setProjectLoading(true);
      }
      setError(null);
      try {
        const detail = await unwrap(api.projects.open(projectId));
        applyProject(detail);
      } catch (caught) {
        setError(asDesktopError(caught));
      } finally {
        if (showLoading) {
          setProjectLoading(false);
        }
      }
    },
    [api, applyProject]
  );

  const loadWorkspace = useCallback(async () => {
    setProjectLoading(true);
    setError(null);
    try {
      const [page, recent] = await Promise.all([
        unwrap(api.projects.list()),
        unwrap(api.projects.restoreRecent())
      ]);
      setProjects(page.items);
      if (recent !== null) {
        applyProject(recent);
      }
    } catch (caught) {
      setError(asDesktopError(caught));
    } finally {
      setProjectLoading(false);
    }
  }, [api, applyProject]);

  const refreshSystemHealth = useCallback(async () => {
    if (!connected) {
      return;
    }
    try {
      const [providers, capability] = await Promise.all([
        unwrap(api.providers.health()),
        unwrap(api.capabilities.ffmpeg())
      ]);
      setProviderHealth(providers);
      setFfmpeg(capability);
    } catch (caught) {
      setError(asDesktopError(caught));
    }
  }, [api, connected]);

  useEffect(() => {
    const unsubscribe = api.backend.onStatus(setBackend);
    void api.backend.getStatus().then((result) => {
      if (result.ok) {
        setBackend(result.value);
      } else {
        setError(result.error);
      }
    });
    return unsubscribe;
  }, [api]);

  useEffect(() => {
    if (connected && !wasConnected.current) {
      void loadWorkspace();
    }
    wasConnected.current = connected;
  }, [connected, loadWorkspace]);

  useEffect(() => {
    if (project === null || !connected) {
      return;
    }
    const activeJobs = project.jobs.filter((job) =>
      activeJobStates.has(job.state)
    );
    if (activeJobs.length === 0) {
      return;
    }
    const timer = window.setInterval(() => {
      void Promise.all(
        activeJobs.map(async (job) => {
          const result = await api.jobs.get(job.jobId);
          if (!result.ok) {
            return null;
          }
          return result.value.job;
        })
      ).then((updates) => {
        const received = updates.filter((job): job is Job => job !== null);
        if (received.length === 0) {
          return;
        }
        setProject((current) => {
          if (current === null) {
            return current;
          }
          const byId = new Map(received.map((job) => [job.jobId, job]));
          return {
            ...current,
            jobs: current.jobs.map((job) => byId.get(job.jobId) ?? job)
          };
        });
        if (received.some((job) => !activeJobStates.has(job.state))) {
          void openProject(project.project.projectId, false);
        }
      });
    }, 1_250);
    return () => {
      window.clearInterval(timer);
    };
  }, [api, connected, openProject, project]);

  const chapters = useMemo(
    () =>
      project === null
        ? []
        : [...project.chapters].sort(
            (left, right) => left.ordinal - right.ordinal
          ),
    [project]
  );
  const selectedChapter =
    chapters.find((chapter) => chapter.chapterId === selectedChapterId) ??
    chapters[0];
  const scenes = useMemo(
    () =>
      project === null || selectedChapter === undefined
        ? []
        : [...project.scenes]
            .filter(
              (scene) => scene.chapterId === selectedChapter.chapterId
            )
            .sort((left, right) => left.ordinal - right.ordinal),
    [project, selectedChapter]
  );
  const selectedScene =
    scenes.find((scene) => scene.sceneId === selectedSceneId) ?? scenes[0];

  const selectChapter = (chapterId: string) => {
    setSelectedChapterId(chapterId);
    const firstScene = project?.scenes
      .filter((scene) => scene.chapterId === chapterId)
      .sort((left, right) => left.ordinal - right.ordinal)[0];
    setSelectedSceneId(firstScene?.sceneId ?? null);
  };

  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = projectName.trim();
    if (name.length === 0 || !connected) {
      return;
    }
    setBusyAction("create-project");
    setError(null);
    try {
      const created = await unwrap(
        api.projects.create({
          name,
          idempotencyKey: crypto.randomUUID()
        })
      );
      setProjectName("");
      setNotice(`Created ${created.project.name}.`);
      await openProject(created.project.projectId);
      const page = await unwrap(api.projects.list());
      setProjects(page.items);
    } catch (caught) {
      setError(asDesktopError(caught));
    } finally {
      setBusyAction(null);
    }
  };

  const importStory = async () => {
    if (project === null || !connected) {
      return;
    }
    setBusyAction("import-story");
    setError(null);
    setNotice(null);
    try {
      const imported = await unwrap(
        api.projects.importSelectedFile(project.project.projectId)
      );
      if (imported !== null) {
        setNotice(
          `Imported ${imported.sourceDocument.displayName} without changing its text.`
        );
        await openProject(project.project.projectId, false);
      }
    } catch (caught) {
      setError(asDesktopError(caught));
    } finally {
      setBusyAction(null);
    }
  };

  const createAnalysisJob = async () => {
    if (project === null || project.story === null || !connected) {
      return;
    }
    setBusyAction("create-job");
    setError(null);
    try {
      const response = await unwrap(
        api.jobs.create({
          projectId: project.project.projectId,
          type: "analyze_story",
          inputRevision: project.story.revision,
          idempotencyKey: crypto.randomUUID()
        })
      );
      setProject((current) =>
        current === null
          ? current
          : {
              ...current,
              jobs: [
                response.job,
                ...current.jobs.filter(
                  (job) => job.jobId !== response.job.jobId
                )
              ]
            }
      );
      setNotice("Story analysis queued.");
    } catch (caught) {
      setError(asDesktopError(caught));
    } finally {
      setBusyAction(null);
    }
  };

  const updateDraft = (
    lineId: string,
    field: keyof DialogueDraft,
    value: string
  ) => {
    setDialogueDrafts((current) => ({
      ...current,
      [lineId]: {
        characterId: current[lineId]?.characterId ?? "",
        reason: current[lineId]?.reason ?? "",
        [field]: value
      }
    }));
  };

  const saveSpeaker = async (line: DialogueLine) => {
    if (project === null || !connected) {
      return;
    }
    const draft = dialogueDrafts[line.lineId];
    if (draft === undefined || draft.reason.trim().length === 0) {
      setError({
        code: "CORRECTION_REASON_REQUIRED",
        message: "Add a reason so this human correction remains auditable.",
        retryable: false
      });
      return;
    }
    setBusyAction(`speaker-${line.lineId}`);
    setError(null);
    setConflictLineId(null);
    try {
      await unwrap(
        api.dialogue.correctSpeaker({
          projectId: project.project.projectId,
          lineId: line.lineId,
          characterId:
            draft.characterId.length === 0 ? null : draft.characterId,
          reason: draft.reason.trim(),
          expectedRevision: line.revision
        })
      );
      setDialogueDrafts((current) => ({
        ...current,
        [line.lineId]: {
          characterId: draft.characterId,
          reason: ""
        }
      }));
      setNotice("Speaker correction saved as human provenance.");
      await openProject(project.project.projectId, false);
    } catch (caught) {
      const desktopError = asDesktopError(caught);
      setError(desktopError);
      if (
        desktopError.code === "REVISION_CONFLICT" ||
        desktopError.code === "SERVICE_HTTP_409"
      ) {
        setConflictLineId(line.lineId);
      }
    } finally {
      setBusyAction(null);
    }
  };

  const controlJob = async (
    jobId: string,
    action: "cancel" | "retry" | "resume"
  ) => {
    if (!connected) {
      return;
    }
    setBusyAction(`${action}-${jobId}`);
    setError(null);
    try {
      const result = await unwrap(api.jobs[action](jobId));
      setProject((current) =>
        current === null
          ? current
          : {
              ...current,
              jobs: current.jobs.map((job) =>
                job.jobId === jobId ? result.job : job
              )
            }
      );
    } catch (caught) {
      setError(asDesktopError(caught));
    } finally {
      setBusyAction(null);
    }
  };

  const reconnect = async () => {
    setBusyAction("reconnect");
    setError(null);
    const result = await api.backend.reconnect();
    if (result.ok) {
      setBackend(result.value);
    } else {
      setError(result.error);
    }
    setBusyAction(null);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Project navigation">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            CS
          </span>
          <div>
            <strong>Cinematic</strong>
            <span>Story Studio</span>
          </div>
        </div>

        <form className="new-project" onSubmit={(event) => void createProject(event)}>
          <label htmlFor="project-name">New production</label>
          <div className="field-row">
            <input
              id="project-name"
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="Project name"
              maxLength={120}
              disabled={!connected}
            />
            <button
              className="icon-button"
              type="submit"
              aria-label="Create project"
              disabled={
                !connected ||
                projectName.trim().length === 0 ||
                busyAction === "create-project"
              }
            >
              +
            </button>
          </div>
        </form>

        <nav className="project-list" aria-label="Projects">
          <div className="section-label">
            <span>Productions</span>
            <span>{projects.length}</span>
          </div>
          {projects.length === 0 ? (
            <p className="quiet">No projects yet.</p>
          ) : (
            projects.map((item) => (
              <button
                type="button"
                className={
                  project?.project.projectId === item.projectId
                    ? "project-link active"
                    : "project-link"
                }
                key={item.projectId}
                onClick={() => void openProject(item.projectId)}
                disabled={!connected}
              >
                <span className="project-monogram" aria-hidden="true">
                  {item.name.slice(0, 1).toUpperCase()}
                </span>
                <span>
                  <strong>{item.name}</strong>
                  <small>{sentenceCase(item.status)}</small>
                </span>
              </button>
            ))
          )}
        </nav>

        <div className="sidebar-footer">
          <BackendPill backend={backend} />
          <span>Local-first workspace</span>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div>
            <span className="eyebrow">Production workspace</span>
            <h1>{project?.project.name ?? "Cinematic Story Studio"}</h1>
          </div>
          <nav className="view-switcher" aria-label="Workspace views">
            {(
              [
                ["studio", "Story"],
                ["casting", "Casting"],
                ["systems", "Systems"]
              ] as const
            ).map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-current={view === value ? "page" : undefined}
                className={view === value ? "active" : ""}
                onClick={() => {
                  setView(value);
                  if (value === "systems" && connected) {
                    void refreshSystemHealth();
                  }
                }}
              >
                {label}
              </button>
            ))}
          </nav>
          <BackendPill backend={backend} compact />
        </header>

        {!connected && (
          <section className="connection-banner" role="alert">
            <div>
              <strong>{backendStatusTitle(backend)}</strong>
              <span>{backend.message}</span>
            </div>
            <button
              type="button"
              onClick={() => void reconnect()}
              disabled={
                backend.state === "starting" ||
                backend.state === "stopping" ||
                busyAction === "reconnect"
              }
            >
              {busyAction === "reconnect" ? "Retrying..." : "Retry connection"}
            </button>
          </section>
        )}

        {(notice !== null || error !== null) && (
          <div className="toast-stack" aria-live="polite">
            {notice !== null && (
              <div className="notice">
                <span>{notice}</span>
                <button
                  type="button"
                  aria-label="Dismiss notification"
                  onClick={() => setNotice(null)}
                >
                  Close
                </button>
              </div>
            )}
            {error !== null && (
              <div className="notice error" role="alert">
                <span>
                  <strong>{error.code}</strong> {error.message}
                </span>
                <button
                  type="button"
                  aria-label="Dismiss error"
                  onClick={() => setError(null)}
                >
                  Close
                </button>
              </div>
            )}
          </div>
        )}

        {projectLoading && project === null ? (
          <LoadingWorkspace />
        ) : project === null ? (
          <EmptyWorkspace
            connected={connected}
            projectName={projectName}
            onProjectName={setProjectName}
          />
        ) : view === "studio" ? (
          <StoryWorkspace
            project={project}
            chapters={chapters}
            selectedChapterId={selectedChapter?.chapterId ?? null}
            scenes={scenes}
            selectedScene={selectedScene}
            selectedSceneId={selectedScene?.sceneId ?? null}
            connected={connected}
            busyAction={busyAction}
            dialogueDrafts={dialogueDrafts}
            conflictLineId={conflictLineId}
            onSelectChapter={selectChapter}
            onSelectScene={setSelectedSceneId}
            onImport={() => void importStory()}
            onAnalyze={() => void createAnalysisJob()}
            onUpdateDraft={updateDraft}
            onSaveSpeaker={(line) => void saveSpeaker(line)}
            onRefreshConflict={() =>
              void openProject(project.project.projectId, false)
            }
            onControlJob={(jobId, action) =>
              void controlJob(jobId, action)
            }
          />
        ) : view === "casting" ? (
          <CastingWorkspace project={project} />
        ) : (
          <SystemsWorkspace
            providerHealth={providerHealth}
            ffmpeg={ffmpeg}
            connected={connected}
            onRefresh={() => void refreshSystemHealth()}
          />
        )}
      </main>
    </div>
  );
}

function StoryWorkspace({
  project,
  chapters,
  selectedChapterId,
  scenes,
  selectedScene,
  selectedSceneId,
  connected,
  busyAction,
  dialogueDrafts,
  conflictLineId,
  onSelectChapter,
  onSelectScene,
  onImport,
  onAnalyze,
  onUpdateDraft,
  onSaveSpeaker,
  onRefreshConflict,
  onControlJob
}: {
  readonly project: ProjectDetail;
  readonly chapters: ProjectDetail["chapters"];
  readonly selectedChapterId: string | null;
  readonly scenes: readonly Scene[];
  readonly selectedScene: Scene | undefined;
  readonly selectedSceneId: string | null;
  readonly connected: boolean;
  readonly busyAction: string | null;
  readonly dialogueDrafts: Readonly<Record<string, DialogueDraft>>;
  readonly conflictLineId: string | null;
  readonly onSelectChapter: (chapterId: string) => void;
  readonly onSelectScene: (sceneId: string) => void;
  readonly onImport: () => void;
  readonly onAnalyze: () => void;
  readonly onUpdateDraft: (
    lineId: string,
    field: keyof DialogueDraft,
    value: string
  ) => void;
  readonly onSaveSpeaker: (line: DialogueLine) => void;
  readonly onRefreshConflict: () => void;
  readonly onControlJob: (
    jobId: string,
    action: "cancel" | "retry" | "resume"
  ) => void;
}) {
  return (
    <div className="workspace-grid">
      <section className="story-column">
        <div className="project-actions">
          <div>
            <span className="status-chip">{sentenceCase(project.project.status)}</span>
            <span className="revision">Revision {project.project.revision}</span>
          </div>
          <div>
            <button
              type="button"
              className="secondary-button"
              onClick={onImport}
              disabled={!connected || busyAction === "import-story"}
            >
              {busyAction === "import-story" ? "Importing..." : "Import TXT / MD"}
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={onAnalyze}
              disabled={
                !connected ||
                project.story === null ||
                busyAction === "create-job"
              }
            >
              {busyAction === "create-job" ? "Queueing..." : "Analyze story"}
            </button>
          </div>
        </div>

        {project.story === null ? (
          <section className="import-callout">
            <span className="callout-number">01</span>
            <div>
              <span className="eyebrow">Begin with the written word</span>
              <h2>Import the story exactly as authored.</h2>
              <p>
                Select a TXT or Markdown file. The desktop streams it through
                the authenticated local service and never exposes its path to
                this renderer.
              </p>
              <button
                type="button"
                className="primary-button"
                onClick={onImport}
                disabled={!connected || busyAction === "import-story"}
              >
                Choose story file
              </button>
            </div>
          </section>
        ) : chapters.length === 0 ? (
          <section className="waiting-card">
            <span className="pulse-dot" aria-hidden="true" />
            <div>
              <h2>Story imported</h2>
              <p>
                Queue analysis to identify chapters, scenes, characters, and
                dialogue while preserving the source.
              </p>
            </div>
          </section>
        ) : (
          <>
            <nav className="chapter-tabs" aria-label="Chapters">
              {chapters.map((chapter) => (
                <button
                  type="button"
                  key={chapter.chapterId}
                  className={
                    selectedChapterId === chapter.chapterId ? "active" : ""
                  }
                  aria-current={
                    selectedChapterId === chapter.chapterId
                      ? "page"
                      : undefined
                  }
                  onClick={() => onSelectChapter(chapter.chapterId)}
                >
                  <span>{String(chapter.ordinal + 1).padStart(2, "0")}</span>
                  {chapter.title ?? `Chapter ${chapter.ordinal + 1}`}
                </button>
              ))}
            </nav>

            <div className="scene-layout">
              <nav className="scene-rail" aria-label="Scenes">
                <span className="section-label">Scenes</span>
                {scenes.map((scene) => (
                  <button
                    type="button"
                    key={scene.sceneId}
                    className={
                      selectedSceneId === scene.sceneId ? "active" : ""
                    }
                    aria-current={
                      selectedSceneId === scene.sceneId ? "page" : undefined
                    }
                    onClick={() => onSelectScene(scene.sceneId)}
                  >
                    <span>{String(scene.ordinal + 1).padStart(2, "0")}</span>
                    <strong>
                      {scene.heading ?? `Scene ${scene.ordinal + 1}`}
                    </strong>
                    <small>{scene.location ?? scene.mood ?? "Story scene"}</small>
                  </button>
                ))}
              </nav>
              {selectedScene === undefined ? (
                <div className="empty-scene">Select a scene to inspect it.</div>
              ) : (
                <SceneReview
                  project={project}
                  scene={selectedScene}
                  connected={connected}
                  busyAction={busyAction}
                  dialogueDrafts={dialogueDrafts}
                  conflictLineId={conflictLineId}
                  onUpdateDraft={onUpdateDraft}
                  onSaveSpeaker={onSaveSpeaker}
                  onRefreshConflict={onRefreshConflict}
                />
              )}
            </div>
          </>
        )}
      </section>

      <aside className="inspector-column" aria-label="Production inspector">
        <CharacterInspector project={project} scene={selectedScene} />
        <JobsInspector
          jobs={project.jobs}
          connected={connected}
          busyAction={busyAction}
          onControl={onControlJob}
        />
      </aside>
    </div>
  );
}

function SceneReview({
  project,
  scene,
  connected,
  busyAction,
  dialogueDrafts,
  conflictLineId,
  onUpdateDraft,
  onSaveSpeaker,
  onRefreshConflict
}: {
  readonly project: ProjectDetail;
  readonly scene: Scene;
  readonly connected: boolean;
  readonly busyAction: string | null;
  readonly dialogueDrafts: Readonly<Record<string, DialogueDraft>>;
  readonly conflictLineId: string | null;
  readonly onUpdateDraft: (
    lineId: string,
    field: keyof DialogueDraft,
    value: string
  ) => void;
  readonly onSaveSpeaker: (line: DialogueLine) => void;
  readonly onRefreshConflict: () => void;
}) {
  const beats = [...project.beats]
    .filter((beat) => beat.sceneId === scene.sceneId)
    .sort((left, right) => left.ordinal - right.ordinal);
  const sceneLines = [...project.dialogueLines]
    .filter((line) => line.sceneId === scene.sceneId)
    .sort((left, right) => left.ordinal - right.ordinal);
  const renderedLineIds = new Set<string>();

  return (
    <article className="scene-review" aria-labelledby="scene-title">
      <header className="scene-header">
        <div>
          <span className="eyebrow">Scene {scene.ordinal + 1}</span>
          <h2 id="scene-title">
            {scene.heading ?? `Scene ${scene.ordinal + 1}`}
          </h2>
        </div>
        <div className="scene-metadata">
          {scene.location !== undefined && <span>{scene.location}</span>}
          {scene.mood !== undefined && <span>{scene.mood}</span>}
          <span>{Math.round(scene.confidence.score * 100)}% confidence</span>
        </div>
      </header>

      {scene.warnings.length > 0 && (
        <div className="warning-strip" role="status">
          Review requested: {scene.warnings.map((item) => item.message).join(" ")}
        </div>
      )}

      <div className="story-flow">
        {beats.map((beat) => {
          const line = dialogueForBeat(beat, sceneLines);
          if (line !== undefined) {
            renderedLineIds.add(line.lineId);
            return (
              <DialogueCard
                key={beat.beatId}
                line={line}
                attribution={project.dialogueAttributions.find(
                  (item) => item.lineId === line.lineId
                )}
                characters={project.characters}
                draft={dialogueDrafts[line.lineId]}
                connected={connected}
                saving={busyAction === `speaker-${line.lineId}`}
                conflict={conflictLineId === line.lineId}
                onUpdateDraft={onUpdateDraft}
                onSave={onSaveSpeaker}
                onRefreshConflict={onRefreshConflict}
              />
            );
          }
          return <NarrationBeat key={beat.beatId} beat={beat} />;
        })}
        {sceneLines
          .filter((line) => !renderedLineIds.has(line.lineId))
          .map((line) => (
            <DialogueCard
              key={line.lineId}
              line={line}
              attribution={project.dialogueAttributions.find(
                (item) => item.lineId === line.lineId
              )}
              characters={project.characters}
              draft={dialogueDrafts[line.lineId]}
              connected={connected}
              saving={busyAction === `speaker-${line.lineId}`}
              conflict={conflictLineId === line.lineId}
              onUpdateDraft={onUpdateDraft}
              onSave={onSaveSpeaker}
              onRefreshConflict={onRefreshConflict}
            />
          ))}
        {beats.length === 0 && sceneLines.length === 0 && (
          <div className="empty-scene">
            No analyzed narration or dialogue is available for this scene.
          </div>
        )}
      </div>
    </article>
  );
}

function NarrationBeat({ beat }: { readonly beat: StoryBeat }) {
  return (
    <section className="narration-beat">
      <span>Narration</span>
      <p>
        {beat.summary ??
          `Source passage ${beat.sourceSpan.startOffset}-${beat.sourceSpan.endOffset}`}
      </p>
    </section>
  );
}

function DialogueCard({
  line,
  attribution,
  characters,
  draft,
  connected,
  saving,
  conflict,
  onUpdateDraft,
  onSave,
  onRefreshConflict
}: {
  readonly line: DialogueLine;
  readonly attribution: DialogueAttribution | undefined;
  readonly characters: readonly Character[];
  readonly draft: DialogueDraft | undefined;
  readonly connected: boolean;
  readonly saving: boolean;
  readonly conflict: boolean;
  readonly onUpdateDraft: (
    lineId: string,
    field: keyof DialogueDraft,
    value: string
  ) => void;
  readonly onSave: (line: DialogueLine) => void;
  readonly onRefreshConflict: () => void;
}) {
  const effectiveCharacter = characters.find(
    (character) =>
      character.characterId === attribution?.effectiveSpeakerId
  );
  const uncertain =
    attribution === undefined ||
    attribution.effectiveSpeakerId === null ||
    attribution.warnings.some((warning) => warning.requiresHumanReview);
  const selectId = `speaker-${line.lineId}`;
  const reasonId = `reason-${line.lineId}`;

  return (
    <section className={uncertain ? "dialogue-card uncertain" : "dialogue-card"}>
      <header>
        <div>
          <span className="dialogue-avatar" aria-hidden="true">
            {(effectiveCharacter?.displayName ?? "?").slice(0, 1).toUpperCase()}
          </span>
          <div>
            <strong>
              {effectiveCharacter?.displayName ?? "Uncertain speaker"}
            </strong>
            <small>
              {attribution?.effectiveAuthority === "human"
                ? "Human correction"
                : attribution === undefined
                  ? "Attribution unavailable"
                  : `${Math.round(attribution.confidence.score * 100)}% agent confidence`}
            </small>
          </div>
        </div>
        {uncertain && <span className="review-badge">Review</span>}
      </header>
      <blockquote>{line.verbatimText}</blockquote>
      {attribution !== undefined && attribution.warnings.length > 0 && (
        <p className="attribution-warning">
          {attribution.warnings.map((warning) => warning.message).join(" ")}
        </p>
      )}
      <div className="correction-form">
        <label htmlFor={selectId}>
          Speaker
          <select
            id={selectId}
            value={draft?.characterId ?? ""}
            onChange={(event) =>
              onUpdateDraft(line.lineId, "characterId", event.target.value)
            }
            disabled={!connected || saving}
          >
            <option value="">Unassigned / uncertain</option>
            {characters.map((character) => (
              <option value={character.characterId} key={character.characterId}>
                {character.displayName}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={reasonId}>
          Correction reason
          <input
            id={reasonId}
            value={draft?.reason ?? ""}
            onChange={(event) =>
              onUpdateDraft(line.lineId, "reason", event.target.value)
            }
            placeholder="Why is this speaker correct?"
            maxLength={500}
            disabled={!connected || saving}
          />
        </label>
        <button
          type="button"
          className="secondary-button"
          onClick={() => onSave(line)}
          disabled={
            !connected || saving || (draft?.reason.trim().length ?? 0) === 0
          }
        >
          {saving ? "Saving..." : "Save correction"}
        </button>
      </div>
      {conflict && (
        <div className="conflict-message" role="alert">
          <span>The line changed since this view loaded.</span>
          <button type="button" onClick={onRefreshConflict}>
            Refresh and compare
          </button>
        </div>
      )}
    </section>
  );
}

function CharacterInspector({
  project,
  scene
}: {
  readonly project: ProjectDetail;
  readonly scene: Scene | undefined;
}) {
  const sceneCharacters =
    scene === undefined
      ? project.characters
      : project.characters.filter((character) =>
          scene.characterIds.includes(character.characterId)
        );
  return (
    <section className="inspector-card">
      <header>
        <div>
          <span className="eyebrow">Cast in scene</span>
          <h2>Characters</h2>
        </div>
        <span>{sceneCharacters.length}</span>
      </header>
      {sceneCharacters.length === 0 ? (
        <p className="quiet">No detected characters in this scene.</p>
      ) : (
        <ul className="character-list">
          {sceneCharacters.map((character) => (
            <li key={character.characterId}>
              <span className="dialogue-avatar" aria-hidden="true">
                {character.displayName.slice(0, 1).toUpperCase()}
              </span>
              <div>
                <strong>{character.displayName}</strong>
                <small>
                  {Math.round(character.confidence.score * 100)}% confidence
                </small>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function JobsInspector({
  jobs,
  connected,
  busyAction,
  onControl
}: {
  readonly jobs: ProjectDetail["jobs"];
  readonly connected: boolean;
  readonly busyAction: string | null;
  readonly onControl: (
    jobId: string,
    action: "cancel" | "retry" | "resume"
  ) => void;
}) {
  const ordered = [...jobs].sort((left, right) =>
    right.createdAt.localeCompare(left.createdAt)
  );
  return (
    <section className="inspector-card jobs-card">
      <header>
        <div>
          <span className="eyebrow">Background work</span>
          <h2>Analysis jobs</h2>
        </div>
        <span>{jobs.length}</span>
      </header>
      {ordered.length === 0 ? (
        <p className="quiet">No analysis jobs yet.</p>
      ) : (
        <div className="jobs-list" aria-live="polite">
          {ordered.map((job) => (
            <article key={job.jobId} className="job-row">
              <div className="job-title">
                <strong>{sentenceCase(job.stage || job.type)}</strong>
                <span className={`job-state state-${job.state}`}>
                  {sentenceCase(job.state)}
                </span>
              </div>
              <progress
                max={1}
                value={job.progress}
                aria-label={`${sentenceCase(job.type)} progress`}
              />
              <div className="job-meta">
                <span>{Math.round(job.progress * 100)}%</span>
                <span>Attempt {job.attempt}</span>
              </div>
              {job.error !== undefined && (
                <p className="job-error">
                  {job.error.code}: {job.error.message}
                </p>
              )}
              <div className="job-controls">
                {(job.state === "queued" || job.state === "running") && (
                  <button
                    type="button"
                    onClick={() => onControl(job.jobId, "cancel")}
                    disabled={!connected || busyAction === `cancel-${job.jobId}`}
                  >
                    Cancel
                  </button>
                )}
                {job.state === "failed" && (
                  <button
                    type="button"
                    onClick={() => onControl(job.jobId, "retry")}
                    disabled={!connected || busyAction === `retry-${job.jobId}`}
                  >
                    Retry
                  </button>
                )}
                {(job.state === "interrupted" || job.state === "paused") && (
                  <button
                    type="button"
                    onClick={() => onControl(job.jobId, "resume")}
                    disabled={!connected || busyAction === `resume-${job.jobId}`}
                  >
                    Resume
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function CastingWorkspace({ project }: { readonly project: ProjectDetail }) {
  const charactersById = new Map(
    project.characters.map((character) => [character.characterId, character])
  );
  return (
    <section className="full-workspace">
      <header className="workspace-heading">
        <div>
          <span className="eyebrow">Voice continuity</span>
          <h2>Casting board</h2>
          <p>
            Every detected character receives a typed casting slot. Phase 0
            leaves provider and voice identifiers explicitly unassigned.
          </p>
        </div>
        <span className="large-count">
          {project.castingPlaceholders.length}
        </span>
      </header>
      {project.castingPlaceholders.length === 0 ? (
        <div className="empty-panel">
          No typed casting placeholders are available yet. Analyze the story to
          detect characters.
        </div>
      ) : (
        <div className="casting-grid">
          {project.castingPlaceholders.map((placeholder) => {
            const character = charactersById.get(placeholder.characterId);
            return (
              <article
                className="casting-card"
                key={placeholder.characterId}
              >
                <span className="casting-avatar" aria-hidden="true">
                  {(character?.displayName ?? "?").slice(0, 1).toUpperCase()}
                </span>
                <div>
                  <span className="eyebrow">Character</span>
                  <h3>{character?.displayName ?? "Unknown character"}</h3>
                  <p>{character?.description ?? "No description available."}</p>
                </div>
                <div className="assignment">
                  <span className="unassigned-dot" aria-hidden="true" />
                  <div>
                    <strong>Unassigned</strong>
                    <small>No provider or voice selected.</small>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function SystemsWorkspace({
  providerHealth,
  ffmpeg,
  connected,
  onRefresh
}: {
  readonly providerHealth: ProviderHealthResponse | null;
  readonly ffmpeg: FfmpegCapabilityResponse | null;
  readonly connected: boolean;
  readonly onRefresh: () => void;
}) {
  return (
    <section className="full-workspace">
      <header className="workspace-heading">
        <div>
          <span className="eyebrow">Local runtime</span>
          <h2>Systems and providers</h2>
          <p>
            Health checks disclose capability only. They do not transmit story
            content or expose credentials.
          </p>
        </div>
        <button
          type="button"
          className="secondary-button"
          onClick={onRefresh}
          disabled={!connected}
        >
          Refresh health
        </button>
      </header>

      <div className="systems-grid">
        <section className="system-card ffmpeg-card">
          <div className="system-icon" aria-hidden="true">
            FF
          </div>
          <div>
            <span className="eyebrow">Audio toolchain</span>
            <h3>FFmpeg</h3>
          </div>
          {ffmpeg === null ? (
            <p className="quiet">Checking capability...</p>
          ) : (
            <>
              <StatusLabel status={ffmpeg.status} />
              <p>{ffmpeg.redactedReason ?? ffmpeg.version ?? "Capability checked."}</p>
              <ul className="capability-list">
                {ffmpeg.capabilities.map((capability) => (
                  <li key={capability}>{capability}</li>
                ))}
              </ul>
            </>
          )}
        </section>

        {(providerHealth?.providers ?? []).map((provider) => (
          <section className="system-card" key={provider.providerId}>
            <div className="system-icon" aria-hidden="true">
              {provider.providerId.toLowerCase().includes("kokoro")
                ? "KO"
                : provider.kind.slice(0, 2).toUpperCase()}
            </div>
            <div>
              <span className="eyebrow">
                {provider.executionLocation} {provider.kind}
              </span>
              <h3>
                {provider.providerId.toLowerCase().includes("kokoro")
                  ? "Kokoro"
                  : provider.providerId}
              </h3>
            </div>
            <StatusLabel status={provider.status} />
            <p>{provider.redactedReason ?? "Provider is ready."}</p>
            <ul className="capability-list">
              {provider.capabilities.map((capability) => (
                <li key={capability}>{capability}</li>
              ))}
            </ul>
          </section>
        ))}

        {providerHealth !== null && providerHealth.providers.length === 0 && (
          <div className="empty-panel">No provider adapters are registered.</div>
        )}
      </div>
    </section>
  );
}

function StatusLabel({ status }: { readonly status: string }) {
  return (
    <span className={`system-status status-${status}`}>
      <span aria-hidden="true" />
      {sentenceCase(status)}
    </span>
  );
}

function BackendPill({
  backend,
  compact = false
}: {
  readonly backend: BackendSnapshot;
  readonly compact?: boolean;
}) {
  return (
    <div
      className={`backend-pill backend-${backend.state}${compact ? " compact" : ""}`}
      role="status"
      aria-live="polite"
    >
      <span aria-hidden="true" />
      <div>
        <strong>{backendStatusTitle(backend)}</strong>
        {!compact && <small>{backend.message}</small>}
      </div>
    </div>
  );
}

function EmptyWorkspace({
  connected,
  projectName,
  onProjectName
}: {
  readonly connected: boolean;
  readonly projectName: string;
  readonly onProjectName: (value: string) => void;
}) {
  return (
    <section className="welcome">
      <div className="welcome-art" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className="welcome-copy">
        <span className="eyebrow">Local-first story production</span>
        <h2>Shape the page into a cinematic listening experience.</h2>
        <p>
          Create a production, import authored text, inspect the analysis, and
          keep every human decision durable and reviewable.
        </p>
        <label htmlFor="welcome-project-name">
          Name your first production
          <input
            id="welcome-project-name"
            value={projectName}
            onChange={(event) => onProjectName(event.target.value)}
            placeholder="Synthetic Demo"
            disabled={!connected}
            maxLength={120}
          />
        </label>
        <span className="welcome-hint">
          Use the Create project button in the production sidebar.
        </span>
      </div>
    </section>
  );
}

function LoadingWorkspace() {
  return (
    <section className="loading-workspace" role="status">
      <span className="loading-line wide" />
      <span className="loading-line" />
      <div>
        <span />
        <span />
        <span />
      </div>
      <p>Opening project...</p>
    </section>
  );
}

function dialogueForBeat(
  beat: StoryBeat,
  lines: readonly DialogueLine[]
): DialogueLine | undefined {
  if (beat.kind !== "dialogue") {
    return undefined;
  }
  return lines.find(
    (line) =>
      line.lineId === beat.dialogueLineId || line.beatId === beat.beatId
  );
}

async function unwrap<T>(operation: Promise<DesktopResult<T>>): Promise<T> {
  const result = await operation;
  if (!result.ok) {
    throw new UiOperationError(result.error);
  }
  return result.value;
}

class UiOperationError extends Error {
  constructor(readonly desktopError: DesktopError) {
    super(desktopError.message);
  }
}

function asDesktopError(value: unknown): DesktopError {
  if (value instanceof UiOperationError) {
    return value.desktopError;
  }
  return {
    code: "UI_OPERATION_FAILED",
    message: "The operation could not be completed.",
    retryable: false
  };
}

function backendStatusTitle(backend: BackendSnapshot): string {
  switch (backend.state) {
    case "ready":
      return "Backend ready";
    case "degraded":
      return "Backend degraded";
    case "starting":
      return "Backend starting";
    case "stopping":
      return "Backend stopping";
    case "stopped":
      return "Backend stopped";
    case "disconnected":
      return "Backend disconnected";
    case "unavailable":
      return "Backend unavailable";
  }
}

function sentenceCase(value: string): string {
  const normalized = value.replaceAll("_", " ");
  return normalized.slice(0, 1).toUpperCase() + normalized.slice(1);
}
