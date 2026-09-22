Before starting, confirm these integrations are connected and if they are not then ask the user what integrations would they like to use this playbook with

Title: Time Off & Leave Requests

Trigger: Requester asks for PTO, sick day, bereavement, jury duty, or other leave.

1. Instructions: Parse start date, end date, leave type (PTO / sick / bereavement / jury / personal), and reason if provided.
2. #Lookup Users on the requester with includeManager.
3. #Custom Workday Get PTO Balance (or HiBob/UKG equivalent) for the requester, broken down by leave bucket.
4. Validate the request:
- Sufficient balance for PTO? If not, #Send Direct Message with the shortfall and ask to adjust dates or take unpaid.
- Within blackout dates (#Custom Workday Check Blackout Dates action)? If yes, surface the conflict and ask the user to choose a new date.
- Overlap with team's max-out-at-once threshold? #Custom Workday Get Team PTO Calendar action. If exceeded, flag for manager.
5. Branch on leave type:
- PTO / personal → #Request Approval from manager.
- Sick (≤3 days) → auto-approve, no approval needed.
- Sick (>3 days) → auto-approve but #Custom Workday Trigger STD Eligibility Check.
- Bereavement → auto-approve up to policy limit (e.g. 5 days); over that, manager approval.
- Jury duty → auto-approve with proof-of-summons upload via #Trigger Form.
6. On approval:
- #Custom Workday Submit Time Off with the dates and type.
- #Custom Google Calendar Block Time on the requester's calendar with the leave label.
- #Custom Slack Set Status to "On PTO" for the leave window.
- #Out Of Office to set an auto-reply for the leave window (offer to compose; user can edit).
7. #Send Direct Message to the requester confirming with dates, remaining balance, and out-of-office status.
8. #Send Direct Message to the manager confirming the approved leave so they can plan coverage.
9. If leave is >5 consecutive days, schedule_action wake 2 days before the user returns to send a "welcome back" prep with their unread Slack threads and the team's status updates.
10. #Leave Internal Note capturing dates, type, approval path, balance impact.
11. #Resolve Request.

Tools used: Google Calendar; Slack; Workday; HiBob; UKG

Actions used: #Lookup Users; #Send Direct Message; #Request Approval; #Trigger Form; #Out Of Office; #Leave Internal Note; #Resolve Request; #Custom Workday Get PTO Balance; #Custom Workday Check Blackout Dates; #Custom Workday Get Team PTO Calendar; #Custom Workday Trigger STD Eligibility Check; #Custom Workday Submit Time Off; #Custom Google Calendar Block Time; #Custom Slack Set Status