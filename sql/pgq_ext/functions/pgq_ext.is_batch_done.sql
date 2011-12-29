
create or replace function pgq_ext.is_batch_done(
    a_consumer text,
    a_subconsumer text,
    a_batch_id bigint)
returns boolean as $$
declare
    res   boolean;
begin
    select last_batch_id = a_batch_id
      into res from pgq_ext.completed_batch
     where consumer_id = a_consumer
       and subconsumer_id = a_subconsumer;
    if not found then
        return false;
    end if;
    return res;
end;
$$ language plpgsql security definer;

create or replace function pgq_ext.is_batch_done(
    a_consumer text,
    a_batch_id bigint)
returns boolean as $$
begin
    return pgq_ext.is_batch_done(a_consumer, '', a_batch_id);
end;
$$ language plpgsql;
