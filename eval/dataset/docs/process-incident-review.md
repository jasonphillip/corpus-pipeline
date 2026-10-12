# Process: Incident Review

Synthetic document, Larkspur Systems wiki (fictional).

Every incident of SEV1 or SEV2 gets a written review. A SEV3 incident gets a review when the on-call engineer or the platform lead asks for it. The review looks for causes and fixes, not for blame.

## Severity levels

- SEV1 means data loss on more than 25 percent of nodes, or ingest down for more than 30 minutes.
- SEV2 means a visible degradation: delayed data, loss of a launch site or a failed dashboard during a launch, without the SEV1 thresholds.
- SEV3 means an internal fault or a small number of nodes affected, with no visible effect on launch coordinators.

The on-call engineer sets the level during the incident. The platform lead can change it afterwards.

## Timing

Hold the review within 5 business days. Publish the written report within 10 business days. Write the timeline while the incident is fresh, using the chat history and the alert log.

## Roles

- The incident lead owns the review and writes the report.
- A facilitator, from outside the incident, runs the meeting.
- Everyone who touched the incident is invited.

## Report structure

Every report has these parts in order: summary, impact, timeline, root cause, fix, follow-up actions and lessons. Keep the report short. A reader should understand it in 5 minutes.

## Rules for the meeting

1. Start from the timeline and fix errors in it.
2. Ask why until the answer is a fixable condition, not a person.
3. Use neutral phrases, such as the alert did not fire, rather than who missed the alert.
4. Agree follow-up actions with owners and dates.

## Follow-up actions

Every action has one owner and a due date. The platform lead tracks them in a table. An action that is overdue by 30 days is raised at the weekly meeting. Do not close an action without a link to the change that did the work.

## Pattern check

Each quarter the platform lead reads all reports from the quarter and lists repeated causes. A cause that appears three times becomes a project, not a series of fixes.

## Where reports live

Reports are stored in the wiki under the incident section. The file name starts with the date, for example `incident-2026-02-09-queue-overflow`.
