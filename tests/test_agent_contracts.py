from __future__ import annotations

from dataclasses import replace

import pytest

from packages.agent_contracts import (
    ApprovalRequirement,
    AuthorityReference,
    ContractViolation,
    EvidenceReference,
    MutationClass,
    PolicyDisposition,
    PolicyPhase,
    ProviderVerification,
    ReceiptStatus,
    ReplicationIdentityStatus,
    ReplicationObservation,
    ReplicationOperation,
    Service,
    SourceIntegrityStatus,
    VerificationStatus,
    build_structured_receipt,
    canonical_sha256,
    classify_replication,
    propose_replication,
    resolve_replication_policy,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
GIT_SHA = "c" * 40


def _observation() -> ReplicationObservation:
    authority = AuthorityReference(
        service=Service.REPLICATION,
        canonical_repository="Seemorghdev/edge-evidence-platform",
        canonical_commit=GIT_SHA,
        authority_instance="public-contract-fixture",
        projection_schema="operational-truth.v1",
        contract_version="bounded-agent-contracts.v2",
        database_schema_version=10,
        observed_snapshot_sha256=SHA_A,
        proof_class="model-free-public-contract",
    )
    finding = "REPLICATION_PENDING_BACKLOG"
    evidence = EvidenceReference(
        finding_code=finding,
        evidence_code="replication-backlog-counts-v1",
        authority_reference_sha256=authority.digest_sha256,
        evidence_sha256=SHA_B,
    )
    return ReplicationObservation(
        authority=authority,
        semantic_input_sha256=SHA_A,
        finding_codes=(finding,),
        evidence_references=(evidence,),
        target_id="target-001",
        adapter_kind="mounted-nfs-v4",
        identity_status=ReplicationIdentityStatus.MATCH,
        provider_verification=ProviderVerification.PASS,
        source_integrity_status=SourceIntegrityStatus.PASS,
        eligible_objects=1,
        registered_objects=1,
        unregistered_objects=0,
        pending_objects=1,
        verified_objects=0,
        failed_pending_objects=0,
    )


def test_replication_contract_is_model_free_deterministic_and_read_only() -> None:
    observation = _observation()
    classification = classify_replication(observation)
    proposal = propose_replication(observation, classification)
    receipt = build_structured_receipt(
        authority=observation.authority,
        observation=observation,
        classification=classification,
        proposal=proposal,
    )

    assert classification.primary_code == "REPLICATION_PENDING_BACKLOG"
    assert proposal.operation_code is ReplicationOperation.PROPOSE_RUN
    assert proposal.policy_phase is PolicyPhase.WAVE_C
    assert proposal.disposition is PolicyDisposition.ALLOW
    assert proposal.mutation_class is MutationClass.PROPOSAL_ONLY
    assert proposal.approval_requirement is ApprovalRequirement.NONE
    assert receipt.final_status is ReceiptStatus.READ_ONLY_COMPLETE
    assert receipt.approval_sha256 is None
    assert receipt.execution_sha256 is None
    assert receipt.verification_sha256 is None
    assert canonical_sha256(receipt) == receipt.digest_sha256


def test_replication_wave_c_forbids_provider_and_credential_changes() -> None:
    provider = resolve_replication_policy(ReplicationOperation.PROVISION_PROVIDER)
    credentials = resolve_replication_policy(
        ReplicationOperation.CHANGE_IAM_OR_CREDENTIALS
    )
    assert (provider.wave_c, provider.future_successor) == (
        PolicyDisposition.FORBID,
        PolicyDisposition.ESCALATE,
    )
    assert (credentials.wave_c, credentials.future_successor) == (
        PolicyDisposition.FORBID,
        PolicyDisposition.ESCALATE,
    )
    assert VerificationStatus.ESCALATE.value == "escalate"
    assert ReceiptStatus.ESCALATED.value == "escalated"


def test_replication_contract_rejects_forged_classification() -> None:
    observation = _observation()
    classification = classify_replication(observation)
    forged = replace(classification, primary_code="REPLICATION_CONVERGED")
    proposal = propose_replication(observation, classification)

    with pytest.raises(ContractViolation):
        build_structured_receipt(
            authority=observation.authority,
            observation=observation,
            classification=forged,
            proposal=proposal,
        )
