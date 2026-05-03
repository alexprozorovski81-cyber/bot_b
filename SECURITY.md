# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it privately:

**Email**: alexprozorovski81@gmail.com  
**Subject**: `[SECURITY] PredictBet vulnerability`

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

We aim to respond within 48 hours and patch critical issues within 7 days.
Do **not** open a public GitHub issue for security vulnerabilities.

---

## Secrets That Must NEVER Be Committed

The following must stay in `.env` only and never appear in git history:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token — full API access to your bot |
| `YOOKASSA_SECRET_KEY` | ЮKassa payment secret — enables initiating payments |
| `NOWPAYMENTS_IPN_SECRET` | NOWPayments HMAC secret — webhook authentication |
| `TON_HOT_WALLET_MNEMONIC` | 24-word TON mnemonic — controls the withdrawal wallet |
| `ANTHROPIC_API_KEY` | Claude API key — billed per token |
| `API_SECRET_KEY` | Internal API secret |
| `DATABASE_URL` | Contains DB password |

If any of these are accidentally committed, rotate them immediately:
1. Revoke the old key in the provider dashboard
2. Generate a new key
3. Remove from git history: `git filter-repo --path .env --invert-paths`
4. Force-push: `git push origin master --force`

---

## Removing Secrets from Git History

```bash
pip install git-filter-repo

# Remove .env from all history
git filter-repo --path .env --invert-paths

# Remove database file
git filter-repo --path predictbet.db --invert-paths

# Remove large binary (cloudflared.exe)
git filter-repo --path cloudflared.exe --invert-paths

# After filter-repo, re-add remote and force-push
git remote add origin <your-remote-url>
git push origin master --force
```

**Warning**: `--force` rewrites shared history. Coordinate with all collaborators.
