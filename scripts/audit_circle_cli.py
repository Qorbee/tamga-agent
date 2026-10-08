"""Fails on a high or critical advisory in Circle's CLI tree (backend/circle-cli) that is not one
of those already assessed. Reads `npm audit --omit=dev --json` on stdin.

The tree is Circle's, locked as shipped (ADR-0017), and holds advisories it cannot take a fix
for; each is named below with why it does not reach the path that pays on Arc. A new one fails
the audit until it is assessed and named here, or the CLI moves past it.
"""

import json
import sys

# Revisit with the CLI's next release (Dependabot proposes it).
ASSESSED = {
    # node-forge 1.4.0, the newest: RSA PKCS#1 signature verification. The CLI verifies none.
    "GHSA-86w9-cpqp-85rv",
    # toml 3 under the Solana SDK, fixed only in 4.x: it parses no file Tamga gives it.
    "GHSA-82x6-q7mm-w9cf",
    "GHSA-v5mp-jgw5-2x6j",
    # ws 8.18 pinned by viem: a WebSocket peer's frames. The executor's calls go over HTTPS.
    "GHSA-96hv-2xvq-fx4p",
}


def main() -> int:
    report = json.load(sys.stdin)
    # npm exits non-zero both when it finds advisories and when it cannot audit at all (the
    # registry down, a rate limit); the callers ignore its status, so a report with no
    # vulnerabilities map is that failure, never a clean tree.
    if "error" in report or not isinstance(report.get("vulnerabilities"), dict):
        print(f"npm audit gave no report: {report.get('error', report)}")
        return 1
    found = {
        advisory["url"].rsplit("/", 1)[-1]
        for finding in report["vulnerabilities"].values()
        for advisory in finding.get("via", [])
        if isinstance(advisory, dict) and advisory.get("severity") in ("high", "critical")
    }
    new = sorted(found - ASSESSED)
    for advisory in new:
        print(f"not assessed: https://github.com/advisories/{advisory}")
    print(f"{len(found)} high or critical advisories, {len(new)} not assessed")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
