# Contributing

Thanks for helping! Issues and pull requests are welcome.

## Development setup

```bash
git clone https://github.com/recepsuluker/basilisk-mcp.git
cd basilisk-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests tools examples
```

Tests that need Basilisk are skipped automatically when `bsk` is not installed.

## Adding a module type

1. Add a `ModuleType` to `src/basilisk_mcp/registry.py`: unit-explicit parameter names, bounds, ports
   with their Basilisk message types, a priority consistent with Basilisk's execution order.
2. Add its code generation branch and port attribute names to `src/basilisk_mcp/codegen.py`.
3. Add any new message type and its recordable variables to `MESSAGE_TYPES`.
4. Add tests: parameter validation, a generated-script test, and, if possible, a comparison against an
   official Basilisk example in `tests/test_reference_scenarios.py`.
5. Regenerate the catalog docs: `python tools/gen_docs.py`.

## Conventions

The project follows the spirit of Basilisk's own guidelines:

* SI units, with the unit in every physical parameter name and a `# [unit]` comment on physical literals
  in generated code.
* Basilisk frame suffixes (`_B`, `_N`, `_R`) and MRP attitudes.
* Never add a tool that executes client-supplied code or accepts file paths. Read `SECURITY.md` first.
* Error messages say what was wrong and what a valid call looks like.

## Reference data

`tests/data/reference.json` is generated from the unmodified official Basilisk examples:

```bash
git clone --depth 1 https://github.com/AVSLab/basilisk.git /tmp/basilisk
python tools/generate_reference.py /tmp/basilisk
```
