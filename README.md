# NewsClaimChecker

Staked fact-checking on GenLayer. A reporter posts a claim together with a context URL and locks a GEN stake on it. Anyone can trigger verification: the leader node fetches the source page, asks an LLM for a verdict (TRUE / FALSE / MISLEADING / UNVERIFIABLE), and validators independently rerun the fetch + prompt and must land on the exact same verdict category. If the verdict is anything but UNVERIFIABLE, the stake is returned to the reporter as a withdrawable credit; an UNVERIFIABLE claim forfeits the stake to the contract.

## Architecture

- **User action** — `submit_claim(claim_id, text, context_url)` with `msg.value >= MIN_STAKE` (0.1 GEN) opens a claim; `verify(claim_id)` triggers resolution.
- **Evidence source** — the leader fetches the context URL via `gl.nondet.web.get(context_url)`; HTTP 4xx aborts with `[EXTERNAL]`, 5xx with `[TRANSIENT]`. The first 4000 characters of the page body are embedded in the prompt.
- **Nondet call** — `gl.nondet.exec_prompt(..., response_format="json")` asks for `{"verdict", "confidence", "reasoning"}`; output is defensively parsed (`_parse_llm_json`) and the verdict must be one of the four categories or the call reverts `[LLM_ERROR]`.
- **Equivalence principle** — custom validator reruns the whole leader function (fetch + prompt) and accepts only **exact verdict-category agreement** between leader and fresh rerun. Leader errors are reconciled through `_handle_leader_error` ([EXPECTED]/[EXTERNAL] must match exactly, [TRANSIENT] matches by class).
- **Settlement effect** — verdict/confidence/reasoning persist on the claim and status flips to `verified`. Non-UNVERIFIABLE verdicts credit the stake back to the reporter; UNVERIFIABLE keeps the stake in the contract.
- **Appeal path** — none in this contract; GenLayer Optimistic Democracy provides leader-proposes / validator-check / appeal window natively around each nondet decision.

## Quickstart

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# lint
.venv/bin/genvm-lint check contracts/NewsClaimChecker.py --json

# direct tests
.venv/bin/pytest tests/direct/ -v
```

## Interface

| Method | Type | Notes |
| --- | --- | --- |
| `owner()` | view | Contract owner (deployer), as string |
| `is_vetted_domain(domain)` | view | Checks if a news domain is on the approved authoritative list |
| `add_vetted_domain(domain)` | write | Owner-only; whitelists new authoritative news sources |
| `submit_claim(claim_id, text, context_url)` | write, payable | requires `value >= MIN_STAKE` (0.1 GEN); unique id; enforces approved authoritative news domain |
| `verify(claim_id)` | write | leader fetch + LLM verdict; validator reruns and must agree exactly; isolates web text as untrusted input; returns stake only on TRUE |
| `withdraw()` | write | pays out accumulated credits to sender |
| `get_claim(claim_id)` | view | full claim record; `reporter` as string |
| `credit_of(who)` | view | withdrawable credit balance |
| `total_claims()` | view | number of submitted claims |

Verdicts: `TRUE` (refunds stake) / `FALSE` (forfeits stake) / `MISLEADING` (forfeits stake) / `UNVERIFIABLE` (forfeits stake).

## Deployment

- **Network**: StudioNet (GenLayer)
- **Contract Address**: `0x79854d1c034EAc93AE0B6E4DE7566541a0DD38F9`
- **Explorer**: [https://explorer-studio.genlayer.com/address/0x79854d1c034EAc93AE0B6E4DE7566541a0DD38F9](https://explorer-studio.genlayer.com/address/0x79854d1c034EAc93AE0B6E4DE7566541a0DD38F9)

> **StudioNet note:** gasless network — 0 GEN balances are fine for testing.

