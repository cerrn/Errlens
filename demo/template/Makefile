PYTHON ?= python3

.PHONY: bug1 bug2 bug3 all

bug1:  ## KeyError: profile page
	$(PYTHON) -m shop profile 1

bug2:  ## TypeError: greeting script
	node scripts/greet.js 1 2 3

bug3:  ## ValueError: daily report
	$(PYTHON) -m shop report

all: bug1 bug2 bug3
