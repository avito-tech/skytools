
begin;

--- new version have two args
drop function londiste.subscriber_drop_all_table_triggers(i_table_name text);

alter table londiste.subscriber_pending_triggers
add trigger_type char not null;
comment on column londiste.subscriber_pending_triggers.trigger_type is
't — trigger, c - constraint trigger';



create or replace function londiste.find_table_triggers(i_table_name text)
returns setof londiste.subscriber_pending_triggers as $$
declare
    tg        record;
    ver       int4;
begin
    select setting::int4 into ver from pg_settings
     where name = 'server_version_num';

    if ver >= 90000 then
        for tg in
            select n.nspname || '.' || c.relname as table_name, t.tgname::text as name, pg_get_triggerdef(t.oid) as def,
                (case when t.tgconstraint > 0 then 'c' else 't' end)::char as trigger_type
            from pg_trigger t, pg_class c, pg_namespace n
            where n.oid = c.relnamespace and c.oid = t.tgrelid
                and t.tgrelid = londiste.find_table_oid(i_table_name)
                and not t.tgisinternal
        loop
            return next tg;
        end loop;
    else
        for tg in
            select n.nspname || '.' || c.relname as table_name, t.tgname::text as name, pg_get_triggerdef(t.oid) as def,
                't'::char as trigger_type
            from pg_trigger t, pg_class c, pg_namespace n
            where n.oid = c.relnamespace and c.oid = t.tgrelid
                and t.tgrelid = londiste.find_table_oid(i_table_name)
                and not t.tgisconstraint
        loop
            return next tg;
        end loop;
    end if;
    
    return;
end;
$$ language plpgsql strict stable;



create or replace function londiste.subscriber_get_table_pending_triggers(i_table_name text)
returns setof londiste.subscriber_pending_triggers as $$
declare
    trigger    record;
begin
    for trigger in
        select *
        from londiste.subscriber_pending_triggers
        where table_name = i_table_name
    loop
        return next trigger;
    end loop;
    
    return;
end;
$$ language plpgsql strict stable;


create or replace function londiste.subscriber_drop_table_trigger(i_table_name text, i_trigger_name text)
returns integer as $$
declare
    trig_def record;
begin
    select * into trig_def
    from londiste.find_table_triggers(i_table_name)
    where trigger_name = i_trigger_name;
    
    if FOUND is not true then
        return 0;
    end if;
    
    insert into londiste.subscriber_pending_triggers(table_name, trigger_name, trigger_def, trigger_type)
        values (i_table_name, i_trigger_name, trig_def.trigger_def, trig_def.trigger_type);

    execute 'drop trigger ' || quote_ident(i_trigger_name)
        || ' on ' || londiste.quote_fqname(i_table_name);
    
    return 1;
end;
$$ language plpgsql;


create or replace function londiste.subscriber_drop_all_table_triggers(i_table_name text, i_trigger_types char[])
returns integer as $$
declare
    trigger record;
begin
    for trigger in
        select trigger_name as name
        from londiste.find_table_triggers(i_table_name)
        where trigger_type = any (i_trigger_types)
    loop
        perform londiste.subscriber_drop_table_trigger(i_table_name, trigger.name);
    end loop;
    
    return 1;
end;
$$ language plpgsql;


create or replace function londiste.subscriber_restore_table_trigger(i_table_name text, i_trigger_name text)
returns integer as $$
declare
    trig_def text;
begin
    select trigger_def into trig_def
    from londiste.subscriber_pending_triggers
    where (table_name, trigger_name) = (i_table_name, i_trigger_name);
    
    if not found then
        return 0;
    end if;
    
    delete from londiste.subscriber_pending_triggers 
    where table_name = i_table_name and trigger_name = i_trigger_name;
    
    execute trig_def;

    return 1;
end;
$$ language plpgsql;


create or replace function londiste.subscriber_restore_all_table_triggers(i_table_name text)
returns integer as $$
declare
    trigger record;
begin
    for trigger in
        select trigger_name as name
        from londiste.subscriber_get_table_pending_triggers(i_table_name)
    loop
        perform londiste.subscriber_restore_table_trigger(i_table_name, trigger.name);
    end loop;
    
    return 1;
end;
$$ language plpgsql;





create or replace function londiste.version()
returns text as $$
begin
    return '2.1.14';
end;
$$ language plpgsql;



end;


