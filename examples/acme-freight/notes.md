# Discovery call notes: Acme Freight GmbH

Fictional sample for the demo. Every name, number and address in this folder is made up.

- Client: Jane Example, Head of Operations, Acme Freight GmbH, jane@acme-example.com
- Country: Germany. Direct contract, not through a marketplace.
- Outcome of the call: warm. She wants the proposal this week, decision with her CFO on Friday.
- Business: regional freight and delivery company, about 200 employees, 40 trucks, B2B customers in retail.

Problem
- Their larger retail customers now ask for proof per delivery: who drove, arrival time, condition of the goods.
- Drivers fill paper logbooks. Two office staff type them into Excel every evening.
- Customer reports take 2-3 days, and typing errors slip through.
- Customer questions about single deliveries come in by email and pile up.

What we discussed as the solution
- Short mobile check-in form per delivery for the drivers, no app install (vehicle, arrival time, condition, signature photo).
- One Google Sheet as the single source of truth, with input checks.
- n8n builds a PDF delivery report per customer and emails it the same day.
- AI assistant drafts replies to delivery questions from the Sheet. A person approves every reply for the first month.

Platforms: n8n (self-hosted), Google Workspace (Forms, Sheets, Gmail), OpenAI API.
Timeline: about 3 weeks.
Price: EUR 4,800 net, fixed price, 50% deposit.
Running costs the client pays directly: OpenAI about EUR 20/month, n8n server about EUR 10/month.
