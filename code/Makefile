# Makefile - bf2-counter-collector
#
# Primary target: collect_all, the config-driven collector covering
# all hardware blocks (config.c + catalog.c + collect_all.c).
#   make           -> builds collect_all
#
# Unit tests run on the HOST (x86), never on the device:
#   make test      -> builds and runs test_config
#
# Build natively on BlueField-2 DPU (aarch64 Ubuntu):
#   make
#
# Cross-compile from an x86 host (requires aarch64-linux-gnu-gcc):
#   make CROSS=aarch64-linux-gnu-
#
# Clean:
#   make clean

PROG  := collect_all
SRCS  := collect_all.c config.c catalog.c
OBJS  := $(SRCS:.c=.o)
TEST  := test_config

CROSS  ?=
CC     := $(CROSS)gcc
STRIP  := $(CROSS)strip
HOSTCC ?= cc

# -std=gnu11: needed for strtok_r, clock_gettime, perf_event_open
CFLAGS  := -std=gnu11 -Wall -Wextra -O2 -g
LDFLAGS := -lm -lrt

.PHONY: all test clean release

all: $(PROG)

$(PROG): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)
	@echo "  BUILD  $(PROG)"

$(TEST): test_config.c config.c catalog.c config.h catalog.h
	$(HOSTCC) $(CFLAGS) -o $@ $< config.c catalog.c
	@echo "  BUILD  $(TEST) (host)"

test: $(TEST)
	./$(TEST)

%.o: %.c config.h catalog.h Makefile
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(PROG) $(TEST)

# Build and strip (smaller binary)
release: $(PROG)
	$(STRIP) $(PROG)
	@echo "  STRIP  $(PROG)"
