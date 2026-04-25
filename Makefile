# slu-imc-prosperity-4 — top-level wrapper.
#
# Applies local backtester patches (idempotent) before forwarding any
# target to the backtester submodule's Makefile, so running
#
#     make round3 TRADER=../strategies/round3/my_strat.py
#
# from the repo root always uses the patched simulator (queue-penetration
# wired into the IMC raw-CSV-tape matcher).
#
# Bypass via `cd backtester && make ...` if you specifically want the
# unpatched upstream behavior.

.DEFAULT_GOAL := help

# Don't have make confuse variable assignments (TRADER=foo, DAY=0) for
# targets — they're variables and pass through to the submodule via env.

help: ## Show available backtester targets
	@$(MAKE) -s -C backtester help

# Catch-all: apply backtester patches, then forward the target verbatim to
# the submodule's Makefile.
%:
	@./scripts/patch_backtester.sh
	@$(MAKE) -C backtester $@
