from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from cinematic_story_service import ServiceSettings, create_app
from cinematic_story_service.audition_repository import AuditionRepository
from cinematic_story_service.models import SpeechProviderRequestRow, SpeechRuntimeInstanceRow
from cinematic_story_service.speech_runtime import (
    ManagedSpeechRuntime,
    SpeechRuntimeConfig,
    SpeechRuntimeError,
    SpeechRuntimeExitEvidence,
    SpeechWorkerIdentity,
)
from tests.conftest import wait_for_job
from tests.test_phase3b_atomic_publication import _prepare_generation, _queue_generation
from tests.test_phase3b_workflow import _create_session_and_script, _workspace


class _IncompleteTerminationRuntime(ManagedSpeechRuntime):
    def synthesize(self, *_args: object, **_kwargs: object) -> None:
        identity = self.identity
        assert identity is not None
        with self._lock:
            self._last_exit = SpeechRuntimeExitEvidence(
                pid=identity.pid,
                launcher_pid=identity.launcher_pid,
                exit_code=None,
                reason="protocol_error",
                ownership_confirmed=True,
                shutdown_acknowledged=False,
                graceful_shutdown_confirmed=False,
                terminated_by_parent=True,
                confirmed_exited=False,
                job_object_assigned=identity.job_object_assigned,
                owned_processes_confirmed_exited=False,
                denied_network_attempt_count=0,
            )
        raise SpeechRuntimeError(
            "SPEECH_WORKER_PROTOCOL_INVALID",
            "Injected incomplete owned-worker teardown.",
            retryable=True,
        )


class _CommittedDispatchFailureRuntime(ManagedSpeechRuntime):
    def __init__(self, config: SpeechRuntimeConfig) -> None:
        super().__init__(config)
        self.dispatch_calls = 0

    def synthesize(
        self,
        *_args: object,
        on_dispatch_committed: Callable[[], None] | None = None,
        expected_identity: SpeechWorkerIdentity | None = None,
        **_kwargs: object,
    ) -> None:
        assert expected_identity == self.identity
        assert on_dispatch_committed is not None
        on_dispatch_committed()
        self.dispatch_calls += 1
        evidence = self._terminate_owned("process_error")
        assert evidence is not None
        assert evidence.confirmed_exited is True
        raise SpeechRuntimeError(
            "SPEECH_WORKER_EXITED",
            "Injected failure after durable dispatch commitment.",
            retryable=True,
        )


def test_repository_persists_authenticated_natural_idle_exit_and_shutdown_proof(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    settings = replace(settings, runtime_shutdown_evidence_enabled=True)
    sidecar = settings.data_dir / "phase3b-runtime-shutdown-evidence.json"
    instance_id = ""
    worker_pid = 0
    with TestClient(create_app(settings)) as client:
        repository = cast(AuditionRepository, client.app.state.auditions)
        repository._runtime_factory = lambda config: ManagedSpeechRuntime(
            replace(config, idle_timeout_seconds=0.2)
        )
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-natural-idle-runtime",
        )
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded"},
            timeout=30.0,
        )
        assert terminal["state"] == "succeeded"
        with repository._runtime_lock:
            assert len(repository._runtimes) == 1
            runtime, instance_id = next(iter(repository._runtimes.values()))
            identity = runtime.identity
            assert identity is not None
            worker_pid = identity.pid
        deadline = time.monotonic() + 5.0
        while runtime.is_running and time.monotonic() < deadline:
            time.sleep(0.02)
        assert runtime.is_running is False

        workspace = _workspace(client, auth_headers, project_id)
        persisted = next(
            value
            for value in workspace["runtimeInstances"]
            if value["runtimeInstanceId"] == instance_id
        )
        assert repository._runtimes == {}
        assert persisted["workerPid"] == worker_pid
        assert persisted["state"] == "stopped"
        assert persisted["stopReasonCode"] == "idle"
        assert persisted["exitCode"] == 0
        assert persisted["shutdownAcknowledged"] is True
        assert persisted["gracefulShutdownConfirmed"] is True
        assert persisted["ownershipConfirmed"] is True
        assert persisted["terminatedByParent"] is False
        assert persisted["confirmedExited"] is True
        assert persisted["ownedProcessesConfirmedExited"] is True
        assert persisted["deniedNetworkAttemptCount"] == 0
        assert persisted["restartReconciliation"] is None

    evidence = json.loads(sidecar.read_text(encoding="utf-8"))
    assert evidence["ownedRuntimeCount"] == 1
    assert evidence["allGracefulShutdownsConfirmed"] is True
    assert len(evidence["runtimeExits"]) == 1
    assert evidence["runtimeExits"][0]["runtimeInstanceId"] == instance_id
    assert evidence["runtimeExits"][0]["workerPid"] == worker_pid
    assert evidence["runtimeExits"][0]["stopReasonCode"] == "idle"
    assert evidence["runtimeExits"][0]["gracefulShutdownConfirmed"] is True


def test_acquisition_persists_idle_exit_instead_of_rebinding_a_new_process(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings)) as client:
        repository = cast(AuditionRepository, client.app.state.auditions)
        created_runtimes: list[ManagedSpeechRuntime] = []

        def runtime_factory(config: SpeechRuntimeConfig) -> ManagedSpeechRuntime:
            runtime = ManagedSpeechRuntime(replace(config, idle_timeout_seconds=0.2))
            created_runtimes.append(runtime)
            return runtime

        repository._runtime_factory = runtime_factory
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-runtime-acquisition-turnover",
        )
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        assert (
            wait_for_job(client, auth_headers, queued["jobId"], {"succeeded"}, timeout=30.0)[
                "state"
            ]
            == "succeeded"
        )
        with repository._runtime_lock:
            runtime, instance_id = next(iter(repository._runtimes.values()))
            identity = runtime.identity
            assert identity is not None
        deadline = time.monotonic() + 5.0
        while runtime.is_running and time.monotonic() < deadline:
            time.sleep(0.02)
        assert runtime.is_running is False

        monkeypatch.setattr(repository, "_reap_idle_runtimes", lambda: ())
        workspace = _workspace(client, auth_headers, project_id)
        role_id = workspace["roles"]["items"][0]["roleId"]
        second_session, _script, second_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned runtime identity turnover boundary.",
            key="phase3b-runtime-acquisition-turnover-second",
        )
        second_queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=second_session["auditionSessionId"],
            generation_request=second_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            second_queued["jobId"],
            {"failed"},
            timeout=30.0,
        )
        assert terminal["error"]["code"] == "AUDITION_RUNTIME_UNAVAILABLE"
        assert terminal["error"]["retryable"] is True

        with client.app.state.database.session() as database_session:
            row = database_session.get(SpeechRuntimeInstanceRow, instance_id)
            failed_request = (
                database_session.query(SpeechProviderRequestRow)
                .filter_by(job_id=second_queued["jobId"])
                .one()
            )
            assert row is not None
            warnings = json.loads(row.warnings_json)
        assert row.worker_pid == identity.pid
        assert row.state == "stopped"
        assert row.stopped_at is not None
        assert warnings["stopReasonCode"] == "idle"
        assert warnings["exitEvidence"]["pid"] == identity.pid
        assert warnings["exitEvidence"]["graceful_shutdown_confirmed"] is True
        failed_details = json.loads(failed_request.provenance_json)["details"]
        assert failed_request.runtime_instance_id is None
        assert failed_request.started_at is None
        assert failed_details["executionClassification"] == "provider_execution"
        assert failed_details["providerDispatchCount"] == 0
        assert repository._runtimes == {}
        assert created_runtimes == [runtime]

        retried = client.post(
            f"/api/v1/jobs/{second_queued['jobId']}/retry",
            headers=auth_headers,
        )
        assert retried.status_code == 200, retried.text
        assert (
            wait_for_job(
                client,
                auth_headers,
                second_queued["jobId"],
                {"succeeded", "failed"},
                timeout=30.0,
            )["state"]
            == "succeeded"
        )
        assert len(created_runtimes) == 2
        with repository._runtime_lock:
            replacement_runtime, replacement_instance_id = next(iter(repository._runtimes.values()))
            replacement_identity = replacement_runtime.identity
            assert replacement_identity is not None
        assert replacement_runtime is created_runtimes[1]
        assert replacement_instance_id != instance_id
        assert replacement_identity.creation_nonce != identity.creation_nonce
        with client.app.state.database.session() as database_session:
            prior_row = database_session.get(SpeechRuntimeInstanceRow, instance_id)
            replacement_row = database_session.get(
                SpeechRuntimeInstanceRow,
                replacement_instance_id,
            )
            assert prior_row is not None
            assert replacement_row is not None
            assert replacement_row.worker_pid == replacement_identity.pid
            assert replacement_row.state == "idle"
            assert replacement_row.creation_identity != prior_row.creation_identity


def test_acquisition_never_resurrects_a_terminal_runtime_row(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        project_id, first_session, first_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-runtime-terminal-reuse",
        )
        workspace = _workspace(client, auth_headers, project_id)
        role_id = workspace["roles"]["items"][0]["roleId"]
        second_session, _script, second_request = _create_session_and_script(
            client,
            auth_headers,
            project_id=project_id,
            role_id=role_id,
            text="Repository-owned terminal runtime reuse boundary.",
            key="phase3b-runtime-terminal-reuse-second",
        )
        first_queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=first_session["auditionSessionId"],
            generation_request=first_request,
        )
        assert (
            wait_for_job(
                client,
                auth_headers,
                first_queued["jobId"],
                {"succeeded"},
                timeout=30.0,
            )["state"]
            == "succeeded"
        )
        repository = cast(AuditionRepository, client.app.state.auditions)
        with repository._runtime_lock:
            runtime, instance_id = next(iter(repository._runtimes.values()))
            identity = runtime.identity
            assert identity is not None
        with client.app.state.database.immediate_session() as database_session:
            row = database_session.get(SpeechRuntimeInstanceRow, instance_id)
            assert row is not None
            row.state = "failed"
            row.health_status = "unavailable"

        second_queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=second_session["auditionSessionId"],
            generation_request=second_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            second_queued["jobId"],
            {"failed"},
            timeout=30.0,
        )
        assert terminal["error"]["code"] == "AUDITION_RUNTIME_IDENTITY_INVALID"
        assert terminal["error"]["retryable"] is True
        assert runtime.is_running is True
        assert runtime.identity == identity
        with repository._runtime_lock:
            assert (runtime, instance_id) in repository._runtimes.values()


def test_incomplete_live_teardown_is_quarantined_and_never_reused(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        repository = cast(AuditionRepository, client.app.state.auditions)
        created_runtimes: list[ManagedSpeechRuntime] = []

        def runtime_factory(config: SpeechRuntimeConfig) -> ManagedSpeechRuntime:
            runtime: ManagedSpeechRuntime
            if not created_runtimes:
                runtime = _IncompleteTerminationRuntime(config)
            else:
                runtime = ManagedSpeechRuntime(config)
            created_runtimes.append(runtime)
            return runtime

        repository._runtime_factory = runtime_factory
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-runtime-incomplete-live-teardown",
        )
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"failed"},
            timeout=30.0,
        )
        assert terminal["error"]["code"] == "AUDITION_FAILED"
        poisoned_runtime = created_runtimes[0]
        poisoned_identity = poisoned_runtime.identity
        assert poisoned_identity is not None
        assert poisoned_runtime.is_running is True

        with client.app.state.database.session() as database_session:
            request = (
                database_session.query(SpeechProviderRequestRow)
                .filter_by(job_id=queued["jobId"])
                .one()
            )
            runtime_row = (
                database_session.query(SpeechRuntimeInstanceRow)
                .filter_by(worker_pid=poisoned_identity.pid)
                .one()
            )
            warnings = json.loads(runtime_row.warnings_json)
        request_details = json.loads(request.provenance_json)["details"]
        assert request.runtime_instance_id is None
        assert request.started_at is None
        assert request_details["providerDispatchCount"] == 0
        assert runtime_row.state == "failed"
        assert warnings["exitEvidence"]["confirmed_exited"] is False
        with repository._runtime_lock:
            assert repository._runtimes == {}
            quarantine = repository._quarantined_runtimes[runtime_row.id]
            assert quarantine.runtime is poisoned_runtime
            assert quarantine.identity == poisoned_identity
            assert quarantine.bound is True

        retried = client.post(
            f"/api/v1/jobs/{queued['jobId']}/retry",
            headers=auth_headers,
        )
        assert retried.status_code == 200, retried.text
        retry_terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded", "failed"},
            timeout=30.0,
        )
        assert retry_terminal["state"] == "succeeded"
        assert len(created_runtimes) == 2
        assert created_runtimes[1] is not poisoned_runtime


def test_committed_dispatch_failure_retains_exact_attempt_evidence_without_retry(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    with TestClient(create_app(settings)) as client:
        repository = cast(AuditionRepository, client.app.state.auditions)
        runtime: _CommittedDispatchFailureRuntime | None = None

        def runtime_factory(config: SpeechRuntimeConfig) -> ManagedSpeechRuntime:
            nonlocal runtime
            assert runtime is None
            runtime = _CommittedDispatchFailureRuntime(config)
            return runtime

        repository._runtime_factory = runtime_factory
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-runtime-committed-dispatch-failure",
        )
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"failed"},
            timeout=30.0,
        )
        assert terminal["error"]["code"] == "AUDITION_FAILED"
        assert runtime is not None
        assert runtime.dispatch_calls == 1
        with client.app.state.database.session() as database_session:
            request = (
                database_session.query(SpeechProviderRequestRow)
                .filter_by(job_id=queued["jobId"])
                .one()
            )
            assert request.runtime_instance_id is not None
            runtime_row = database_session.get(
                SpeechRuntimeInstanceRow,
                request.runtime_instance_id,
            )
            assert runtime_row is not None
        details = json.loads(request.provenance_json)["details"]
        assert details["executionClassification"] == "provider_execution"
        assert details["providerDispatchCount"] == 1
        assert request.started_at is not None
        assert runtime_row.state == "failed"
        assert runtime_row.stopped_at is not None
        assert repository._runtimes == {}


def test_startup_reconciles_prior_live_state_without_pid_claim_or_termination(
    settings: ServiceSettings,
    auth_headers: dict[str, str],
) -> None:
    project_id = ""
    instance_id = ""
    worker_pid = 0
    with TestClient(create_app(settings)) as client:
        project_id, audition_session, generation_request = _prepare_generation(
            client,
            auth_headers,
            key="phase3b-runtime-restart-reconciliation",
        )
        queued = _queue_generation(
            client,
            auth_headers,
            project_id=project_id,
            session_id=audition_session["auditionSessionId"],
            generation_request=generation_request,
        )
        terminal = wait_for_job(
            client,
            auth_headers,
            queued["jobId"],
            {"succeeded"},
            timeout=30.0,
        )
        assert terminal["state"] == "succeeded"
        repository = cast(AuditionRepository, client.app.state.auditions)
        with repository._runtime_lock:
            runtime, instance_id = next(iter(repository._runtimes.values()))
            repository._runtimes.clear()
        evidence = runtime.stop(reason="clean")
        assert evidence is not None
        assert evidence.graceful_shutdown_confirmed is True
        worker_pid = evidence.pid
        with client.app.state.database.immediate_session() as database_session:
            row = database_session.get(SpeechRuntimeInstanceRow, instance_id)
            assert row is not None
            row.state = "idle"
            row.health_status = "available"
            row.exit_code = None
            row.stopped_at = None

    with TestClient(create_app(settings)) as restarted:
        workspace = _workspace(restarted, auth_headers, project_id)
        reconciled = next(
            value
            for value in workspace["runtimeInstances"]
            if value["runtimeInstanceId"] == instance_id
        )
        assert reconciled["workerPid"] == worker_pid
        assert reconciled["state"] == "failed"
        assert reconciled["stoppedAt"] is None
        assert reconciled["stopReasonCode"] is None
        assert reconciled["exitCode"] is None
        assert reconciled["shutdownAcknowledged"] is None
        assert reconciled["gracefulShutdownConfirmed"] is None
        assert reconciled["ownershipConfirmed"] is None
        assert reconciled["terminatedByParent"] is None
        assert reconciled["confirmedExited"] is None
        assert reconciled["ownedProcessesConfirmedExited"] is None
        restart_reconciliation = reconciled["restartReconciliation"]
        assert restart_reconciliation == {
            "contractVersion": "1.0.0",
            "reasonCode": "SERVICE_RESTART_INTERRUPTED",
            "priorState": "idle",
            "observedAt": restart_reconciliation["observedAt"],
            "observerServiceInstanceId": restarted.app.state.settings.instance_id,
            "ownershipConfirmed": False,
            "gracefulShutdownConfirmed": False,
            "processExitConfirmed": False,
        }
        assert isinstance(restart_reconciliation["observedAt"], str)
        with restarted.app.state.database.session() as database_session:
            row = database_session.get(SpeechRuntimeInstanceRow, instance_id)
            assert row is not None
            warnings = json.loads(row.warnings_json)
        assert "exitEvidence" not in warnings
        assert warnings["stopReasonCode"] == "service_restart_interrupted"
        assert warnings["restartReconciliation"]["priorState"] == "idle"
        assert warnings["restartReconciliation"]["ownershipConfirmed"] is False
        assert warnings["restartReconciliation"]["gracefulShutdownConfirmed"] is False
        assert warnings["restartReconciliation"]["processExitConfirmed"] is False
