---- -*- indent-tabs-mode: nil -*-
---
--- psql -1 -X --set ON_ERROR_STOP=1 -h $PGHOST -U postgres -d londiste_test -f postgres/share/contrib/londiste_undo.sql
---

do $$
begin
  if not exists (select true from pg_type where typname = 'hstore') then
    create extension if not exists hstore;
  end if;
end
$$;

create schema londiste_undo;

create table londiste_undo.triggers (
    consumer_name text not null,
    dst_schema text not null,
    dst_table  text not null,
    trg_name   text not null,
    is_active  boolean default true,
    last_update_txtime timestamptz default now(),
    --- два consumer на одну таблицу нельзя подписывать
    unique(dst_schema, dst_table)
);

create unlogged table londiste_undo.undo_log (
    id      bigserial,
    txtime  timestamptz default now(),

    -- // из окружения через результат pgq.get_batch_info()
    consumer_name  text        default current_setting('londiste.consumer_name') not null,
    -- 
    batch_id       bigint      default current_setting('londiste.batch_id')     ::bigint,
    batch_start    timestamptz default current_setting('londiste.batch_start')  ::timestamptz,
    batch_end      timestamptz default current_setting('londiste.batch_end')    ::timestamptz,
    -- //!!! откатываем всё, что: batch_tick_id >= ( select sub_last_tick from pgq.subscription where sub_queue, sub_consumer )  /// ?? < или <=
    tick_id        bigint      default current_setting('londiste.tick_id')      ::bigint,
    prev_tick_id   bigint      default current_setting('londiste.prev_tick_id') ::bigint,

    -- // по триггеру
    dst_schema text,
    dst_table  text,
    undo_cmd   char,   -- // I, U, D
    cmd_data   hstore, -- // нужно OLD/NEW, без PK
    cmd_pk     hstore  -- // только PK
);

comment on column londiste_undo.undo_log.undo_cmd is
'I, U, D — содержит тип команды, противоположной исходной';
comment on column londiste_undo.undo_log.consumer_name is
'not null - для защиты от ошибок, упадёт если set_config не сработал';

--- таблица для сохранения проигранных (и соответственно удалённых) записей из undo_log
--- потом будет выгружаться в файл
create unlogged table londiste_undo.applied_undo_log( like londiste_undo.undo_log );
alter table londiste_undo.applied_undo_log add apply_txtime timestamptz;
--- таблица для временного хранения проигранных (и соответственно удалённых) записей из undo_log
--- перед commit их нужно переместить в applied_undo_log с помощью move_temp_to_applied()
create unlogged table londiste_undo.tmp_applied_undo_log( like londiste_undo.applied_undo_log );

create table londiste_undo.last_rotate (
    max_tick_id bigint
);
create unique index on londiste_undo.last_rotate ((1)); --- only one row

comment on table londiste_undo.last_rotate is
'таблица с информацией о последнем ротейте UNDO';
comment on column londiste_undo.last_rotate.max_tick_id is
'максимальный tick_id который был в undo_log при последнем ротейтей';

create table londiste_undo.subscriber_undo_hooks (
    consumer_name text not null,
    func_schema text not null,
    func_name text not null
);
create unique index on londiste_undo.subscriber_undo_hooks (consumer_name, (quote_ident(func_schema) || '.' || quote_ident(func_name)));

comment on table londiste_undo.subscriber_undo_hooks is
'вызывать на subscriber указанные хранимки перед применением строки из UNDO';

create table londiste_undo.provider_undo_hooks (
    consumer_name text not null,
    func_schema text not null,
    func_name text not null
);
create unique index on londiste_undo.provider_undo_hooks (consumer_name, (quote_ident(func_schema) || '.' || quote_ident(func_name)));

comment on table londiste_undo.provider_undo_hooks is
'вызывать на provider указанные хранимки после проигрывания UNDO на subscriber';

create table londiste_undo.conf_ext (
    tbl_schema text not null,
    tbl_name text not null,
    log_trig_schema text,
    log_trig_name text,
    use_triggers boolean
);
create unique index on londiste_undo.conf_ext ((quote_ident(tbl_schema) || '.' || quote_ident(tbl_name)));

comment on table londiste_undo.conf_ext is
'доп. параметры UNDO для таблиц: спец. триггер для ведения UNDO, флаг активности триггеров на таблице при откате';

comment on column londiste_undo.conf_ext.log_trig_schema is
'схема хранимки-триггера для записи в UNDO изменений этой таблицы';
comment on column londiste_undo.conf_ext.log_trig_name is
'имя хранимки-триггера для записи в UNDO изменений этой таблицы';
comment on column londiste_undo.conf_ext.use_triggers is
'если true - оставить триггеры этой таблицы включенными при откате UNDO (например партицирование)';

