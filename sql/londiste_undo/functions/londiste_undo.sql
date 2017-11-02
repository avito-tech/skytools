---- -*- indent-tabs-mode: nil -*-
---
--- psql -1 -X --set ON_ERROR_STOP=1 -h $PGHOST -U postgres -d londiste_test -f postgres/share/contrib/londiste_undo.upgrade.sql
---

create or replace function londiste_undo.clean_all_undo() returns void language plpgsql
as $$
begin
  truncate londiste_undo.undo_log;
end
$$;

create or replace function londiste_undo.clean_table_undo(
    i_dst_schema text, i_dst_table text, out cleaned_count int
) returns int language plpgsql
as $$
--- undo для таблицы становится не полным после его отключения
--- соответственно его нельзя использовать для отката, то есть
--- после отключения undo он должен быть очищен
begin
  delete from londiste_undo.undo_log l
  where l.dst_schema = i_dst_schema and l.dst_table = i_dst_table;
  get diagnostics cleaned_count = row_count;
end
$$;

create or replace function londiste_undo.add_trigger(
    i_consumer_name text, i_dst_schema text, i_dst_table text, i_pk text, i_trg_name text default 'xx99_londiste_undo'
) returns void language plpgsql
as $$
declare
  conf_row record;
  v_trg_schema text default 'londiste_undo';
  v_trg_func text default 'undo_trg';
begin
  -- sanity check
  if i_pk is null then
    raise exception 'londiste_undo.add_trigger: PK is unknown, abort';
  end if;

  select * into conf_row from londiste_undo.conf_ext c where c.tbl_schema = i_dst_schema and c.tbl_name = i_dst_table;
  if FOUND then
    v_trg_schema := conf_row.log_trig_schema;
    v_trg_func := conf_row.log_trig_name;
  end if;

  --- если уже есть - будет exception
  insert into londiste_undo.triggers(consumer_name, dst_schema, dst_table, trg_name)
  values (i_consumer_name, i_dst_schema, i_dst_table, i_trg_name);

  execute format($str$
    create trigger %I
    after insert or delete or update
    on %I.%I
    for each row
    execute procedure %I.%I(%L);
  $str$,
    i_trg_name,
    i_dst_schema, i_dst_table,
    v_trg_schema, v_trg_func,
    i_pk
  );
end
$$;

create or replace function londiste_undo.remove_trigger(
   i_dst_schema text, i_dst_table text, i_force_drop boolean default false
) returns void language plpgsql
as $$
declare
  v_trg_name text;
begin
  delete from londiste_undo.triggers t
  where (t.dst_schema, t.dst_table) = (i_dst_schema, i_dst_table)
  returning t.trg_name into v_trg_name;

  if FOUND then
    begin
      execute format($str$
        drop trigger %I
        on %I.%I
      $str$,
        v_trg_name,
        i_dst_schema, i_dst_table
      );
    exception
      when undefined_object then
        if i_force_drop then
          raise notice '% %', SQLSTATE, SQLERRM;
        else
          raise;
        end if;
    end;
  end if;

  perform londiste_undo.clean_table_undo(i_dst_schema, i_dst_table);
end
$$;

create or replace function londiste_undo.enable_trigger(
   i_consumer_name text, i_dst_schema text, i_dst_table text, i_pk text default null
) returns text language plpgsql
as $$
declare
  v_trg_name text;
  v_table text default format('%I.%I', i_dst_schema, i_dst_table);
begin
  --- если is_active уже true - включим триггер ещё раз,
  --- ничего страшного, зато алгоритм включения проще
  update londiste_undo.triggers t set
    last_update_txtime = now(),
    is_active = true
  where (t.dst_schema, t.dst_table) = (i_dst_schema, i_dst_table)
  returning t.trg_name into v_trg_name;

  if not FOUND then
    perform londiste_undo.add_trigger(i_consumer_name, i_dst_schema, i_dst_table, i_pk);
    return v_table;
  end if;

  execute format($str$
    alter table %s enable trigger %I
  $str$,
    v_table, v_trg_name
  );

  return v_table;
end
$$;

create or replace function londiste_undo.disable_trigger(
   i_dst_schema text, i_dst_table text, i_keep_undo boolean default false
) returns text language plpgsql
as $$
declare
  v_trg_name text;
  v_table text default format('%I.%I', i_dst_schema, i_dst_table);
begin
  update londiste_undo.triggers t set
    last_update_txtime = now(),
    is_active = false
  where is_active and (t.dst_schema, t.dst_table) = (i_dst_schema, i_dst_table)
  returning t.trg_name into v_trg_name;

  if not FOUND then
    return v_table;
  end if;

  execute format($str$
    alter table %s disable trigger %I
  $str$,
    v_table, v_trg_name
  );

  if not i_keep_undo then
    perform londiste_undo.clean_table_undo(i_dst_schema, i_dst_table);
  end if;

  return v_table;
end
$$;

create or replace function londiste_undo.all_triggers(i_consumer_name text, i_cmd text, i_keep_undo boolean default false) returns text language plpgsql
as $$
declare
  res text;
begin
  --- sanity check
  if nullif(i_consumer_name, '') is null then
    raise exception 'all_triggers: consumer name must be set "%"', i_consumer_name;
  end if;
  if coalesce(i_cmd, '') not in ('disable', 'enable') then
    raise exception 'all_triggers: unknown command "%"', i_cmd;
  end if;

  select string_agg(
    case i_cmd
      when 'disable' then londiste_undo.disable_trigger(t.dst_schema, t.dst_table, i_keep_undo := i_keep_undo)
      when 'enable'  then londiste_undo.enable_trigger(t.consumer_name, t.dst_schema, t.dst_table)
    end,
    ','
  ) into res
  from londiste_undo.triggers t
  where t.consumer_name = i_consumer_name;

  -- если триггеры выключаются при londiste subscriber add TABLE то триггеров ещё может и не быть
  -- так что в таком случае это не ошибка
  -- --- sanity check
  -- if coalesce(res, '') = '' then
  --   --- если all_triggers вызыван для несуществующих триггеров, значит это ошибка
  --   raise exception 'all_triggers: no triggers for consumer "%"', i_consumer_name;
  -- end if;

  res := i_cmd || ': ' || res;

  return res;
end
$$;

create or replace function londiste_undo.undo_trg() returns trigger language plpgsql
as $$
declare
  cmd     char;
  res     record;
  data    hstore;
  pk_data hstore;
  pk_name text;
  tmp     hstore;
begin
  -- --- sanity check
  -- if TG_OP = 'UPDATE' and NEW.PK is distinct from OLD.PK then
  --   raise exception 'undo_trg does not support primary key change';
  -- end if;
  -- if TG_NARGS <> 1 then
  --   raise exception 'undo_trg PK name not set for CREATE TRIGGER';
  -- end if;

  pk_name := TG_ARGV[0];

  if TG_OP = 'INSERT' then
      --- откат для INSERT -> DELETE, нужен PK из NEW
      res := NEW;
      cmd := 'D'; pk_data := hstore(pk_name, hstore(NEW)->pk_name);
  elsif TG_OP = 'UPDATE' then
      --- откат для UPDATE -> UPDATE, нужно старое значение строки и новый PK
      --- для оптимизации, будем хранить только изменённые колонки и новый PK
      res := NEW;
      tmp := hstore(NEW);
      cmd := 'U'; pk_data := hstore(pk_name, tmp->pk_name); data := (hstore(OLD) - tmp);
      --- TODO: если data = ''::hstore то можно не сохранять такую операцию в UNDO, так как
      --- при откате она всё равно будет пропущена (триггеры при откате выключены и делать
      --- пустой UPDATE бессмысленно)
  elsif TG_OP = 'DELETE' then
      --- откат для DELETE -> INSERT, нужно старое значение строки
      res := OLD;
      cmd := 'I';
      --- NULL можно не хранить, так как undo делает populate_record(NULL::%s, $1)
      --- и все отсутствующие поля и так станут NULL
      --- (populate_record(NULL::%s, NULL) работает правильно, так что coalesce тут не нужен)
      select hstore(array_agg(h.key), array_agg(h.value))
             into data
      from each(hstore(OLD)) h
      where h.value is not null;
  end if;

  insert into londiste_undo.undo_log (dst_schema, dst_table, undo_cmd, cmd_data, cmd_pk)
  values (TG_TABLE_SCHEMA, TG_TABLE_NAME, cmd, data, pk_data);

  return res;
end
$$;

create or replace function londiste_undo.x_rotate_undolog(i_log_verbose boolean default true, i_keep_log_time interval default null) returns int language plpgsql
as $$
--- удалять нужно тиками (батчами), то есть либо tick есть целиком, либо все его записи в UNDO log удалены
--- нельзя оставлять часть записей tick так как это приведёт таблицу к неконсистентному виду при проигрывании UNDO
declare
  keep_log_time constant interval := coalesce(i_keep_log_time, '1 hour 20 minutes');
  del_cnt integer;
  v_size text;
  v_count int;
  v_max_tick_id bigint;
  v_last_max_tick_id bigint;
begin
  --- если в undo_log ничего не писали с последнего ротейта то ротейтить не нужно
  select max(l.tick_id) into v_max_tick_id from londiste_undo.undo_log l;
  select l.max_tick_id into v_last_max_tick_id from londiste_undo.last_rotate l;
  if not FOUND then
    insert into londiste_undo.last_rotate (max_tick_id) values (v_max_tick_id);
  else
    if v_max_tick_id is not distinct from v_last_max_tick_id then
      return -1;
    end if;
    update londiste_undo.last_rotate set max_tick_id = v_max_tick_id;
  end if;

  if i_log_verbose then
    select pg_size_pretty(pg_total_relation_size('londiste_undo.undo_log')), count(1)
           into v_size, v_count
    from londiste_undo.undo_log;
  end if;

  --- так как батч проигрывается в одной транзакции то у всех его записей будет гарантировано одно и то же время
  --- так что можно удалять записи из лога просто по времени
  delete from londiste_undo.undo_log l
  where l.txtime < now() - keep_log_time;

  get diagnostics del_cnt = row_count;

  if i_log_verbose then
    --- write only to log and skip CONTEXT
    set local log_min_messages to debug1;
    set local log_error_verbosity to terse;
    raise debug 'londiste_undo.x_rotate_undolog: size %, count % -> %, delete %',
                v_size, v_count, v_count - del_cnt, del_cnt;
    reset log_error_verbosity;
    reset log_min_messages;
  end if;

  return del_cnt;
end
$$;

--------------------------------------------------------------------------------
-- do undo functions
--------------------------------------------------------------------------------

create or replace function londiste_undo._gen_where_pk(i_alias text, i_keys hstore) returns text language sql
as $$
  select string_agg(format('%I.%I = %L', i_alias, x.key, x.value), ' AND ') from each(i_keys) x;
$$;

create or replace function londiste_undo._gen_upd_set(i_keys hstore) returns text language sql
as $$
  select string_agg(format('%I = %L', x.key, x.value), ', ') from each(i_keys) x;
$$;

create or replace function londiste_undo.run_undo(i_consumer_name text, i_keep_tick_id bigint, i_use_tmp_applied boolean default false) returns int language plpgsql
as $$
--- откатываем в порядке обратном их записи в log (order by id desc)
--- i_keep_tick_id — это последний tick_id который _останется_ применённым после отката
--- то есть откатываются все данные, tick_id которых строго больше указанного i_keep_tick_id
---
--- стоит ли группировать запросы в блоки или выполнять сразу по одному?
--- как отключать триггер undo log при проигрывании undo?
--- если таблица с партицирующим триггером, как быть тогда?
--- нужно ли для INSERT выделять PK по аналогии с D,U?
declare
  tbl_alias constant text := 'd';
  tbl_name text;
  query text;
  u londiste_undo.undo_log;
  applied_cnt int default 0;
  update_empty_cnt int default 0;
  tmp text;
  locked text[] default '{}';
  hooks_query text;
  rec record;
  disabled_tables_trg text[] default '{}';
begin
  --- sanity check
  if coalesce(i_consumer_name, '') = '' then
    raise exception 'run_undo: consumer name must be set, abort';
  end if;
  if coalesce(i_keep_tick_id, 0) = 0 then
    raise exception 'run_undo: i_keep_tick_id must be set, abort';
  end if;
  if  i_keep_tick_id >= londiste.get_last_tick(i_consumer_name) then
    raise exception 'run_undo: i_keep_tick_id must be less than current subscriber last tick';
  end if;

  select string_agg('select ' || o_func || '($1)', ';') into hooks_query from londiste_undo.subscriber_undo_hooks(i_consumer_name) o_func;

  truncate londiste_undo.tmp_applied_undo_log;

  --- установить последний проигранный tick londiste в tick который останется
  --- после проигрывания UNDO (i_keep_tick_id)
  --- TODO: использовать londiste.set_last_tick?
  update londiste.completed c set last_tick_id = i_keep_tick_id where c.consumer_id = i_consumer_name;
  if not found then
    raise exception 'current state for consumer ''%'' not found in londiste.completed table, abort', i_consumer_name;
  end if;
  raise notice 'set ''%'' last_tick_id to %', i_consumer_name, i_keep_tick_id;

  --- необходимо заблокировать саму таблицу с UNDO что бы гарантировать
  --- что со следующим select ... from undo выбраны и заблокированы все
  --- находящиеся там таблицы
  ---
  --- чтобы небыло deadlocks нужно чтобы при undo сами londiste были выключены
  --- TODO: возможно достаточно блокировки INSERT, а SELECT разрешить?
  lock londiste_undo.undo_log;

  --- свои триггеры пишущие UNDO отключаем всегда
  raise notice 'disable all UNDO triggers';
  perform londiste_undo.all_triggers(i_consumer_name, 'disable', i_keep_undo := true);

  --- необходимо заблокировать все таблицы которые будут откатываться
  --- что бы откат шёл с самой последней записи и не получилось вдруг
  --- «висящих» записей (от конкурентных транзакций) сверху уже отменённых строк
  ---
  --- так как откат это не реальные события системы, то все триггеры нужно отключить
  --- но если откатываемая таблица партицируется - то возможно отключать триггеры не нужно
  --- подсматриваем londiste_undo.conf_ext таблицу
  for u in
    select * from londiste_undo.undo_log x
    where x.consumer_name = i_consumer_name and x.tick_id > i_keep_tick_id
  loop
    tbl_name := format('%I.%I', u.dst_schema, u.dst_table);
    if tbl_name <> all (locked) then
      raise notice 'locking % ...', tbl_name;
      execute 'lock ' || tbl_name;
      locked := locked || array[tbl_name];

      --- если есть спец. конф. для этой таблицы и там стоит «использовать триггеры»
      --- то НЕ отключать триггеры для неё, иначе - отключать
      perform 1 from londiste_undo.conf_ext c
      where c.tbl_schema = u.dst_schema and c.tbl_name = u.dst_table
            and c.use_triggers;
      if FOUND then
        raise notice 'use triggers %', tbl_name;
      else
        execute format('alter table %s disable trigger all', tbl_name);
        raise notice 'disable triggers %', tbl_name;
        disabled_tables_trg := disabled_tables_trg || array[tbl_name];
      end if;
    end if;
  end loop;

  raise notice 'hooks query: "%"', hooks_query;

  --- так как при аварии теряется не так много записей, то для отката можно их сразу
  --- все выбрать, без деления на батчи
  for u in with
    src as (
      delete from londiste_undo.undo_log x
      where x.consumer_name = i_consumer_name and x.tick_id > i_keep_tick_id returning *
    ),
    ins as (
      insert into londiste_undo.tmp_applied_undo_log
      select *, now() from src
    )
    select * from src order by src.id desc
  loop
    if hooks_query is not null then
      execute hooks_query using u;
    end if;

    if u.undo_cmd = 'U' and u.cmd_data is null or u.cmd_data = ''::hstore then
      --- пустой update, так как триггеры отключены, то делать его бессмысленно, пропускаем
      update_empty_cnt := update_empty_cnt + 1;
      continue;
    end if;
    applied_cnt := applied_cnt + 1;
    tbl_name := format('%I.%I', u.dst_schema, u.dst_table);

    query := case u.undo_cmd
      when 'D' then 'DELETE FROM ONLY '
      when 'I' then 'INSERT INTO '
      when 'U' then 'UPDATE ONLY '
    end;

    query := query || format('%s %s ', tbl_name, case when u.undo_cmd = 'I' then '' else quote_ident(tbl_alias) end);

    query := query || case u.undo_cmd
      when 'D' then ' WHERE ' || londiste_undo._gen_where_pk(tbl_alias, u.cmd_pk)
      when 'I' then format(' SELECT (populate_record(NULL::%s, $1)).* ', tbl_name)
      when 'U' then ' SET ' || londiste_undo._gen_upd_set(u.cmd_data) || ' WHERE ' || londiste_undo._gen_where_pk(tbl_alias, u.cmd_pk)
    end;

    if u.undo_cmd = 'I' then
      execute query using u.cmd_data;
    else
      execute query;
    end if;
  end loop;

  if update_empty_cnt > 0 then
    raise notice 'skiped empty UPDATEs % ...', update_empty_cnt;
  end if;

  --- вернём обратно отключенные триггеры
  for rec in select unnest(disabled_tables_trg) as tbl_name
  loop
    execute format('alter table %s enable trigger all', rec.tbl_name);
    raise notice 'enable triggers %', rec.tbl_name;
  end loop;

  --- так как UNDO есть только там где были включены триггеры, включим их обратно
  raise notice 'enable all UNDO triggers';
  perform londiste_undo.all_triggers(i_consumer_name, 'enable');

  if not i_use_tmp_applied then
    perform londiste_undo.move_temp_to_applied();
  end if;

  return applied_cnt;
end
$$;

-- хук вызываем для каждого потребителя, так как на разных потребителях могут быть разные items требующие refresh
create or replace function londiste_undo.provider_undo_hooks(IN i_consumer_name text, OUT o_func text) returns setof text language sql
as $$
  select format('%I.%I', h.func_schema, h.func_name) from londiste_undo.provider_undo_hooks h
  where h.consumer_name = i_consumer_name;
$$;

-- хук вызываем для каждого потребителя, так как на разных потребителях могут быть разные items требующие refresh
create or replace function londiste_undo.subscriber_undo_hooks(IN i_consumer_name text, OUT o_func text) returns setof text language sql
as $$
  select format('%I.%I', h.func_schema, h.func_name) from londiste_undo.subscriber_undo_hooks h
  where h.consumer_name = i_consumer_name;
$$;

create or replace function londiste_undo.subscriber_child_tables_func(IN i_func_schema text, IN i_func_name text, OUT o_func text) returns text language plpgsql
as $$
declare
begin
  execute format($str$select '%I.%I(text)'::regprocedure::regproc$str$, i_func_schema, i_func_name) into o_func;
exception
  when undefined_function then
    null;
end
$$;

create or replace function londiste_undo.get_temp_applied_undo(i_consumer_name text, i_keep_tick_id bigint) returns setof londiste_undo.applied_undo_log language sql
as $$
  select * from londiste_undo.tmp_applied_undo_log l
  where l.consumer_name = i_consumer_name and l.tick_id > i_keep_tick_id
  order by l.id desc
$$;

create or replace function londiste_undo.move_temp_to_applied() returns void language sql
as $$
  insert into londiste_undo.applied_undo_log select * from londiste_undo.tmp_applied_undo_log;
  truncate londiste_undo.tmp_applied_undo_log;
$$;

create or replace function londiste_undo.version()
returns text as $$
begin
    return '2.1.13';
end;
$$ language plpgsql;
