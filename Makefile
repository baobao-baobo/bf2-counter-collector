# bf2-counter-collector - root Makefile, delegates to code/
#
#   make [CROSS=aarch64-linux-gnu-]   build the collector
#   make test                         build and run host unit tests
#   make clean                        remove build artifacts

all:
	$(MAKE) -C code all

test:
	$(MAKE) -C code test

clean:
	$(MAKE) -C code clean

.PHONY: all test clean
