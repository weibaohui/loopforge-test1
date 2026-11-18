PYTHON ?= python3

.PHONY: all test check clean

all: test

# Single entry point for the self-verification suite (AC-1).
test:
	$(PYTHON) -m unittest discover -s tests -t . -v

# Static hygiene checks: byte compilation, no leftover markers, documented
# public API (AC-12).
check:
	$(PYTHON) -m compileall -q httpbinary tests
	@if grep -rnE "TODO|FIXME" httpbinary tests; then \
		echo "ERROR: TODO/FIXME markers found"; \
		exit 1; \
	fi
	@$(PYTHON) -c "import httpbinary, inspect; missing=[n for n in httpbinary.__all__ if (inspect.isfunction(getattr(httpbinary, n)) or inspect.isclass(getattr(httpbinary, n))) and not getattr(httpbinary, n).__doc__]; assert not missing, 'missing docstring: %s' % missing; print('public API is documented')"

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
