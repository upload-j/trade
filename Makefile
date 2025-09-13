.PHONY: help venv install install-local serve delayed-serve chain rank clean

PY?=python3
PIP?=$(PY) -m pip
VENV_DIR:=.venv
PY_VENV:=$(VENV_DIR)/bin/python
PIP_VENV:=$(VENV_DIR)/bin/pip

# Defaults (override on command line):
SYMBOL?=AAPL
EXPIRY?=2025-09-19
RIGHT?=BOTH
WINDOW?=20
MAX_CONTRACTS?=200
METRIC?=delta_per_theta
TOP?=10
MD_TYPE?=

help:
	@echo "Targets: venv install install-local serve delayed-serve chain rank clean"
	@echo "Variables: SYMBOL EXPIRY RIGHT WINDOW MAX_CONTRACTS METRIC TOP MD_TYPE"

venv:
	@test -d $(VENV_DIR) || $(PY) -m venv $(VENV_DIR)
	$(PIP_VENV) install -U pip

install: venv
	$(PIP_VENV) install -r requirements_ib_options_mcp.txt

# Use local vendored ib_async via editable install
install-local: venv
	$(PIP_VENV) install -e ./ib_async
	$(PIP_VENV) install -r requirements_ib_options_mcp.txt

serve: venv
	IB_MD_TYPE=$(MD_TYPE) $(PY_VENV) ib_options_mcp_server.py serve

delayed-serve: venv
	IB_MD_TYPE=3 $(PY_VENV) ib_options_mcp_server.py serve

chain: venv
	$(PY_VENV) ib_options_mcp_server.py chain \
		--symbol $(SYMBOL) \
		--expiry $(EXPIRY) \
		--right $(RIGHT) \
		--window $(WINDOW) \
		--max-contracts $(MAX_CONTRACTS) \
		$$(test -n "$(MD_TYPE)" && echo --md-type $(MD_TYPE) || true)

rank: venv
	$(PY_VENV) ib_options_mcp_server.py rank \
		--symbol $(SYMBOL) \
		--expiry $(EXPIRY) \
		--right $(RIGHT) \
		--window $(WINDOW) \
		--max-contracts $(MAX_CONTRACTS) \
		--metric $(METRIC) \
		--top $(TOP) \
		$$(test -n "$(MD_TYPE)" && echo --md-type $(MD_TYPE) || true)

clean:
	rm -rf $(VENV_DIR)
