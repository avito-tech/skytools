
set client_min_messages = 'warning';
\pset null <NULL>

--select londiste_undo.remove_trigger('public', 'undo_test');
create table undo_test (id integer primary key, v text, r float);

select londiste_undo.enable_trigger('londiste_test', 'public', 'undo_test', 'id');
select consumer_name, dst_schema, dst_table, trg_name, is_active from londiste_undo.triggers;
\d undo_test

select londiste_undo.disable_trigger('public', 'undo_test');
select consumer_name, dst_schema, dst_table, trg_name, is_active from londiste_undo.triggers;
\d undo_test

select londiste_undo.remove_trigger('public', 'undo_test');
table londiste_undo.triggers;
\d undo_test

select londiste_undo.add_trigger('londiste_test', 'public', 'undo_test', 'id');
select consumer_name, dst_schema, dst_table, trg_name, is_active from londiste_undo.triggers;
\d undo_test

select londiste_undo.disable_trigger('public', 'undo_test');
select consumer_name, dst_schema, dst_table, trg_name, is_active from londiste_undo.triggers;
\d undo_test

select londiste_undo.enable_trigger('londiste_test', 'public', 'undo_test');
select consumer_name, dst_schema, dst_table, trg_name, is_active from londiste_undo.triggers;
\d undo_test

do $$
begin
  begin
    perform londiste_undo.all_triggers(null, null);
    raise warning '*** TEST ERROR';
  exception when others then
    raise warning '*** TEST EXCEPTION: (%) (%)',  SQLSTATE, SQLERRM;
  end;
  begin
    perform londiste_undo.all_triggers('', null);
    raise warning '*** TEST ERROR';
  exception when others then
    raise warning '*** TEST EXCEPTION: (%) (%)',  SQLSTATE, SQLERRM;
  end;
  begin
    perform londiste_undo.all_triggers('', 'enable');
    raise warning '*** TEST ERROR';
  exception when others then
    raise warning '*** TEST EXCEPTION: (%) (%)',  SQLSTATE, SQLERRM;
  end;
  begin
    perform londiste_undo.all_triggers('londiste_test', null);
    raise warning '*** TEST ERROR';
  exception when others then
    raise warning '*** TEST EXCEPTION: (%) (%)',  SQLSTATE, SQLERRM;
  end;
  begin
    perform londiste_undo.all_triggers('londiste_test', '');
    raise warning '*** TEST ERROR';
  exception when others then
    raise warning '*** TEST EXCEPTION: (%) (%)',  SQLSTATE, SQLERRM;
  end;
  begin
    perform londiste_undo.all_triggers('londiste_test', 'xxx');
    raise warning '*** TEST ERROR';
  exception when others then
    raise warning '*** TEST EXCEPTION: (%) (%)',  SQLSTATE, SQLERRM;
  end;
end
$$;

select londiste_undo.all_triggers('londiste_test', 'enable');
select consumer_name, dst_schema, dst_table, trg_name, is_active from londiste_undo.triggers;
\d undo_test

select londiste_undo.all_triggers('londiste_test', 'disable');
select consumer_name, dst_schema, dst_table, trg_name, is_active from londiste_undo.triggers;
\d undo_test

select londiste_undo.all_triggers('londiste_test', 'enable');
select consumer_name, dst_schema, dst_table, trg_name, is_active from londiste_undo.triggers;
\d undo_test

-- truncate londiste_undo.undo_log, undo_test restart identity;
table londiste_undo.undo_log;
table londiste_undo.applied_undo_log;

begin; --- tick id 5586

select londiste.set_last_tick('londiste_test', 5586);

--- установка переменных которые выставляет наш патченный londiste
select set_config('londiste.tick_id', 5586::text, true);
select set_config('londiste.prev_tick_id', 5585::text, true);
select set_config('londiste.batch_end', '2014-10-24T17:13:49.391251+04:00'::timestamptz::text, true);
select set_config('londiste.batch_id', 114924310::text, true);
select set_config('londiste.consumer_name', E'londiste_test'::text, true);
select set_config('londiste.batch_start', '2014-10-24T17:14:40.477007+04:00'::timestamptz::text, true);

--- эмуляция проигрывание событий londiste

insert into undo_test values (10, 'one 10', 1./3.);
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

update undo_test set v = v || ' 20' where id = 10;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

delete from undo_test where id = 10;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

insert into undo_test values (10, 'one 22', 11./33.);
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

table undo_test;

commit;

begin; --- tick id 5587

select londiste.set_last_tick('londiste_test', 5587);

--- установка переменных которые выставляет наш патченный londiste
select set_config('londiste.tick_id', 5587::text, true);
select set_config('londiste.prev_tick_id', 5586::text, true);
select set_config('londiste.batch_end', '2014-10-24T17:14:49.477007+04:00'::timestamptz::text, true);
select set_config('londiste.batch_id', 114924317::text, true);
select set_config('londiste.consumer_name', E'londiste_test'::text, true);
select set_config('londiste.batch_start', '2014-10-24T17:13:49.391251+04:00'::timestamptz::text, true);

--- эмуляция проигрывание событий londiste

insert into undo_test values (1, 'one', 1./3.);
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

update undo_test set r = r + 10 where id = 1;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

update undo_test set r = r + 20, v = v || ' f1' where id = 1;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

update undo_test set r = r + 20, v = null where id = 1;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

update undo_test set r = null, v = 'xxx' where id = 1;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

update undo_test set r = null, v = null where id = 1;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

update undo_test set r = 123, v = '321' where id = 1;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

delete from undo_test where id = 1;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

insert into undo_test values (1, 'one', 11./33.);
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;

table undo_test;

commit;

set client_min_messages = 'notice';
select londiste_undo.run_undo('londiste_test', 5586);
set client_min_messages = 'warning';

select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.undo_log;
select id, consumer_name, batch_id, batch_start, batch_end, tick_id, prev_tick_id,
       dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk
from londiste_undo.applied_undo_log;

table undo_test;

select pg_sleep(2);

set client_min_messages = 'debug1';
select londiste_undo.x_rotate_undolog(i_keep_log_time := '1 second');
set client_min_messages = 'warning';
