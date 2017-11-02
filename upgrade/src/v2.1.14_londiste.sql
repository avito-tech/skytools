begin;

--- new version have two args
drop function londiste.subscriber_drop_all_table_triggers(i_table_name text);

alter table londiste.subscriber_pending_triggers
add trigger_type char not null;
comment on column londiste.subscriber_pending_triggers.trigger_type is
't — trigger, c - constraint trigger';

\i ../sql/londiste/functions/londiste.find_table_triggers.sql
\i ../sql/londiste/functions/londiste.subscriber_trigger_funcs.sql
\i ../sql/londiste/functions/londiste.version.sql

end;

