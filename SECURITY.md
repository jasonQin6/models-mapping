# Security Assessment: GitHub Actions → SSH → Server

## Architecture

```
GitHub Actions (ubuntu-latest)
  │
  │ 1. curl raw.githubusercontent.com → hash check
  │ 2. SSH into user's server
  ▼
User's Server (public IP)
  │
  │ 3. codex exec → fetch_data.py → compute_mapping.py
  ▼
Output: mapping CSV
```

## Threat Model

### 1. SSH Key Exposure

**Risk:** The SSH private key is stored in GitHub Secrets. If GitHub's secret storage is compromised, or if a malicious workflow gains access to secrets, the key is exposed.

**Mitigation:**
- Use a **dedicated SSH key** for this workflow only, not your personal key
- Restrict the SSH user to a **non-root, limited-privilege account**
- Use **ed25519** key type (stronger, shorter than RSA)
- Rotate the key periodically (e.g. every 90 days)
- Consider using GitHub's **encrypted secrets** with environment protection rules

**Severity:** Medium — GitHub's secret storage is generally secure, but supply-chain attacks on Actions are possible.

---

### 2. SSH Attack Surface

**Risk:** Your server's SSH port is exposed to the internet. GitHub Actions runner IPs are dynamic and shared across many users. An attacker could:
- Brute-force the SSH port
- Exploit SSH vulnerabilities (rare, but possible)
- Use a compromised GitHub runner to SSH into your server

**Mitigation:**
- **Restrict SSH access by IP** (if possible): GitHub publishes [their IP ranges](https://api.github.com/meta). You can whitelist the `actions` IP range in your firewall.
- Use **key-based authentication only** (disable password auth)
- Use **non-standard SSH port** (security through obscurity, but reduces automated scans)
- Enable **fail2ban** or similar brute-force protection
- Consider **SSH certificate authentication** instead of static keys (more complex, but better)

**Severity:** Medium — SSH is a well-audited protocol, but any exposed port is a risk.

---

### 3. Command Injection

**Risk:** If the workflow's SSH command includes untrusted input (e.g. commit message, branch name), an attacker could inject malicious commands.

**Current workflow:** The SSH command is hardcoded:
```bash
ssh ... "cd '$WORKDIR' && codex exec --quiet '...'"
```

The only variable is `$WORKDIR`, which comes from GitHub Secrets (trusted).

**Mitigation:**
- **Never interpolate untrusted input** into the SSH command
- Use **single quotes** around variables to prevent shell expansion
- Validate `CODEX_WORKDIR` secret to ensure it doesn't contain shell metacharacters

**Severity:** Low — current implementation is safe, but requires discipline to maintain.

---

### 4. GitHub Actions Supply Chain

**Risk:** The workflow uses third-party Actions (`actions/checkout@v4`). If these are compromised, the attacker gains access to secrets and the SSH key.

**Mitigation:**
- **Pin Actions by SHA** instead of tag (e.g. `actions/checkout@a5ac7e51b26de5...` instead of `@v4`)
- Regularly audit the Actions you use
- Use **GitHub's dependency graph** to monitor for vulnerabilities

**Example:**
```yaml
- uses: actions/checkout@a5ac7e51b26de5b6c89c4f3e7e4c8b5e8f3e7e51  # v4.1.0
```

**Severity:** Medium — supply-chain attacks are rare but high-impact.

---

### 5. Codex Exec Privileges

**Risk:** The `codex exec` command runs on your server with the privileges of the SSH user. If codex is compromised or executes malicious code, it could:
- Read/write files in the working directory
- Execute arbitrary commands
- Access environment variables (e.g. API keys)

**Mitigation:**
- Run codex as a **dedicated, non-root user**
- Use **filesystem sandboxing** (if codex supports it)
- Limit the user's access to only the necessary directories
- Use **environment-specific API keys** (e.g. a test key, not production)

**Severity:** High — codex exec has broad capabilities, but this is inherent to the design.

---

### 6. Data Integrity

**Risk:** An attacker could tamper with the `opencode-source.hash` file in the repo, causing the workflow to skip legitimate updates or trigger on false positives.

**Mitigation:**
- The hash file is updated by the workflow itself (trusted)
- Use **branch protection rules** to require PR reviews for changes to `models-mapping/references/`
- Monitor the git history for unauthorized changes

**Severity:** Low — the hash file is not critical, and tampering would be visible in git history.

---

## Recommendations

### Must-Do

1. **Use a dedicated SSH key** — never use your personal key
2. **Disable password authentication** — key-only
3. **Restrict SSH user privileges** — non-root, limited to the working directory
4. **Pin Actions by SHA** — prevent supply-chain attacks
5. **Enable branch protection** — require PR reviews for sensitive files

### Recommended

6. **Whitelist GitHub Actions IPs** — reduce SSH attack surface
7. **Enable fail2ban** — brute-force protection
8. **Use a non-standard SSH port** — reduce automated scans
9. **Rotate SSH key every 90 days** — limit exposure window
10. **Audit workflow logs regularly** — detect anomalies

### Optional (High Security)

11. **SSH certificate authentication** — short-lived credentials
12. **Hardware security module (HSM)** for key storage
13. **Network segmentation** — run codex in a container/VM

---

## Comparison: SSH vs. Alternatives

| Approach | Security | Complexity | Cost |
|----------|----------|------------|------|
| **SSH (this workflow)** | Medium — key exposure risk | Low | Free |
| **Hermes webhook** | High — HMAC auth, no SSH | Medium | Free (self-hosted) |
| **Self-hosted runner** | High — no exposed ports | Medium | Free |
| **Telegram bot** | Medium — bot token exposure | Low | Free |

**Conclusion:** SSH is acceptable for this use case if you follow the must-do recommendations. For higher security, consider a self-hosted runner (eliminates SSH entirely) or Hermes webhook (if you need bidirectional communication).

---

## Incident Response

If you suspect a compromise:

1. **Revoke the SSH key** immediately (remove from `~/.ssh/authorized_keys` on the server)
2. **Rotate GitHub Secrets** (generate a new SSH key, update the secret)
3. **Audit server logs** (`/var/log/auth.log` or equivalent)
4. **Check git history** for unauthorized changes to the hash file
5. **Review GitHub Actions logs** for anomalous workflow runs

---

## References

- [GitHub Actions security hardening](https://docs.github.com/en/actions/security-guides/security-hardening)
- [SSH best practices](https://www.ssh.com/academy/ssh/best-practices)
- [GitHub Actions IP ranges](https://api.github.com/meta)
