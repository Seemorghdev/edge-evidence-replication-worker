"""Deterministic replication observation classification and proposal design."""

from __future__ import annotations

from enum import Enum

from .model import (
    Actionability,
    Classification,
    ContractViolation,
    MutationClass,
    PolicyDisposition,
    PolicyPhase,
    ProviderVerification,
    Proposal,
    ReplicationIdentityStatus,
    ReplicationObservation,
    ReplicationOperation,
    Service,
    Severity,
    SourceIntegrityStatus,
)
from .policy import resolve_replication_policy

_REPLICATION_POLICY_VERSION = "replication-mutation-policy.v2"


class ReplicationClassificationCode(str, Enum):
    IDENTITY_MISMATCH = "REPLICATION_IDENTITY_MISMATCH"
    SOURCE_INTEGRITY_FAILURE = "REPLICATION_SOURCE_INTEGRITY_FAILURE"
    TARGET_VERIFICATION_FAILURE = "REPLICATION_TARGET_VERIFICATION_FAILURE"
    TARGET_UNAVAILABLE = "REPLICATION_TARGET_UNAVAILABLE"
    FAILED_PENDING = "REPLICATION_FAILED_PENDING"
    UNREGISTERED_BACKLOG = "REPLICATION_UNREGISTERED_BACKLOG"
    PENDING_BACKLOG = "REPLICATION_PENDING_BACKLOG"
    CONVERGED = "REPLICATION_CONVERGED"
    NOT_CONFIGURED = "REPLICATION_NOT_CONFIGURED"


def _findings(observation: ReplicationObservation) -> tuple[str, ...]:
    values: set[str] = set()
    if observation.identity_status is ReplicationIdentityStatus.MISMATCH:
        values.add(ReplicationClassificationCode.IDENTITY_MISMATCH.value)
    if observation.identity_status is ReplicationIdentityStatus.NOT_CONFIGURED:
        values.add(ReplicationClassificationCode.NOT_CONFIGURED.value)
    if observation.identity_status is ReplicationIdentityStatus.UNAVAILABLE:
        values.add(ReplicationClassificationCode.TARGET_UNAVAILABLE.value)
    if observation.source_integrity_status is SourceIntegrityStatus.FAIL:
        values.add(ReplicationClassificationCode.SOURCE_INTEGRITY_FAILURE.value)
    if observation.source_integrity_status is SourceIntegrityStatus.UNAVAILABLE:
        values.add(ReplicationClassificationCode.TARGET_UNAVAILABLE.value)
    if observation.provider_verification is ProviderVerification.FAIL:
        values.add(ReplicationClassificationCode.TARGET_VERIFICATION_FAILURE.value)
    if observation.provider_verification is ProviderVerification.UNAVAILABLE:
        values.add(ReplicationClassificationCode.TARGET_UNAVAILABLE.value)
    if observation.failed_pending_objects:
        values.add(ReplicationClassificationCode.FAILED_PENDING.value)
    if observation.unregistered_objects:
        values.add(ReplicationClassificationCode.UNREGISTERED_BACKLOG.value)
    if observation.pending_objects:
        values.add(ReplicationClassificationCode.PENDING_BACKLOG.value)
    if not values:
        values.add(ReplicationClassificationCode.CONVERGED.value)
    return tuple(sorted(values))


def classify_replication(observation: ReplicationObservation) -> Classification:
    if not isinstance(observation, ReplicationObservation):
        raise ContractViolation("replication classifier requires ReplicationObservation")
    findings = _findings(observation)
    if observation.finding_codes != findings:
        raise ContractViolation("replication observation findings contradict state")
    if observation.identity_status is ReplicationIdentityStatus.MISMATCH:
        code, severity, action = ReplicationClassificationCode.IDENTITY_MISMATCH, Severity.FAIL, Actionability.ESCALATE
    elif observation.source_integrity_status is SourceIntegrityStatus.FAIL:
        code, severity, action = ReplicationClassificationCode.SOURCE_INTEGRITY_FAILURE, Severity.FAIL, Actionability.ESCALATE
    elif observation.provider_verification is ProviderVerification.FAIL:
        code, severity, action = ReplicationClassificationCode.TARGET_VERIFICATION_FAILURE, Severity.FAIL, Actionability.ESCALATE
    elif observation.identity_status is ReplicationIdentityStatus.NOT_CONFIGURED:
        code, severity, action = ReplicationClassificationCode.NOT_CONFIGURED, Severity.ATTENTION, Actionability.ESCALATE
    elif (
        observation.identity_status is ReplicationIdentityStatus.UNAVAILABLE
        or observation.source_integrity_status is SourceIntegrityStatus.UNAVAILABLE
        or observation.provider_verification is ProviderVerification.UNAVAILABLE
    ):
        code, severity, action = ReplicationClassificationCode.TARGET_UNAVAILABLE, Severity.UNAVAILABLE, Actionability.RETRY
    elif observation.failed_pending_objects:
        code, severity, action = ReplicationClassificationCode.FAILED_PENDING, Severity.FAIL, Actionability.PROPOSE
    elif observation.unregistered_objects:
        code, severity, action = ReplicationClassificationCode.UNREGISTERED_BACKLOG, Severity.ATTENTION, Actionability.PROPOSE
    elif observation.pending_objects:
        code, severity, action = ReplicationClassificationCode.PENDING_BACKLOG, Severity.ATTENTION, Actionability.PROPOSE
    else:
        code, severity, action = ReplicationClassificationCode.CONVERGED, Severity.PASS, Actionability.NONE
    return Classification(
        service=Service.REPLICATION,
        primary_code=code.value,
        severity=severity,
        actionability=action,
        finding_codes=findings,
        observation_sha256=observation.digest_sha256,
        classifier_version="replication-classifier.v1",
    )


def validate_replication_classification(
    observation: ReplicationObservation,
    classification: Classification,
) -> None:
    if not isinstance(observation, ReplicationObservation):
        raise ContractViolation("replication classification validation requires ReplicationObservation")
    if not isinstance(classification, Classification):
        raise ContractViolation("classification must be Classification")
    expected = classify_replication(observation)
    if classification != expected:
        raise ContractViolation("replication classification contradicts observation")


def _allowed_operations(
    observation: ReplicationObservation,
    classification: Classification,
) -> frozenset[ReplicationOperation]:
    code = classification.primary_code
    if code == ReplicationClassificationCode.CONVERGED.value:
        return frozenset({ReplicationOperation.NO_ACTION})
    if code == ReplicationClassificationCode.TARGET_UNAVAILABLE.value:
        if observation.source_integrity_status is SourceIntegrityStatus.UNAVAILABLE:
            return frozenset({ReplicationOperation.RETRY_OBSERVATION})
        return frozenset({
            ReplicationOperation.PROPOSE_VERIFY,
            ReplicationOperation.INVOKE_VERIFY,
        })
    if code == ReplicationClassificationCode.FAILED_PENDING.value:
        return frozenset({
            ReplicationOperation.PROPOSE_RECONCILE,
            ReplicationOperation.INVOKE_RECONCILE,
        })
    if code in {
        ReplicationClassificationCode.UNREGISTERED_BACKLOG.value,
        ReplicationClassificationCode.PENDING_BACKLOG.value,
    }:
        return frozenset({
            ReplicationOperation.PROPOSE_RUN,
            ReplicationOperation.INVOKE_RUN,
        })
    return frozenset({ReplicationOperation.ESCALATE_STATE})


def _parameters(
    observation: ReplicationObservation,
    operation: ReplicationOperation,
) -> tuple[tuple[str, object], ...]:
    if operation is ReplicationOperation.NO_ACTION:
        return ()
    if operation is ReplicationOperation.RETRY_OBSERVATION:
        return tuple(sorted((
            ("expected_snapshot_sha256", observation.semantic_input_sha256),
            ("target_id", observation.target_id),
        )))
    if operation in {
        ReplicationOperation.PROPOSE_VERIFY,
        ReplicationOperation.INVOKE_VERIFY,
        ReplicationOperation.PROPOSE_RECONCILE,
        ReplicationOperation.INVOKE_RECONCILE,
        ReplicationOperation.PROPOSE_RUN,
        ReplicationOperation.INVOKE_RUN,
        ReplicationOperation.ESCALATE_STATE,
    }:
        return tuple(sorted((
            ("expected_snapshot_sha256", observation.semantic_input_sha256),
            ("target_id", observation.target_id),
        )))
    raise ContractViolation("replication operation is not proposal-compatible")


def _make_proposal(
    observation: ReplicationObservation,
    classification: Classification,
    operation: ReplicationOperation,
    phase: PolicyPhase,
) -> Proposal:
    validate_replication_classification(observation, classification)
    if operation not in _allowed_operations(observation, classification):
        raise ContractViolation("replication operation contradicts classification")
    row = resolve_replication_policy(operation)
    disposition = row.disposition_for(phase)
    if disposition is PolicyDisposition.FORBID or row.mutation_class is MutationClass.FORBIDDEN:
        raise ContractViolation("replication operation is forbidden in selected policy phase")
    return Proposal(
        service=Service.REPLICATION,
        authority_reference_sha256=observation.authority.digest_sha256,
        operation_code=operation,
        policy_phase=phase,
        disposition=disposition,
        mutation_class=row.mutation_class,
        exact_parameters=_parameters(observation, operation),
        observation_sha256=observation.digest_sha256,
        classification_sha256=classification.digest_sha256,
        approval_requirement=row.approval_requirement,
        precondition_codes=row.precondition_codes,
        required_evidence_codes=row.required_evidence_codes,
        verification_requirement_codes=row.verification_requirement_codes,
        policy_version=_REPLICATION_POLICY_VERSION,
    )


def propose_replication(
    observation: ReplicationObservation,
    classification: Classification | None = None,
) -> Proposal:
    classification = classification or classify_replication(observation)
    validate_replication_classification(observation, classification)
    code = classification.primary_code
    if code == ReplicationClassificationCode.CONVERGED.value:
        operation = ReplicationOperation.NO_ACTION
    elif code == ReplicationClassificationCode.TARGET_UNAVAILABLE.value:
        operation = (
            ReplicationOperation.RETRY_OBSERVATION
            if observation.source_integrity_status is SourceIntegrityStatus.UNAVAILABLE
            else ReplicationOperation.PROPOSE_VERIFY
        )
    elif code == ReplicationClassificationCode.FAILED_PENDING.value:
        operation = ReplicationOperation.PROPOSE_RECONCILE
    elif code in {
        ReplicationClassificationCode.UNREGISTERED_BACKLOG.value,
        ReplicationClassificationCode.PENDING_BACKLOG.value,
    }:
        operation = ReplicationOperation.PROPOSE_RUN
    else:
        operation = ReplicationOperation.ESCALATE_STATE
    return _make_proposal(
        observation,
        classification,
        operation,
        PolicyPhase.WAVE_C,
    )


def validate_replication_proposal(
    observation: ReplicationObservation,
    classification: Classification,
    proposal: Proposal,
) -> None:
    validate_replication_classification(observation, classification)
    if not isinstance(proposal, Proposal):
        raise ContractViolation("proposal must be Proposal")
    if proposal.service is not Service.REPLICATION:
        raise ContractViolation("replication proposal has wrong service")
    if not isinstance(proposal.operation_code, ReplicationOperation):
        raise ContractViolation("replication proposal has wrong operation type")
    if proposal.operation_code not in _allowed_operations(observation, classification):
        raise ContractViolation("replication proposal contradicts classification")
    row = resolve_replication_policy(proposal.operation_code)
    expected_disposition = row.disposition_for(proposal.policy_phase)
    if expected_disposition is PolicyDisposition.FORBID:
        raise ContractViolation("replication proposal operation is forbidden in selected phase")
    expected = {
        "authority_reference_sha256": observation.authority.digest_sha256,
        "disposition": expected_disposition,
        "mutation_class": row.mutation_class,
        "exact_parameters": _parameters(observation, proposal.operation_code),
        "observation_sha256": observation.digest_sha256,
        "classification_sha256": classification.digest_sha256,
        "approval_requirement": row.approval_requirement,
        "precondition_codes": row.precondition_codes,
        "required_evidence_codes": row.required_evidence_codes,
        "verification_requirement_codes": row.verification_requirement_codes,
        "policy_version": _REPLICATION_POLICY_VERSION,
    }
    for field, value in expected.items():
        if getattr(proposal, field) != value:
            raise ContractViolation(f"replication proposal {field} differs from frozen policy")
