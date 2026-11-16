# Security policy

## Supported versions

The project is pre-alpha. Only the `main` branch is supported.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
**Security → Report a vulnerability** on this repository
(<https://github.com/jcjmenez/aliasgate/security/advisories/new>).

Do not open a public issue and do not include real personal data in the report. A synthetic
reproduction is enough.

What counts as a vulnerability here:

- a way to make the gateway forward a detected value to the provider;
- a way to make the gateway write request text, response text or mappings to logs, metrics or
  disk;
- a way to make restoration insert a value from another request;
- a bypass of the fail-closed behaviour.

A value that no detector recognises is a detection gap, not a vulnerability. Report it as a
normal issue with a synthetic example.

You should receive an acknowledgement within seven days.
