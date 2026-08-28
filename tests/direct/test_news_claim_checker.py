import json

STAKE = 5 * 10**17

CLAIM_TEXT = "The city council voted unanimously to ban plastic straws."
CONTEXT_URL = "https://example.com/news/council-vote"

WEB_REGEX = r"https://example\.com/news/.*"
LLM_REGEX = r"Fact-check this claim"

PAGE_BODY = json.dumps({"title": "Council vote", "content": "The council voted 9-0."})


def _deploy(direct_deploy):
    return direct_deploy("contracts/NewsClaimChecker.py")


def _submit_claim(direct_vm, contract, reporter, claim_id="claim-1", text=CLAIM_TEXT):
    direct_vm.sender = reporter
    direct_vm.value = STAKE
    contract.submit_claim(claim_id, text, CONTEXT_URL)
    direct_vm.value = 0


def test_submit_and_verify_true_claim_returns_stake_as_credit(
    direct_vm, direct_deploy, direct_alice
):
    """A TRUE verdict verifies the claim and credits the reporter's stake back."""
    contract = _deploy(direct_deploy)
    _submit_claim(direct_vm, contract, direct_alice)

    stored = contract.get_claim("claim-1")
    assert len(stored["reporter"]) > 0
    assert stored["text"] == CLAIM_TEXT
    assert stored["context_url"] == CONTEXT_URL
    assert stored["stake_atto"] == STAKE
    assert stored["status"] == "open"
    assert stored["verdict"] == ""
    assert stored["confidence"] == 0
    assert contract.total_claims() == 1

    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"verdict": "TRUE", "confidence": 92, "reasoning": "page confirms vote"}),
    )

    contract.verify("claim-1")

    verified = contract.get_claim("claim-1")
    assert verified["status"] == "verified"
    assert verified["verdict"] == "TRUE"
    assert verified["confidence"] == 92
    assert verified["reasoning"] == "page confirms vote"
    assert contract.credit_of(direct_alice) == STAKE


def test_duplicate_claim_id_is_rejected(direct_vm, direct_deploy, direct_alice):
    """Submitting two claims with the same id is rejected."""
    contract = _deploy(direct_deploy)
    _submit_claim(direct_vm, contract, direct_alice, claim_id="claim-1")
    _submit_claim(direct_vm, contract, direct_alice, claim_id="claim-2", text="Other claim")

    direct_vm.sender = direct_alice
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Claim id already exists"):
        contract.submit_claim("claim-1", "Duplicate id claim", CONTEXT_URL)
    direct_vm.value = 0
    assert contract.total_claims() == 2


def test_low_stake_is_reverted(direct_vm, direct_deploy, direct_alice):
    """Stakes below MIN_STAKE are rejected and no claim is stored."""
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = 10**17 - 1
    with direct_vm.expect_revert("[EXPECTED]"):
        contract.submit_claim("claim-low", CLAIM_TEXT, CONTEXT_URL)
    direct_vm.value = 0
    assert contract.total_claims() == 0

    direct_vm.value = 10**17
    contract.submit_claim("claim-min", CLAIM_TEXT, CONTEXT_URL)
    direct_vm.value = 0
    assert contract.total_claims() == 1


def test_unverifiable_verdict_keeps_stake_with_contract(
    direct_vm, direct_deploy, direct_alice
):
    """An UNVERIFIABLE verdict verifies the claim but the stake is not returned."""
    contract = _deploy(direct_deploy)
    _submit_claim(direct_vm, contract, direct_alice)

    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"verdict": "UNVERIFIABLE", "confidence": 40, "reasoning": "page unrelated"}),
    )

    contract.verify("claim-1")

    verified = contract.get_claim("claim-1")
    assert verified["status"] == "verified"
    assert verified["verdict"] == "UNVERIFIABLE"
    assert contract.credit_of(direct_alice) == 0


def test_false_verdict_credits_reporter(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Anyone can verify; a FALSE verdict still returns the stake to the reporter."""
    contract = _deploy(direct_deploy)
    _submit_claim(direct_vm, contract, direct_alice)

    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"verdict": "FALSE", "confidence": 77, "reasoning": "vote never happened"}),
    )

    with direct_vm.prank(direct_bob):
        contract.verify("claim-1")

    verified = contract.get_claim("claim-1")
    assert verified["verdict"] == "FALSE"
    assert contract.credit_of(direct_alice) == STAKE
    assert contract.credit_of(direct_bob) == 0


def test_double_verify_is_reverted(direct_vm, direct_deploy, direct_alice):
    """An already-verified claim cannot be verified again."""
    contract = _deploy(direct_deploy)
    _submit_claim(direct_vm, contract, direct_alice)

    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"verdict": "TRUE", "confidence": 90, "reasoning": "confirmed"}),
    )
    contract.verify("claim-1")

    with direct_vm.expect_revert("Claim already verified"):
        contract.verify("claim-1")

    assert contract.credit_of(direct_alice) == STAKE


def test_invalid_llm_verdict_reverts_with_llm_error(direct_vm, direct_deploy, direct_alice):
    """An LLM verdict outside the four allowed categories raises [LLM_ERROR]."""
    contract = _deploy(direct_deploy)
    _submit_claim(direct_vm, contract, direct_alice)

    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"verdict": "MAYBE", "confidence": 50, "reasoning": "unsure"}),
    )

    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.verify("claim-1")

    assert contract.get_claim("claim-1")["status"] == "open"
    assert contract.credit_of(direct_alice) == 0


def test_unknown_claim_verify_is_reverted(direct_vm, direct_deploy, direct_alice):
    """Verifying a claim id that was never submitted is rejected."""
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("Unknown claim id"):
        contract.verify("missing-claim")


def test_missing_source_page_404_reverts_external(direct_vm, direct_deploy, direct_alice):
    """A 404 from the source page aborts verification with an [EXTERNAL] error."""
    contract = _deploy(direct_deploy)
    _submit_claim(direct_vm, contract, direct_alice)

    direct_vm.mock_web(WEB_REGEX, {"status": 404, "body": "not found"})

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.verify("claim-1")

    assert contract.get_claim("claim-1")["status"] == "open"


def test_withdraw_returns_credited_stake(direct_vm, direct_deploy, direct_alice):
    """After a TRUE verdict the reporter withdraws the credited stake."""
    contract = _deploy(direct_deploy)
    _submit_claim(direct_vm, contract, direct_alice)

    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"verdict": "TRUE", "confidence": 88, "reasoning": "confirmed"}),
    )
    contract.verify("claim-1")

    direct_vm.sender = direct_alice
    contract.withdraw()
    assert contract.credit_of(direct_alice) == 0


def test_empty_claim_inputs_and_non_https_rejected(direct_vm, direct_deploy, direct_alice):
    """Empty claim id, empty text, or non-HTTPS URLs are rejected."""
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = STAKE

    with direct_vm.expect_revert("must not be empty"):
        contract.submit_claim("claim-bad", "   ", "https://news.example.com/page")

    with direct_vm.expect_revert("Context URL must start with https://"):
        contract.submit_claim("claim-bad-url", "Valid text", "http://insecure.example.com/page")

    with direct_vm.expect_revert("Nothing to withdraw"):
        contract.withdraw()
