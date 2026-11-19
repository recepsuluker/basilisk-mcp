# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[security advisory form](https://github.com/recepsuluker/basilisk-mcp/security/advisories/new)
rather than a public issue. Include steps to reproduce and the affected version. You
should receive an acknowledgement within a week.

## Threat model

basilisk-mcp is a **local, single-user** server. Its tool arguments come from a
language model, and the model can be influenced by untrusted text it has read
(web pages, documents, other tool results). The server therefore treats **every tool
argument as untrusted input**, even though the person using it is trusted.

In scope:

* a malicious or manipulated tool call trying to execute code, read or write files
  outside the data directory, exhaust resources, crash the server, or leak the
  server's environment;
* network attackers reaching the optional HTTP transport, including DNS rebinding
  from a web page in the user's browser;
* tampered files in the data directory.

Out of scope:

* a malicious Basilisk installation or Python interpreter (the server runs whatever
  `python` points to);
* other local users with access to your account;
* multi-tenant or internet-facing hosting (not supported).

## Controls

| Risk | Control | Where |
|---|---|---|
| Code execution | No tool accepts code. Simulations are assembled from a whitelisted module catalog; the generated script contains only `repr()` numbers, fixed enum values and names matching `^[a-z][a-z0-9_]{0,31}$`; it is parsed with `ast` before running. | `registry.py`, `codegen.py` |
| File access | Tools never take paths. Session and run ids are server-generated and validated by regex before lookup. Outputs are written only below `data_dir` (mode `0700`). | `manager.py` |
| Resource exhaustion | Limits on sessions, modules, recorders, runs, simulated duration, integration steps, recorded samples and returned points; wall-clock timeout; concurrency semaphore; POSIX `RLIMIT_AS` (Linux), `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_CORE=0`. | `config.py`, `spec.py`, `runner.py` |
| Server crash | Basilisk only runs in a worker process with its own process group, killed as a group on timeout or cancellation. | `runner.py` |
| Environment leakage / import hijacking | Worker runs `python -I -B` (ignores `PYTHON*` variables, user site-packages and the script directory) with an environment allow-list. | `runner.py` |
| Unsafe deserialization | `numpy.load(allow_pickle=False)`; JSON elsewhere. Stored sessions are replayed through full validation on load. | `results.py`, `manager.py` |
| Network exposure | stdio by default. HTTP refuses non-loopback addresses, requires `BASILISK_MCP_TOKEN` (≥ 32 characters, compared in constant time), and validates `Host`/`Origin`. | `cli.py` |
| Protocol corruption | Logging goes to stderr; stdout is reserved for JSON-RPC. | `cli.py` |
| Over-trusting tools | Tool annotations mark read-only and destructive tools so hosts can ask for confirmation. | `server.py` |

Each control has tests in `tests/test_codegen_security.py`, `tests/test_runner.py`
and `tests/test_spec.py`.

## Residual risks

* The worker is a separate process with resource limits, **not an OS sandbox**
  (no seccomp, namespaces or containers). Because no client-controlled code reaches
  it, this is a defense-in-depth layer rather than the primary control. Run the
  server in a container or VM if you need stronger isolation.
* Windows has no POSIX resource limits; the timeout and process-group termination
  still apply.
* macOS does not enforce `RLIMIT_AS`; memory is bounded indirectly by the step and
  sample limits.
* Plots and CSV exports are written to the data directory and are readable by your
  user account.
