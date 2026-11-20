SELECT (SELECT count(*) FROM crm.leads) leads, (SELECT count(*) FROM crm.customers) cust, (SELECT count(*) FROM deals.deals) deals, (SELECT count(*) FROM talk.threads) threads,
 (SELECT count(*) FROM talk.messages) msgs, (SELECT count(*) FROM flow.tasks) tasks, (SELECT count(*) FROM ledger.postings) postings, (SELECT count(*) FROM kb.documents) docs,
 (SELECT count(*) FROM acct.receivables) recv, (SELECT count(*) FROM commission.payees) payees, (SELECT count(*) FROM crm.activities) acts, (SELECT count(*) FROM leadflow.lead_state) ls
