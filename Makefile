# `make test` runs every test in this room, whatever the language.
# The tests run against an empty settings file, so your own ~/.config/antechamber/config.toml never leaks in.
.PHONY: test
test:
	cd engine && ANTECHAMBER_CONFIG=/nonexistent/antechamber.toml python3 -m unittest discover -p 'test_*.py'
