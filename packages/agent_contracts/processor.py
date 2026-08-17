"""Deterministic processor observation classification and proposal design."""

from __future__ import annotations

from enum import Enum

from .model import (
    Actionability,
    Classification,
    ContractViolation,
    MutationClass,
    PolicyDisposition,
    PolicyPhase,
    ProcessorIntegrityStatus,
    ProcessorObservation,
    ProcessorOperation,
    Proposal,
    Service,
    Severity,
)
from .policy import resolve_processor_policy

_PROCESSOR_POLICY_VERSION = "processor-mutation-policy.v2"


class ProcessorClassificationCode(str, Enum):
    INTEGRITY_FAILURE = "PROCESSOR_INTEGRITY_FAILURE"
    IDENTITY_CONFLICT = "PROCESSOR_IDENTITY_CONFLICT"
    UNAVAILABLE = "PROCESSOR_UNAVAILABLE"
    PREPARED_BACKLOG = "PROCESSOR_PREPARED_BACKLOG"
    MISSING_BACKLOG = "PROCESSOR_MISSING_BACKLOG"
    DEFERRED_BUSY = "PROCESSOR_DEFERRED_BUSY"
    CONVERGED = "PROCESSOR_CONVERGED"


def _findings(observation: ProcessorObservation) -> tuple[str, ...]:
    values: set[str] = set()
    if observation.integrity_status is ProcessorIntegrityStatus.FAIL:
        values.add(ProcessorClassificationCode.INTEGRITY_FAILURE.value)
    if observation.integrity_status is ProcessorIntegrityStatus.UNAVAILABLE:
        values.add(ProcessorClassificationCode.UNAVAILABLE.value)
    if observation.identity_conflict_count:
        values.add(ProcessorClassificationCode.IDENTITY_CONFLICT.value)
    if observation.prepared_count:
        values.add(ProcessorClassificationCode.PREPARED_BACKLOG.value)
    if observation.missing_count:
        values.add(ProcessorClassificationCode.MISSING_BACKLOG.value)
    if observation.deferred_lock_count:
        values.add(ProcessorClassificationCode.DEFERRED_BUSY.value)
    if not values:
        values.add(ProcessorClassificationCode.CONVERGED.value)
    return tuple(sorted(values))


def classify_processor(observation: ProcessorObservation) -> Classification:
    if not isinstance(observation, ProcessorObservation):
        raise ContractViolation("processor classifier requires ProcessorObservation")
    findings = _findings(observation)
    if observation.finding_codes != findings:
        raise ContractViolation("processor observation findings contradict state")
    if observation.integrity_status is ProcessorIntegrityStatus.FAIL:
        code, severity, action = ProcessorClassificationCode.INTEGRITY_FAILURE, Severity.FAIL, Actionability.ESCALATE
    elif observation.identity_conflict_count:
        code, severity, action = ProcessorClassificationCode.IDENTITY_CONFLICT, Severity.FAIL, Actionability.ESCALATE
    elif observation.integrity_status is ProcessorIntegrityStatus.UNAVAILABLE:
        code, severity, action = ProcessorClassificationCode.UNAVAILABLE, Severity.UNAVAILABLE, Actionability.RETRY
    elif observation.prepared_count:
        code, severity, action = ProcessorClassificationCode.PREPARED_BACKLOG, Severity.ATTENTION, Actionability.PROPOSE
    elif observation.missing_count:
        code, severity, action = ProcessorClassificationCode.MISSING_BACKLOG, Severity.ATTENTION, Actionability.PROPOSE
    elif observation.deferred_lock_count:
        code, severity, action = ProcessorClassificationCode.DEFERRED_BUSY, Severity.ATTENTION, Actionability.RETRY
    else:
        code, severity, action = ProcessorClassificationCode.CONVERGED, Severity.PASS, Actionability.NONE
    return Classification(
        service=Service.PROCESSOR,
        primary_code=code.value,
        severity=severity,
        actionability=action,
        finding_codes=findings,
        observation_sha256=observation.digest_sha256,
        classifier_version="processor-classifier.v1",
    )


def validate_processor_classification(
    observation: ProcessorObservation,
    classification: Classification,
) -> None:
    if not isinstance(observation, ProcessorObservation):
        raise ContractViolation("processor classification validation requires ProcessorObservation")
    if not isinstance(classification, Classification):
        raise ContractViolation("classification must be Classification")
    expected = classify_processor(observation)
    if classification != expected:
        raise ContractViolation("processor classification contradicts observation")


def _allowed_operations(classification: Classification) -> frozenset[ProcessorOperation]:
    code = classification.primary_code
    if code == ProcessorClassificationCode.CONVERGED.value:
        return frozenset({ProcessorOperation.NO_ACTION})
    if code in {
        ProcessorClassificationCode.DEFERRED_BUSY.value,
        ProcessorClassificationCode.UNAVAILABLE.value,
    }:
        return frozenset({ProcessorOperation.RETRY_OBSERVATION})
    if code in {
        ProcessorClassificationCode.MISSING_BACKLOG.value,
        ProcessorClassificationCode.PREPARED_BACKLOG.value,
    }:
        return frozenset({
            ProcessorOperation.PROPOSE_CATCH_UP,
            ProcessorOperation.INVOKE_WORKER_RUN,
        })
    return frozenset({ProcessorOperation.ESCALATE_STATE})


def _parameters(
    observation: ProcessorObservation,
    operation: ProcessorOperation,
) -> tuple[tuple[str, object], ...]:
    if operation is ProcessorOperation.NO_ACTION:
        return ()
    if operation in {
        ProcessorOperation.RETRY_OBSERVATION,
        ProcessorOperation.ESCALATE_STATE,
    }:
        return (("expected_snapshot_sha256", observation.semantic_input_sha256),)
    if operation in {
        ProcessorOperation.PROPOSE_CATCH_UP,
        ProcessorOperation.INVOKE_WORKER_RUN,
    }:
        return tuple(sorted((
            ("eligible_count", observation.eligible_count),
            ("expected_snapshot_sha256", observation.semantic_input_sha256),
        )))
    raise ContractViolation("processor operation is not proposal-compatible")


def _make_proposal(
    observation: ProcessorObservation,
    classification: Classification,
    operation: ProcessorOperation,
    phase: PolicyPhase,
) -> Proposal:
    validate_processor_classification(observation, classification)
    if operation not in _allowed_operations(classification):
        raise ContractViolation("processor operation contradicts classification")
    row = resolve_processor_policy(operation)
    disposition = row.disposition_for(phase)
    if disposition is PolicyDisposition.FORBID or row.mutation_class is MutationClass.FORBIDDEN:
        raise ContractViolation("processor operation is forbidden in selected policy phase")
    return Proposal(
        service=Service.PROCESSOR,
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
        policy_version=_PROCESSOR_POLICY_VERSION,
    )


def propose_processor(
    observation: ProcessorObservation,
    classification: Classification | None = None,
) -> Proposal:
    classification = classification or classify_processor(observation)
    validate_processor_classification(observation, classification)
    code = classification.primary_code
    if code == ProcessorClassificationCode.CONVERGED.value:
        operation = ProcessorOperation.NO_ACTION
    elif code in {
        ProcessorClassificationCode.DEFERRED_BUSY.value,
        ProcessorClassificationCode.UNAVAILABLE.value,
    }:
        operation = ProcessorOperation.RETRY_OBSERVATION
    elif code in {
        ProcessorClassificationCode.MISSING_BACKLOG.value,
        ProcessorClassificationCode.PREPARED_BACKLOG.value,
    }:
        operation = ProcessorOperation.PROPOSE_CATCH_UP
    else:
        operation = ProcessorOperation.ESCALATE_STATE
    return _make_proposal(
        observation,
        classification,
        operation,
        PolicyPhase.WAVE_C,
    )


def validate_processor_proposal(
    observation: ProcessorObservation,
    classification: Classification,
    proposal: Proposal,
) -> None:
    validate_processor_classification(observation, classification)
    if not isinstance(proposal, Proposal):
        raise ContractViolation("proposal must be Proposal")
    if proposal.service is not Service.PROCESSOR:
        raise ContractViolation("processor proposal has wrong service")
    if not isinstance(proposal.operation_code, ProcessorOperation):
        raise ContractViolation("processor proposal has wrong operation type")
    if proposal.operation_code not in _allowed_operations(classification):
        raise ContractViolation("processor proposal contradicts classification")
    row = resolve_processor_policy(proposal.operation_code)
    expected_disposition = row.disposition_for(proposal.policy_phase)
    if expected_disposition is PolicyDisposition.FORBID:
        raise ContractViolation("processor proposal operation is forbidden in selected phase")
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
        "policy_version": _PROCESSOR_POLICY_VERSION,
    }
    for field, value in expected.items():
        if getattr(proposal, field) != value:
            raise ContractViolation(f"processor proposal {field} differs from frozen policy")
