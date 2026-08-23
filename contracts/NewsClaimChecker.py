# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"

STATUS_OPEN = "open"
STATUS_VERIFIED = "verified"

MIN_STAKE = u256(10**17)
MAX_PAGE_CHARS = 4000

VALID_VERDICTS = ("TRUE", "FALSE", "MISLEADING", "UNVERIFIABLE")


def _parse_llm_json(text) -> dict:
	import re
	if isinstance(text, dict):
		return text
	s = str(text)
	first = s.find("{")
	last = s.rfind("}")
	if first == -1 or last <= first:
		raise gl.vm.UserError(f"{ERROR_LLM} no JSON object found in LLM output")
	s = s[first : last + 1]
	s = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", s)
	try:
		parsed = json.loads(s)
	except Exception:
		raise gl.vm.UserError(f"{ERROR_LLM} malformed JSON from LLM")
	if not isinstance(parsed, dict):
		raise gl.vm.UserError(f"{ERROR_LLM} non-dict JSON from LLM")
	return parsed


def _handle_leader_error(leaders_res, leader_fn) -> bool:
	leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
	try:
		leader_fn()
		return False
	except gl.vm.UserError as e:
		validator_msg = e.message if hasattr(e, "message") else str(e)
		if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
			return validator_msg == leader_msg
		if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
			return True
		return False
	except Exception:
		return False


@gl.evm.contract_interface
class _Recipient:
	class View:
		pass

	class Write:
		pass


@allow_storage
@dataclass
class Claim:
	text: str
	context_url: str
	reporter: Address
	stake_atto: u256
	status: str
	verdict: str
	confidence: u256
	reasoning: str


class NewsClaimChecker(gl.Contract):
	claims: TreeMap[str, Claim]
	claim_ids: DynArray[str]
	credits: TreeMap[Address, u256]

	def __init__(self) -> None:
		pass

	def _get_claim(self, claim_id: str) -> Claim:
		claim = self.claims.get(claim_id)
		if claim is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unknown claim id")
		return claim

	@gl.public.write.payable
	def submit_claim(self, claim_id: str, text: str, context_url: str) -> None:
		if gl.message.value < MIN_STAKE:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Stake below minimum of 0.1 GEN")
		if claim_id in self.claims:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Claim id already exists")
		self.claims[claim_id] = Claim(
			text=text,
			context_url=context_url,
			reporter=gl.message.sender_address,
			stake_atto=u256(gl.message.value),
			status=STATUS_OPEN,
			verdict="",
			confidence=u256(0),
			reasoning="",
		)
		self.claim_ids.append(claim_id)

	@gl.public.write
	def verify(self, claim_id: str) -> None:
		claim = self._get_claim(claim_id)
		if claim.status != STATUS_OPEN:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Claim already verified")

		claim_text = str(claim.text)
		context_url = str(claim.context_url)

		def leader_fn() -> dict:
			page_res = gl.nondet.web.get(context_url)
			status_code = int(page_res.status)
			if status_code >= 500:
				raise gl.vm.UserError(f"{ERROR_TRANSIENT} Source page returned HTTP {status_code}")
			if status_code >= 400:
				raise gl.vm.UserError(f"{ERROR_EXTERNAL} Source page returned HTTP {status_code}")
			try:
				body_text = page_res.body.decode("utf-8")[:MAX_PAGE_CHARS]
			except Exception:
				body_text = ""
			prompt = (
				"Fact-check this claim against the source page.\n"
				f"<claim>{claim_text}</claim>\n"
				f"<page>{body_text}</page>\n"
				'Reply JSON {"verdict": "TRUE|FALSE|MISLEADING|UNVERIFIABLE", '
				'"confidence": <int 0-100>, "reasoning": "..."}'
			)
			analysis = gl.nondet.exec_prompt(prompt, response_format="json")
			parsed = _parse_llm_json(analysis)
			raw_verdict = parsed.get("verdict", "")
			verdict = str(raw_verdict).upper().strip()
			if verdict not in VALID_VERDICTS:
				raise gl.vm.UserError(f"{ERROR_LLM} invalid verdict in LLM output")
			raw_confidence = parsed.get("confidence", 0)
			try:
				confidence = int(float(raw_confidence))
			except Exception:
				confidence = 0
			if confidence < 0:
				confidence = 0
			if confidence > 100:
				confidence = 100
			return {
				"verdict": verdict,
				"confidence": int(confidence),
				"reasoning": str(parsed.get("reasoning", "")),
			}

		def validator_fn(leaders_res: gl.vm.Result) -> bool:
			if not isinstance(leaders_res, gl.vm.Return):
				return _handle_leader_error(leaders_res, leader_fn)
			leader_data = leaders_res.calldata
			fresh = leader_fn()
			leader_verdict = str(leader_data.get("verdict", "")).upper().strip()
			fresh_verdict = str(fresh.get("verdict", "")).upper().strip()
			return leader_verdict == fresh_verdict

		result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

		verdict = str(result["verdict"])
		claim.verdict = verdict
		claim.confidence = u256(int(result["confidence"]))
		claim.reasoning = str(result["reasoning"])
		claim.status = STATUS_VERIFIED

		if verdict != "UNVERIFIABLE":
			reporter = claim.reporter
			stake = claim.stake_atto
			self.credits[reporter] = self.credits.get(reporter, u256(0)) + u256(stake)

	@gl.public.write
	def withdraw(self) -> None:
		who = gl.message.sender_address
		amount = self.credits.get(who, u256(0))
		if amount == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Nothing to withdraw")
		self.credits[who] = u256(0)
		_Recipient(who).emit_transfer(value=u256(amount))

	@gl.public.view
	def get_claim(self, claim_id: str) -> dict:
		claim = self._get_claim(claim_id)
		return {
			"text": claim.text,
			"context_url": claim.context_url,
			"reporter": str(claim.reporter),
			"stake_atto": claim.stake_atto,
			"status": claim.status,
			"verdict": claim.verdict,
			"confidence": claim.confidence,
			"reasoning": claim.reasoning,
		}

	@gl.public.view
	def credit_of(self, who: Address) -> u256:
		return self.credits.get(Address(who), u256(0))

	@gl.public.view
	def total_claims(self) -> u256:
		return u256(len(self.claim_ids))
