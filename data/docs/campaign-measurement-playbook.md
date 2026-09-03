---
title: Campaign Measurement Playbook
doc_id: campaign-measurement-playbook
---
# Campaign Measurement Playbook

## Standard lift calculation
1. Define the **campaign window** as `start_date` to `end_date` inclusive.
2. Define the **baseline window** as the 28 days immediately preceding `start_date`.
3. Compute revenue for the target category in both windows and normalise per day.
4. **Lift %** = (campaign daily revenue − baseline daily revenue) / baseline daily revenue × 100.
5. Report lift with the number of transacting members in each window.

## Attribution rules
- A transaction line is attributed to a campaign when `transactions.campaign_id` is set.
- Multi-campaign overlap: attribute to the campaign with the higher `discount_pct`.
- Flyer campaigns are measured at banner level; e-mail and app push at member level.

## Guardrails for reporting
- Never report lift on fewer than 200 transacting members.
- Always state the baseline window explicitly in the write-up.
