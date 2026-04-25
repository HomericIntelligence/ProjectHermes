# Security Policy

## Supported Versions

| Version | Supported          | Notes                                                     |
|---------|--------------------|-----------------------------------------------------------|
| main    | Yes                | Latest development version; receives all security fixes   |
| 0.x     | Latest commit only | Pre-v1.0; only the latest commit on main is supported     |

**Version Support Policy:**

ProjectHermes is in active development (pre-v1.0). We recommend always running the latest commit from the `main` branch to receive the most up-to-date security fixes and improvements. Once we reach v1.0, we will provide a more detailed long-term support (LTS) policy.

## Reporting Security Vulnerabilities

**Do not open public issues for security vulnerabilities.**

We take security seriously. If you discover a security vulnerability, please report it responsibly.

## How to Report

### Email (Preferred)

Send an email to: **<4211002+mvillmow@users.noreply.github.com>**

Or use the GitHub private vulnerability reporting feature if available.

### What to Include

Please include as much of the following information as possible:

- **Description** - Clear description of the vulnerability
- **Impact** - Potential impact and severity assessment
- **Steps to reproduce** - Detailed steps to reproduce the issue
- **Affected files** - Which source files, endpoints, or configurations are affected
- **Suggested fix** - If you have a suggested fix or mitigation

### Example Report

```text
Subject: [SECURITY] Webhook registration accepts arbitrary callback URLs (SSRF)

Description:
The /webhooks/register endpoint does not validate callback URLs,
allowing an attacker to register webhooks pointing to internal services
and use Hermes as a Server-Side Request Forgery (SSRF) proxy.

Impact:
An attacker could probe internal services, access metadata endpoints,
or exfiltrate data via outbound webhook calls.

Steps to Reproduce:
1. POST /webhooks/register with callback_url="http://169.254.169.254/metadata"
2. Trigger the webhook event
3. Observe Hermes makes requests to the internal metadata endpoint

Affected Files:
src/hermes/routes.py (webhook registration handler)

Suggested Fix:
Validate callback URLs against an allowlist of permitted domains/schemes.
```

## Response Timeline

We aim to respond to security reports within the following timeframes:

| Stage                    | Timeframe              |
|--------------------------|------------------------|
| Initial acknowledgment   | 48 hours               |
| Preliminary assessment   | 1 week                 |
| Fix development          | Varies by severity     |
| Public disclosure        | After fix is released  |

## Severity Assessment

We use the following severity levels:

| Severity     | Description                          | Response           |
|--------------|--------------------------------------|--------------------|
| **Critical** | Remote code execution, data breach   | Immediate priority |
| **High**     | Privilege escalation, data exposure  | High priority      |
| **Medium**   | Limited impact vulnerabilities       | Standard priority  |
| **Low**      | Minor issues, hardening              | Scheduled fix      |

## Responsible Disclosure

We follow responsible disclosure practices:

1. **Report privately** - Do not disclose publicly until a fix is available
2. **Allow reasonable time** - Give us time to investigate and develop a fix
3. **Coordinate disclosure** - We will work with you on disclosure timing
4. **Credit** - We will credit you in the security advisory (if desired)

## What We Will Do

When you report a vulnerability:

1. Acknowledge receipt within 48 hours
2. Investigate and validate the report
3. Develop and test a fix
4. Release the fix
5. Publish a security advisory

## Scope

### In Scope

- Python FastAPI source code and route handlers
- Webhook registration and delivery logic
- NATS client configuration and subject routing
- Dockerfile and container configuration
- Environment configuration (`.env`)

### Out of Scope

- NATS server itself (report to [nats-io](https://github.com/nats-io))
- Odysseus meta-repo (report to [Odysseus](https://github.com/HomericIntelligence/Odysseus))
- Other HomericIntelligence submodule repos (report to that repo directly)
- Social engineering attacks
- Physical security

## Security Best Practices

When contributing to ProjectHermes:

- Validate all webhook callback URLs against an allowlist
- Authenticate webhook senders using signatures or shared secrets
- Never log sensitive payload data or credentials
- Use environment variables for NATS and external service credentials
- Sanitize all external input before publishing to NATS subjects

## Contact

For security-related questions that are not vulnerability reports:

- Open a GitHub Discussion with the "security" tag
- Email: <4211002+mvillmow@users.noreply.github.com>

---

Thank you for helping keep HomericIntelligence secure!
