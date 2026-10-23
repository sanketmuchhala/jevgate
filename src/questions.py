from __future__ import annotations
from .schemas import ChoiceSpec, NoulSpec, QuestionSpec, ScoreSpec

# Every document gets all of these in one Jev request (speculative fan-out). Jev
# evaluates them in parallel and independently, so a question no rule ends up
# needing costs a few tokens and no latency. Instructions point at `document`
# by path because the state is {"document": ..., "metadata": ...}.
BATTERY: dict[str, QuestionSpec] = {
    # --- Choice ---------------------------------------------------------------
    "document_type": ChoiceSpec(
        instructions="What kind of institutional document is `document`?",
        options={
            "mission_brief": "Plans, objectives, or briefing material for an upcoming mission, visit, or delegation.",
            "operational_record": "A log, status report, meeting minutes, or record of work already done.",
            "policy_memo": "A memo that proposes, interprets, or announces a policy or decision.",
            "external_correspondence": "A letter, email, or note exchanged with a party outside the institution.",
            "incident_report": "A report of a security, safety, or data incident.",
            "other": "None of the above.",
        },
    ),
    "intended_audience": ChoiceSpec(
        instructions="Who is `document` written for, or about to be shared with?",
        options={
            "internal_team": "Only the author's own team or unit.",
            "institution_wide": "Staff across the institution, but no one outside it.",
            "partner_government": "Officials of a partner or host government.",
            "public_release": "The press or the general public.",
            "unclear": "The document gives no indication of its audience.",
        },
    ),
    # --- Score ----------------------------------------------------------------
    "sensitivity": ScoreSpec(
        instructions="How much harm would follow if `document` were disclosed outside the institution?",
        levels=[
            "Public: no harm; it could be published as written.",
            "Internal: minor embarrassment or inconvenience; routine internal business.",
            "Confidential: would damage a relationship, a negotiation, or an operation.",
            "Restricted: could endanger people, or compromise security or a negotiation outright.",
        ],
    ),
    "urgency": ScoreSpec(
        instructions="How soon does `document` require someone to act?",
        levels=[
            "No action or deadline.",
            "Action needed this week.",
            "Action needed within 24 hours.",
            "Immediate action needed: a safety risk, or a deadline within hours.",
        ],
    ),
    # --- Noul (yes/no) --------------------------------------------------------
    "personal_data": NoulSpec(
        instructions=(
            "Does `document` contain personal data about an identifiable individual, such as a personal "
            "phone number, home address, passport or ID number, or health information?"
        ),
    ),
    "foreign_official_named": NoulSpec(
        instructions="Does `document` name a specific official of a foreign or host government?",
    ),
    "negotiating_position": NoulSpec(
        instructions="Does `document` state the institution's negotiating position, red lines, or fallback terms?",
    ),
    "movement_details": NoulSpec(
        instructions=(
            "Does `document` disclose travel itineraries, accommodation, routes, or security arrangements "
            "for named staff or delegations?"
        ),
    ),
    "financial_commitment": NoulSpec(
        instructions="Does `document` commit the institution to spend, transfer, or guarantee funds?",
    ),
    "legal_exposure": NoulSpec(
        instructions=(
            "Does `document` mention litigation, sanctions, export controls, or a regulatory investigation "
            "involving the institution?"
        ),
    ),
    "security_incident": NoulSpec(
        instructions="Does `document` report a security, safety, or data-breach incident?",
    ),
    "release_requested": NoulSpec(
        instructions="Does `document` ask for itself or its contents to be shared outside the institution?",
    ),
}
