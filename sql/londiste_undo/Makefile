
DATA_built = londiste_undo.sql londiste_undo.upgrade.sql

FUNCS = $(wildcard functions/*.sql)
SRCS = structure/londiste_undo.sql $(FUNCS)

REGRESS = londiste_undo_install londiste_undo
REGRESS_OPTS = --load-language=plpgsql

include ../../config.mak

include $(PGXS)

londiste_undo.sql: $(SRCS)
	cat $(SRCS) > $@

londiste_undo.upgrade.sql: $(FUNCS)
	cat $(FUNCS) > $@

test: londiste_undo.sql
	$(MAKE) installcheck || { less regression.diffs; exit 1; }

ack:
	cp results/* expected/

